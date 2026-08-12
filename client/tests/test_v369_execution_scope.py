from __future__ import annotations

import asyncio
import threading

import pytest


pytestmark = pytest.mark.unit


def _root(**overrides):
    from hashmm.agent.execution_scope import build_root_scope

    values = {
        "owner_id": "u1",
        "conversation_id": "c1",
        "run_id": "g1",
        "allowed_tools": ["kb_search", "fetch_url", "browser_open", "spawn_worker"],
        "approval_mode": "read_only",
        "network_mode": "allowlist",
        "allowed_origins": ["https://Example.com/docs", "https://example.com:443/other"],
        "allow_subagents": True,
    }
    values.update(overrides)
    return build_root_scope(**values)


def test_scope_normalizes_origins_and_binds_owner_conversation_and_tool():
    from hashmm.agent.execution_scope import check_execution_scope

    scope = _root()
    assert scope["network"]["origins"] == ["https://example.com"]
    assert check_execution_scope(
        scope, "fetch_url", {"url": "https://example.com/a"},
        user_id="u1", conversation_id="c1",
    )[0] is True
    assert check_execution_scope(
        scope, "fetch_url", {"url": "https://other.example/a"},
        user_id="u1", conversation_id="c1",
    )[0] is False
    assert check_execution_scope(
        scope, "kb_search", {"query": "x"},
        user_id="other", conversation_id="c1",
    )[0] is False
    assert check_execution_scope(
        scope, "kb_search", {"query": "x"},
        user_id="u1", conversation_id="other",
    )[0] is False
    assert check_execution_scope(
        scope, "execute_code", {"code": "1"},
        user_id="u1", conversation_id="c1",
    )[0] is False


def test_network_deny_removes_network_tools_and_empty_allowlist_is_not_allow_all():
    denied = _root(network_mode="deny")
    assert "fetch_url" not in denied["allowed_tools"]
    assert "browser_open" not in denied["allowed_tools"]

    empty = _root(network_mode="allowlist", allowed_origins=[])
    assert empty["network"]["mode"] == "deny"
    assert "fetch_url" not in empty["allowed_tools"]


def test_child_scope_can_only_narrow_parent_and_cannot_delegate_again():
    from hashmm.agent.execution_scope import derive_child_scope

    parent = _root()
    child = derive_child_scope(
        parent, role="research",
        role_tools=["kb_search", "web_search", "create_file", "spawn_worker"],
    )
    assert child is not None
    assert child["parent_scope_id"] == parent["scope_id"]
    assert child["depth"] == 1
    assert child["allowed_tools"] == ["kb_search"]
    # Delegation is one level only even if a future role accidentally includes spawn_worker.
    assert child["allow_subagents"] is False


def test_scope_guard_fails_closed_before_runtime_permission_guard():
    from hashmm.agent.tool_pipeline import ToolPipeline, TurnState

    class AllowAll:
        def check(self, *args, **kwargs):
            return True, "ok"

    scope = _root(network_mode="deny")
    decision = ToolPipeline().evaluate(
        "fetch_url", {"url": "https://example.com"}, ("fetch_url", "x"), TurnState(),
        permissions=AllowAll(), user_id="u1", conv_id="c1", execution_scope=scope,
    )
    assert decision is not None
    assert decision.guard == "scope"
    assert decision.result["status"] == "denied"


def test_worker_uses_permission_system_and_preserves_conversation_boundary():
    from hashmm.agent.worker import Worker

    class Fn:
        def __init__(self, name, arguments):
            self.name, self.arguments = name, arguments

    class Call:
        id = "w1"
        type = "function"
        function = Fn("kb_search", '{"query":"scope"}')

    class Message:
        def __init__(self, content="", calls=None):
            self.content, self.tool_calls = content, calls

    class Response:
        def __init__(self, message):
            self.message = message

    class Llm:
        def __init__(self):
            self.calls = 0

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            return Response(Message(calls=[Call()])) if self.calls == 1 else Response(Message("完成"))

    seen = []

    class Deny:
        def check(self, name, args, user_id, cwd=None, conv_id=None):
            seen.append((name, user_id, conv_id, cwd))
            return False, "test deny"

    worker = Worker(Llm(), role="research", user_id="u1", conv_id="c1")
    worker.permissions = Deny()
    executed = []

    async def fake_exec(name, args):
        executed.append(name)
        return "should not execute"

    worker._exec = fake_exec
    result = asyncio.run(worker.run("read evidence"))
    assert result["summary"] == "完成"
    assert seen and seen[0][:3] == ("kb_search", "u1", "c1")
    assert executed == []
    assert len(result["execution_receipts"]) == 1
    receipt = result["execution_receipts"][0]
    assert receipt["outcome"]["status"] == "denied"
    assert receipt["permission"]["decision"] == "denied"
    from hashmm.agent.execution_receipt import validate_execution_receipt
    assert validate_execution_receipt(receipt)["valid"] is True


def test_durable_loop_persists_contract_and_redacts_owner(tmp_path, monkeypatch):
    from hashmm.agent import loop_engine as engine

    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    engine._reset_state_for_tests()
    monkeypatch.setattr(engine, "_spawn", lambda loop: loop.update(status="queued") or True)
    try:
        result = engine.start_goal_loop(
            "核实资料并生成报告",
            user="u1", conv_id="c1", acceptance="报告含三个可解析证据",
            approval_mode="workspace", network_mode="deny", allow_subagents=True,
        )
        assert result["ok"] is True
        loop = engine.get_loop(result["id"], "u1")
        assert loop is not None
        assert loop["task_contract"]["execution_scope_id"] == loop["execution_scope"]["scope_id"]
        labels = [item["label"] for item in loop["task_contract"]["success_criteria"]]
        assert "报告含三个可解析证据" in labels
        assert loop["execution_scope"]["network"]["mode"] == "deny"
        assert "owner_id" not in loop["execution_scope"]
        assert "spawn_worker" in loop["execution_scope"]["allowed_tools"]
    finally:
        engine._reset_state_for_tests()


def test_task_contract_verification_requires_plan_closure_and_real_tools():
    from hashmm.agent import loop_engine as engine
    from hashmm.agent.task_method import build_task_contract

    loop = {
        "threshold": 85,
        "task_contract": build_task_contract(
            user_goal="检查项目并修复问题", task_type="long_task",
            execution_mode="durable_agent_loop", acceptance="测试通过",
        ),
    }
    attempt = {
        "result": "完成",
        "tools": [{"name": "read_file", "status": "done"}],
        "files": [],
        "todo": [{"text": "检查", "status": "completed"}, {"text": "修复", "status": "pending"}],
    }
    failed = engine._verify_task_contract(loop, attempt, score=90, evidence_ok=True)
    assert "plan_closure" in failed["failed_required"]
    attempt["todo"][1]["status"] = "completed"
    passed = engine._verify_task_contract(loop, attempt, score=90, evidence_ok=True)
    assert passed["failed_required"] == []
    assert passed["model_self_report_used"] is False
