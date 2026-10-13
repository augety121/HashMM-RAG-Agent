"""Provider strategies for HashMM model connections.

The registry deliberately separates three concerns that used to be mixed in
``model_manager.py`` and the UI:

* provider identity and endpoint rules;
* the wire API used by the provider;
* capabilities that decide which request fields may be sent.

Model identifiers are not used as the source of truth here.  Providers retire
and rename models independently of HashMM, so the model id saved by the user (or
returned by a provider model-list endpoint) always wins.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


WIRE_CHAT = "chat_completions"
WIRE_RESPONSES = "responses"
WIRE_ANTHROPIC = "anthropic_messages"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    base_url: str
    wire_apis: tuple[str, ...] = (WIRE_CHAT,)
    default_wire_api: str = WIRE_CHAT
    auth: str = "bearer"
    tools: bool = True
    streaming: bool = True
    vision: bool = True
    json_schema: bool = False
    model_discovery: bool = True
    local: bool = False
    base_url_required: bool = False
    endpoint_note: str = ""
    model_hints: tuple[str, ...] = ()
    # These are protocol-level features, not a promise that every model sold by
    # the provider supports them. Per-model config must explicitly opt in.
    native_state: bool = False
    prompt_cache_telemetry: bool = False
    reasoning_control: str = ""


# Endpoints below are protocol presets, not an assertion that an account has
# access to any particular model.  Account/region-specific services keep an
# empty endpoint or an explicit note so HashMM never fabricates tenant details.
_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("openai", "OpenAI", "https://api.openai.com/v1",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_RESPONSES,
                 json_schema=True, native_state=True,
                 prompt_cache_telemetry=True, reasoning_control="responses"),
    ProviderSpec("azure_openai", "Azure OpenAI / Microsoft Foundry", "",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_RESPONSES,
                 json_schema=True, base_url_required=True, native_state=True,
                 model_discovery=False,
                 prompt_cache_telemetry=True, reasoning_control="responses",
                 endpoint_note="填写资源端点并以 /openai/v1 结尾；模型名填写部署名。"),
    ProviderSpec("anthropic", "Anthropic Claude", "https://api.anthropic.com",
                 (WIRE_ANTHROPIC,), WIRE_ANTHROPIC, model_discovery=False,
                 prompt_cache_telemetry=True, reasoning_control="anthropic"),
    ProviderSpec("deepseek", "DeepSeek", "https://api.deepseek.com",
                 (WIRE_CHAT,), WIRE_CHAT, prompt_cache_telemetry=True,
                 reasoning_control="deepseek",
                 model_hints=("deepseek-v4-flash", "deepseek-v4-pro")),
    ProviderSpec("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                 (WIRE_CHAT,), WIRE_CHAT, json_schema=True,
                 reasoning_control="explicit"),
    ProviderSpec("qwen", "阿里云百炼 / 通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 (WIRE_CHAT,), WIRE_CHAT, reasoning_control="explicit",
                 endpoint_note="生产环境建议粘贴百炼业务空间对应地域的专属 compatible-mode/v1 地址。"),
    ProviderSpec("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
                 reasoning_control="explicit"),
    ProviderSpec("moonshot", "Moonshot / Kimi", "https://api.moonshot.cn/v1",
                 reasoning_control="explicit"),
    ProviderSpec("doubao", "火山方舟 / 豆包", "https://ark.cn-beijing.volces.com/api/v3",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_CHAT,
                 reasoning_control="explicit"),
    ProviderSpec("baidu_qianfan", "百度智能云千帆", "", base_url_required=True,
                 endpoint_note="从千帆控制台复制当前 OpenAI 兼容 Base URL；不要填写旧版完整 chat/completions URL。"),
    ProviderSpec("tencent_hunyuan", "腾讯混元 / TokenHub", "https://tokenhub.tencentmaas.com/v1"),
    ProviderSpec("minimax", "MiniMax", "https://api.minimaxi.com/v1",
                 (WIRE_CHAT, WIRE_RESPONSES), WIRE_CHAT,
                 reasoning_control="explicit"),
    ProviderSpec("mistral", "Mistral AI", "https://api.mistral.ai/v1"),
    ProviderSpec("xai", "xAI", "https://api.x.ai/v1",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_CHAT, json_schema=True,
                 native_state=True, reasoning_control="explicit"),
    ProviderSpec("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1",
                 json_schema=True),
    ProviderSpec("groq", "Groq", "https://api.groq.com/openai/v1"),
    ProviderSpec("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    ProviderSpec("siliconflow", "SiliconFlow 硅基流动", "https://api.siliconflow.cn/v1"),
    ProviderSpec("together", "Together AI", "https://api.together.xyz/v1"),
    ProviderSpec("nvidia_nim", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1"),
    ProviderSpec("aws_bedrock", "Amazon Bedrock", "",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_RESPONSES,
                 base_url_required=True, model_discovery=False, native_state=True,
                 reasoning_control="responses",
                 endpoint_note=(
                     "填写 https://bedrock-mantle.<region>.api.aws/v1，"
                     "使用 Bedrock API Key；生产环境建议改用短期凭证。"
                 )),
    ProviderSpec("oci_genai", "Oracle OCI Generative AI", "",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_RESPONSES,
                 base_url_required=True, model_discovery=False, native_state=True,
                 reasoning_control="responses",
                 endpoint_note=(
                     "填写 https://inference.generativeai.<region>."
                     "oci.oraclecloud.com/openai/v1，使用同地域 OCI GenAI API Key。"
                 )),
    ProviderSpec("perplexity", "Perplexity Agent API", "https://api.perplexity.ai/v1",
                 (WIRE_RESPONSES, WIRE_CHAT), WIRE_RESPONSES,
                 json_schema=True, reasoning_control="explicit"),
    ProviderSpec("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1"),
    ProviderSpec("cerebras", "Cerebras Inference", "https://api.cerebras.ai/v1"),
    ProviderSpec("github_models", "GitHub Models", "https://models.github.ai/inference",
                 model_discovery=False,
                 endpoint_note="使用具备 Models 权限的 GitHub token；模型 ID 以 GitHub Models 目录为准。"),
    ProviderSpec("sambanova", "SambaNova", "", (WIRE_RESPONSES, WIRE_CHAT),
                 WIRE_CHAT, base_url_required=True,
                 endpoint_note="填写账户控制台提供的 SambaNova OpenAI 兼容 Base URL。"),
    ProviderSpec("ollama", "Ollama", "http://127.0.0.1:11434/v1",
                 local=True, auth="optional"),
    ProviderSpec("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1",
                 local=True, auth="optional"),
    ProviderSpec("vllm", "vLLM", "http://127.0.0.1:8000/v1",
                 local=True, auth="optional"),
    ProviderSpec("custom", "OpenAI 兼容服务", "", base_url_required=True,
                 endpoint_note="填写服务商给出的 OpenAI 兼容 API 根地址。"),
    ProviderSpec("sub2api", "Sub2API / 自有反代", "",
                 (WIRE_CHAT, WIRE_RESPONSES), WIRE_CHAT,
                 base_url_required=True,
                 endpoint_note="填写你有权使用的 Sub2API HTTPS 根地址；保存后先测试，再明确设为当前模型。"),
)

_BY_ID = {spec.id: spec for spec in _SPECS}
_ALIASES = {
    "google": "gemini", "google_gemini": "gemini", "google_ai_studio": "gemini",
    "claude": "anthropic",
    "azure": "azure_openai", "azureopenai": "azure_openai",
    "dashscope": "qwen", "aliyun": "qwen", "bailian": "qwen",
    "glm": "zhipu", "kimi": "moonshot",
    "volc": "doubao", "volcengine": "doubao", "ark": "doubao",
    "qianfan": "baidu_qianfan", "baidu": "baidu_qianfan",
    "hunyuan": "tencent_hunyuan", "tencent": "tencent_hunyuan",
    "minimaxi": "minimax", "nim": "nvidia_nim", "nvidia": "nvidia_nim",
    "grok": "xai", "open_router": "openrouter",
    "bedrock": "aws_bedrock", "amazon_bedrock": "aws_bedrock",
    "aws": "aws_bedrock", "oci": "oci_genai", "oracle": "oci_genai",
    "pplx": "perplexity", "github": "github_models",
    "githubmodels": "github_models", "samba": "sambanova",
    "openai_compatible": "custom", "env": "custom",
}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
_RESERVED_EXTRA_BODY = {
    "model", "messages", "input", "instructions", "tools", "tool_choice",
    "stream", "temperature", "max_tokens", "max_completion_tokens",
    "max_output_tokens", "reasoning", "reasoning_effort", "text",
    "previous_response_id", "context_management",
    "prompt_cache_retention",
}


class ProviderConfigError(ValueError):
    """A model configuration is invalid before any network request is made."""


def canonical_provider(provider: str) -> str:
    raw = re.sub(r"[^a-z0-9_]+", "_", str(provider or "").strip().lower()).strip("_")
    return _ALIASES.get(raw, raw or "custom")


def provider_spec(provider: str) -> ProviderSpec:
    return _BY_ID.get(canonical_provider(provider), _BY_ID["custom"])


def provider_credentials_ready(model_cfg: dict[str, Any]) -> bool:
    """Return whether the saved connection has the required credential.

    Optional-auth local runtimes are valid without an invented API key. Invalid
    endpoint/protocol configurations remain unavailable.
    """

    try:
        normal = normalize_model_config(model_cfg)
    except ProviderConfigError:
        return False
    spec: ProviderSpec = normal["provider_spec"]
    if spec.auth == "optional":
        return True
    return bool(str(model_cfg.get("api_key") or "").strip())


def _public_spec(spec: ProviderSpec) -> dict[str, Any]:
    item = asdict(spec)
    item["wire_apis"] = list(spec.wire_apis)
    item["model_hints"] = list(spec.model_hints)
    item["capabilities"] = {
        "tools": spec.tools, "streaming": spec.streaming,
        "vision": spec.vision, "json_schema": spec.json_schema,
        "model_discovery": spec.model_discovery,
        "native_state": spec.native_state,
        "prompt_cache_telemetry": spec.prompt_cache_telemetry,
        "reasoning_control": spec.reasoning_control,
    }
    return item


def list_provider_specs() -> list[dict[str, Any]]:
    return [_public_spec(spec) for spec in _SPECS]


def provider_presets() -> dict[str, dict[str, Any]]:
    """Legacy-shaped preset payload used by existing admin clients."""
    return {
        spec.id: {
            "name": spec.name,
            "base_url": spec.base_url,
            "models": list(spec.model_hints),
            "wire_apis": list(spec.wire_apis),
            "default_wire_api": spec.default_wire_api,
            "endpoint_note": spec.endpoint_note,
        }
        for spec in _SPECS
    }


def provider_capability_profile(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded capability contract used by context manifests.

    Registry values are provider-level defaults, not claims about every model
    sold by that provider.  A saved model may narrow those defaults through its
    ``config`` object.  It may not broaden a capability that the selected wire
    strategy does not advertise.
    """
    normal = (
        model_cfg if model_cfg.get("provider_spec") is not None
        else normalize_model_config(model_cfg)
    )
    spec: ProviderSpec = normal["provider_spec"]
    options = normal.get("provider_options") or parse_model_options(normal)

    def narrowed(name: str, advertised: bool) -> bool:
        value = options.get(name)
        if isinstance(value, bool):
            return advertised and value
        return advertised

    profile: dict[str, Any] = {
        "schema": "hashmm.provider-capability.v1",
        "provider": spec.id,
        "model": str(normal.get("model_name") or "")[:200],
        "wire_api": str(normal.get("wire_api") or spec.default_wire_api),
        "supports_tools": narrowed("supports_tools", spec.tools),
        # Parallel tool execution is model-specific.  Never infer it from the
        # provider name; it must be explicitly declared for this connection.
        "supports_parallel_tools": bool(
            options.get("supports_parallel_tools") is True
            and narrowed("supports_tools", spec.tools)
        ),
        "supports_vision": narrowed("supports_vision", spec.vision),
        "supports_streaming": narrowed("supports_streaming", spec.streaming),
        "supports_structured_output": narrowed(
            "supports_structured_output", spec.json_schema,
        ),
        "supports_reasoning": bool(options.get("supports_reasoning") is True),
        "supports_native_state": bool(
            spec.native_state and normal.get("wire_api") == WIRE_RESPONSES
        ),
        "reports_prompt_cache": spec.prompt_cache_telemetry,
        "reasoning_control": spec.reasoning_control if options.get("supports_reasoning") is True else "",
    }
    for source, target in (
        (options.get("max_input_tokens"), "max_input_tokens"),
        (normal.get("max_tokens"), "max_output_tokens"),
    ):
        try:
            value = int(source or 0)
        except (TypeError, ValueError, OverflowError):
            value = 0
        if value > 0:
            profile[target] = min(value, 10_000_000)
    return profile


def parse_model_options(model_cfg: dict[str, Any]) -> dict[str, Any]:
    raw = model_cfg.get("config")
    if isinstance(raw, dict):
        return dict(raw)
    raw = model_cfg.get("config_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _normalise_url(spec: ProviderSpec, base_url: str) -> str:
    url = str(base_url or spec.base_url or "").strip().rstrip("/")
    if not url:
        if spec.base_url_required:
            raise ProviderConfigError(f"{spec.name} 需要填写账号或地域对应的 Base URL")
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderConfigError("Base URL 必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise ProviderConfigError("Base URL 不能包含用户名或密码")
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOCAL_HOSTS:
        raise ProviderConfigError("远程模型 Base URL 必须使用 HTTPS；HTTP 仅允许本机模型")
    path = parsed.path.rstrip("/")
    pid = spec.id
    if pid == "azure_openai" and not path.endswith("/openai/v1"):
        path = f"{path}/openai/v1" if path else "/openai/v1"
    elif pid == "oci_genai" and not path.endswith("/openai/v1"):
        path = f"{path}/openai/v1" if path else "/openai/v1"
    elif pid == "qwen" and path in {"", "/"}:
        path = "/compatible-mode/v1"
    elif pid == "gemini" and not path.endswith("/v1beta/openai"):
        path = f"{path}/v1beta/openai" if path else "/v1beta/openai"
    elif pid in {"zhipu"} and path in {"", "/"}:
        path = "/api/paas/v4"
    elif pid in {"doubao"} and path in {"", "/"}:
        path = "/api/v3"
    elif pid not in {"anthropic", "deepseek"} and path in {"", "/"} and spec.default_wire_api != WIRE_ANTHROPIC:
        path = "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def normalize_model_config(model_cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(model_cfg or {})
    pid = canonical_provider(cfg.get("provider", ""))
    spec = provider_spec(pid)
    options = parse_model_options(cfg)
    explicit_wire = cfg.get("wire_api") or options.get("wire_api")
    # Existing OpenAI records predate Responses support and must keep their
    # Chat-Completions behaviour. New clients read the public provider preset
    # (whose preferred default is Responses) and save that choice explicitly.
    legacy_default = WIRE_CHAT if pid == "openai" and not explicit_wire else spec.default_wire_api
    wire = str(explicit_wire or legacy_default).strip().lower()
    if wire not in spec.wire_apis:
        raise ProviderConfigError(
            f"{spec.name} 不支持协议 {wire or '(空)'}；可选：{', '.join(spec.wire_apis)}"
        )
    cfg["provider"] = pid
    cfg["base_url"] = _normalise_url(spec, cfg.get("base_url", ""))
    cfg["wire_api"] = wire
    cfg["provider_spec"] = spec
    cfg["provider_options"] = options
    return cfg


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ProviderConfigError("厂商扩展参数嵌套过深")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, list):
        return [_bounded_json(v, depth=depth + 1) for v in value[:100]]
    if isinstance(value, dict):
        return {
            str(k)[:120]: _bounded_json(v, depth=depth + 1)
            for k, v in list(value.items())[:100]
        }
    raise ProviderConfigError("厂商扩展参数只能包含 JSON 值")


def request_options(cfg: dict[str, Any], *, messages: list[dict], tools: list[dict] | None = None,
                    tool_choice: str = "auto", stream: bool = False,
                    temperature: float | None = None, max_tokens: int | None = None,
                    runtime_mode: str | None = None) -> dict[str, Any]:
    """Build a provider-aware request without allowing extra_body to replace core fields."""
    normal = normalize_model_config(cfg) if "provider_spec" not in cfg else cfg
    spec: ProviderSpec = normal["provider_spec"]
    profile = provider_capability_profile(normal)
    if tools and not profile["supports_tools"]:
        raise ProviderConfigError(f"{spec.name} 当前策略未声明工具调用能力")
    options = normal.get("provider_options") or {}
    if runtime_mode is None:
        try:
            from hashmm.model_runtime import current_runtime_mode
            runtime_mode = current_runtime_mode()
        except Exception:
            runtime_mode = "auto"
    out: dict[str, Any] = {
        "model": str(normal.get("model_name") or "").strip(),
        "messages": messages,
        "stream": bool(stream),
    }
    if not out["model"]:
        raise ProviderConfigError("模型 ID 不能为空")
    # Responses-capable reasoning models often reject sampling fields.  Keep
    # existing Chat Completions behaviour, while requiring an explicit opt-in
    # before sending temperature through the Responses wire API.
    send_temperature = options.get(
        "send_temperature", normal.get("wire_api") != WIRE_RESPONSES
    )
    if temperature is not None and send_temperature:
        out["temperature"] = float(temperature)
    if max_tokens is not None and int(max_tokens) > 0:
        default_token_field = (
            "max_output_tokens" if normal.get("wire_api") == WIRE_RESPONSES
            else "max_tokens"
        )
        token_field = str(options.get("token_field") or default_token_field)
        if token_field not in {"max_tokens", "max_completion_tokens", "max_output_tokens"}:
            raise ProviderConfigError("token_field 只允许 max_tokens/max_completion_tokens/max_output_tokens")
        out[token_field] = int(max_tokens)
    if tools:
        out["tools"] = tools
        if tool_choice and tool_choice != "none":
            out["tool_choice"] = tool_choice
    # Provider-specific reasoning is opt-in per saved model. Provider presets
    # only describe which protocol control *can* be used.
    if profile.get("supports_reasoning"):
        mode = str(runtime_mode or "auto")
        effort = {"fast": "low", "auto": "medium", "deep": "high"}.get(mode, "medium")
        configured = str(options.get("reasoning_effort") or "").strip().lower()
        if configured in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            effort = configured
        control = str(profile.get("reasoning_control") or "")
        if control == "responses" and normal.get("wire_api") == WIRE_RESPONSES:
            out["reasoning"] = {"effort": effort}
            verbosity = str(options.get("text_verbosity") or "").strip().lower()
            if verbosity in {"low", "medium", "high"}:
                out["text"] = {"verbosity": verbosity}
        elif control == "deepseek":
            # DeepSeek's OpenAI-compatible SDK accepts provider extensions via
            # extra_body.  V4 maps the strongest HashMM mode to max; a user can
            # override this explicitly in the connection configuration.
            ds_effort = {"low": "high", "medium": "high", "high": "high",
                         "xhigh": "max", "max": "max"}.get(effort, "high")
            out.setdefault("extra_body", {})["reasoning_effort"] = ds_effort
        elif control == "explicit":
            parameter = str(options.get("reasoning_parameter") or "").strip()
            if parameter and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", parameter):
                out.setdefault("extra_body", {})[parameter] = effort
    if normal.get("wire_api") == WIRE_RESPONSES:
        if isinstance(options.get("store"), bool):
            out["store"] = options["store"]
        retention = str(options.get("prompt_cache_retention") or "").strip()
        if retention in {"in_memory", "24h"}:
            out["prompt_cache_retention"] = retention
    extra = options.get("extra_body") or {}
    if extra:
        if not isinstance(extra, dict):
            raise ProviderConfigError("extra_body 必须是 JSON 对象")
        clean = {k: v for k, v in extra.items() if str(k) not in _RESERVED_EXTRA_BODY}
        if clean:
            existing = out.get("extra_body") if isinstance(out.get("extra_body"), dict) else {}
            out["extra_body"] = {**existing, **_bounded_json(clean)}
    return out


def classify_provider_error(provider: str, err: Exception | str) -> dict[str, Any]:
    raw = str(err or "")[:1000]
    low = raw.lower()
    status = getattr(err, "status_code", None) if not isinstance(err, str) else None
    code = "provider_error"
    retryable = False
    if status in {401, 403} or any(x in low for x in ("invalid api key", "unauthorized", "authentication")):
        code = "authentication"
    elif status == 429 or any(x in low for x in ("rate limit", "too many requests", "rpm", "tpm")):
        code, retryable = "rate_limit", True
    elif status in {500, 502, 503, 504} or any(x in low for x in ("overloaded", "service unavailable")):
        code, retryable = "provider_unavailable", True
    elif any(x in low for x in ("timeout", "timed out", "connection reset", "connection error")):
        code, retryable = "network", True
    elif status == 404 and "model" in low:
        code = "model_not_found"
    elif any(x in low for x in ("context_length", "context length", "maximum context", "too many tokens")):
        code = "context_limit"
    elif any(x in low for x in ("insufficient_quota", "insufficient balance", "余额不足", "payment required")):
        code = "quota"
    elif any(x in low for x in ("unsupported", "unknown parameter", "extra inputs are not permitted")):
        code = "unsupported_parameter"
    label = provider_spec(provider).name
    guidance = {
        "authentication": "检查 API Key、项目/业务空间以及密钥权限。",
        "rate_limit": "稍后重试或降低并发；不要在已经输出内容后静默切换模型。",
        "provider_unavailable": "服务商暂时不可用，可在任务开始前切换备援模型。",
        "network": "检查 Base URL、DNS、代理和 TLS；远程服务必须使用 HTTPS。",
        "model_not_found": "从服务商控制台复制当前可用的精确模型或部署 ID。",
        "context_limit": "压缩历史、减少附件或选择更长上下文模型。",
        "quota": "检查账号余额、配额和计费状态。",
        "unsupported_parameter": "在模型配置中关闭不支持参数，或改用该厂商支持的 wire API。",
        "provider_error": "核对模型 ID、Base URL、协议和服务商控制台请求日志。",
    }[code]
    return {
        "provider": canonical_provider(provider), "provider_name": label,
        "code": code, "retryable": retryable, "status_code": status,
        "message": f"{label}：{guidance}", "raw": raw[:300],
    }
