"""Server-authoritative execution scopes for durable and delegated Agent work.

An execution scope is the capability envelope of one run.  It is created by
the server from the authenticated principal and never from a model response.
Child agents may only receive an intersection of their role toolset and their
parent's scope; delegation can therefore narrow authority, never expand it.

The scope complements :mod:`hashmm.agent.permissions`: the permission system
answers "may this principal perform this concrete call now?", while this
module answers "was this capability part of this task at all?".  Both checks
must pass before execution.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable
from urllib.parse import urlsplit


SCOPE_SCHEMA = "hashmm.execution-scope.v1"
NETWORK_MODES = {"deny", "allow", "allowlist"}

_NETWORK_TOOLS = {
    "fetch_url", "web_search", "browser_open", "browser_act",
    "browser_read", "browser_screenshot",
}
_URL_KEYS = ("url", "target_url", "href", "uri")
_WRITE_TOOLS = {
    "create_file", "create_document", "create_xlsx", "create_pdf",
    "create_pptx_from_plan", "str_replace", "insert_lines",
    "file_restore", "canvas_block_patch",
}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def canonical_origin(value: Any) -> str:
    """Return a strict http(s) origin or an empty string for invalid input."""
    raw = _text(value, 2048)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        port_text = f":{port}" if port and port != default_port else ""
        return f"{parsed.scheme.lower()}://{host}{port_text}"
    except (TypeError, ValueError):
        return ""


def normalize_origins(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = []
    out: list[str] = []
    for value in list(values)[:32]:
        origin = canonical_origin(value)
        if origin and origin not in out:
            out.append(origin)
    return out


def _tools(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        name = _text(value, 120)
        if name and name not in out:
            out.append(name)
    return out[:160]


def build_root_scope(
    *,
    owner_id: str,
    conversation_id: str,
    run_id: str,
    allowed_tools: Iterable[str],
    approval_mode: str,
    network_mode: str = "deny",
    allowed_origins: Any = None,
    allow_subagents: bool = False,
    max_tool_calls: int = 20,
    max_workers: int = 3,
    scope_id: str = "",
) -> dict[str, Any]:
    """Build a normalized root scope from server-owned identity fields."""
    mode = network_mode if network_mode in NETWORK_MODES else "deny"
    origins = normalize_origins(allowed_origins)
    if mode == "allowlist" and not origins:
        # An empty allowlist is semantically a network deny, not allow-all.
        mode = "deny"
    tools = _tools(allowed_tools)
    if mode == "deny":
        tools = [name for name in tools if name not in _NETWORK_TOOLS]
    if not allow_subagents:
        tools = [name for name in tools if name != "spawn_worker"]
    return {
        "schema": SCOPE_SCHEMA,
        "scope_id": _text(scope_id, 80) or "s" + uuid.uuid4().hex[:16],
        "run_id": _text(run_id, 80),
        "parent_scope_id": "",
        "owner_id": _text(owner_id, 160),
        "conversation_id": _text(conversation_id, 160),
        "depth": 0,
        "sharing": "private",
        "memory_scope": "conversation",
        "approval_mode": "workspace" if approval_mode == "workspace" else "read_only",
        "allowed_tools": tools,
        "allow_subagents": bool(allow_subagents),
        "network": {"mode": mode, "origins": origins if mode == "allowlist" else []},
        "budgets": {
            "max_tool_calls": max(1, min(int(max_tool_calls or 20), 200)),
            "max_workers": max(0, min(int(max_workers or 0), 8)),
        },
    }


def derive_child_scope(
    parent: dict[str, Any] | None,
    *,
    role: str,
    role_tools: Iterable[str],
    workspace_branch: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Derive a child scope by intersection; return None for legacy unscoped chat."""
    if not isinstance(parent, dict):
        return None
    parent_tools = set(_tools(parent.get("allowed_tools") or []))
    child_tools = [
        name for name in _tools(role_tools)
        if name in parent_tools and name != "spawn_worker"
    ]
    branch = workspace_branch if isinstance(workspace_branch, dict) else {}
    branch_verified = bool(
        branch.get("verified")
        and branch.get("mode") in {"copy_on_write", "worktree"}
        and _text(branch.get("root_path"), 2_048)
    )
    if not branch_verified:
        # A delegated worker without a server-provisioned branch is read-only,
        # even when an older parent scope contained a writing capability.
        child_tools = [name for name in child_tools if name not in _WRITE_TOOLS]
    network = parent.get("network") if isinstance(parent.get("network"), dict) else {}
    budgets = parent.get("budgets") if isinstance(parent.get("budgets"), dict) else {}
    result = {
        "schema": SCOPE_SCHEMA,
        "scope_id": "s" + uuid.uuid4().hex[:16],
        "run_id": _text(parent.get("run_id"), 80),
        "parent_scope_id": _text(parent.get("scope_id"), 80),
        "owner_id": _text(parent.get("owner_id"), 160),
        "conversation_id": _text(parent.get("conversation_id"), 160),
        "depth": max(0, min(int(parent.get("depth") or 0) + 1, 16)),
        "role": _text(role, 40),
        "sharing": "private",
        "memory_scope": "conversation",
        "approval_mode": "workspace" if parent.get("approval_mode") == "workspace" else "read_only",
        "allowed_tools": child_tools,
        "allow_subagents": False,
        "network": {
            "mode": network.get("mode") if network.get("mode") in NETWORK_MODES else "deny",
            "origins": normalize_origins(network.get("origins") or []),
        },
        "budgets": {
            "max_tool_calls": min(4, max(1, int(budgets.get("max_tool_calls") or 4))),
            "max_workers": 0,
        },
    }
    if branch_verified:
        result["workspace_branch"] = dict(branch)
    return result


def normalize_persisted_scope(
    raw: Any,
    *,
    owner_id: str,
    conversation_id: str,
    run_id: str,
    allowed_tools: Iterable[str],
    approval_mode: str,
    legacy_network_mode: str = "allow",
) -> dict[str, Any]:
    """Rebuild a persisted envelope against current server capabilities.

    Identity fields are always taken from the owning loop.  Persisted tool
    names are intersected with the current registry so a stale/tampered state
    file cannot grant a capability added later.
    """
    current = set(_tools(allowed_tools))
    supplied = raw if isinstance(raw, dict) else {}
    requested = _tools(supplied.get("allowed_tools") or current)
    network = supplied.get("network") if isinstance(supplied.get("network"), dict) else {}
    mode = network.get("mode") if network.get("mode") in NETWORK_MODES else legacy_network_mode
    return build_root_scope(
        owner_id=owner_id,
        conversation_id=conversation_id,
        run_id=run_id,
        scope_id=_text(supplied.get("scope_id"), 80),
        allowed_tools=[name for name in requested if name in current],
        approval_mode=approval_mode,
        network_mode=mode,
        allowed_origins=network.get("origins") or [],
        allow_subagents=bool(supplied.get("allow_subagents", raw is None)),
        max_tool_calls=int((supplied.get("budgets") or {}).get("max_tool_calls") or 20)
        if isinstance(supplied.get("budgets"), dict) else 20,
        max_workers=int((supplied.get("budgets") or {}).get("max_workers") or 3)
        if isinstance(supplied.get("budgets"), dict) else 3,
    )


def check_execution_scope(
    scope: dict[str, Any] | None,
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    user_id: str,
    conversation_id: str,
) -> tuple[bool, str]:
    """Validate a server-authored run capability envelope."""
    if scope is None:
        return False, "缺少服务端任务执行范围"
    if not isinstance(scope, dict) or scope.get("schema") != SCOPE_SCHEMA:
        return False, "执行范围格式无效"
    if _text(scope.get("owner_id"), 160) != _text(user_id, 160):
        return False, "执行范围与当前用户不匹配"
    if _text(scope.get("conversation_id"), 160) != _text(conversation_id, 160):
        return False, "执行范围与当前会话不匹配"
    allowed = set(_tools(scope.get("allowed_tools") or []))
    if tool_name not in allowed:
        return False, f"工具 {tool_name} 不在本任务授权范围内"
    if tool_name in _WRITE_TOOLS and int(scope.get("depth") or 0) > 0:
        branch = scope.get("workspace_branch")
        if not (
            isinstance(branch, dict)
            and branch.get("verified")
            and branch.get("mode") in {"copy_on_write", "worktree"}
            and _text(branch.get("root_path"), 2_048)
        ):
            return False, "写入型子任务缺少独立工作分支"
    if tool_name == "spawn_worker" and not scope.get("allow_subagents"):
        return False, "本任务未授权派生专员"
    if tool_name in _NETWORK_TOOLS:
        network = scope.get("network") if isinstance(scope.get("network"), dict) else {}
        mode = network.get("mode") if network.get("mode") in NETWORK_MODES else "deny"
        if mode == "deny":
            return False, "本任务未授权联网"
        if mode == "allowlist":
            target = ""
            for key in _URL_KEYS:
                if (args or {}).get(key):
                    target = str((args or {}).get(key))
                    break
            origin = canonical_origin(target)
            allowed_origins = set(normalize_origins(network.get("origins") or []))
            if not origin:
                return False, f"工具 {tool_name} 无法在站点白名单模式下确定目标站点"
            if origin not in allowed_origins:
                return False, f"站点 {origin} 不在本任务联网白名单内"
    return True, "scope_allowed"


def public_scope(scope: Any) -> dict[str, Any]:
    """Return the non-secret user-visible contract (owner identity is redacted)."""
    if not isinstance(scope, dict):
        return {}
    projected = {key: value for key, value in scope.items() if key != "owner_id"}
    branch = projected.get("workspace_branch")
    if isinstance(branch, dict):
        projected["workspace_branch"] = {
            key: value for key, value in branch.items()
            if key not in {"owner_id", "root_path"}
        }
    return projected


def describe_scope(scope: Any) -> str:
    if not isinstance(scope, dict):
        return "未建立持久执行范围"
    network = scope.get("network") if isinstance(scope.get("network"), dict) else {}
    mode_label = {"deny": "禁止联网", "allow": "允许联网", "allowlist": "仅允许指定站点"}.get(
        str(network.get("mode") or "deny"), "禁止联网")
    return (
        f"工具 {len(scope.get('allowed_tools') or [])} 个；{mode_label}；"
        f"子 Agent {'允许' if scope.get('allow_subagents') else '关闭'}；"
        "记忆仅限当前会话；结果默认私有"
    )
