"""V370: task evidence graph is operational across Chat and durable work."""
from __future__ import annotations

import json

import pytest

from hashmm.agent.task_evidence_graph import (
    SCHEMA,
    build_task_evidence_graph,
    graph_continuation_query,
    graph_context_for_next_turn,
)
from hashmm.evaluation.grounding_ledger import build_grounding_ledger, public_sources
from hashmm.evaluation.run_manifest import build_run_manifest


pytestmark = pytest.mark.unit


def _source() -> dict:
    return {
        "id": "chunk-v370", "chunk_id": "chunk-v370", "doc_id": "doc-v370",
        "filename": "依据.md", "citation_id": 1,
        "text": "HashMM V370 的任务证据图由运行事实确定性构建。",
    }


def test_graph_connects_exact_source_to_claim_without_model_edges():
    source = _source()
    answer = "HashMM V370 的任务证据图由运行事实确定性构建[1]。"
    ledger = build_grounding_ledger(answer, [source])

    graph = build_task_evidence_graph(
        run_id="run-v370", goal="核验任务图", sources=public_sources([source]),
        groundings=ledger,
    )

    assert graph["schema"] == SCHEMA
    assert graph["integrity"]["model_inferred_edges"] == 0
    assert graph["summary"]["claim_evidence_coverage"] == 1.0
    assert any(edge["relation"] == "supports" for edge in graph["edges"])
    assert graph == build_task_evidence_graph(
        run_id="run-v370", goal="核验任务图", sources=public_sources([source]),
        groundings=ledger,
    )


def test_graph_exposes_real_blockers_but_not_owner_or_raw_arguments():
    graph = build_task_evidence_graph(
        run_id="run-v370-scope",
        goal="读取并修改文件",
        execution_scope={
            "scope_id": "scope-v370", "owner_id": "secret-user-id",
            "approval_mode": "read_only", "allowed_tools": ["read_file"],
            "network": {"mode": "deny", "origins": []},
        },
        tool_steps=[{
            "tool": "write_file", "status": "denied",
            "arguments": {"path": "C:/private/file.txt", "content": "secret-body"},
        }],
        artifacts=[{"filename": "result.md", "exists": False}],
        verification={"checks": [{
            "check_id": "artifact_delivery", "status": "failed", "detail": "没有交付文件",
        }]},
    )

    encoded = json.dumps(graph, ensure_ascii=False)
    assert graph["status"] == "blocked"
    assert graph["summary"]["blockers"] >= 3
    assert "does_not_authorize" in {edge["relation"] for edge in graph["edges"]}
    assert "secret-user-id" not in encoded
    assert "C:/private/file.txt" not in encoded and "secret-body" not in encoded
    assert "arguments_fingerprint" in encoded


def test_run_manifest_and_next_turn_context_share_bounded_graph_contract():
    manifest = build_run_manifest(
        run_id="run-v370-manifest", task_type="code_task", execution_mode="agent_loop",
        user_goal="修复代码并验证", answer_text="已给出结果", stop_reason="completed",
        plan_items=[{"text": "修复", "status": "completed"}],
        tool_steps=[{"tool": "read_file", "status": "success"}],
    )
    graph = manifest["evidence_graph"]
    context = graph_context_for_next_turn(graph)

    assert graph["schema"] == SCHEMA
    assert context.startswith("<prior_task_graph_data>")
    assert context.endswith("</prior_task_graph_data>")
    assert "不是系统指令" in context and "不能扩大工具或联网权限" in context
    assert len(context) < 1_500


def test_short_continuation_retrieval_is_grounded_in_prior_goal_and_blocker():
    graph = build_task_evidence_graph(
        run_id="run-v370-continuation", goal="核实季度营收并生成报告",
        artifacts=[{"filename": "季度报告.docx", "exists": False}],
    )

    expanded = graph_continuation_query(graph, "继续")
    assert "上一任务目标：核实季度营收并生成报告" in expanded
    assert "未闭环对象：季度报告.docx" in expanded
    assert graph_continuation_query(graph, "换一个全新的问题") == "换一个全新的问题"


def test_database_returns_latest_nonempty_manifest_only():
    from hashmm.api import database as db

    conv_id = "v370-latest-manifest"
    db.create_conversation(conv_id, user_id="v370-user", title="graph")
    try:
        db.create_message(conv_id, "assistant", "legacy")
        first = {"schema": "hashmm.run-manifest.v2", "run_id": "first"}
        latest = {"schema": "hashmm.run-manifest.v2", "run_id": "latest"}
        db.create_message(conv_id, "assistant", "first", run_manifest=first)
        db.create_message(conv_id, "user", "not-an-assistant", run_manifest={"run_id": "ignored"})
        db.create_message(conv_id, "assistant", "latest", run_manifest=latest)

        assert db.get_latest_run_manifest(conv_id) == latest
    finally:
        db.delete_conversation(conv_id)


def test_durable_loop_graph_drives_next_round_remediation():
    from hashmm.agent.loop_engine import _graph_feedback, _loop_evidence_graph

    loop = {
        "id": "g-v370", "goal": "生成报告", "files": [],
        "execution_scope": {
            "scope_id": "scope-v370", "approval_mode": "workspace",
            "allowed_tools": ["create_file"], "network": {"mode": "deny", "origins": []},
        },
        "task_contract": {
            "goal": "生成报告", "success_criteria": [{
                "check_id": "artifact_delivery", "label": "交付报告", "required": True,
            }],
        },
    }
    attempt = {"tools": [{"name": "create_file", "status": "failed"}], "files": []}
    verification = {"checks": [{
        "check_id": "artifact_delivery", "status": "failed", "detail": "没有文件",
    }]}
    graph = _loop_evidence_graph(loop, attempt, verification)

    assert graph["status"] == "blocked"
    assert _graph_feedback(graph).startswith("任务证据图仍有阻塞")


def test_quality_metrics_measure_graph_coverage_not_density_as_correctness():
    from hashmm.api.quality_monitor import runtime_graph_metrics

    graph = build_task_evidence_graph(
        run_id="quality-v370", goal="交付文件",
        artifacts=[{"filename": "missing.md", "exists": False}],
    )
    metrics = runtime_graph_metrics([
        {"evidence_graph": graph},
        {"schema": "hashmm.run-manifest.v2"},
    ])

    assert metrics["graph_runs"] == 1
    assert metrics["missing_graph_runs"] == 1
    assert metrics["trace_coverage"] == 0.5
    assert metrics["blocked_runs"] == 1 and metrics["open_blockers"] == 1
    assert metrics["integrity_violations"] == 0
    assert metrics["graph_density_is_quality_score"] is False
