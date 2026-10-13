from __future__ import annotations

from hashmm.agent.task_method import build_task_contract, clean_goal, method_prompt
from hashmm.agent.execution_receipt import build_execution_receipt
from hashmm.evaluation.run_manifest import build_run_manifest


def _manifest(**overrides):
    receipt = build_execution_receipt(
        run_id="run-v346",
        call_id="call-read",
        tool_name="read_file",
        arguments={"path": "README.md"},
        result={"status": "ok", "bytes": 24},
        started_at=1,
        finished_at=1.01,
        execution_scope={"scope_id": "scope-v346"},
        side_effect={"class": "observe", "external": False, "reversible": True},
        idempotency_key="run-v346:call-read",
    )
    values = {
        "run_id": "run-v346",
        "task_type": "code_task",
        "execution_mode": "agent_loop",
        "user_goal": "读取真实代码，修复问题并运行测试",
        "answer_text": "已完成修复，并附上验证结果。",
        "stop_reason": "completed",
        "plan_items": [
            {"text": "检查代码", "status": "completed"},
            {"text": "运行测试", "status": "completed"},
        ],
        "tool_steps": [{
            "tool": "read_file",
            "status": "success",
            "call_id": "call-read",
            "receipt": receipt,
        }],
        "execution_receipts": [receipt],
    }
    values.update(overrides)
    return build_run_manifest(**values)


def test_contract_preserves_user_goal_without_invented_summary():
    raw = "  请读取   实际文件，再完成任务  "
    contract = build_task_contract(
        user_goal=raw,
        task_type="code_task",
        execution_mode="agent_loop",
    )
    assert contract["goal"] == "请读取 实际文件，再完成任务"
    assert contract["goal_source"] == "user_message"
    assert contract["evidence_policy"] == "runtime_facts_only"
    assert clean_goal("甲" * 700).endswith("…")


def test_complex_prompt_requires_evidence_not_model_self_report():
    prompt = method_prompt("请全面完善项目、修复问题、测试并打包", "code_task")
    assert "真实状态" in prompt
    assert "模型自述" in prompt
    assert "重跑同一验证" in prompt


def test_completed_plan_and_successful_tool_produce_checkable_handoff():
    manifest = _manifest()
    assert manifest["schema"] == "hashmm.run-manifest.v2"
    assert manifest["task_contract"]["run_id"] == "run-v346"
    assert manifest["verification"]["model_self_score_used"] is False
    assert manifest["handoff"]["failed_checks"] == []
    assert manifest["handoff"]["status"] == "checks_passed"
    assert manifest["completion_gate"]["can_claim_complete"] is True
    # A code task which did not use retrieval must not acquire an unrelated
    # grounding obligation after the run.
    assert "claim_grounding" not in manifest["handoff"]["not_evaluable_checks"]


def test_required_plan_without_trace_is_not_reported_complete():
    manifest = _manifest(plan_items=[])
    assert "plan_closure" in manifest["verification"]["failed_checks"]
    assert manifest["handoff"]["status"] == "needs_attention"


def test_failed_tool_or_missing_artifact_cannot_be_upgraded_by_answer_prose():
    manifest = _manifest(
        answer_text="所有工具都成功，文件已经生成。",
        tool_steps=[{"tool": "write_file", "status": "failed"}],
        artifact_required=True,
        artifacts=[{"filename": "report.docx", "exists": False}],
    )
    assert set(manifest["verification"]["failed_checks"]) >= {
        "tool_execution", "artifact_delivery",
    }
    assert manifest["verification"]["status"] == "failed"


def test_non_terminal_run_is_blocked_even_with_nonempty_answer():
    manifest = _manifest(stop_reason="stopped")
    assert manifest["handoff"]["status"] == "blocked"
    assert manifest["verification"]["status"] == "failed"
