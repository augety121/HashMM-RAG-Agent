"""HashMM Agent harness: one authoritative contract for a model turn.

The old module only wrapped lightweight LLM calls with retry logic.  The main
AgentLoop, delegated Workers, tool guardrails, run manifest and cross-device
runtime consequently described the same run using separate pieces of state.

This module keeps the backwards-compatible :func:`run_llm` helper and adds the
small deterministic kernel shared by those surfaces:

* a frozen, server-authored turn context (identity, scope, tools and budgets);
* a capability snapshot which never advertises a schema without an executor;
* bounded, argument-free lifecycle events suitable for trajectory evaluation;
* child admission which honours depth and per-run worker caps; and
* normalized, sticky terminal outcomes so a late callback cannot rewrite an
  interrupt or hard timeout into a successful completion.

The harness is orchestration evidence, not proof that model prose is true.
Raw prompts, tool arguments, credentials and tool results are deliberately not
stored here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Iterable, Mapping

from hashmm.utils import get_logger

logger = get_logger("hashmm.harness")

HARNESS_SCHEMA = "hashmm.agent-harness.v1"
TURN_CONTEXT_SCHEMA = "hashmm.turn-context.v1"
CAPABILITY_SCHEMA = "hashmm.capability-snapshot.v1"
TERMINAL_SCHEMA = "hashmm.terminal-outcome.v1"

_INTERNAL_TOOLS = frozenset({
    "update_todo", "spawn_worker", "memory_recall", "remember_preference",
})
_NETWORK_TOOLS = frozenset({
    "fetch_url", "web_search", "browser_open", "browser_act",
    "browser_read", "browser_screenshot",
})
_STICKY_TERMINAL_REASONS = frozenset({
    "cancelled", "hard_timeout", "waiting_approval", "waiting_input",
})
_MAX_EVENTS = 240
_EVENT_DETAIL_FIELDS = {
    "turn_admitted": {"effective_tools", "missing_executors"},
    "iteration_started": {"iteration"},
    "tool_started": {"iteration"},
    "tool_finished": {
        "elapsed_ms", "hook_count", "receipt_id", "receipt_status",
        "side_effect_class",
    },
    "approval_requested": set(),
    "context_compaction": {"messages", "chars", "hooks"},
    "context_assembled": {"hit_count", "total_chars", "generation"},
    "context_checkpoint": {"generation", "compacted", "checkpointed"},
    "child_admission": {"cap", "message", "role", "ordinal"},
    "child_finished": {"role", "active_children"},
    "subagent_stop_hooks": {"hooks"},
    "turn_finished": {"stop_reason"},
}


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _fingerprint(value: Any, length: int = 16) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _tool_name(schema: Any) -> str:
    if not isinstance(schema, Mapping):
        return ""
    function = schema.get("function")
    return _clip(function.get("name") if isinstance(function, Mapping) else "", 120)


def build_capability_snapshot(
    tool_schemas: Iterable[Mapping[str, Any]] | None,
    executors: Mapping[str, Any] | None,
    execution_scope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the exact tools a turn can really execute.

    A tool is effective only when all three facts agree: a valid model schema
    exists, the server-authored execution scope allows its name, and a callable
    executor (or one of the harness-owned internal implementations) exists.
    This closes the common "card/schema exists but Chat cannot use it" gap.
    """
    scope = dict(execution_scope or {})
    scope_valid = scope.get("schema") == "hashmm.execution-scope.v1"
    allowed = {
        _clip(name, 120) for name in list(scope.get("allowed_tools") or [])
        if _clip(name, 120)
    }
    network = scope.get("network") if isinstance(scope.get("network"), Mapping) else {}
    executor_names = {
        _clip(name, 120) for name, fn in dict(executors or {}).items()
        if _clip(name, 120) and callable(fn)
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schema in list(tool_schemas or [])[:200]:
        name = _tool_name(schema)
        if not name or name in seen:
            continue
        seen.add(name)
        # An empty scope is deny-all.  Treating it as allow-all would let a
        # malformed or intentionally read-only run advertise every registered
        # tool to the model even though execution_scope rejects the call later.
        in_scope = bool(scope_valid and name in allowed)
        if name == "spawn_worker" and not scope.get("allow_subagents", False):
            in_scope = False
        if name in _NETWORK_TOOLS and network.get("mode") == "deny":
            in_scope = False
        executor_bound = name in executor_names or name in _INTERNAL_TOOLS
        rows.append({
            "name": name,
            "schema_hash": _fingerprint(schema, 12),
            "in_scope": in_scope,
            "executor_bound": executor_bound,
            "ready": bool(in_scope and executor_bound),
        })
    effective = sorted(row["name"] for row in rows if row["ready"])
    missing = sorted(
        row["name"] for row in rows if row["in_scope"] and not row["executor_bound"]
    )
    payload = {
        "schema": CAPABILITY_SCHEMA,
        "declared_count": len(rows),
        "effective_count": len(effective),
        "effective_tools": effective,
        "missing_executors": missing,
        "tools": rows,
    }
    payload["revision"] = _fingerprint(payload)
    return payload


@dataclass(frozen=True)
class TurnContextSnapshot:
    """Immutable runtime facts for a single AgentLoop invocation."""

    run_id: str
    owner_fingerprint: str
    conversation_id: str
    goal_fingerprint: str
    scope_id: str
    parent_scope_id: str
    depth: int
    approval_mode: str
    network_mode: str
    max_iterations: int
    max_tool_calls: int
    max_search_calls: int
    max_exec_calls: int
    max_workers: int
    allow_subagents: bool
    capability_revision: str
    effective_tools: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        public = {
            "schema": TURN_CONTEXT_SCHEMA,
            "run_id": self.run_id,
            "owner_fingerprint": self.owner_fingerprint,
            "conversation_id": self.conversation_id,
            "goal_fingerprint": self.goal_fingerprint,
            "scope_id": self.scope_id,
            "parent_scope_id": self.parent_scope_id,
            "depth": self.depth,
            "approval_mode": self.approval_mode,
            "network_mode": self.network_mode,
            "budgets": {
                "max_iterations": self.max_iterations,
                "max_tool_calls": self.max_tool_calls,
                "max_search_calls": self.max_search_calls,
                "max_exec_calls": self.max_exec_calls,
                "max_workers": self.max_workers,
            },
            "allow_subagents": self.allow_subagents,
            "capability_revision": self.capability_revision,
            "effective_tools": list(self.effective_tools),
        }
        return public


def build_terminal_outcome(
    stop_reason: str,
    *,
    started_at: float = 0,
    ended_at: float = 0,
    error: str = "",
) -> dict[str, Any]:
    """Normalize loop-specific stop reasons into one stable outcome."""
    raw = _clip(stop_reason or "unknown", 64).lower()
    if raw in {"completed", "complete", "done"}:
        reason, status = "completed", "ok"
    elif raw in {"waiting_approval", "waiting_input"}:
        reason, status = raw, "waiting"
    elif raw in {"interrupted", "cancelled", "client_disconnected", "stopped"}:
        reason, status = "cancelled", "error"
    elif raw in {"deadline", "hard_timeout"}:
        reason, status = "hard_timeout", "timeout"
    elif raw in {"timeout", "timed_out"}:
        reason, status = "timed_out", "timeout"
    elif raw in {"budget_exceeded", "no_progress", "max_iterations"}:
        reason, status = "incomplete", "error"
    else:
        reason, status = "failed", "error"
    outcome: dict[str, Any] = {
        "schema": TERMINAL_SCHEMA,
        "reason": reason,
        "status": status,
        "stop_reason": raw,
    }
    if error:
        outcome["error"] = _clip(error, 500)
    if started_at > 0:
        outcome["started_at"] = float(started_at)
    if ended_at > 0:
        outcome["ended_at"] = float(ended_at)
    return outcome


def merge_terminal_outcome(
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge terminal observations without losing cancellation/timeout ownership."""
    new = dict(incoming or {})
    if not current:
        return new
    old = dict(current)
    if str(old.get("reason") or "") in _STICKY_TERMINAL_REASONS:
        return old
    if str(new.get("reason") or "") in _STICKY_TERMINAL_REASONS:
        return new
    return new


class AgentRunKernel:
    """Bounded state machine used by the main AgentLoop for one turn."""

    def __init__(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        goal: str,
        execution_scope: Mapping[str, Any] | None,
        tool_schemas: Iterable[Mapping[str, Any]] | None,
        executors: Mapping[str, Any] | None,
        max_iterations: int,
        max_tool_calls: int,
        max_search_calls: int,
        max_exec_calls: int,
        max_workers: int,
    ):
        self.started_at = time.time()
        self.capabilities = build_capability_snapshot(
            tool_schemas, executors, execution_scope)
        scope = dict(execution_scope or {})
        network = scope.get("network") if isinstance(scope.get("network"), Mapping) else {}
        budgets = scope.get("budgets") if isinstance(scope.get("budgets"), Mapping) else {}
        scope_worker_cap = (
            int(budgets.get("max_workers") or 0)
            if "max_workers" in budgets else int(max_workers or 0)
        )
        allow_subagents = bool(scope.get("allow_subagents", False))
        scope_tool_cap = (
            max(1, int(budgets.get("max_tool_calls") or 1))
            if "max_tool_calls" in budgets else max(1, int(max_tool_calls or 1))
        )
        self.context = TurnContextSnapshot(
            run_id=_clip(scope.get("run_id"), 80),
            owner_fingerprint=_fingerprint({"owner": _clip(owner_id, 160)}, 12),
            conversation_id=_clip(conversation_id, 160),
            goal_fingerprint=_fingerprint({"goal": _clip(goal, 8_000)}, 16),
            scope_id=_clip(scope.get("scope_id"), 80),
            parent_scope_id=_clip(scope.get("parent_scope_id"), 80),
            depth=max(0, min(int(scope.get("depth") or 0), 16)),
            approval_mode=_clip(scope.get("approval_mode") or "read_only", 32),
            network_mode=_clip(network.get("mode") or "deny", 24),
            max_iterations=max(1, int(max_iterations or 1)),
            max_tool_calls=min(max(1, int(max_tool_calls or 1)), scope_tool_cap),
            max_search_calls=max(0, int(max_search_calls or 0)),
            max_exec_calls=max(0, int(max_exec_calls or 0)),
            max_workers=(max(0, min(scope_worker_cap, 8)) if allow_subagents else 0),
            allow_subagents=allow_subagents,
            capability_revision=str(self.capabilities["revision"]),
            effective_tools=tuple(self.capabilities["effective_tools"]),
        )
        self._events: list[dict[str, Any]] = []
        self._terminal: dict[str, Any] | None = None
        self._active_children = 0
        self._total_children = 0
        self.record("turn_admitted", status="running", detail={
            "effective_tools": len(self.context.effective_tools),
            "missing_executors": len(self.capabilities.get("missing_executors") or []),
        })

    @property
    def effective_tools(self) -> set[str]:
        return set(self.context.effective_tools)

    def record(
        self,
        event_type: str,
        *,
        status: str = "",
        tool_name: str = "",
        args: Mapping[str, Any] | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if len(self._events) >= _MAX_EVENTS:
            return
        row: dict[str, Any] = {
            "seq": len(self._events) + 1,
            "offset_ms": max(0, round((time.time() - self.started_at) * 1000)),
            "type": _clip(event_type, 64),
        }
        if status:
            row["status"] = _clip(status, 32)
        if tool_name:
            row["tool"] = _clip(tool_name, 120)
        if args is not None:
            # A non-reversible signature is enough for retry/loop evaluation.
            row["args_hash"] = _fingerprint(dict(args), 16)
        if detail:
            safe: dict[str, Any] = {}
            allowed_detail = _EVENT_DETAIL_FIELDS.get(str(event_type or ""), set())
            for key, value in list(detail.items())[:16]:
                name = _clip(key, 64)
                if not name or name not in allowed_detail:
                    continue
                if isinstance(value, bool):
                    safe[name] = value
                elif isinstance(value, (int, float)):
                    safe[name] = value
                else:
                    safe[name] = _clip(value, 240)
            if safe:
                row["detail"] = safe
        self._events.append(row)

    def admit_child(self, role: str) -> dict[str, Any]:
        """Apply OpenClaw-style depth/active/total admission before spawning."""
        if self.context.depth >= 2:
            result = {"ok": False, "cap": "max_spawn_depth",
                      "message": "当前 Agent 深度已达到上限，不能继续派生专员"}
        elif not self.context.allow_subagents or self.context.max_workers <= 0:
            result = {"ok": False, "cap": "subagents_disabled",
                      "message": "本任务执行范围未授权多智能体"}
        elif self._active_children >= self.context.max_workers:
            result = {"ok": False, "cap": "max_active_children",
                      "message": f"同时运行的专员已达上限 {self.context.max_workers}"}
        elif self._total_children >= self.context.max_workers:
            result = {"ok": False, "cap": "max_total_children",
                      "message": f"本轮专员数量已达上限 {self.context.max_workers}"}
        else:
            self._active_children += 1
            self._total_children += 1
            result = {"ok": True, "role": _clip(role or "research", 40),
                      "ordinal": self._total_children}
        self.record("child_admission", status="admitted" if result["ok"] else "denied",
                    detail={k: v for k, v in result.items() if k != "ok"})
        return result

    def finish_child(self, role: str, status: str) -> None:
        self._active_children = max(0, self._active_children - 1)
        self.record("child_finished", status=status,
                    detail={"role": _clip(role, 40), "active_children": self._active_children})

    def finish(self, stop_reason: str, *, error: str = "") -> dict[str, Any]:
        incoming = build_terminal_outcome(
            stop_reason, started_at=self.started_at, ended_at=time.time(), error=error)
        self._terminal = merge_terminal_outcome(self._terminal, incoming)
        self.record("turn_finished", status=str(self._terminal.get("reason") or "unknown"),
                    detail={"stop_reason": self._terminal.get("stop_reason", "unknown")})
        return dict(self._terminal)

    def public(self) -> dict[str, Any]:
        event_types: dict[str, int] = {}
        for row in self._events:
            kind = str(row.get("type") or "unknown")
            event_types[kind] = event_types.get(kind, 0) + 1
        public = {
            "schema": HARNESS_SCHEMA,
            "context": self.context.public(),
            "capabilities": dict(self.capabilities),
            "trajectory": {
                "event_count": len(self._events),
                "event_types": event_types,
                "events": [dict(row) for row in self._events],
                "truncated": len(self._events) >= _MAX_EVENTS,
            },
            "children": {
                "active": self._active_children,
                "total": self._total_children,
                "limit": self.context.max_workers,
            },
            "terminal": dict(self._terminal or {}),
            "limitation": "运行轨迹证明系统观察到的动作，不证明模型文本或外部世界状态为真。",
        }
        try:
            from hashmm.evaluation.harness_levels import evaluate_harness_levels
            public["levels"] = evaluate_harness_levels(public)
        except Exception:
            public["levels"] = {"schema": "hashmm.harness-levels.v1", "claim": "local_public_trajectory_replay_only", "levels": []}
        return public


def run_llm(fn, prompt: str, *, tag: str = "llm", retries: int = 1, min_len: int = 1) -> str:
    """带重试与校验的轻量模型调用；彻底失败返回空串。"""
    if fn is None:
        return ""
    attempt = 0
    t0 = time.time()
    while True:
        attempt += 1
        try:
            out = str(fn(prompt) or "").strip()
        except Exception as exc:  # noqa: BLE001
            out = ""
            logger.debug("[harness:%s] 第 %d 次调用异常：%s", tag, attempt, exc)
        if len(out) >= min_len:
            _journal(tag, attempt, len(out), time.time() - t0, ok=True)
            return out
        if attempt > retries:
            _journal(tag, attempt, 0, time.time() - t0, ok=False)
            return ""
        time.sleep(0.8 * attempt)


def _journal(tag: str, attempts: int, out_len: int, secs: float, *, ok: bool) -> None:
    try:
        from hashmm.agent.global_workspace import gw
        channel = "loops" if tag.startswith("loop") else "team" if tag.startswith("team") else "chat"
        gw().submit(channel, "harness",
                    f"{tag}：{'成功' if ok else '失败'} · {attempts} 次 · {out_len} 字 · {secs:.1f}s",
                    salience=0.2)
    except Exception:
        pass


__all__ = [
    "AgentRunKernel", "TurnContextSnapshot", "build_capability_snapshot",
    "build_terminal_outcome", "merge_terminal_outcome", "run_llm",
    "HARNESS_SCHEMA", "TURN_CONTEXT_SCHEMA", "CAPABILITY_SCHEMA", "TERMINAL_SCHEMA",
]
