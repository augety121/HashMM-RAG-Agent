"""V350: strict, shared tool-call judge and production tool health metrics."""
from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.unit


SCHEMAS = {
    "search": {
        "required": ["query"],
        "properties": {"query": {}, "limit": {}},
        "additionalProperties": False,
    },
    "read": {
        "required": ["path"],
        "properties": {"path": {}},
        "additionalProperties": False,
    },
}


def test_strict_tool_judge_accepts_contract_without_executing_side_effects():
    from hashmm.evaluation.tool_call_eval import evaluate_tool_calls

    result = evaluate_tool_calls(
        [{"name": "search", "args": {"query": {"contains": "HashMM"}}}],
        [{"name": "search", "args": {"query": "HashMM 发布说明", "limit": 5}}],
        tool_schemas=SCHEMAS,
    )
    assert result["passed"] is True
    assert result["executed"] is False
    assert all(result["dimensions"].values())


def test_strict_tool_judge_locates_missing_extra_and_wrong_value():
    from hashmm.evaluation.tool_call_eval import evaluate_tool_calls

    result = evaluate_tool_calls(
        [{"name": "search", "args": {"query": {"contains": "HashMM"}}}],
        [{"name": "search", "args": {"limit": 5, "invented": "x"}}],
        tool_schemas=SCHEMAS,
    )
    codes = result["failure_counts"]
    assert result["passed"] is False
    assert codes["missing_required_arg"] >= 1
    assert codes["extra_arg"] == 1


def test_strict_tool_judge_catches_no_tool_hallucination_and_order():
    from hashmm.evaluation.tool_call_eval import evaluate_tool_calls

    restraint = evaluate_tool_calls([], [{"name": "search", "args": {"query": "hi"}}],
                                    tool_schemas=SCHEMAS)
    assert restraint["failure_counts"] == {"no_tool_violation": 1}
    assert restraint["score"] == 0.0 and restraint["dimensions"]["restraint"] is False
    ordered = evaluate_tool_calls(
        [{"name": "search", "args": {"query": "q"}},
         {"name": "read", "args": {"path": "a.md"}}],
        [{"name": "read", "args": {"path": "a.md"}},
         {"name": "search", "args": {"query": "q"}}],
        tool_schemas=SCHEMAS,
    )
    assert ordered["passed"] is False
    assert ordered["failure_counts"]["wrong_order"] == 1


def test_agent_trace_argument_matching_rejects_invented_keys():
    from hashmm.evaluation.agent_eval import tool_call_accuracy

    expected = [{"name": "search", "args": {"query": "HashMM"}}]
    clean = tool_call_accuracy(expected, [{"name": "search", "args": {"query": "HashMM"}}], match_args=True)
    poisoned = tool_call_accuracy(
        expected,
        [{"name": "search", "args": {"query": "HashMM", "invented": "x"}}],
        match_args=True,
    )
    assert clean["f1"] == 1.0
    assert poisoned["f1"] == 0.0


def test_runtime_tool_metrics_separate_execution_health_from_correctness():
    from hashmm.api.quality_monitor import runtime_tool_metrics

    manifests = [
        {"schema": "hashmm.run-manifest.v2", "verification": {"checks": [
            {"id": "tool_execution", "status": "passed", "evidence": {"total": 2, "failed": 0}},
        ]}},
        {"schema": "hashmm.run-manifest.v2", "verification": {"checks": [
            {"id": "tool_execution", "status": "failed", "evidence": {"total": 3, "failed": 1}},
        ]}},
        {"schema": "hashmm.run-manifest.v2", "verification": {"checks": [
            {"id": "tool_execution", "status": "not_evaluable"},
        ]}},
        "bad-json",
    ]
    metrics = runtime_tool_metrics(manifests, wrong_tool_feedback=2)
    assert metrics["run_manifests"] == 3
    assert metrics["evaluable_runs"] == 2
    assert metrics["passed_runs"] == 1 and metrics["failed_runs"] == 1
    assert metrics["tool_calls"] == 5 and metrics["failed_tool_calls"] == 1
    assert metrics["execution_success_rate"] == 0.8
    assert metrics["wrong_tool_feedback"] == 2
    assert metrics["measures_correct_selection"] is False


def test_quality_dashboard_reads_authoritative_run_manifests(tmp_db, monkeypatch):
    from pathlib import Path
    from hashmm.api import database as db
    from hashmm.api import quality_monitor

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    monkeypatch.setattr(quality_monitor, "_SAMPLE_RATE", 1)
    user = db.create_user("tool-quality", "pw12345678", "工具质量", "user")
    db.create_conversation("tool-quality-conv", user["id"], "工具质量")
    manifest = {"schema": "hashmm.run-manifest.v2", "verification": {"checks": [
        {"id": "tool_execution", "status": "failed", "evidence": {"total": 2, "failed": 1}},
    ]}}
    db.create_message("tool-quality-conv", "assistant", "done", run_manifest=manifest)
    out = quality_monitor.dashboard(days=7)
    assert out["tool_quality"]["run_manifests"] == 1
    assert out["tool_quality"]["failed_tool_calls"] == 1
    assert out["tool_quality"]["execution_success_rate"] == 0.5
