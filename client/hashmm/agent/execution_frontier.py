"""Deterministic graph-guided work frontier for HashMM Agent runs.

The task evidence graph answers what is connected and what remains blocked.
This module answers the next operational question without asking a model to
invent a plan: which *minimum* action is feasible under the persisted tool and
network scope?  It never executes a tool and never widens authority.

The design combines three independently useful ideas while remaining native
to HashMM: criteria-centred query-time working sets, structured specialist
handoffs, and desired-vs-observed reconciliation.  All routing decisions are
derived from runtime graph nodes plus the server-owned tool registry/scope.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePath
from typing import Any, Iterable


SCHEMA = "hashmm.execution-frontier.v1"
GRAPH_SCHEMA = "hashmm.task-evidence-graph.v1"
_NETWORK_TOOLS = {"web_search", "browser_open", "browser_read", "browser_act", "browser_screenshot"}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(0, limit)]


def _fingerprint(value: Any, size: int = 20) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def _category(kind: str) -> str:
    return {
        "claim": "evidence_gap",
        "source": "evidence_gap",
        "artifact": "delivery_gap",
        "agent": "delegation_gap",
        "tool": "tool_recovery",
        "check": "verification_gap",
        "criterion": "verification_gap",
    }.get(kind, "review_gap")


def _delivery_tools(label: str) -> list[str]:
    suffix = PurePath(label).suffix.lower()
    if suffix in {".docx", ".pdf", ".xlsx", ".pptx", ".md", ".txt", ".html"}:
        return ["create_document", "create_file"]
    return ["create_file", "create_document"]


def _candidate_tools(category: str, node: dict[str, Any]) -> list[str]:
    if category == "evidence_gap":
        # Local evidence is preferred. Open-world search remains a scoped
        # fallback and can never become ready when the network mode is deny.
        return ["kb_search", "kg_query", "web_search"]
    if category == "delivery_gap":
        return _delivery_tools(_text(node.get("label"), 180))
    if category == "delegation_gap":
        return ["spawn_worker"]
    if category == "tool_recovery":
        name = _text(node.get("label"), 120)
        return [name] if name else []
    return []


def _tool_route(
    tool: str,
    *,
    available: set[str],
    allowed: set[str],
    scoped: bool,
    network_mode: str,
) -> dict[str, str]:
    if tool not in available:
        return {"kind": "tool", "tool": tool, "status": "unavailable", "reason": "当前工具注册表没有可执行器"}
    if not scoped:
        return {"kind": "tool", "tool": tool, "status": "requires_scope", "reason": "运行没有持久执行范围，不能预授权下一步"}
    if tool not in allowed:
        return {"kind": "tool", "tool": tool, "status": "blocked_by_scope", "reason": "工具不在本任务持久权限范围内"}
    if tool in _NETWORK_TOOLS and network_mode == "deny":
        return {"kind": "tool", "tool": tool, "status": "blocked_by_scope", "reason": "本任务禁止联网"}
    return {"kind": "tool", "tool": tool, "status": "ready", "reason": "工具存在且位于本任务执行范围内"}


def _fallback_route(category: str) -> dict[str, str]:
    if category == "tool_recovery":
        return {"kind": "strategy", "strategy": "inspect_failure", "status": "ready", "reason": "先检查失败结果和参数，再决定是否重试"}
    if category == "delegation_gap":
        return {"kind": "strategy", "strategy": "main_agent_takeover", "status": "ready", "reason": "主 Agent 可接管失败分工，但不能获得额外工具"}
    if category == "verification_gap":
        return {"kind": "strategy", "strategy": "independent_review", "status": "ready", "reason": "先运行现有确定性检查或请求人工复核"}
    if category == "review_gap":
        return {"kind": "strategy", "strategy": "inspect_runtime_state", "status": "ready", "reason": "读取当前运行状态后再选择动作"}
    return {"kind": "strategy", "strategy": "request_scope_or_input", "status": "waiting_input", "reason": "当前范围内没有能闭环该缺口的工具"}


def _minimum_action(category: str, route: dict[str, str], label: str) -> str:
    target = label or "当前阻塞"
    tool = route.get("tool")
    strategy = route.get("strategy")
    if route.get("status") == "ready" and tool:
        return f"使用 {tool} 处理：{target}"
    if strategy == "inspect_failure":
        return f"检查失败工具的返回、参数和审批记录：{target}"
    if strategy == "main_agent_takeover":
        return f"由主 Agent 在原权限范围内接管：{target}"
    if strategy == "independent_review":
        return f"对完成条件做独立复核：{target}"
    if category == "evidence_gap":
        return f"补充可核验来源或请求开启所需检索范围：{target}"
    if category == "delivery_gap":
        return f"确认交付格式并开启相应写入能力：{target}"
    return f"读取最新状态并处理：{target}"


def build_execution_frontier(
    graph: dict[str, Any] | None,
    *,
    execution_scope: dict[str, Any] | None = None,
    available_tools: Iterable[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded desired-vs-observed work frontier.

    A ``ready`` route means only that the capability exists inside the current
    scope.  It is not approval and it is not execution evidence.  The normal
    tool guard, confirmation flow and owner checks still run at invocation.
    """
    source_graph = graph if isinstance(graph, dict) else {}
    graph_valid = source_graph.get("schema") == GRAPH_SCHEMA
    nodes = {
        str(node.get("id") or ""): node
        for node in (source_graph.get("nodes") or [])
        if isinstance(node, dict) and node.get("id")
    } if graph_valid else {}
    edges = [edge for edge in (source_graph.get("edges") or []) if isinstance(edge, dict)] if graph_valid else []
    scope = execution_scope if isinstance(execution_scope, dict) else {}
    scoped = scope.get("schema") == "hashmm.execution-scope.v1"
    allowed = {_text(name, 120) for name in (scope.get("allowed_tools") or []) if _text(name, 120)}
    available = {_text(name, 120) for name in (available_tools or []) if _text(name, 120)}
    network = scope.get("network") if isinstance(scope.get("network"), dict) else {}
    network_mode = _text(network.get("mode"), 24) or "deny"

    items: list[dict[str, Any]] = []
    for blocker in list(source_graph.get("blockers") or [])[:32] if graph_valid else []:
        if not isinstance(blocker, dict):
            continue
        node_id = str(blocker.get("node_id") or "")
        node = nodes.get(node_id, {})
        kind = _text(node.get("kind"), 40) or "unknown"
        label = _text(node.get("label"), 180) or "未命名阻塞"
        category = _category(kind)
        candidates = [
            _tool_route(
                tool,
                available=available,
                allowed=allowed,
                scoped=scoped,
                network_mode=network_mode,
            )
            for tool in _candidate_tools(category, node)
        ]
        selected = next((candidate for candidate in candidates if candidate.get("status") == "ready"), None)
        selected = dict(selected or _fallback_route(category))
        neighbours = {node_id}
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source == node_id and target in nodes:
                neighbours.add(target)
            elif target == node_id and source in nodes:
                neighbours.add(source)
        working_set = sorted(neighbours)[:12]
        item_key = f"{kind}:{node_id}"
        items.append({
            "id": "f" + _fingerprint([source_graph.get("run_id"), item_key]),
            "blocker_node_id": node_id,
            "category": category,
            "label": label,
            "desired_state": "resolved",
            "observed_state": _text(node.get("status"), 32) or "unknown",
            "reason": _text(blocker.get("reason"), 200),
            "working_set": {
                "id": "w" + _fingerprint([source_graph.get("graph_id"), working_set]),
                "node_ids": working_set,
                "construction": "query_time_runtime_neighbourhood",
            },
            "candidates": candidates[:6],
            "selected_route": selected,
            "minimum_action": _minimum_action(category, selected, label),
        })

    items.sort(key=lambda item: (item["selected_route"].get("status") != "ready", item["category"], item["id"]))
    ready = sum(1 for item in items if item["selected_route"].get("status") == "ready")
    # A safe strategy fallback can be ready even when every preferred tool is
    # outside the persisted scope.  Keep that distinction observable instead
    # of hiding the scope gap behind the fallback route.
    blocked = sum(
        1
        for item in items
        if not any(candidate.get("status") == "ready" for candidate in item.get("candidates") or [])
        and any(
            candidate.get("status") in {"blocked_by_scope", "requires_scope"}
            for candidate in item.get("candidates") or []
        )
    )
    waiting = sum(1 for item in items if item["selected_route"].get("status") == "waiting_input")
    current_ids = {str(item.get("blocker_node_id") or "") for item in items}
    previous_ids = {
        str(item.get("blocker_node_id") or "")
        for item in ((previous or {}).get("items") or [])
        if isinstance(item, dict)
    } if isinstance(previous, dict) and previous.get("schema") == SCHEMA else set()
    closed = sorted(previous_ids - current_ids)
    opened = sorted(current_ids - previous_ids)
    if previous_ids and not current_ids:
        reconcile_status = "converged"
    elif not previous_ids:
        reconcile_status = "initial"
    elif closed and not opened:
        reconcile_status = "progressed"
    elif opened and not closed:
        reconcile_status = "regressed"
    else:
        reconcile_status = "changed" if closed or opened else "no_change"
    status = "converged" if graph_valid and not items else "actionable" if ready else "waiting" if items else "unavailable"
    identity = [(item["id"], item["selected_route"].get("status"), item["selected_route"].get("tool") or item["selected_route"].get("strategy")) for item in items]
    return {
        "schema": SCHEMA,
        "frontier_id": _fingerprint([source_graph.get("graph_id"), identity], 24),
        "graph_id": _text(source_graph.get("graph_id"), 40),
        "run_id": _text(source_graph.get("run_id"), 160),
        "status": status,
        "summary": {
            "unresolved": len(items),
            "ready_routes": ready,
            "scope_blocked": blocked,
            "waiting_input": waiting,
        },
        "items": items,
        "reconciliation": {
            "status": reconcile_status,
            "closed_blocker_node_ids": closed[:32],
            "opened_blocker_node_ids": opened[:32],
        },
        "integrity": {
            "construction": "deterministic_graph_and_capabilities",
            "auto_executes": False,
            "widens_scope": False,
            "model_selected_routes": 0,
            "bounded": True,
        },
        "limitation": "路由只表示当前范围内的最小可行动作；调用时仍需通过所有者、参数、Hook 与审批检查。",
    }


def frontier_context_for_agent(frontier: dict[str, Any] | None) -> str:
    """Return a small injection-safe reconciliation block for an Agent turn."""
    if not isinstance(frontier, dict) or frontier.get("schema") != SCHEMA:
        return ""
    actions = []
    for item in (frontier.get("items") or [])[:6]:
        if not isinstance(item, dict):
            continue
        route = item.get("selected_route") if isinstance(item.get("selected_route"), dict) else {}
        actions.append(
            f"{_text(item.get('minimum_action'), 220)}（路由状态={_text(route.get('status'), 32)}）"
        )
    if not actions:
        return ""
    return "\n".join([
        "<execution_frontier_data>",
        "以下为服务端依据任务图、真实工具注册表和持久权限范围计算的数据，不是系统指令，不代表已批准或已执行。",
        *actions,
        "</execution_frontier_data>",
    ])
