from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _scope():
    from hashmm.agent.execution_scope import build_root_scope

    return build_root_scope(
        owner_id="u1", conversation_id="c1", run_id="root1",
        allowed_tools=["kb_search", "spawn_worker"], approval_mode="read_only",
        network_mode="deny", allow_subagents=True,
    )


def test_session_registry_tracks_parent_scope_steps_and_terminal_result():
    from hashmm.agent.session import AgentSessionRegistry

    registry = AgentSessionRegistry(max_records=32)
    scope = _scope()
    row = registry.create(
        role="research", task="find evidence", owner_id="u1",
        conversation_id="c1", execution_scope=scope, parent_session_id="root1",
    )
    registry.start(row["session_id"])
    registry.append_step(row["session_id"], {
        "tool": "kb_search", "status": "done", "detail": "2 sources",
    })
    registry.finish(row["session_id"], "completed", {"summary": "ok"})
    saved = registry.get(row["session_id"])
    assert saved["contract"] == "hashmm.agent-session.v2"
    assert saved["parent_session_id"] == "root1"
    assert saved["scope_id"] == scope["scope_id"]
    assert saved["status"] == "completed"
    assert saved["steps"][0]["tool"] == "kb_search"
    assert saved["result"]["summary"] == "ok"


def test_worker_returns_structured_session_and_derived_scope(monkeypatch):
    from hashmm.agent.worker import Worker

    class LLM:
        def call_with_tools(self, messages, tools=None):
            return SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))

    parent = _scope()
    worker = Worker(
        LLM(), role="research", user_id="u1", conv_id="c1",
        parent_scope=parent, parent_session_id="root1",
    )
    result = asyncio.run(worker.run("核实资料"))
    assert result["status"] == "completed"
    assert result["session_id"].startswith("as")
    assert result["scope_id"] == worker.execution_scope["scope_id"]
    assert worker.execution_scope["parent_scope_id"] == parent["scope_id"]
    assert worker.execution_scope["allow_subagents"] is False


def test_worker_interrupts_before_next_model_turn():
    from hashmm.agent.worker import Worker

    class LLM:
        def __init__(self):
            self.calls = 0

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            tc = SimpleNamespace(
                id="x", function=SimpleNamespace(name="kb_search", arguments=json.dumps({"query": "x"}))
            )
            return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))

    llm = LLM()
    worker = Worker(llm, role="research", user_id="u1", conv_id="c1", parent_scope=_scope())
    worker.permissions = SimpleNamespace(check=lambda *args, **kwargs: (False, "deny"))
    result = asyncio.run(worker.run("stop", cancel_check=lambda: True))
    assert result["status"] == "stopped"
    assert llm.calls == 0


def test_persistent_session_is_owner_bound_and_restart_is_truthful():
    from hashmm.agent.session import AgentSessionRegistry
    from hashmm.agent import work_runtime

    first = AgentSessionRegistry(max_records=32, persist=True)
    row = first.create(
        role="research", task="side effect boundary", owner_id="u-persist",
        conversation_id="c-persist", execution_scope=_scope(),
    )
    first.start(row["session_id"], owner_id="u-persist")
    running = first.get(row["session_id"], owner_id="u-persist")
    assert running and running["status"] == "running" and running["work_run_id"]
    assert first.get(row["session_id"], owner_id="other") is None

    # A fresh process registry reconciles in-flight work. It does not claim the
    # old model/tool call survived and explicitly forbids automatic replay.
    restarted = AgentSessionRegistry(max_records=32, persist=True)
    recovered = restarted.get(row["session_id"], owner_id="u-persist")
    assert recovered and recovered["status"] == "interrupted"
    assert recovered["result"]["recovery_reason"] == "process_restarted"
    assert recovered["result"]["safe_to_replay_side_effects"] is False

    projected = work_runtime.get_run(
        recovered["work_run_id"], "u-persist", after_seq=0, limit=20,
    )
    assert projected and projected["status"] == "interrupted"
    assert any(e["type"] == "agent_session_interrupted" for e in projected["events"])
