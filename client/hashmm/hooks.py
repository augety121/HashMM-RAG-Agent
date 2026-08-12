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
LifecycleHook = Callable[[dict, dict], HookDecision | None]

LIFECYCLE_EVENTS = frozenset({
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PermissionRequest", "PostToolUse",
    "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Stop",
})

_PRE_HOOKS: list[tuple[str, PreToolHook]] = []
_POST_HOOKS: list[tuple[str, PostToolHook]] = []
_PRECOMPACT_HOOKS: list[tuple[str, PreCompactHook]] = []
_SUBAGENT_STOP_HOOKS: list[tuple[str, SubagentStopHook]] = []
_LIFECYCLE_HOOKS: dict[str, list[tuple[str, LifecycleHook, bool]]] = {
    event: [] for event in LIFECYCLE_EVENTS
}
_CRITICAL_PRE_HOOKS: set[str] = set()
_MAX_TRACE_RUNS = 64


def _append_hook_run(ctx: dict, *, lifecycle: str, name: str, status: str,
                     started: float, reason: str = "") -> None:
    """Append one bounded, argument-free lifecycle record to the current run.

    Hook traces are execution evidence, so they contain only deterministic
    runtime facts.  Arguments and credentials are deliberately excluded.
    """
    try:
        rows = ctx.setdefault("_hook_runs", [])
        if not isinstance(rows, list):
            rows = []
            ctx["_hook_runs"] = rows
        rows.append({
            "hook": str(name)[:120],
            "lifecycle": str(lifecycle)[:40],
            "status": str(status)[:32],
            "elapsed_ms": max(0, round((time.perf_counter() - started) * 1000)),
            "reason": str(reason or "")[:240],
        })
        if len(rows) > _MAX_TRACE_RUNS:
            del rows[:-_MAX_TRACE_RUNS]
    except Exception as exc:
        log_suppressed(logger, exc, "hook trace")


def get_hook_runs(ctx: dict | None) -> list[dict]:
    """Return a defensive copy of the current call's hook lifecycle trace."""
    if not isinstance(ctx, dict) or not isinstance(ctx.get("_hook_runs"), list):
        return []
    return [dict(row) for row in ctx["_hook_runs"] if isinstance(row, dict)]


def register_pre_hook(name: str, fn: PreToolHook, *, before: str = "",
                      critical: bool = False) -> None:
    """Register a named pre-hook, optionally before another named boundary.

    User PreToolUse hooks are inserted before ``permission`` so a deterministic
    block is visible before an approval prompt, matching the lifecycle exposed
    by mature coding agents. Built-in role/tenant/plan gates still run first.
    """
    item = (name, fn)
    if critical:
        _CRITICAL_PRE_HOOKS.add(name)
    if before:
        for index, (existing, _) in enumerate(_PRE_HOOKS):
            if existing == before:
                _PRE_HOOKS.insert(index, item)
                return
    _PRE_HOOKS.append(item)


def register_post_hook(name: str, fn: PostToolHook) -> None:
    _POST_HOOKS.append((name, fn))


def register_compact_hook(name: str, fn: PreCompactHook) -> None:
    """Register a PreCompact hook (fires before context compaction drops history)."""
    _PRECOMPACT_HOOKS.append((name, fn))


def register_subagent_stop_hook(name: str, fn: SubagentStopHook) -> None:
    """Register a SubagentStop hook (fires when a sub-agent finishes)."""
    _SUBAGENT_STOP_HOOKS.append((name, fn))


def register_lifecycle_hook(
    event: str,
    name: str,
    fn: LifecycleHook,
    *,
    critical: bool = False,
) -> None:
    """Register a bounded harness lifecycle hook.

    Critical hooks fail closed. Observer hooks record failure without turning
    an availability problem into an implicit permission grant.
    """
    if event not in LIFECYCLE_EVENTS:
        raise ValueError(f"unsupported hook lifecycle: {event}")
    _LIFECYCLE_HOOKS[event].append((str(name)[:120], fn, bool(critical)))


def run_lifecycle_hooks(
    event: str,
    payload: dict | None = None,
    ctx: dict | None = None,
) -> HookDecision:
    if event not in LIFECYCLE_EVENTS:
        return HookDecision(allow=False, reason="unknown hook lifecycle", risk="high")
    context = ctx if isinstance(ctx, dict) else {}
    final = HookDecision()
    # Payloads are bounded projections prepared by the caller; no raw secrets
    # or tool arguments are copied into the lifecycle trace.
    public_payload = dict(payload or {})
    for name, hook, critical in _LIFECYCLE_HOOKS[event]:
        started = time.perf_counter()
        try:
            decision = hook(public_payload, context)
        except Exception as exc:
            log_suppressed(logger, exc)
            _append_hook_run(
                context, lifecycle=event, name=name, status="error",
                started=started, reason=type(exc).__name__,
            )
            if critical:
                return HookDecision(
                    allow=False,
                    reason=f"关键生命周期 Hook {name} 执行异常",
                    risk="high",
                    hook=name,
                )
            continue
        if decision is not None and not isinstance(decision, HookDecision):
            return HookDecision(
                allow=False, reason=f"Hook {name} 返回无效裁决",
                risk="high", hook=name,
            )
        if decision is not None:
            if decision.risk == "high":
                final.risk = "high"
            if decision.require_approval:
                final.require_approval = True
                final.reason = final.reason or decision.reason
            if not decision.allow:
                decision.hook = decision.hook or name
                _append_hook_run(
                    context, lifecycle=event, name=name, status="denied",
                    started=started, reason=decision.reason,
                )
                return decision
        _append_hook_run(
            context, lifecycle=event, name=name,
            status="completed", started=started,
        )
    return final


def run_compact_hooks(messages: list, ctx: dict | None = None) -> None:
    """Run all PreCompact hooks. No-op when none registered (zero behaviour change).
    Never raises — a misbehaving hook must not break compaction."""
    ctx = ctx if ctx is not None else {}
    lifecycle = run_lifecycle_hooks(
        "PreCompact",
        {"message_count": len(messages or [])},
        ctx,
    )
    if not lifecycle.allow:
        logger.warning("PreCompact lifecycle denied compaction: %s", lifecycle.reason)
        return
    for name, hook in _PRECOMPACT_HOOKS:
        started = time.perf_counter()
        try:
            hook(messages or [], ctx)
            _append_hook_run(ctx, lifecycle="PreCompact", name=name,
                             status="completed", started=started)
        except Exception as _e:
            log_suppressed(logger, _e)
            _append_hook_run(ctx, lifecycle="PreCompact", name=name,
                             status="error", started=started,
                             reason=type(_e).__name__)


def run_post_compact_hooks(
    *,
    before_count: int,
    after_count: int,
    ctx: dict | None = None,
) -> HookDecision:
    return run_lifecycle_hooks(
        "PostCompact",
        {
            "before_count": max(0, int(before_count or 0)),
            "after_count": max(0, int(after_count or 0)),
        },
        ctx,
    )


def run_subagent_start_hooks(
    subtask_id: str,
    role: str,
    ctx: dict | None = None,
) -> HookDecision:
    return run_lifecycle_hooks(
        "SubagentStart",
        {"subtask_id": str(subtask_id)[:120], "role": str(role)[:64]},
        ctx,
    )


def run_subagent_stop_hooks(subtask_id: str, result: str, ctx: dict | None = None) -> None:
    """Run all SubagentStop hooks. No-op when none registered. Never raises."""
    ctx = ctx if ctx is not None else {}
    run_lifecycle_hooks(
        "SubagentStop",
        {
            "subtask_id": str(subtask_id)[:120],
            "result_chars": len(str(result or "")),
        },
        ctx,
    )
    for name, hook in _SUBAGENT_STOP_HOOKS:
        started = time.perf_counter()
        try:
            hook(subtask_id, result or "", ctx)
            _append_hook_run(ctx, lifecycle="SubagentStop", name=name,
                             status="completed", started=started)
        except Exception as _e:
            log_suppressed(logger, _e)
            _append_hook_run(ctx, lifecycle="SubagentStop", name=name,
                             status="error", started=started,
                             reason=type(_e).__name__)


def reset_hooks() -> None:
    """Clear all hooks (tests)."""
    _PRE_HOOKS.clear()
    _POST_HOOKS.clear()
    _PRECOMPACT_HOOKS.clear()
    _SUBAGENT_STOP_HOOKS.clear()
    for hooks in _LIFECYCLE_HOOKS.values():
        hooks.clear()
    _CRITICAL_PRE_HOOKS.clear()


def run_pre_tool_hooks(tool_name: str, args: dict, ctx: dict | None = None) -> HookDecision:
    """Run pre-tool hooks with explicit availability vs safety semantics.

    Observer hooks remain fail-open.  Hooks registered as ``critical`` are
    policy boundaries: an exception is a denial, never an implicit grant.
    """
    ctx = ctx if ctx is not None else {}
    lifecycle = run_lifecycle_hooks(
        "PreToolUse",
        {
            "tool_name": str(tool_name)[:120],
            "argument_keys": sorted(str(key)[:64] for key in (args or {}).keys())[:64],
        },
        ctx,
    )
    if not lifecycle.allow:
        return lifecycle
    final = HookDecision(allow=True)
    for name, hook in _PRE_HOOKS:
        started = time.perf_counter()
        try:
            d = hook(tool_name, args or {}, ctx)
        except Exception as _e:
            log_suppressed(logger, _e)
            _append_hook_run(ctx, lifecycle="PreToolUse", name=name,
                             status="error", started=started,
                             reason=type(_e).__name__)
            if name in _CRITICAL_PRE_HOOKS:
                return HookDecision(
                    allow=False,
                    reason=f"安全 Hook {name} 执行异常，已按拒绝处理",
                    risk="high",
                    hook=name,
                )
            continue
        if d is None:
            _append_hook_run(ctx, lifecycle="PreToolUse", name=name,
                             status="completed", started=started)
            continue
        if not isinstance(d, HookDecision):
            logger.error("[Hook:%s] 返回了无效裁决类型 %s，按拒绝处理", name, type(d).__name__)
            _append_hook_run(ctx, lifecycle="PreToolUse", name=name,
                             status="invalid", started=started,
                             reason=type(d).__name__)
            return HookDecision(
                allow=False,
                reason=f"Hook {name} 返回无效裁决，已按拒绝处理",
                risk="high",
                hook=name,
            )
        # propagate risk / approval upward
        if d.risk == "high":
            final.risk = "high"
        if d.require_approval:
            final.require_approval = True
            final.reason = final.reason or d.reason
        if not d.allow:
            d.hook = d.hook or name
            logger.info(f"[Hook:{d.hook}] DENY {tool_name}: {d.reason}")
            _append_hook_run(ctx, lifecycle="PreToolUse", name=name,
                             status="denied", started=started, reason=d.reason)
            return d
        _append_hook_run(ctx, lifecycle="PreToolUse", name=name,
                         status="approval_required" if d.require_approval else "completed",
                         started=started, reason=d.reason)
    if final.require_approval:
        approval = run_lifecycle_hooks(
            "PermissionRequest",
            {"tool_name": str(tool_name)[:120], "risk": final.risk},
            ctx,
        )
        if not approval.allow:
            return approval
    return final


def run_post_tool_hooks(tool_name: str, args: dict, ctx: dict | None, ok: bool, latency_ms: int) -> None:
    ctx = ctx if ctx is not None else {}
    run_lifecycle_hooks(
        "PostToolUse",
        {
            "tool_name": str(tool_name)[:120],
            "ok": bool(ok),
            "latency_ms": max(0, int(latency_ms or 0)),
        },
        ctx,
    )
    for name, hook in _POST_HOOKS:
        started = time.perf_counter()
        try:
            hook(tool_name, args or {}, ctx, ok, latency_ms)
            _append_hook_run(ctx, lifecycle="PostToolUse", name=name,
                             status="completed", started=started)
        except Exception as _e:
            log_suppressed(logger, _e)
            _append_hook_run(ctx, lifecycle="PostToolUse", name=name,
                             status="error", started=started,
                             reason=type(_e).__name__)


# ────────────────────────────────────────────────────────────────────
# Default hooks — wired at import so the single entry point is always guarded.
# They reproduce existing behaviour (permission levels + agent_safety risk),
# now enforced architecturally instead of only inside the agent loop.
# ────────────────────────────────────────────────────────────────────

def _permission_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """工具权限钩子——【委托】给唯一事实源 hashmm.agent.permissions。

    V308 修 P0-3：此处原本是 permissions.py 的一份【复制品】，并已经漂移出三个缺陷：
      1. `TOOL_PERMISSIONS.get(tool_name, PermissionLevel.READ)` —— 未登记工具默认按只读
         放行。run_shell 恰好未登记 → 开放式命令执行被自动批准（原 P0 的真身之一）。
      2. 直接读 `config["auto_approve"]`，绕过权限模式 / 参数绑定审批 / 频率限制。
      3. 外层 `except Exception: return allow=True` —— 权限判断出错即放行（fail-open）。

    现在改为单一决策点：一切交给 permissions.check()（它内部已是 deny-first + fail-closed）。
    本函数【不再吞异常】——权限链路异常必须表现为拒绝，而不是放行。
    """
    try:
        from hashmm.agent.permissions import get_permissions, TOOL_PERMISSIONS

        user_id = ctx.get("user_id") or ctx.get("uid") or ""
        cwd = ctx.get("cwd") or ctx.get("workspace") or None
        ok, reason = get_permissions().check(tool_name, args or {}, user_id, cwd=cwd)
        if not ok:
            return HookDecision(allow=False, reason=reason, risk="high", hook="permission")

        # 放行了，但若属于需批准的高危级别（如 admin/已批准的 run_shell），仍标高风险，
        # 让上层 UI 有机会展示"这是一次高危操作"。
        from hashmm.agent.permissions import PermissionLevel
        level = TOOL_PERMISSIONS.get(tool_name)
        if level in (PermissionLevel.DELETE, PermissionLevel.SYSTEM):
            return HookDecision(allow=True, require_approval=True, risk="high",
                                hook="permission")
        return HookDecision(allow=True)
    except Exception as _e:
        # fail-closed：权限判断本身出错 → 拒绝执行，并把错误暴露出来（不 suppress）。
        logger.error("[Hook:permission] 权限判断异常，按拒绝处理: %r", _e)
        return HookDecision(allow=False,
                            reason=f"权限判断异常，已拒绝执行（{type(_e).__name__}）",
                            risk="high", hook="permission")


def _risk_hook(tool_name: str, args: dict, ctx: dict) -> HookDecision:
    """Classify mutating/external/irreversible actions as high-risk (agent_safety).
    High-risk → flagged for approval (does not hard-deny; the approval gate /
    tenant policy decides). Read-only tools pass freely."""
    try:
        from hashmm.agent_safety import classify_tool_risk
        if classify_tool_risk(tool_name, args) == "high":
            return HookDecision(allow=True, require_approval=True, risk="high", hook="risk")
    except Exception as _e:
        logger.error("[Hook:risk] 风险分类异常，按拒绝处理: %r", _e)
        return HookDecision(
            allow=False,
            reason=f"风险分类异常，已拒绝执行（{type(_e).__name__}）",
            risk="high",
            hook="risk",
        )
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
        logger.error("[Hook:tenant_policy] 租户策略异常，按拒绝处理: %r", _e)
        return HookDecision(
            allow=False,
            reason=f"租户策略读取异常，已拒绝执行（{type(_e).__name__}）",
            risk="high",
            hook="tenant_policy",
        )
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
        # Audit transport is observability, not an execution authority.  Keep
        # the already-completed call result, but make the loss visible.
        logger.error("[Hook:audit] 高风险工具审计写入失败: %r", _e)


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
        logger.error("[Hook:plan_mode] 风险分类异常，按拒绝处理: %r", _e)
        return HookDecision(
            allow=False,
            reason=f"计划模式安全检查异常，已拒绝执行（{type(_e).__name__}）",
            risk="high",
            hook="plan_mode",
        )
    return HookDecision(allow=True)


def install_default_hooks() -> None:
    """Register the built-in hooks once. Idempotent."""
    if any(n == "permission" for n, _ in _PRE_HOOKS):
        return
    register_pre_hook("role_scope", _role_scope_hook, critical=True)       # agent-team isolation
    register_pre_hook("plan_mode", _plan_mode_hook, critical=True)          # strict plan-mode gate
    register_pre_hook("tenant_policy", _tenant_policy_hook, critical=True)  # deny-first: tenant blocks
    register_pre_hook("permission", _permission_hook, critical=True)
    register_pre_hook("risk", _risk_hook, critical=True)
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
