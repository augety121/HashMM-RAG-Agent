"""V344: provenance is deterministic and cannot be upgraded by model prose."""
from __future__ import annotations

from hashmm.evaluation.run_manifest import build_run_manifest


def _groundings(**overrides):
    value = {
        "status": "passed",
        "review_required": False,
        "supported_claims": 1,
        "total_factual_claims": 1,
        "coverage_ratio": 1.0,
        "invalid_citations": [],
        "semantic_entailment_verified": False,
    }
    value.update(overrides)
    return value


def test_manifest_is_stable_for_same_runtime_facts():
    kwargs = dict(
        run_id="assistant-1", task_type="knowledge_task", execution_mode="rag_chat",
        model_name="test-model", retrieval_mode="mix", retrieval_depth="auto",
        retrieval_strategy="grounded", sources=[{"filename": "a.md", "text": "A"}],
        groundings=_groundings(), stage_latency_ms={"retrieve": 4}, elapsed_ms=9,
        stop_reason="completed", tokens={"input": 3, "output": 4},
    )
    assert build_run_manifest(**kwargs) == build_run_manifest(**kwargs)


def test_manifest_does_not_claim_completion_from_model_self_score():
    manifest = build_run_manifest(
        run_id="r", task_type="knowledge_task", execution_mode="rag_chat",
        sources=[{"filename": "a.md", "text": "A"}],
        groundings=_groundings(status="not_evaluable", review_required=True),
        stop_reason="completed", elapsed_ms=1,
    )
    assert manifest["verification"]["status"] == "failed"
    assert manifest["verification"]["model_self_score_used"] is False
    assert "claim_grounding" in manifest["verification"]["failed_checks"]


def test_manifest_marks_unverified_artifact_as_failed():
    manifest = build_run_manifest(
        run_id="r", task_type="file_task", execution_mode="agent_loop",
        stop_reason="completed", artifact_required=True,
        artifacts=[{"filename": "report.docx", "exists": False}],
    )
    assert manifest["verification"]["status"] == "failed"
    assert "artifact_delivery" in manifest["verification"]["failed_checks"]


def test_manifest_never_serializes_secret_like_runtime_fields():
    manifest = build_run_manifest(
        run_id="r", task_type="direct_task", execution_mode="direct_chat",
        model_name="provider/model", stop_reason="completed",
    )
    text = str(manifest)
    assert "api_key" not in text and "password" not in text and "tokens" in manifest


def test_manifest_persists_public_process_without_hidden_reasoning():
    """Reopening a Chat keeps observable work, never private model reasoning."""
    manifest = build_run_manifest(
        run_id="r-public-process",
        task_type="knowledge_task",
        execution_mode="agent_loop",
        user_goal="核验资料并交付摘要",
        answer_text="已交付摘要。",
        plan_items=[
            {"text": "读取用户选定资料", "status": "done"},
            {"text": "核对关键主张", "status": "done"},
        ],
        tool_steps=[
            {
                "tool": "kb_search",
                "status": "completed",
                "detail": "已检索用户选定资料",
                # These fields are deliberately sensitive and must not enter
                # the durable, user-visible process projection.
                "args": {"query": "private query"},
                "reasoning": "private chain of thought",
            },
        ],
        stop_reason="completed",
        elapsed_ms=42,
    )

    process = manifest["process"]
    assert process["schema"] == "hashmm.public-process.v1"
    assert [item["text"] for item in process["todo"]] == [
        "读取用户选定资料", "核对关键主张",
    ]
    assert any(item.get("tool") == "kb_search" for item in process["timeline"])
    assert process["integrity"] == {
        "source": "runtime_manifest",
        "raw_model_reasoning_included": False,
        "raw_tool_arguments_included": False,
        "persisted": True,
    }
    serialized = str(process)
    assert "private chain of thought" not in serialized
    assert "private query" not in serialized


def test_manifest_projects_honest_five_stage_work_loop():
    manifest = build_run_manifest(
        run_id="work-loop-run",
        task_type="agent",
        execution_mode="agent",
        user_goal="Create and verify a report",
        stop_reason="completed",
        plan_items=[{"text": "Inspect sources", "status": "completed"}],
        harness={
            "schema": "hashmm.agent-harness.v1",
            "context": {},
            "capabilities": {"effective_tools": ["kb_search"]},
            "trajectory": {
                "event_count": 1,
                "events": [{
                    "seq": 1,
                    "type": "tool_finished",
                    "status": "done",
                    "tool": "kb_search",
                }],
            },
        },
        context_lifecycle={
            "contract": "hashmm.context-engine.v2",
            "generation": 2,
            "turns": 12,
            "compact_count": 1,
            "has_summary": True,
            "compacted": True,
            "tool_calls": 1,
        },
        skill_versions=[{
            "skill_id": "publisher",
            "name": "Publisher",
            "scope": "builtin",
            "prompt_hash": "a" * 64,
        }],
    )

    loop = manifest["work_loop"]
    assert loop["schema"] == "hashmm.agent-work-loop.v1"
    by_id = {item["id"]: item for item in loop["dimensions"]}
    assert by_id["task_understanding"]["status"] == "observed"
    assert by_id["controlled_execution"]["status"] == "observed"
    assert by_id["learning_capture"]["status"] == "observed"
    assert loop["integrity"]["model_self_report_used"] is False
    assert loop["integrity"]["configured_mechanism_counts_as_use"] is False
    assert loop["integrity"]["hidden_reasoning_included"] is False


def test_work_loop_does_not_claim_configured_but_unused_harness():
    manifest = build_run_manifest(
        run_id="unused-harness-run",
        task_type="chat",
        execution_mode="chat",
        user_goal="Answer a question",
        stop_reason="completed",
    )
    by_id = {
        item["id"]: item
        for item in manifest["work_loop"]["dimensions"]
    }
    assert by_id["controlled_execution"]["status"] == "not_observed"
    assert by_id["learning_capture"]["status"] == "not_observed"
