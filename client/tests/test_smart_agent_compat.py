from __future__ import annotations

import pytest


def test_smart_agent_compatibility_uses_real_react_execution():
    from hashmm.api.smart_agent import SmartAgent

    class LLM:
        def __init__(self):
            self.responses = iter(["grounded answer"])

        def chat(self, _messages):
            return next(self.responses)

    history = [{"role": "user", "content": "old"}]
    agent = SmartAgent(llm_fn=LLM(), system_prompt="Use runtime evidence")
    assert agent.run("new", history) == "grounded answer"
    # The adapter must not let ReactAgent mutate the caller's conversation.
    assert history == [{"role": "user", "content": "old"}]


def test_smart_agent_rejects_legacy_raw_tool_callback():
    from hashmm.api.smart_agent import SmartAgent

    with pytest.raises(TypeError, match="raw tool callbacks"):
        SmartAgent(
            llm_fn=lambda _prompt: "answer",
            tool_exec_fn=lambda _tool, _args: {"status": "ok"},
        )


def test_multi_agent_executor_is_bounded_and_reports_partial_failures():
    from hashmm.api.smart_agent import MultiAgentExecutor

    class Worker:
        def __init__(self, answer):
            self.answer = answer

        def run(self, query, messages):
            assert query == "goal"
            assert messages == [{"role": "user", "content": "history"}]
            if self.answer == "bad":
                raise RuntimeError("worker unavailable")
            return self.answer

    executor = MultiAgentExecutor(
        agents=[Worker("one"), Worker("bad"), Worker("two")],
        max_workers=2,
        conv_id="conv-1",
    )
    result = executor.run("goal", [{"role": "user", "content": "history"}])
    assert result["status"] == "partial"
    assert result["worker_count"] == 3
    assert {item["answer"] for item in result["results"] if item["status"] == "completed"} == {
        "one", "two",
    }
    assert result["errors"][0]["code"] == "worker_failed"
    assert result["synthesized"] is False


def test_multi_agent_without_workers_fails_closed():
    from hashmm.api.smart_agent import MultiAgentExecutor

    result = MultiAgentExecutor().run("goal")
    assert result["status"] == "failed"
    assert result["errors"] == [{
        "code": "no_workers",
        "message": "未配置可运行的 Agent",
    }]


def test_multi_agent_rejects_bare_worker_callback():
    from hashmm.api.smart_agent import MultiAgentExecutor

    result = MultiAgentExecutor(agents=[lambda _query: "unscoped"]).run("goal")
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "worker_failed"
    assert "raw worker callbacks" in result["errors"][0]["message"]
