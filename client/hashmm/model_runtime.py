"""One request-time contract for every model provider and Agent surface.

HashMM used to decide *how much work to do* in the UI, in ``streaming.py`` and
again inside the Agent loop.  Provider adapters then guessed which optional
parameters a model might accept.  That creates two expensive failure modes:

* a simple answer is promoted to an Agent run with every tool schema attached;
* an advanced option is sent because a provider supports it, even though the
  configured model was never declared to support it.

This module is deliberately deterministic and provider-neutral.  It does not
claim that a model exists or that an account can use it.  Saved, explicitly
declared model capabilities remain the source of truth.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Iterable


USER_MODE_FAST = "fast"
USER_MODE_AUTO = "auto"
USER_MODE_DEEP = "deep"
_USER_MODES = {USER_MODE_FAST, USER_MODE_AUTO, USER_MODE_DEEP}

EXEC_DIRECT = "direct"
EXEC_RAG = "rag"
EXEC_AGENT = "agent"
EXEC_WORKFLOW = "workflow"
EXEC_MULTI_AGENT = "multi_agent"


@dataclass(frozen=True)
class ModelRequirements:
    """Capabilities that are actually required for this request."""

    tools: bool = False
    vision: bool = False
    structured_output: bool = False
    streaming: bool = True
    reasoning: bool = False
    min_input_tokens: int = 0


@dataclass(frozen=True)
class RuntimePlan:
    """Bounded admission decision shared by Chat, Work and AgentLoop."""

    schema: str
    user_mode: str
    execution: str
    complexity: int
    requires_tools: bool
    requires_retrieval: bool
    allow_planning: bool
    allow_parallel_agents: bool
    max_iterations: int
    max_parallel_agents: int
    tool_schema_budget_chars: int
    reason: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompatibilityResult:
    ok: bool
    missing: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class UsageRecord:
    """Provider-independent token ledger.

    ``input_tokens`` and ``output_tokens`` are the billable top-level counts
    reported by the provider.  Detail fields are subsets and therefore must not
    be added to ``total_tokens`` again.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    def as_legacy(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


_runtime_mode: ContextVar[str] = ContextVar("hashmm_runtime_mode", default=USER_MODE_AUTO)


def normalize_user_mode(value: str | None) -> str:
    """Map old desktop/app effort names to the three user-facing modes."""

    raw = str(value or "").strip().lower()
    aliases = {
        "standard": USER_MODE_AUTO,
        "balanced": USER_MODE_AUTO,
        "max": USER_MODE_DEEP,
        "maximum": USER_MODE_DEEP,
        "high": USER_MODE_DEEP,
        "low": USER_MODE_FAST,
    }
    mode = aliases.get(raw, raw)
    return mode if mode in _USER_MODES else USER_MODE_AUTO


def set_runtime_mode(value: str | None) -> None:
    """Bind mode to the current async request.

    ``asyncio.to_thread`` copies context variables, so synchronous SDK adapters
    see the same request mode without storing per-conversation state in a global
    model callable.
    """

    _runtime_mode.set(normalize_user_mode(value))


def current_runtime_mode() -> str:
    return _runtime_mode.get()


_ACTION_TERMS = (
    "创建", "修改", "删除", "上传", "下载", "运行", "执行", "打开", "点击",
    "填写", "提交", "发布", "部署", "生成文件", "write", "edit", "delete",
    "run ", "execute", "deploy", "upload", "download", "click",
)
_RETRIEVAL_TERMS = (
    "知识库", "资料", "文档", "引用", "证据", "来源", "检索", "调研",
    "research", "source", "document", "knowledge",
)
_PARALLEL_TERMS = (
    "分别", "并行", "多个方案", "对比方案", "多角度", "多个智能体",
    "multi-agent", "parallel", "alternatives",
)
_COMPLEX_TERMS = (
    "完整", "深入", "系统性", "一步一步", "方案", "架构", "实现并验证",
    "测试并修复", "端到端", "long-running", "workflow",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    low = text.lower()
    return any(term.lower() in low for term in terms)


def plan_runtime(
    query: str,
    *,
    requested_mode: str | None = None,
    has_file_context: bool = False,
    has_image: bool = False,
    force_tools: bool = False,
) -> RuntimePlan:
    """Admit the smallest execution shape that can complete the request.

    This is intentionally conservative: multi-Agent is reserved for work with
    separable branches and deep mode.  A long prompt by itself is not evidence
    that multiple Agents will improve the answer.
    """

    text = str(query or "").strip()
    mode = normalize_user_mode(requested_mode)
    # A URL is untrusted data, not an instruction, but reading it still needs
    # the governed browser/fetch path.
    requires_tools = (
        force_tools
        or _contains_any(text, _ACTION_TERMS)
        or bool(re.search(r"https?://", text, flags=re.IGNORECASE))
    )
    requires_retrieval = has_file_context or _contains_any(text, _RETRIEVAL_TERMS)

    clauses = len([p for p in re.split(r"[；;。!\n]|(?:还有|然后|并且)", text) if p.strip()])
    complexity = 1
    if len(text) >= 180:
        complexity += 1
    if clauses >= 3:
        complexity += 1
    if _contains_any(text, _COMPLEX_TERMS):
        complexity += 1
    if requires_tools:
        complexity += 1
    if requires_retrieval:
        complexity += 1
    if _contains_any(text, _PARALLEL_TERMS) and clauses >= 2:
        complexity += 1
    complexity = min(complexity, 5)

    parallelizable = _contains_any(text, _PARALLEL_TERMS) and clauses >= 2
    if mode == USER_MODE_FAST:
        allow_planning = False
        allow_parallel = False
        max_iterations = 5
        tool_budget = 12_000
    elif mode == USER_MODE_DEEP:
        allow_planning = complexity >= 3
        allow_parallel = parallelizable and complexity >= 3
        max_iterations = 14 if requires_tools else 9
        tool_budget = 28_000
    else:
        allow_planning = complexity >= 4
        allow_parallel = False
        max_iterations = 9 if requires_tools else 6
        tool_budget = 18_000

    if allow_parallel:
        execution = EXEC_MULTI_AGENT
        reason = "任务包含可独立验收的并行分支，深度模式允许受限协作"
    elif requires_tools and allow_planning:
        execution = EXEC_WORKFLOW
        reason = "任务需要真实操作且包含多个可验收步骤"
    elif requires_tools:
        execution = EXEC_AGENT
        reason = "任务需要工具产生可验证结果"
    elif requires_retrieval:
        execution = EXEC_RAG
        reason = "任务需要资料或证据，但不需要扩大为操作型 Agent"
    else:
        execution = EXEC_DIRECT
        reason = "直接回答足以完成请求"

    return RuntimePlan(
        schema="hashmm.runtime-plan.v1",
        user_mode=mode,
        execution=execution,
        complexity=complexity,
        requires_tools=requires_tools,
        requires_retrieval=requires_retrieval,
        allow_planning=allow_planning,
        allow_parallel_agents=allow_parallel,
        max_iterations=max_iterations,
        max_parallel_agents=3 if allow_parallel else 1,
        tool_schema_budget_chars=tool_budget,
        reason=reason,
    )


def requirements_for(
    plan: RuntimePlan,
    *,
    vision: bool = False,
    require_reasoning: bool = False,
) -> ModelRequirements:
    return ModelRequirements(
        tools=plan.requires_tools,
        vision=bool(vision),
        structured_output=plan.allow_planning,
        streaming=True,
        # Deep mode means a larger verified work budget. It does not require a
        # provider-native reasoning model unless the caller explicitly asks.
        reasoning=bool(require_reasoning),
    )


def context_input_budget(
    profile: dict[str, Any] | None,
    *,
    user_mode: str | None = None,
) -> int:
    """Derive a safe prompt budget from an explicitly declared model window.

    Unknown windows retain the conservative legacy budget. Large windows are
    capped per user mode so Auto does not spend tokens merely because the
    provider can accept them.
    """

    mode = normalize_user_mode(user_mode)
    mode_caps = {
        USER_MODE_FAST: 8_000,
        USER_MODE_AUTO: 24_000,
        USER_MODE_DEEP: 64_000,
    }
    try:
        declared = int((profile or {}).get("max_input_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        declared = 0
    if declared <= 0:
        return 8_000
    return max(2_048, min(declared, mode_caps[mode]))


def check_compatibility(
    profile: dict[str, Any] | None,
    requirements: ModelRequirements,
) -> CompatibilityResult:
    """Fail closed only for capabilities the request truly requires."""

    p = profile or {}
    missing: list[str] = []
    checks = (
        ("tools", requirements.tools, "supports_tools"),
        ("vision", requirements.vision, "supports_vision"),
        ("structured_output", requirements.structured_output, "supports_structured_output"),
        ("streaming", requirements.streaming, "supports_streaming"),
        ("reasoning", requirements.reasoning, "supports_reasoning"),
    )
    for label, required, key in checks:
        if required and p.get(key) is not True:
            missing.append(label)
    try:
        available_context = int(p.get("max_input_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        available_context = 0
    if requirements.min_input_tokens > 0 and (
        available_context <= 0 or available_context < requirements.min_input_tokens
    ):
        missing.append("context_window")
    if missing:
        return CompatibilityResult(
            False,
            tuple(missing),
            "当前模型未声明请求所需能力：" + "、".join(missing),
        )
    return CompatibilityResult(True, (), "能力契约满足")


def rank_model_configs(
    configs: Iterable[dict[str, Any]],
    requirements: ModelRequirements,
    *,
    user_mode: str = USER_MODE_AUTO,
    preferred_id: str = "",
) -> list[dict[str, Any]]:
    """Rank configured models without hard-coding vendor model names.

    The administrator assigns an optional ``routing_tier`` (fast/auto/deep) in
    each model's config.  A precise model/deployment id remains opaque to
    HashMM.  This keeps routing current when providers rename models.
    """

    from hashmm.model_providers import provider_capability_profile, provider_spec

    mode = normalize_user_mode(user_mode)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for raw in configs or []:
        cfg = dict(raw or {})
        try:
            profile = provider_capability_profile(cfg)
        except Exception:
            continue
        if not check_compatibility(profile, requirements).ok:
            continue
        options = cfg.get("provider_options")
        if not isinstance(options, dict):
            try:
                from hashmm.model_providers import parse_model_options
                options = parse_model_options(cfg)
            except Exception:
                options = {}
        tier = normalize_user_mode(str((options or {}).get("routing_tier") or "auto"))
        score = 0
        if preferred_id and str(cfg.get("id") or "") == preferred_id:
            score += 10_000
        if tier == mode:
            score += 500
        if int(cfg.get("is_default") or 0):
            score += 250
        if mode == USER_MODE_DEEP and profile.get("supports_reasoning") is True:
            score += 120
        if mode == USER_MODE_FAST and provider_spec(
            str(cfg.get("provider") or "")
        ).local:
            score += 80
        # Stable tie-breaker: newest database row first if timestamps exist.
        try:
            score += min(50, int(float(cfg.get("created_at") or 0)) % 51)
        except (TypeError, ValueError, OverflowError):
            pass
        ranked.append((score, str(cfg.get("id") or ""), cfg))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked]


def normalize_usage(value: Any) -> UsageRecord:
    """Normalize OpenAI/Anthropic/compatible usage objects or dictionaries."""

    def get(obj: Any, name: str, default: Any = 0) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    usage = value or {}
    input_tokens = integer(get(usage, "input_tokens", get(usage, "prompt_tokens", 0)))
    output_tokens = integer(get(usage, "output_tokens", get(usage, "completion_tokens", 0)))
    total_tokens = integer(get(usage, "total_tokens", input_tokens + output_tokens))

    input_details = get(usage, "input_tokens_details", get(usage, "prompt_tokens_details", {})) or {}
    output_details = get(usage, "output_tokens_details", get(usage, "completion_tokens_details", {})) or {}
    cache_creation = get(usage, "cache_creation_input_tokens", 0)
    cache_read = get(usage, "cache_read_input_tokens", 0)
    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens or input_tokens + output_tokens,
        reasoning_tokens=integer(get(output_details, "reasoning_tokens", 0)),
        cached_input_tokens=integer(
            get(input_details, "cached_tokens", get(usage, "cached_tokens", cache_read))
        ),
        cache_write_tokens=integer(cache_creation),
    )


def tool_schema_size(tools: Iterable[dict[str, Any]]) -> int:
    """Deterministic schema size used for admission and observability."""

    return sum(
        len(json.dumps(tool, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        for tool in tools or []
    )
