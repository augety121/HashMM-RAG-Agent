from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hashmm.agent import loop_engine as engine


@pytest.fixture(autouse=True)
def isolated_loop_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    engine._reset_state_for_tests()
    yield tmp_path
    engine._reset_state_for_tests()


def _runtime_loop(loop_id: str, user: str, status: str = "running") -> dict:
    return {
        "id": loop_id,
        "type": "goal",
        "goal": "检查项目",
        "acceptance": "有工具证据",
        "max_rounds": 4,
        "threshold": 85,
        "rounds": 0,
        "score": 0,
        "status": status,
        "user": user,
        "approval_mode": "read_only",
        "history": [],
        "trace": [],
        "tools": [],
        "files": [],
        "tokens_used": 0,
        "max_tokens": 50_000,
        "active_seconds": 0,
        "max_seconds": 3_600,
        "created": time.time(),
        "updated": time.time(),
        "generation": 1,
        "_stop": threading.Event(),
    }


def test_parse_score_fails_closed():
    assert engine._parse_score("模型说已经完成") == (0, "模型说已经完成")
    assert engine._parse_score('{"score": 91, "feedback": "ok"}') == (91, "ok")


def test_read_only_filter_excludes_write_and_unknown_tools():
    schemas = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "create_file"}},
        {"type": "function", "function": {"name": "unknown_mcp_side_effect"}},
    ]
    assert [engine._tool_name(t) for t in engine._filter_tools(schemas, "read_only")] == ["read_file"]
    assert engine._filter_tools(schemas, "workspace") == schemas


def test_restart_recovers_read_only_but_pauses_workspace(isolated_loop_store, monkeypatch):
    path = isolated_loop_store / "agent-loops.json"
    base = _runtime_loop("g-read", "alice")
    write = {**_runtime_loop("g-write", "alice"), "approval_mode": "workspace"}
    path.write_text(json.dumps([engine._public(base), engine._public(write)], ensure_ascii=False), encoding="utf-8")
    spawned: list[str] = []
    monkeypatch.setattr(engine, "_spawn", lambda loop: spawned.append(loop["id"]) or True)

    engine._ensure_loaded()

    assert spawned == ["g-read"]
    assert engine.get_loop("g-read")["status"] == "queued"
    recovered_write = engine.get_loop("g-write")
    assert recovered_write["status"] == "paused"
    assert recovered_write["stop_reason"] == "restart_recovery_requires_resume"
    assert recovered_write["recovered"] is True


def test_loop_object_authorization_is_fail_closed():
    engine._LOADED = True
    engine._LOOPS["g1"] = _runtime_loop("g1", "alice")
    engine._LOOPS["g2"] = _runtime_loop("g2", "bob")

    assert [item["id"] for item in engine.list_loops("alice")] == ["g1"]
    assert {item["id"] for item in engine.list_loops("alice", is_admin=True)} == {"g1", "g2"}
    assert engine.get_loop("g2", "alice") is None
    assert engine.stop_loop("g2", "alice") is False
    assert engine._LOOPS["g2"]["status"] == "running"


def test_pause_and_resume_preserve_progress(monkeypatch):
    engine._LOADED = True
    loop = _runtime_loop("g1", "alice")
    loop["rounds"] = 2
    engine._LOOPS["g1"] = loop
    assert engine.pause_loop("g1", "alice") is True
    assert loop["status"] == "paused"
    assert loop["rounds"] == 2
    monkeypatch.setattr(engine, "_spawn", lambda item: item.update(status="queued") or True)
    assert engine.resume_loop("g1", "alice") is True
    assert loop["status"] == "queued"
    assert loop["rounds"] == 2


def test_agent_attempt_uses_real_event_protocol_and_read_only_tools(monkeypatch):
    import hashmm.agent.loop as loop_module
    import hashmm.api.model_manager as model_manager

    seen: dict = {}

    class FakeAgentLoop:
        def __init__(self, _fn, **kwargs):
            seen["kwargs"] = kwargs
            self.tools = [
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "create_file"}},
            ]
            self.system_prompt = ""

        def _default_system_prompt(self):
            return "BASE"

        async def run(self, query, history, user_id):
            seen["query"] = query
            seen["tool_names"] = [engine._tool_name(t) for t in self.tools]
            seen["plan_confirmed"] = self.plan_confirmed
            yield "tool_start", {"id": "1", "name": "read_file", "args": {"secret": "not persisted"}}
            yield "tool_done", {"id": "1", "name": "read_file", "status": "done", "elapsed_ms": 12}
            yield "token", "基于真实文件的结论"
            yield "done", {"stop_reason": "complete", "usage": {"total_tokens": 123}, "files": []}

    monkeypatch.setattr(loop_module, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(model_manager, "get_active_llm_fn", lambda: (object(), {}))
    loop = _runtime_loop("g1", "alice")
    result = asyncio.run(engine._agent_attempt_async(loop, "检查文件", ""))

    assert seen["tool_names"] == ["read_file"]
    assert seen["plan_confirmed"] is False
    assert result["result"] == "基于真实文件的结论"
    assert result["usage"]["total_tokens"] == 123
    assert result["tools"] == [{"name": "read_file", "status": "done", "elapsed_ms": 12}]
    assert "secret" not in json.dumps(result, ensure_ascii=False)


def test_evaluator_caps_unsupported_claims(monkeypatch):
    import hashmm.agent.harness as harness
    import hashmm.api.model_manager as model_manager

    monkeypatch.setattr(harness, "run_llm", lambda *args, **kwargs: '{"score":99,"feedback":"完成"}')
    monkeypatch.setattr(model_manager, "get_active_llm_fn", lambda: (object(), {}))
    loop = _runtime_loop("g1", "alice")
    loop["goal"] = "检查最新项目并生成报告"
    score, feedback, estimated, evidence_ok = engine._evaluate(loop, {
        "result": "我已经检查并生成了报告",
        "tools": [],
        "files": [],
        "trace": [],
    })
    assert score == 60
    assert evidence_ok is False
    assert "缺少工具执行证据" in feedback
    assert estimated > 0


def test_routes_pass_immutable_principal_and_hide_forbidden(monkeypatch):
    import importlib.util
    from pathlib import Path

    route_path = Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "loops.py"
    spec = importlib.util.spec_from_file_location("hashmm_loops_route_v335_test", route_path)
    assert spec and spec.loader
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)

    seen: dict = {}
    monkeypatch.setattr(routes, "require_auth", lambda request: {"uid": "u-alice", "sub": "alice", "role": "user"})
    monkeypatch.setattr(engine, "list_loops", lambda user, is_admin=False: seen.update(user=user, admin=is_admin) or [])
    monkeypatch.setattr(engine, "get_loop", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    response = client.get("/api/loops")
    assert response.status_code == 200
    assert seen == {"user": "u-alice", "admin": False}
    assert client.get("/api/loops/not-mine").status_code == 404
    assert client.post("/api/loops/goal", content="not-json", headers={"content-type": "application/json"}).status_code == 400


def test_goal_route_rejects_foreign_conversation_before_start(monkeypatch):
    import importlib.util
    from pathlib import Path
    from fastapi import HTTPException

    route_path = Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "loops.py"
    spec = importlib.util.spec_from_file_location("hashmm_loops_route_v335_idor_test", route_path)
    assert spec and spec.loader
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    monkeypatch.setattr(routes, "require_auth", lambda request: {"uid": "u-alice", "role": "user"})
    checked: list[str] = []

    def deny_foreign(_request, conv_id):
        checked.append(conv_id)
        raise HTTPException(404, "对话不存在")

    monkeypatch.setattr(routes, "require_conv_access", deny_foreign)
    monkeypatch.setattr(engine, "start_goal_loop", lambda *args, **kwargs: pytest.fail("must not start"))
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post("/api/loops/goal", json={"goal": "读取文件", "conv_id": "foreign"})
    assert response.status_code == 404
    assert checked == ["foreign"]
