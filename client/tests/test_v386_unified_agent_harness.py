"""V386: one runtime contract for Chat, tools, compaction and sub-agents."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _scope(*, tools: list[str], workers: int = 0, subagents: bool = False) -> dict:
    from hashmm.agent.execution_scope import build_root_scope

    return build_root_scope(
        owner_id="owner-v386",
        conversation_id="conv-v386",
        run_id="run-v386",
        allowed_tools=tools,
        approval_mode="read_only",
        network_mode="deny",
        allow_subagents=subagents,
        max_tool_calls=2,
        max_workers=workers,
    )


def _kernel(*, scope: dict, schemas: list[dict], executors: dict) -> object:
    from hashmm.agent.harness import AgentRunKernel

    return AgentRunKernel(
        owner_id="owner-v386",
        conversation_id="conv-v386",
        goal="完成一项有证据的长任务",
        execution_scope=scope,
        tool_schemas=schemas,
        executors=executors,
        max_iterations=6,
        max_tool_calls=9,
        max_search_calls=3,
        max_exec_calls=2,
        max_workers=4,
    )


def test_capability_snapshot_requires_valid_scope_callable_executor_and_network_authority():
    from hashmm.agent.harness import build_capability_snapshot

    schemas = [_tool("ready"), _tool("ghost"), _tool("browser_open"), _tool("spawn_worker")]
    scope = _scope(
        tools=["ready", "ghost", "browser_open", "spawn_worker"],
        workers=0,
        subagents=False,
    )
    # build_root_scope removes network and child tools when those capabilities
    # are disabled; the snapshot also remains deny-first for malformed scopes.
    snapshot = build_capability_snapshot(schemas, {"ready": lambda *_: None}, scope)
    assert snapshot["effective_tools"] == ["ready"]
    assert snapshot["missing_executors"] == ["ghost"]
    assert build_capability_snapshot(schemas, {"ready": lambda *_: None}, {})[
        "effective_tools"
    ] == []


def test_turn_context_is_frozen_and_scope_budgets_cannot_expand():
    kernel = _kernel(
        scope=_scope(tools=["ready"], workers=0, subagents=False),
        schemas=[_tool("ready")],
        executors={"ready": lambda *_: None},
    )
    assert kernel.context.max_tool_calls == 2
    assert kernel.context.max_workers == 0
    assert kernel.context.allow_subagents is False
    with pytest.raises(FrozenInstanceError):
        kernel.context.max_workers = 99
    denied = kernel.admit_child("research")
    assert denied["ok"] is False and denied["cap"] == "subagents_disabled"


def test_child_admission_has_active_then_total_caps_and_bounded_trajectory():
    kernel = _kernel(
        scope=_scope(tools=["spawn_worker"], workers=1, subagents=True),
        schemas=[_tool("spawn_worker")],
        executors={},
    )
    assert kernel.admit_child("research")["ok"] is True
    assert kernel.admit_child("review")["cap"] == "max_active_children"
    kernel.finish_child("research", "completed")
    assert kernel.admit_child("review")["cap"] == "max_total_children"
    kernel.record(
        "tool_started",
        status="running",
        tool_name="ready",
        args={"password": "must-not-be-stored"},
        detail={"iteration": 1, "raw_result": "must-not-be-stored"},
    )
    public = kernel.public()
    encoded = json.dumps(public, ensure_ascii=False)
    assert "must-not-be-stored" not in encoded
    assert "args_hash" in encoded and "raw_result" not in encoded
    assert [row["seq"] for row in public["trajectory"]["events"]] == list(
        range(1, public["trajectory"]["event_count"] + 1)
    )


def test_terminal_cancellation_and_timeout_are_sticky():
    from hashmm.agent.harness import build_terminal_outcome, merge_terminal_outcome

    cancelled = build_terminal_outcome("interrupted")
    completed = build_terminal_outcome("completed")
    assert merge_terminal_outcome(cancelled, completed)["reason"] == "cancelled"
    timeout = build_terminal_outcome("deadline")
    assert merge_terminal_outcome(completed, timeout)["reason"] == "hard_timeout"
    assert merge_terminal_outcome(timeout, completed)["reason"] == "hard_timeout"


def test_main_agent_loop_fires_precompact_and_returns_same_harness(monkeypatch):
    from hashmm.agent import loop as loop_module
    from hashmm.agent.loop import AgentLoop
    import hashmm.hooks as hooks

    class LLM:
        def __init__(self):
            self.calls = 0

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                call = SimpleNamespace(
                    id="read-1",
                    function=SimpleNamespace(name="read_file", arguments='{"path":"notes.txt"}'),
                )
                message = SimpleNamespace(content="", tool_calls=[call], reasoning_content="")
            else:
                message = SimpleNamespace(
                    content="结论：已读取资料并完成可核对的总结。",
                    tool_calls=[],
                    reasoning_content="",
                )
            return SimpleNamespace(message=message)

    class Loop(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            assert name == "read_file"
            return {"status": "ok", "message": "x" * 2_000}

    calls: list[int] = []

    class ContextLifecycle:
        def __init__(self):
            self.calls: list[str] = []
            self.generation = 1

        def compact(self, *, reason):
            self.calls.append(f"compact:{reason}")
            self.generation += 1
            return "bounded summary"

        def checkpoint(self, *, reason):
            self.calls.append(f"checkpoint:{reason}")
            return f"ctx-{self.generation}"

        def inspect(self):
            return {"generation": self.generation}

        def after_response(self, response, *, status, tool_calls):
            self.calls.append(f"after:{status}:{tool_calls}")
            return {
                "contract": "hashmm.context-engine.v2",
                "generation": self.generation,
                "turns": 1,
                "compact_count": 1,
                "has_summary": True,
                "checkpoint_id": f"ctx-{self.generation}",
                "compacted": False,
                "tool_calls": tool_calls,
            }

    def compact(messages, ctx):
        calls.append(len(messages))
        ctx["_hook_runs"] = [{"lifecycle": "PreCompact", "status": "completed"}]

    monkeypatch.setattr(loop_module, "MAX_CONTEXT_CHARS", 300)
    monkeypatch.setattr(hooks, "run_compact_hooks", compact)
    monkeypatch.setattr(hooks, "get_hook_runs", lambda ctx: list(ctx.get("_hook_runs") or []))
    loop = Loop(
        LLM(),
        tools=[_tool("read_file")],
        user_id="owner-v386",
        conv_id="conv-v386",
        execution_scope=_scope(tools=["read_file"]),
        max_iterations=3,
        max_tool_calls=2,
        max_search_calls=0,
        max_exec_calls=0,
    )
    loop.permissions = SimpleNamespace(check=lambda *args, **kwargs: (True, "allowed"))
    loop._tool_executors["read_file"] = lambda *_: None
    lifecycle = ContextLifecycle()
    loop._context_lifecycle = lifecycle
    loop._context_observability = {
        "hit_count": 3, "total_chars": 220, "generation": 1,
    }

    async def collect():
        return [item async for item in loop.run("读取资料后总结", history=[])]

    events = asyncio.run(collect())
    done = next(payload for kind, payload in events if kind == "done")
    assert calls
    assert done["terminal"]["reason"] == "completed"
    assert done["harness"]["terminal"] == done["terminal"]
    assert done["harness"]["context"]["budgets"]["max_tool_calls"] == 2
    assert done["context"]["contract"] == "hashmm.context-engine.v2"
    assert lifecycle.calls[:2] == [
        "compact:agent_precompact", "checkpoint:agent_precompact",
    ]
    assert lifecycle.calls[-1].startswith("after:completed:")
    event_types = done["harness"]["trajectory"]["event_types"]
    assert event_types["context_assembled"] == 1
    assert event_types["context_checkpoint"] == 2
    assert any(
        row["type"] == "context_compaction"
        for row in done["harness"]["trajectory"]["events"]
    )


def test_context_engine_classifies_retrieval_as_bounded_untrusted_data():
    from hashmm.agent.context_engine import assemble_context

    bundle = assemble_context(
        {"retrieval": lambda: "verified excerpt"},
        order=["retrieval"],
        budgets={"retrieval": 8},
        global_budget=20,
    )
    source = bundle.sources[0]
    assert source.key == "retrieval"
    assert source.trust == "untrusted_data"
    assert source.truncated is True
    rendered = bundle.profile_text()
    assert "<untrusted-context source=\"retrieval\">" in rendered


def test_normal_worker_finish_calls_subagent_stop_and_manifest_is_argument_free(monkeypatch):
    from hashmm.agent.loop import AgentLoop
    from hashmm.evaluation.run_manifest import build_run_manifest
    import hashmm.hooks as hooks

    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        hooks,
        "run_subagent_stop_hooks",
        lambda subtask_id, result, ctx: observed.append((subtask_id, ctx["status"])),
    )
    monkeypatch.setattr(hooks, "get_hook_runs", lambda ctx: [])
    loop = AgentLoop(
        llm_fn=None,
        tools=[_tool("spawn_worker")],
        user_id="owner-v386",
        conv_id="conv-v386",
        execution_scope=_scope(tools=["spawn_worker"], workers=1, subagents=True),
    )
    loop._worker_results = []
    loop._run_kernel = _kernel(
        scope=_scope(tools=["spawn_worker"], workers=1, subagents=True),
        schemas=[_tool("spawn_worker")],
        executors={},
    )
    loop._run_kernel.admit_child("research")
    loop._finish_worker(
        "research",
        "private task body",
        {"session_id": "child-1", "message": "bounded result", "tool_calls": 1},
        "completed",
    )
    assert observed == [("child-1", "completed")]

    loop._run_kernel.record(
        "tool_started", tool_name="read_file", args={"token": "top-secret"},
        detail={"iteration": 1, "raw": "top-secret"},
    )
    loop._run_kernel.finish("completed")
    manifest = build_run_manifest(
        run_id="run-v386",
        task_type="long_task",
        execution_mode="agent_loop",
        user_goal="完成任务",
        answer_text="已完成",
        stop_reason="completed",
        harness=loop._run_kernel.public(),
        orchestration={"strategy": "supervisor_worker", "members": loop._worker_results},
    )
    encoded = json.dumps(manifest["harness"], ensure_ascii=False)
    assert "top-secret" not in encoded and "private task body" not in encoded
    checks = {row["id"]: row for row in manifest["verification"]["checks"]}
    assert checks["trajectory_integrity"]["status"] == "passed"
    assert checks["runtime_wiring"]["status"] == "passed"


def test_work_runtime_projection_keeps_bounded_harness_for_desktop_and_app():
    from hashmm.agent.work_runtime import project_run_manifest

    kernel = _kernel(
        scope=_scope(tools=["ready"]),
        schemas=[_tool("ready")],
        executors={"ready": lambda *_: None},
    )
    kernel.record("tool_started", tool_name="ready", args={"secret": "never-copy"})
    kernel.finish("completed")
    projected = project_run_manifest({
        "schema": "hashmm.run-manifest.v2",
        "run_id": "run-v386",
        "answer_text": "model prose must not enter work runtime",
        "harness": kernel.public(),
        "context_lifecycle": {
            "contract": "hashmm.context-engine.v2",
            "generation": 4,
            "checkpoint_id": "ctx-safe",
            "rolling_summary": "private summary must not enter runtime",
        },
    })
    encoded = json.dumps(projected, ensure_ascii=False)
    assert projected["harness"]["schema"] == "hashmm.agent-harness.v1"
    assert "never-copy" not in encoded
    assert "model prose must not enter work runtime" not in encoded
    assert projected["context_lifecycle"]["generation"] == 4
    assert "private summary must not enter runtime" not in encoded


def test_document_delivery_updates_run_state_instead_of_reprompting_forever():
    from hashmm.agent.loop import AgentLoop

    class LLM:
        def __init__(self):
            self.calls = 0
            self.forced_document_prompts: list[int] = []

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            self.forced_document_prompts.append(sum(
                "已检索到足够信息" in str(item.get("content") or "")
                for item in messages if isinstance(item, dict)
            ))
            if self.calls == 1:
                call = SimpleNamespace(
                    id="document-1",
                    function=SimpleNamespace(
                        name="create_document",
                        arguments='{"filename":"result.docx","content":"verified"}',
                    ),
                )
                message = SimpleNamespace(content="", tool_calls=[call], reasoning_content="")
            else:
                message = SimpleNamespace(
                    content="文档已生成并完成交付。", tool_calls=[], reasoning_content="",
                )
            return SimpleNamespace(message=message)

    class Loop(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            return {
                "status": "ok",
                "message": "文档已生成",
                "file": {"filename": "result.docx", "download_url": "/download/result.docx"},
            }

    llm = LLM()
    loop = Loop(
        llm,
        tools=[_tool("create_document")],
        user_id="owner-v386",
        conv_id="conv-v386",
        execution_scope=_scope(tools=["create_document"]),
        max_iterations=3,
        max_search_calls=0,
    )
    loop.permissions = SimpleNamespace(check=lambda *args, **kwargs: (True, "allowed"))
    loop._tool_executors["create_document"] = lambda *_: None

    async def collect():
        return [item async for item in loop.run("生成 Word 文档", history=[])]

    events = asyncio.run(collect())
    files = [payload for kind, payload in events if kind == "file"]
    assert loop._doc_produced is True
    assert files and files[0]["filename"] == "result.docx"
    assert llm.forced_document_prompts == [1, 1]
