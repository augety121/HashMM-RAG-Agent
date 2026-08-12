from __future__ import annotations

import pytest


pytestmark = pytest.mark.unit


def test_react_agent_forwards_server_execution_context_to_every_tool():
    from hashmm.agent.execution_scope import build_root_scope
    from hashmm.react_agent import ReactAgent

    calls: list[tuple[str, dict, dict]] = []

    def execute(name, args, ctx):
        calls.append((name, args, ctx))
        return "ok"

    scope = build_root_scope(
        owner_id="u1", conversation_id="c1", run_id="r1",
        allowed_tools=["kb_search", "execute_code", "create_file"],
        approval_mode="read_only", network_mode="deny",
    )
    context = {
        "user_id": "u1", "conv_id": "c1", "session_id": "c1",
        "execution_scope": scope,
    }
    agent = ReactAgent(llm_fn=lambda _prompt: "", tool_exec_fn=execute,
                       exec_context=context)

    agent._exec_search("资料")
    agent._exec_code("print(1)")
    agent._exec_file("answer.txt", "result")

    assert [item[0] for item in calls] == ["kb_search", "execute_code", "create_file"]
    assert all(item[2]["user_id"] == "u1" for item in calls)
    assert all(item[2]["conv_id"] == "c1" for item in calls)
    assert all(item[2]["execution_scope"]["scope_id"] == scope["scope_id"] for item in calls)


def test_react_agent_does_not_accept_model_supplied_context():
    from hashmm.react_agent import ReactAgent

    seen = {}

    def execute(_name, _args, ctx):
        seen.update(ctx)
        return "ok"

    agent = ReactAgent(
        llm_fn=lambda _prompt: "", tool_exec_fn=execute,
        exec_context={"user_id": "server-owner", "conv_id": "server-conv"},
    )
    agent._exec_code("# user_id=model-owner\nprint('safe')")

    assert seen == {"user_id": "server-owner", "conv_id": "server-conv"}


def test_agent_loop_binds_server_run_id_to_execution_scope():
    from hashmm.agent.loop import AgentLoop

    loop = AgentLoop(
        llm_fn=lambda *_args, **_kwargs: "",
        tools=[],
        user_id="server-owner",
        conv_id="server-conv",
        run_id="assistant-message-1",
    )

    assert loop.execution_scope["run_id"] == "assistant-message-1"
    assert loop.execution_scope["owner_id"] == "server-owner"
