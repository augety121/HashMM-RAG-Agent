"""V371 graph-guided execution frontier regression tests."""
from __future__ import annotations

import json

import pytest

from hashmm.agent.execution_frontier import (
    SCHEMA,
    build_execution_frontier,
    frontier_context_for_agent,
)
from hashmm.api.quality_monitor import runtime_frontier_metrics
from hashmm.evaluation.run_manifest import build_run_manifest


pytestmark = pytest.mark.unit


def _graph(kind: str, label: str, status: str = "missing") -> dict:
    node_id = f"{kind}:blocked"
    return {
        "schema": "hashmm.task-evidence-graph.v1",
        "graph_id": f"graph-{kind}",
        "run_id": "run-v371",
        "status": "blocked",
        "nodes": [
            {"id": "goal:root", "kind": "goal", "label": "Finish the task", "status": "active"},
            {"id": node_id, "kind": kind, "label": label, "status": status},
        ],
        "edges": [{"source": node_id, "target": "goal:root", "relation": "blocks"}],
        "blockers": [{"node_id": node_id, "reason": "runtime evidence is incomplete"}],
    }


def _scope(*tools: str, network: str = "deny") -> dict:
    return {
        "schema": "hashmm.execution-scope.v1",
        "scope_id": "scope-v371",
        "allowed_tools": list(tools),
        "network": {"mode": network, "origins": []},
    }


def test_local_evidence_route_is_preferred_and_never_auto_executes():
    frontier = build_execution_frontier(
        _graph("claim", "Revenue claim lacks a source"),
        execution_scope=_scope("kb_search", "web_search", network="allow"),
        available_tools=["kb_search", "web_search"],
    )

    item = frontier["items"][0]
    assert frontier["schema"] == SCHEMA
    assert item["selected_route"]["tool"] == "kb_search"
    assert set(item["working_set"]["node_ids"]) == {"claim:blocked", "goal:root"}
    assert frontier["integrity"]["auto_executes"] is False
    assert frontier["integrity"]["widens_scope"] is False


def test_network_and_tool_scope_gaps_remain_visible_behind_safe_fallback():
    frontier = build_execution_frontier(
        _graph("claim", "Needs an external source"),
        execution_scope=_scope(network="deny"),
        available_tools=["web_search"],
    )

    item = frontier["items"][0]
    assert item["selected_route"]["status"] == "waiting_input"
    assert frontier["summary"]["ready_routes"] == 0
    assert frontier["summary"]["scope_blocked"] == 1
    assert frontier["summary"]["waiting_input"] == 1


def test_delivery_and_failed_agent_receive_capability_aware_routes():
    delivery = build_execution_frontier(
        _graph("artifact", "report.docx"),
        execution_scope=_scope("create_document"),
        available_tools=["create_document", "create_file"],
    )
    delegation = build_execution_frontier(
        _graph("agent", "Research specialist", "failed"),
        execution_scope=_scope(),
        available_tools=["spawn_worker"],
    )

    assert delivery["items"][0]["selected_route"]["tool"] == "create_document"
    assert delegation["items"][0]["selected_route"]["strategy"] == "main_agent_takeover"
    assert delegation["summary"]["scope_blocked"] == 1


def test_reconciliation_reports_closed_blockers_as_converged():
    previous = build_execution_frontier(
        _graph("artifact", "result.md"),
        execution_scope=_scope("create_file"),
        available_tools=["create_file"],
    )
    complete_graph = {
        "schema": "hashmm.task-evidence-graph.v1",
        "graph_id": "graph-complete",
        "run_id": "run-v371",
        "status": "ready",
        "nodes": [],
        "edges": [],
        "blockers": [],
    }
    current = build_execution_frontier(complete_graph, previous=previous)

    assert current["status"] == "converged"
    assert current["reconciliation"]["status"] == "converged"
    assert current["reconciliation"]["closed_blocker_node_ids"] == ["artifact:blocked"]


def test_manifest_exposes_frontier_and_handoff_uses_minimum_action():
    manifest = build_run_manifest(
        run_id="manifest-v371",
        task_type="document_task",
        execution_mode="agent_loop",
        user_goal="Deliver a report",
        answer_text="The report could not be delivered.",
        stop_reason="completed",
        artifact_required=True,
        artifacts=[{"filename": "report.docx", "exists": False}],
        execution_scope=_scope("create_document"),
    )

    assert manifest["execution_frontier"]["schema"] == SCHEMA
    assert manifest["execution_frontier"]["summary"]["unresolved"] >= 1
    assert manifest["handoff"]["next_action"] == manifest["execution_frontier"]["items"][0]["minimum_action"]


def test_agent_context_is_bounded_data_not_authority_or_execution_evidence():
    frontier = build_execution_frontier(
        _graph("tool", "write_file", "failed"),
        execution_scope=_scope("write_file"),
        available_tools=["write_file"],
    )
    context = frontier_context_for_agent(frontier)
    encoded = json.dumps(frontier, ensure_ascii=False)

    assert context.startswith("<execution_frontier_data>")
    assert context.endswith("</execution_frontier_data>")
    assert "不代表已批准或已执行" in context
    assert "owner_id" not in encoded and "arguments" not in encoded
    assert len(context) < 2_000


def test_quality_metrics_measure_route_coverage_without_claiming_correctness():
    frontier = build_execution_frontier(
        _graph("artifact", "report.pdf"),
        execution_scope=_scope("create_document"),
        available_tools=["create_document"],
    )
    metrics = runtime_frontier_metrics([
        {"execution_frontier": frontier},
        {"schema": "hashmm.run-manifest.v2"},
    ])

    assert metrics["frontier_runs"] == 1
    assert metrics["missing_frontier_runs"] == 1
    assert metrics["trace_coverage"] == 0.5
    assert metrics["route_coverage"] == 1.0
    assert metrics["integrity_violations"] == 0
    assert metrics["ready_route_is_execution_or_approval"] is False


def test_durable_loop_reconciles_frontier_and_feeds_only_ready_minimum_action():
    from hashmm.agent.loop_engine import _graph_feedback, _loop_execution_frontier

    graph = _graph("artifact", "report.md")
    loop = {"execution_scope": _scope("create_file")}
    frontier = _loop_execution_frontier(loop, graph)
    feedback = _graph_feedback(graph, frontier)

    assert frontier["schema"] == SCHEMA
    assert frontier["items"][0]["selected_route"]["status"] == "ready"
    assert frontier["items"][0]["selected_route"]["tool"] == "create_file"
    assert frontier["items"][0]["minimum_action"] in feedback
