"""Evidence-gated completion shared by Chat, loops and agent teams.

The gate answers one narrow question: which *observable* obligations allow the
runtime to say a task is complete?  It never asks a model to judge its own
work, widens execution scope, or treats a fluent final answer as proof.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable


SCHEMA = "hashmm.completion-gate.v1"
_TERMINAL = {"completed", "complete", "done", "acceptance_reached", "max_runs_reached"}
_FAIL = {"failed", "fail", "error", "denied", "blocked", "stopped", "cancelled"}
_OPEN = {"not_evaluable", "pending", "running", "waiting", "waiting_input", "waiting_approval", "unknown", ""}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(0, limit)]


def _fingerprint(value: Any, size: int = 20) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def _check_map(verification: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in list(verification.get("checks") or [])[:80]:
        if not isinstance(item, dict):
            continue
        check_id = _text(item.get("id") or item.get("check_id"), 100)
        if check_id and check_id not in result:
            result[check_id] = item
    return result


def _trajectory(tool_steps: Iterable[dict] | None, orchestration: dict | None) -> dict[str, Any]:
    states: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    for step in list(tool_steps or [])[-120:]:
        if not isinstance(step, dict):
            continue
        name = _text(step.get("tool") or step.get("name"), 120)
        if not name:
            continue
        state = _text(step.get("status"), 32).lower() or "unknown"
        states[state] += 1
        args = step.get("args") or step.get("arguments") or {}
        calls[f"{name}:{_fingerprint(args, 12)}"] += 1
    members = [item for item in list((orchestration or {}).get("members") or [])[:32] if isinstance(item, dict)]
    agent_states = Counter(_text(item.get("status"), 32).lower() or "unknown" for item in members)
    repeats = sorted((count for count in calls.values() if count > 1), reverse=True)
    return {
        "tool_calls": sum(states.values()),
        "unique_tool_calls": len(calls),
        "failed_tool_calls": sum(count for state, count in states.items() if state in _FAIL),
        "open_tool_calls": sum(count for state, count in states.items() if state in _OPEN),
        "repeated_call_groups": len(repeats),
        "max_identical_repeats": repeats[0] if repeats else 0,
        "agents": len(members),
        "failed_agents": sum(count for state, count in agent_states.items() if state in _FAIL),
        "open_agents": sum(count for state, count in agent_states.items() if state in _OPEN),
    }


def build_completion_gate(
    *,
    task_contract: dict | None,
    verification: dict | None,
    evidence_graph: dict | None = None,
    execution_frontier: dict | None = None,
    termination_reason: str = "completed",
    tool_steps: Iterable[dict] | None = None,
    orchestration: dict | None = None,
    causal_work_graph: dict | None = None,
    previous: dict | None = None,
) -> dict[str, Any]:
    """Build a deterministic completion decision from persisted run facts."""
    contract = dict(task_contract or {})
    verify = dict(verification or {})
    graph = dict(evidence_graph or {})
    frontier = dict(execution_frontier or {})
    causal = dict(causal_work_graph or {})
    checks = _check_map(verify)
    criteria: list[dict[str, Any]] = []
    missing: list[str] = []
    failed: list[str] = []
    review: list[str] = []
    passed: list[str] = []

    for item in list(contract.get("success_criteria") or [])[:40]:
        if not isinstance(item, dict):
            continue
        check_id = _text(item.get("check_id"), 100)
        if not check_id:
            continue
        required = bool(item.get("required", True))
        source = _text(item.get("source"), 32) or "runtime"
        observed = checks.get(check_id)
        status = _text((observed or {}).get("status"), 32).lower() or "not_evaluable"
        authority = _text((observed or {}).get("authority"), 40) or "runtime_fact"
        # An LLM judge or deterministic proxy cannot approve on behalf of the user.
        if source == "user" and authority != "actual_user_confirmation":
            status = "not_evaluable"
            authority = "user_confirmation_required"
        row = {
            "check_id": check_id,
            "label": _text(item.get("label"), 240),
            "required": required,
            "source": source,
            "status": status,
            "authority": authority,
            "detail": _text((observed or {}).get("detail") or "没有对应的运行时检查记录", 300),
        }
        criteria.append(row)
        if not required:
            continue
        if observed is None:
            missing.append(check_id)
        elif status in _FAIL:
            failed.append(check_id)
        elif status == "passed":
            passed.append(check_id)
        else:
            review.append(check_id)

    terminal = _text(termination_reason, 80).lower() in _TERMINAL
    graph_blockers = int((graph.get("summary") or {}).get("blockers") or 0)
    frontier_summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}
    ready_routes = int(frontier_summary.get("ready_routes") or 0)
    scope_blocked = int(frontier_summary.get("scope_blocked") or 0)
    waiting_input = int(frontier_summary.get("waiting_input") or 0)
    causal_status = _text(causal.get("status"), 32).lower()
    causal_summary = causal.get("summary") if isinstance(causal.get("summary"), dict) else {}
    stale_nodes = int(causal_summary.get("stale_nodes") or 0)
    invalid_receipts = int(causal_summary.get("invalid_receipts") or 0)
    trajectory = _trajectory(tool_steps, orchestration)

    failure_modes: list[dict[str, str]] = []
    def add_failure(code: str, severity: str, detail: str) -> None:
        failure_modes.append({"code": code, "severity": severity, "detail": detail})

    if not terminal:
        add_failure("non_terminal", "blocking", "运行没有以完成状态结束")
    if failed:
        add_failure("required_criterion_failed", "blocking", f"{len(failed)} 个必需完成条件未通过")
    if missing:
        add_failure("missing_required_check", "blocking", f"{len(missing)} 个必需条件没有检查记录")
    if review:
        add_failure("required_criterion_unverified", "review", f"{len(review)} 个必需条件仍需独立或人工复核")
    if graph_blockers:
        add_failure("evidence_graph_blocked", "blocking", f"任务证据图仍有 {graph_blockers} 个阻塞")
    if invalid_receipts or causal_status == "invalid":
        add_failure(
            "invalid_execution_receipt", "blocking",
            f"因果工作图发现 {max(1, invalid_receipts)} 个无效执行回执",
        )
    if stale_nodes or causal_status == "stale":
        add_failure(
            "stale_dependency", "blocking",
            f"来源变化使 {max(1, stale_nodes)} 个下游节点失效，必须定向重验",
        )
    if ready_routes:
        add_failure("unfinished_action_available", "blocking", f"仍有 {ready_routes} 条在当前权限内可继续的路径")
    if scope_blocked:
        add_failure("scope_blocked", "review", f"{scope_blocked} 条路径超出当前授权范围")
    if waiting_input:
        add_failure("waiting_input", "review", f"{waiting_input} 条路径等待用户输入")
    if trajectory["failed_tool_calls"]:
        add_failure("tool_failure", "blocking", f"轨迹中有 {trajectory['failed_tool_calls']} 个失败工具调用")
    if trajectory["open_tool_calls"]:
        add_failure("open_tool_call", "blocking", f"轨迹中有 {trajectory['open_tool_calls']} 个未结束工具调用")
    if trajectory["failed_agents"]:
        add_failure("agent_failure", "blocking", f"有 {trajectory['failed_agents']} 个协作 Agent 未成功交付")
    if trajectory["open_agents"]:
        add_failure("open_agent", "blocking", f"有 {trajectory['open_agents']} 个协作 Agent 尚未结束")
    if trajectory["max_identical_repeats"] >= 3:
        add_failure("repeated_tool_call", "warning", "相同工具与参数重复三次以上，需要检查循环或无效重试")

    blocking = any(item["severity"] == "blocking" for item in failure_modes)
    needs_review = any(item["severity"] == "review" for item in failure_modes)
    contract_valid = contract.get("schema") == "hashmm.task-contract.v1" and bool(criteria)
    if not contract_valid:
        status = "unavailable"
    elif blocking:
        status = "blocked" if (not terminal or scope_blocked) else "incomplete"
    elif needs_review:
        status = "delivered_with_limits"
    else:
        status = "verified"
    can_claim_complete = status == "verified"

    next_action = "保留当前完成证据；输入变化时只重跑受影响的检查。"
    for item in list(frontier.get("items") or [])[:20]:
        if isinstance(item, dict) and _text(item.get("minimum_action"), 300):
            next_action = _text(item.get("minimum_action"), 300)
            break
    if status == "delivered_with_limits" and review:
        next_action = "复核未能自动验证的必需条件：" + "、".join(review[:6])
    elif status == "unavailable":
        next_action = "先建立任务契约和对应的运行时检查，再判断是否完成。"

    identity = {
        "contract": contract.get("run_id") or contract.get("goal"),
        "criteria": [(item["check_id"], item["status"], item["authority"]) for item in criteria],
        "terminal": terminal,
        "graph": graph.get("graph_id"),
        "frontier": frontier.get("frontier_id"),
        "causal_graph": causal.get("graph_id"),
        "trajectory": trajectory,
    }
    previous_status = _text((previous or {}).get("status"), 40)
    return {
        "schema": SCHEMA,
        "gate_id": _fingerprint(identity, 24),
        "run_id": _text(contract.get("run_id") or graph.get("run_id"), 160),
        "status": status,
        "can_claim_complete": can_claim_complete,
        "can_claim_verified": can_claim_complete,
        "decision": {
            "method": "deterministic_required_criteria_and_trajectory",
            "terminal": terminal,
            "termination_reason": _text(termination_reason, 120),
            "previous_status": previous_status,
            "changed": bool(previous_status and previous_status != status),
        },
        "summary": {
            "required": sum(1 for item in criteria if item["required"]),
            "passed": len(passed),
            "failed": len(failed),
            "review": len(review),
            "missing": len(missing),
            "graph_blockers": graph_blockers,
            "ready_routes": ready_routes,
            "scope_blocked": scope_blocked,
            "stale_nodes": stale_nodes,
            "invalid_receipts": invalid_receipts,
        },
        "criteria": criteria,
        "failure_modes": failure_modes,
        "trajectory": trajectory,
        "next_action": next_action,
        "integrity": {
            "model_self_report_is_evidence": False,
            "model_judge_can_accept_for_user": False,
            "auto_executes": False,
            "widens_scope": False,
            "bounded": True,
            "causal_freshness_enforced": bool(causal),
        },
        "limitation": "门禁只证明可观察的契约条件与执行轨迹已闭环，不证明现实世界中的结论必然正确。",
    }
