import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_registry_scopes_turns_and_prevents_stale_steering():
    from hashmm.api.active_runs import (
        ActiveRunConflict,
        ActiveRunNotFound,
        ActiveRunNotSteerable,
        ActiveRunRegistry,
    )

    registry = ActiveRunRegistry()
    run = registry.reserve("conv-1", "user-1", "生成报告", turn_id="turn-1")
    assert run.public()["steerable"] is False
    with pytest.raises(ActiveRunConflict):
        registry.reserve("conv-1", "user-1", "重复任务", turn_id="turn-2")
    with pytest.raises(ActiveRunNotFound):
        registry.get("conv-1", "other-user", "turn-1")
    with pytest.raises(ActiveRunNotFound):
        registry.get("conv-1", "user-1", "stale-turn")
    with pytest.raises(ActiveRunNotSteerable):
        run.enqueue_steer("先核验数据", "client-1")

    assert run.mark_steerable("agent_loop") is True
    first, duplicate = run.enqueue_steer("先核验数据", "client-1")
    same, is_duplicate = run.enqueue_steer("不会覆盖原内容", "client-1")
    assert duplicate is False
    assert is_duplicate is True
    assert same is first
    assert run.public()["pending_steers"] == 1

    drained = run.drain_steering()
    assert [item["content"] for item in drained] == ["先核验数据"]
    assert [item["content"] for item in run.accepted_steering()] == ["先核验数据"]
    assert run.close_steering_if_empty() is True
    with pytest.raises(ActiveRunNotSteerable):
        run.enqueue_steer("收尾后不能悄悄丢掉", "client-2")


def test_finalization_race_leaves_accepted_steer_for_agent_loop():
    from hashmm.api.active_runs import ActiveRunRegistry

    registry = ActiveRunRegistry()
    run = registry.reserve("conv-race", "owner", "长任务")
    run.mark_steerable()
    entry, _ = run.enqueue_steer("把结论改成表格", "client-race")
    assert run.close_steering_if_empty() is False
    assert run.drain_steering()[0]["entry_id"] == entry.entry_id
    assert run.close_steering_if_empty() is True


def test_interrupt_is_cooperative_and_disables_further_steering():
    from hashmm.api.active_runs import ActiveRunNotSteerable, ActiveRunRegistry

    run = ActiveRunRegistry().reserve("conv-stop", "owner", "浏览并整理")
    run.mark_steerable()
    assert run.request_interrupt() is True
    assert run.is_interrupted() is True
    assert run.public()["status"] == "interrupting"
    assert run.public()["steerable"] is False
    with pytest.raises(ActiveRunNotSteerable):
        run.enqueue_steer("停止后不再执行", "late")


def test_agent_loop_injects_authenticated_steering_as_delimited_user_input():
    from hashmm.agent.loop import AgentLoop
    from hashmm.api.active_runs import ActiveRunRegistry

    run = ActiveRunRegistry().reserve("conv-loop", "owner", "初始目标")
    run.mark_steerable()
    run.enqueue_steer("同时核验 https://example.com/source", "client-loop")
    loop = object.__new__(AgentLoop)
    loop._run_control = run
    loop._original_query = "初始目标"
    loop._user_urls = set()
    messages = [{"role": "user", "content": "初始目标"}]

    entries = loop._drain_run_steering(messages)

    assert len(entries) == 1
    assert messages[-1]["role"] == "user"
    assert "<user_steering>" in messages[-1]["content"]
    assert "不绕过权限" in messages[-1]["content"]
    assert "同时核验" in loop._original_query
    assert "https://example.com/source" in loop._user_urls


def test_active_turn_routes_owner_check_persist_idempotently_and_interrupt(monkeypatch):
    from hashmm.api.active_runs import registry
    from hashmm.api.routes import conversations as routes

    registry.clear()
    checked = []
    audits = []
    messages = []
    monkeypatch.setattr(
        routes,
        "require_conv_access",
        lambda _request, conv_id: checked.append(conv_id) or {"id": conv_id, "user_id": "owner"},
    )
    monkeypatch.setattr(
        routes,
        "get_current_user",
        lambda _request: {"uid": "owner", "sub": "alice", "role": "user"},
    )
    monkeypatch.setattr(
        routes.db,
        "create_message",
        lambda conv_id, role, content: messages.append((conv_id, role, content)) or "message-1",
    )
    monkeypatch.setattr(routes.db, "audit", lambda *args: audits.append(args))
    run = registry.reserve("conv-route", "owner", "生成分析", turn_id="turn-route")
    run.mark_steerable()

    state = asyncio.run(routes.get_active_turn("conv-route", object()))
    assert state["turn"]["turn_id"] == "turn-route"
    body = routes.TurnSteerRequest(content="增加风险清单", client_message_id="client-route")
    accepted = asyncio.run(routes.steer_active_turn("conv-route", "turn-route", body, object()))
    duplicate = asyncio.run(routes.steer_active_turn("conv-route", "turn-route", body, object()))
    assert accepted == {
        "accepted": True,
        "duplicate": False,
        "turn_id": "turn-route",
        "message_id": "message-1",
        "queued_at": accepted["queued_at"],
    }
    assert duplicate["duplicate"] is True
    assert duplicate["message_id"] == "message-1"
    assert messages == [("conv-route", "user", "增加风险清单")]

    stopped = asyncio.run(routes.interrupt_active_turn("conv-route", "turn-route", object()))
    assert stopped["status"] == "interrupting"
    assert checked == ["conv-route", "conv-route", "conv-route", "conv-route"]
    assert [item[2] for item in audits] == ["turn_steer", "turn_interrupt"]
    registry.clear()


def test_steer_route_rolls_back_queue_when_durable_message_fails(monkeypatch):
    from hashmm.api.active_runs import registry
    from hashmm.api.routes import conversations as routes

    registry.clear()
    monkeypatch.setattr(routes, "require_conv_access", lambda *_args: {"id": "conv-fail"})
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "owner", "sub": "alice"})
    monkeypatch.setattr(
        routes.db,
        "create_message",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    run = registry.reserve("conv-fail", "owner", "任务", turn_id="turn-fail")
    run.mark_steerable()
    body = routes.TurnSteerRequest(content="不能只留在内存", client_message_id="client-fail")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(routes.steer_active_turn("conv-fail", "turn-fail", body, object()))
    assert run.public()["pending_steers"] == 0
    assert run.accepted_steering() == []
    registry.clear()


def test_stale_turn_route_returns_conflict_without_writing_message(monkeypatch):
    from hashmm.api.active_runs import registry
    from hashmm.api.routes import conversations as routes

    registry.clear()
    monkeypatch.setattr(routes, "require_conv_access", lambda *_args: {"id": "conv-stale"})
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "owner", "sub": "alice"})
    writes = []
    monkeypatch.setattr(routes.db, "create_message", lambda *_args: writes.append(True))
    run = registry.reserve("conv-stale", "owner", "任务", turn_id="new-turn")
    run.mark_steerable()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.steer_active_turn(
            "conv-stale", "old-turn",
            routes.TurnSteerRequest(content="迟到的请求", client_message_id="late"),
            object(),
        ))
    assert exc.value.status_code == 409
    assert writes == []
    registry.clear()
