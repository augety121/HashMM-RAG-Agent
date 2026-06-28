"""Tool-execution hooks — deterministic, architecture-level safety boundary.

Inspired by Claude Code's hooks: control points that run around every tool call
in *code*, not via prompt instructions the model could reason around. A pre-hook
can DENY a call (it never runs); a post-hook records the outcome. Because hooks
sit at the single execution entry point (`tool_registry.execute_tool`), no code
path — main chat, agent loop, sub-agent orchestrator — can bypass them.

What this consolidates (previously scattered / bypassable):
  - permission model (read/write/execute/network/delete/system) — was only
    enforced inside agent/loop.py, so the main chat tool path had NO checks;
  - agent_safety risk classification (high-risk mutating/external actions);
  - per-tenant policy (deny / require-approval lists) — ties into multi-tenancy;
  - audit of every call.

Design:
  - **Deny-first & deterministic.** A PreToolHook returns a HookDecision; the
    first DENY stops the call. No model involved.
  - **Behaviour-preserving by default.** With no policy configured and tenancy
    off, the default hooks allow everything read-ish and only gate the same
    high-risk actions agent_safety already flagged — i.e. current behaviour.
  - **Composable.** Extra hooks can be registered (e.g. a tenant policy hook).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.hooks")


@dataclass
class HookDecision:
    """Result of a pre-tool hook. ``allow=False`` denies the call."""
    allow: bool = True
    reason: str = ""
    require_approval: bool = False
    risk: str = "low"          # low | high
    hook: str = ""             # which hook produced this (for audit)


# A pre-hook: (tool_name, args, ctx) -> HookDecision
PreToolHook = Callable[[str, dict, dict], HookDecision]
# A post-hook: (tool_name, args, ctx, ok, latency_ms) -> None
PostToolHook = Callable[[str, dict, dict, bool, int], None]
# v17: extra lifecycle hooks aligning with Claude Code harness (PreCompact / SubagentStop).
# PreCompact: (messages, ctx) -> None  — fires before context compaction drops history,
#   so a hook can archive / extract key facts before they are summarized away.
PreCompactHook = Callable[[list, dict], None]
# SubagentStop: (subtask_id, result, ctx) -> None — fires when a sub-agent finishes,
#   so a hook can aggregate / log / verify the worker's output.
SubagentStopHook = Callable[[str, str, dict], None]

_PRE_HOOKS: list[tuple[str, PreToolHook]] = []
_POST_HOOKS: list[tuple[str, PostToolHook]] = []
_PRECOMPACT_HOOKS: list[tuple[str, PreCompactHook]] = []
_SUBAGENT_STOP_HOOKS: list[tuple[str, SubagentStopHook]] = []


def register_pre_hook(name: str, fn: PreToolHook) -> None:
    _PRE_HOOKS.append((name, fn))


def register_post_hook(name: str, fn: PostToolHook) -> None:
    _POST_HOOKS.append((name, fn))


def register_compact_hook(name: str, fn: PreCompactHook) -> None:
    """Register a PreCompact hook (fires before context compaction drops history)."""
    _PRECOMPACT_HOOKS.append((name, fn))


def register_subagent_stop_hook(name: str, fn: SubagentStopHook) -> None:
    """Register a SubagentStop hook (fires when a sub-agent finishes)."""
    _SUBAGENT_STOP_HOOKS.append((name, fn))


def run_compact_hooks(messages: list, ctx: dict | None = None) -> None:
    """Run all PreCompact hooks. No-op when none registered (zero behaviour change).
    Never raises — a misbehaving hook must not break compaction."""
    ctx = ctx or {}
    for name, hook in _PRECOMPACT_HOOKS:
        try:
            hook(messages or [], ctx)
        except Exception as _e:
            log_suppressed(logger, _e)


def run_subagent_stop_hooks(subtask_id: str, result: str, ctx: dict | None = None) -> None:
    """Run all SubagentStop hooks. No-op when none registered. Never raises."""
    ctx = ctx or {}
    for name, hook in _SUBAGENT_STOP_HOOKS:
        try:
            hook(subtask_id, result or "", ctx)
        except Exception as _e:
            log_suppressed(logger, _e)


def reset_hooks() -> None:
    """Clear all hooks (tests)."""
    _PRE_HOOKS.clear()
    _POST_HOOKS.clear()
    _PRECOMPACT_HOOKS.clear()
    _SUBAGENT_STOP_HOOKS.clear()


def run_pre_tool_hooks(tool_name: str, args: dict, ctx: dict | None = None) -> HookDecision:
    """Run all pre-tool hooks; first DENY wins. Never raises — a hook that throws
    is treated as 'allow' but logged (fail-open for availability, but the failure
    is visible; deny-critical hooks must be written not to throw)."""
    ctx = ctx or {}
    final = HookDecision(allow=True)
    for name, hook in _PRE_HOOKS:
        try:
            d = hook(tool_name, args or {}, ctx)
        except Exception as _e:
            log_suppressed(logger, _e)
            continue
        if d is None:
            continue
        # propagate risk / approval upward
        if d.risk == "high":
            final.risk = "high"
        if d.require_approval:
            final.require_approval = True
            final.reason = final.reason or d.reason
        if not d.allow:
            d.hook = d.hook or name
            logger.info(f"[Hook:{d.hook}] DENY {tool_name}: {d.reason}")
            return d
    return final


def run_post_tool_hooks(tool_name: str, args: dict, ctx: dict | None, ok: bool, latency_ms: int) -> None:
    ctx = ctx or {}
    for name, hook in _POST_HOOKS:
        try:
            hook(tool_name, args or {}, ctx, ok, latency_ms)
        except Exception as _e:
            log_suppressed(logger, _e)


# ────────────────────────────────────────────────────────────────────
# Default hooks — wired at import so the single entry point is always guarded.
# They reproduce existing behaviour (permission levels + agent_safety risk),
# now enforced architecturally instead of only inside the agent loop.
# ────────────────────────────────────────────────────────────────────

def _permission_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Deny tools that need elevated rights when the caller lacks them; flag
    high-risk mutating tools for approval. Mirrors permissions.py + agent_safety."""
    try:
        from hashmm.agent.permissions import TOOL_PERMISSIONS, PERMISSION_CONFIG, PermissionLevel
        level = TOOL_PERMISSIONS.get(tool_name, PermissionLevel.READ)
        config = PERMISSION_CONFIG.get(level, {})
        # system/delete-class tools that aren't auto-approved → admin only
        if not config.get("auto_approve", True):
            user_id = ctx.get("user_id") or ctx.get("uid")
            is_admin = False
            try:
                from hashmm.api import database as db
                u = db.get_user_by_id(user_id) if user_id else None
                is_admin = bool(u and u.get("role") == "admin")
            except Exception as _e:
                log_suppressed(logger, _e)
            if not is_admin:
                return HookDecision(allow=False,
                                    reason=f"工具 {tool_name} 需要管理员权限（{level}）",
                                    risk="high", hook="permission")
            # admin: allowed but mark high-risk + approval
            return HookDecision(allow=True, require_approval=True, risk="high", hook="permission")
    except Exception as _e:
        log_suppressed(logger, _e)
    return HookDecision(allow=True)


def _risk_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Classify mutating/external/irreversible actions as high-risk (agent_safety).
    High-risk → flagged for approval (does not hard-deny; the approval gate /
    tenant policy decides). Read-only tools pass freely."""
    try:
        from hashmm.agent_safety import classify_tool_risk
        if classify_tool_risk(tool_name, args) == "high":
            return HookDecision(allow=True, require_approval=True, risk="high", hook="risk")
    except Exception as _e:
        log_suppressed(logger, _e)
    return HookDecision(allow=True)


def _tenant_policy_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Per-tenant deny/allow lists (only when multi-tenancy is enabled).

    A tenant may forbid certain tools (e.g. disable web_search / code execution
    for a locked-down tenant). No-op when tenancy is off, so single-tenant is
    unchanged."""
    try:
        from hashmm import tenancy
        if not tenancy.multi_tenant_enabled():
            return HookDecision(allow=True)
        user_id = ctx.get("user_id") or ctx.get("uid")
        if not user_id:
            return HookDecision(allow=True)
        from hashmm.api import database as db
        tid = tenancy.resolve_tenant(db, user_id)
        denied = _tenant_denied_tools(tid)
        if tool_name in denied:
            return HookDecision(allow=False,
                                reason=f"租户 {tid} 已禁用工具 {tool_name}",
                                risk="high", hook="tenant_policy")
    except Exception as _e:
        log_suppressed(logger, _e)
    return HookDecision(allow=True)


# Tenant tool-deny lists are kept tiny and in-memory here; persisting them is a
# later step. Empty by default → no tenant restricts anything.
_TENANT_DENY: dict[str, set] = {}


def _tenant_denied_tools(tenant_id: str) -> set:
    return _TENANT_DENY.get(tenant_id, set())


def set_tenant_denied_tools(tenant_id: str, tools: set) -> None:
    _TENANT_DENY[tenant_id] = set(tools)


def _audit_post_hook(tool_name: str, args: dict, ctx: dict, ok: bool, latency_ms: int) -> None:
    """Record every tool call's outcome to observability (already wired) — here
    we add an audit line for high-risk tools so they're traceable per user."""
    try:
        from hashmm.agent_safety import classify_tool_risk
        if classify_tool_risk(tool_name, args) == "high":
            from hashmm.api import database as db
            uid = ctx.get("user_id") or ctx.get("uid") or "system"
            db.audit(uid, uid, "high_risk_tool",
                     f"{tool_name} → {'ok' if ok else 'fail'} ({latency_ms}ms)")
    except Exception as _e:
        log_suppressed(logger, _e)


# Active agent-team role (set by the orchestrator while a sub-agent runs). When
# set, the role-scope hook denies any tool outside that role's allowlist — so a
# "research" worker physically cannot call a write/code tool, even if the model
# tries. Thread-local so concurrent requests don't clobber each other.
import threading as _threading
_active_role = _threading.local()


def set_active_role(role: str | None, allowed_tools: set | None = None) -> None:
    _active_role.role = role
    _active_role.allowed = set(allowed_tools) if allowed_tools else None


def _role_scope_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Deny tools outside the active sub-agent role's allowlist (agent-team
    permission isolation). No active role → no restriction."""
    allowed = getattr(_active_role, "allowed", None)
    role = getattr(_active_role, "role", None)
    if allowed is not None and tool_name not in allowed:
        return HookDecision(allow=False,
                            reason=f"角色「{role}」无权调用工具 {tool_name}",
                            risk="high", hook="role_scope")
    return HookDecision(allow=True)


def _plan_mode_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Strict Plan Mode: in strict mode, a side-effecting tool (file/doc/code
    generation, deletion) is DENIED unless the user has confirmed this turn.

    This lives at the tool layer (not the planner) so it works no matter which
    path generates — Agent Loop, orchestrator, or a direct tool call. The
    streaming layer sets ctx['plan_confirmed']=True when the user's message is a
    confirmation (e.g. "确认执行")."""
    import os
    if os.environ.get("HASHMM_PLAN_MODE", "") != "strict":
        return HookDecision(allow=True)
    if ctx.get("plan_confirmed"):
        return HookDecision(allow=True)
    try:
        from hashmm.agent_safety import classify_tool_risk
        if classify_tool_risk(tool_name, args) == "high":
            return HookDecision(
                allow=False,
                reason=(f"strict 计划模式：工具 {tool_name} 会产生副作用，"
                        f"需用户确认后执行（请回复「确认执行」）"),
                risk="high", hook="plan_mode")
    except Exception as _e:
        log_suppressed(logger, _e)
    return HookDecision(allow=True)


def install_default_hooks() -> None:
    """Register the built-in hooks once. Idempotent."""
    if any(n == "permission" for n, _ in _PRE_HOOKS):
        return
    register_pre_hook("role_scope", _role_scope_hook)       # agent-team isolation
    register_pre_hook("plan_mode", _plan_mode_hook)          # strict plan-mode gate
    register_pre_hook("tenant_policy", _tenant_policy_hook)  # deny-first: tenant blocks
    register_pre_hook("permission", _permission_hook)
    register_pre_hook("risk", _risk_hook)
    register_post_hook("audit", _audit_post_hook)
    # v17 Phase 77: agent-action governance — approval gate (enforces the
    # require_approval the risk hooks compute) + structured audit stream. Both
    # are inert unless HASHMM_TOOL_APPROVAL / HASHMM_AUDIT_TOOLS are set.
    try:
        from hashmm.agent.tool_governance import register_governance_hooks
        register_governance_hooks()
    except Exception as _e:
        log_suppressed(logger, _e)


# Install on import so the entry point is guarded without explicit setup.
install_default_hooks()
