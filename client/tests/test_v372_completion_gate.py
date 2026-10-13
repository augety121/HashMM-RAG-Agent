import json
import threading
import time

import pytest

from hashmm.agent.completion_gate import build_completion_gate
from hashmm.agent import loop_engine
from hashmm.agent.task_method import build_task_contract
from hashmm.api.quality_monitor import runtime_completion_gate_metrics


def _graph(*, blockers=0):
    return {
        "schema": "hashmm.task-evidence-graph.v1",
        "graph_id": "graph-1",
        "run_id": "run-1",
        "summary": {"blockers": blockers},
    }


def _frontier(*, ready=0, scope=0, waiting=0):
    return {
        "schema": "hashmm.execution-frontier.v1",
        "frontier_id": "frontier-1",
        "summary": {
            "ready_routes": ready,
            "scope_blocked": scope,
            "waiting_input": waiting,
        },
        "items": [],
    }


def test_gate_verifies_only_when_every_required_runtime_check_passes():
    contract = build_task_contract(
        user_goal="给出简短答案",
        task_type="chat",
        execution_mode="rag",
        run_id="run-1",
        requires_plan=False,
    )
    gate = build_completion_gate(
        task_contract=contract,
        verification={"checks": [{
            "id": "output_delivery", "status": "passed", "detail": "有输出",
        }]},
        evidence_graph=_graph(),
        execution_frontier=_frontier(),
        termination_reason="completed",
    )
    assert gate["schema"] == "hashmm.completion-gate.v1"
    assert gate["status"] == "verified"
    assert gate["can_claim_complete"] is True


def test_gate_does_not_treat_missing_required_check_as_complete():
    contract = build_task_contract(
        user_goal="实现并测试功能",
        task_type="code_task",
        execution_mode="agent_loop",
        run_id="run-1",
        requires_plan=False,
    )
    gate = build_completion_gate(
        task_contract=contract,
        verification={"checks": [{"id": "output_delivery", "status": "passed"}]},
        evidence_graph=_graph(),
        execution_frontier=_frontier(),
        termination_reason="completed",
    )
    assert gate["status"] == "incomplete"
    assert gate["can_claim_complete"] is False
    assert gate["summary"]["missing"] == 1
    assert any(item["code"] == "missing_required_check" for item in gate["failure_modes"])


def test_model_judge_cannot_approve_user_acceptance():
    contract = build_task_contract(
        user_goal="生成最终方案",
        task_type="long_task",
        execution_mode="rag",
        run_id="run-1",
        requires_plan=False,
        acceptance="我确认方案可用",
    )
    gate = build_completion_gate(
        task_contract=contract,
        verification={"checks": [
            {"id": "output_delivery", "status": "passed"},
            {"id": "user_acceptance", "status": "passed", "authority": "llm_judge"},
        ]},
        evidence_graph=_graph(),
        execution_frontier=_frontier(),
        termination_reason="completed",
    )
    acceptance = next(item for item in gate["criteria"] if item["check_id"] == "user_acceptance")
    assert acceptance["status"] == "not_evaluable"
    assert acceptance["authority"] == "user_confirmation_required"
    assert gate["status"] == "delivered_with_limits"
    assert gate["can_claim_complete"] is False


def test_multi_agent_contract_checks_agents_instead_of_inventing_tool_requirement():
    contract = build_task_contract(
        user_goal="并行研究并汇总",
        task_type="multi_agent_task",
        execution_mode="agent_team_parallel",
        run_id="run-1",
        requires_plan=False,
    )
    ids = [item["check_id"] for item in contract["success_criteria"]]
    assert "agent_completion" in ids
    assert "tool_execution" not in ids


def test_gate_surfaces_agent_failures_and_repeated_tool_loops():
    contract = build_task_contract(
        user_goal="完成多 Agent 调研",
        task_type="multi_agent_task",
        execution_mode="agent_team_parallel",
        run_id="run-1",
        requires_plan=False,
    )
    gate = build_completion_gate(
        task_contract=contract,
        verification={"checks": [
            {"id": "output_delivery", "status": "passed"},
            {"id": "agent_completion", "status": "failed"},
        ]},
        evidence_graph=_graph(blockers=1),
        execution_frontier=_frontier(ready=1),
        termination_reason="completed",
        tool_steps=[{"name": "web_search", "status": "passed", "args": {"q": "x"}}] * 3,
        orchestration={"members": [
            {"id": "a", "status": "done"},
            {"id": "b", "status": "failed"},
        ]},
    )
    codes = {item["code"] for item in gate["failure_modes"]}
    assert gate["status"] == "incomplete"
    assert {"required_criterion_failed", "agent_failure", "repeated_tool_call"} <= codes
    assert gate["trajectory"]["max_identical_repeats"] == 3


def test_online_metrics_keep_verified_and_limited_outcomes_separate():
    contract = build_task_contract(
        user_goal="回答", task_type="chat", execution_mode="rag",
        run_id="run-1", requires_plan=False,
    )
    verified = build_completion_gate(
        task_contract=contract,
        verification={"checks": [{"id": "output_delivery", "status": "passed"}]},
        evidence_graph=_graph(), execution_frontier=_frontier(),
        termination_reason="completed",
    )
    limited_contract = build_task_contract(
        user_goal="交付并让我验收", task_type="chat", execution_mode="rag",
        run_id="run-2", requires_plan=False, acceptance="用户确认",
    )
    limited = build_completion_gate(
        task_contract=limited_contract,
        verification={"checks": [
            {"id": "output_delivery", "status": "passed"},
            {"id": "user_acceptance", "status": "not_evaluable"},
        ]},
        evidence_graph=_graph(), execution_frontier=_frontier(),
        termination_reason="completed",
    )
    metrics = runtime_completion_gate_metrics([
        {"completion_gate": verified}, {"completion_gate": limited}, {},
    ])
    assert metrics["verified_runs"] == 1
    assert metrics["delivered_with_limits_runs"] == 1
    assert metrics["missing_gate_runs"] == 1
    assert metrics["unsafe_completion_claims"] == 0


@pytest.fixture
def acceptance_loop_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    loop_engine._reset_state_for_tests()
    contract = build_task_contract(
        user_goal="交付报告",
        task_type="long_task",
        execution_mode="durable_agent_loop",
        run_id="goal-accept-1",
        requires_plan=False,
        acceptance="用户确认报告可用",
    )
    loop_engine._LOADED = True
    loop_engine._LOOPS["goal-accept-1"] = {
        "id": "goal-accept-1",
        "type": "goal",
        "goal": "交付报告",
        "acceptance": "用户确认报告可用",
        "status": "done",
        "stop_reason": "delivered_with_limits",
        "user": "owner-1",
        "conv_id": "",
        "approval_mode": "read_only",
        "task_contract": contract,
        "verification": {"status": "not_evaluable", "checks": [
            {"check_id": "output_delivery", "label": "交付可读取结果", "required": True,
             "status": "passed", "detail": "已交付"},
            {"check_id": "tool_execution", "label": "工具调用已执行", "required": True,
             "status": "passed", "detail": "工具轨迹已闭合"},
            {"check_id": "user_acceptance", "label": "用户确认报告可用", "required": True,
             "status": "not_evaluable", "detail": "等待用户确认"},
        ]},
        "execution_scope": {"schema": "hashmm.execution-scope.v1", "allowed_tools": [],
                            "network": {"mode": "deny", "origins": []}},
        "history": [], "trace": [], "tools": [], "files": [],
        "created": time.time(), "updated": time.time(), "generation": 1,
        "_stop": threading.Event(),
    }
    yield tmp_path
    loop_engine._reset_state_for_tests()


def test_owner_acceptance_is_persisted_and_unlocks_completion(acceptance_loop_store):
    assert loop_engine.record_user_acceptance("goal-accept-1", True, "other-user") is None
    result = loop_engine.record_user_acceptance(
        "goal-accept-1", True, "owner-1", note="已检查下载文件",
    )
    assert result is not None
    assert result["verified"] is True
    assert result["completion_gate"]["status"] == "verified"
    user_check = next(
        item for item in result["verification"]["checks"]
        if item["check_id"] == "user_acceptance"
    )
    assert user_check["status"] == "passed"
    assert user_check["authority"] == "actual_user_confirmation"
    persisted = json.loads((acceptance_loop_store / "agent-loops.json").read_text(encoding="utf-8"))
    assert persisted[0]["acceptance_confirmation"]["accepted"] is True
    assert persisted[0]["completion_gate"]["can_claim_complete"] is True


def test_user_rejection_is_authoritative_and_never_claims_complete(acceptance_loop_store):
    result = loop_engine.record_user_acceptance(
        "goal-accept-1", False, "owner-1", note="缺少第三个来源",
    )
    assert result is not None
    assert result["verified"] is False
    assert result["stop_reason"] == "user_rejected"
    assert result["completion_gate"]["status"] == "incomplete"
    assert any(
        item["code"] == "required_criterion_failed"
        for item in result["completion_gate"]["failure_modes"]
    )
