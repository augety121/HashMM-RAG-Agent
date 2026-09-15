"""v17 Phase 77 — agent-action governance (轴 E): approval gate + audit stream.

Two production-governance gaps closed, both via the existing tool-hook boundary
(so no path can bypass them) and both **no-op unless their env flag is on** (zero
behavior change by default):

1. **Approval enforcement.** The hook layer already *computes* ``require_approval``
   for high-risk (mutating / external / irreversible) tools — but ``execute_tool``
   only checks ``allow``, so approval was never actually enforced outside strict
   plan-mode. ``HASHMM_TOOL_APPROVAL=1`` adds a deny-first pre-hook: a high-risk
   tool is DENIED unless the call is explicitly approved (ctx ``approved=True`` or
   an ``approver(tool, args)`` callback returning True). Human-in-the-loop for
   irreversible actions (OWASP LLM06).

2. **Structured audit stream.** The existing audit only logs a one-line db.audit
   for high-risk tools. ``HASHMM_AUDIT_TOOLS=1`` adds a queryable JSONL stream of
   **every** tool call — actor, tenant, tool, risk, decision, redacted args,
   ok/latency, timestamp — for incident review / compliance.

Domain-agnostic; never raises.  Approval/risk errors fail closed; the optional
audit sink remains best-effort because logging availability is not authority to
execute a tool.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)

_SECRET_HINT = ("token", "password", "passwd", "secret", "api_key", "apikey",
                "authorization", "cookie", "credential", "private_key")

# v17 Phase 79: lightweight in-memory governance counters for Prometheus.
_COUNTERS = {"approval_denied": 0, "approval_granted": 0, "high_risk": 0}


def governance_metrics() -> dict:
    return dict(_COUNTERS)


def reset_counters() -> None:
    for k in _COUNTERS:
        _COUNTERS[k] = 0


def approval_enabled() -> bool:
    return os.environ.get("HASHMM_TOOL_APPROVAL", "0").strip().lower() in ("1", "true", "yes", "on")


def audit_enabled() -> bool:
    # 默认开启审计记录 —— 让「权限审计」台开箱即有数据可看（甲方反馈面板空）。
    # 仍可显式设 HASHMM_AUDIT_TOOLS=0 关闭。仅记录（脱敏入参 + ok/耗时），不拦截任何调用。
    return os.environ.get("HASHMM_AUDIT_TOOLS", "1").strip().lower() in ("1", "true", "yes", "on")


def is_high_risk(tool_name: str, args: dict | None = None) -> bool:
    try:
        from hashmm.agent_safety import classify_tool_risk
        return classify_tool_risk(tool_name, args or {}) == "high"
    except Exception as e:
        log_suppressed(logger, e)
        # Unknown classifier state is not evidence that an action is safe.
        return True


def is_approved(ctx: dict, tool_name: str = "", args: dict | None = None) -> bool:
    """A call is approved when the context carries an explicit grant: a truthy
    ``approved`` flag, or an ``approver(tool, args)`` callback that returns True."""
    try:
        if ctx.get("approved") is True:
            return True
        approver = ctx.get("approver")
        if callable(approver):
            return bool(approver(tool_name, args or {}))
    except Exception as e:
        log_suppressed(logger, e)
    return False


# ── approval gate (pre-hook) ──────────────────────────────────────────────
def approval_pre_hook(tool_name: str, args: dict, ctx: dict):
    """Deny high-risk tools that haven't been approved (only when approval mode
    is on). No-op otherwise."""
    from hashmm.hooks import HookDecision
    try:
        if not approval_enabled():
            return HookDecision(allow=True)
        if not is_high_risk(tool_name, args):
            return HookDecision(allow=True)
        if is_approved(ctx, tool_name, args):
            _COUNTERS["approval_granted"] += 1
            return HookDecision(allow=True, require_approval=True, risk="high", hook="approval")
        _COUNTERS["approval_denied"] += 1
        return HookDecision(
            allow=False,
            reason=f"高危工具 {tool_name} 需要审批：请确认后重试（或由审批人放行）。",
            require_approval=True, risk="high", hook="approval")
    except Exception as e:
        log_suppressed(logger, e)
        return HookDecision(
            allow=False,
            reason=f"审批安全检查异常，已拒绝执行（{type(e).__name__}）",
            require_approval=True,
            risk="high",
            hook="approval",
        )


# ── audit stream (post-hook) ──────────────────────────────────────────────
def _audit_path() -> Path:
    base = Path(os.environ.get("HASHMM_AUDIT_DIR", "data/audit"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "tool_actions.jsonl"


def redact_args(args: dict | None, max_len: int = 200) -> dict:
    """Shallow-redact secrets and truncate long values so the audit stream never
    stores credentials or huge blobs."""
    out: dict = {}
    if not isinstance(args, dict):
        return out
    for k, v in args.items():
        kl = str(k).lower()
        if any(h in kl for h in _SECRET_HINT):
            out[k] = "***"
        elif isinstance(v, str):
            out[k] = v if len(v) <= max_len else v[:max_len] + f"…(+{len(v) - max_len})"
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            s = str(v)
            out[k] = s if len(s) <= max_len else s[:max_len] + "…"
    return out


def record_action(tool_name: str, args: dict, ctx: dict, ok: bool, latency_ms: int) -> bool:
    """Append one structured audit entry. Best-effort; never raises."""
    try:
        entry = {
            "ts": round(time.time(), 3),
            "actor": ctx.get("user_id") or ctx.get("uid") or "system",
            "tenant": ctx.get("tenant_id", ""),
            "tool": tool_name,
            "risk": "high" if is_high_risk(tool_name, args) else "low",
            "ok": bool(ok),
            "latency_ms": int(latency_ms),
            "args": redact_args(args),
        }
        if entry["risk"] == "high":
            _COUNTERS["high_risk"] += 1
        with _audit_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        log_suppressed(logger, e)
        return False


def audit_stream_post_hook(tool_name: str, args: dict, ctx: dict, ok: bool, latency_ms: int) -> None:
    if audit_enabled():
        record_action(tool_name, args, ctx, ok, latency_ms)


def read_recent(n: int = 50) -> list[dict]:
    """Read the last n audit entries (for an admin/incident view)."""
    try:
        p = _audit_path()
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        return [json.loads(ln) for ln in lines[-n:] if ln.strip()]
    except Exception as e:
        log_suppressed(logger, e)
        return []


# ── registration ──────────────────────────────────────────────────────────
def register_governance_hooks() -> None:
    """Register the approval pre-hook + audit post-hook once. Idempotent.

    Both are inert unless HASHMM_TOOL_APPROVAL / HASHMM_AUDIT_TOOLS are set, so
    registering them is safe and changes nothing by default."""
    from hashmm import hooks
    if any(n == "approval" for n, _ in hooks._PRE_HOOKS):
        return
    hooks.register_pre_hook("approval", approval_pre_hook, critical=True)
    hooks.register_post_hook("audit_stream", audit_stream_post_hook)
