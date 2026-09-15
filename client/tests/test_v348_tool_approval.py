"""V348: durable, exact, owner-bound and one-shot Agent tool approvals."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def _seed(db, suffix: str = "a"):
    user = db.create_user(f"approval-{suffix}", "pw12345678", "审批用户", "user")
    conv_id = f"approval-conv-{suffix}"
    db.create_conversation(conv_id, user["id"], "长任务审批")
    message_id = db.create_message(conv_id, "assistant", "等待审批", status="streaming")
    return user, conv_id, message_id


def test_approval_lifecycle_is_exact_one_shot_and_cross_device_visible(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent.permissions import approval_fingerprint

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db)
    args = {"command": "echo safe", "api_key": "must-not-leak"}
    cwd = str(db.conv_files_dir(conv_id))
    request = db.create_tool_approval_request(
        user_id=user["id"], conv_id=conv_id, message_id=message_id,
        fingerprint=approval_fingerprint("run_shell", args, cwd),
        tool_name="run_shell", arguments=args, cwd=cwd,
        reason="需要明确批准或管理员权限", risk="system",
    )

    public = db.public_tool_approval(request)
    assert public["arguments"]["command"] == "echo safe"
    assert public["arguments"]["api_key"] == "[已隐藏敏感值]"
    persisted = next(m for m in db.get_messages(conv_id) if m["id"] == message_id)
    assert persisted["status"] == "waiting_approval"
    assert persisted["run_manifest"]["approval_request"]["request_id"] == request["id"]
    assert db.get_active_chats(user["id"])[0]["status"] == "waiting_approval"

    decided = db.decide_tool_approval(
        request_id=request["id"], conv_id=conv_id,
        actor_user_id=user["id"], approve=True,
    )
    assert decided["status"] == "approved"
    # Different args do not consume the decision.
    assert db.consume_tool_approval(
        user_id=user["id"], conv_id=conv_id,
        fingerprint=approval_fingerprint("run_shell", {"command": "echo changed"}, cwd),
    ) is None
    consumed = db.consume_tool_approval(
        user_id=user["id"], conv_id=conv_id,
        fingerprint=approval_fingerprint("run_shell", args, cwd),
    )
    assert consumed["status"] == "consumed"
    assert db.consume_tool_approval(
        user_id=user["id"], conv_id=conv_id,
        fingerprint=approval_fingerprint("run_shell", args, cwd),
    ) is None
    resolved = next(m for m in db.get_messages(conv_id) if m["id"] == message_id)
    assert resolved["status"] == "resolved"
    assert resolved["run_manifest"]["approval_request"]["status"] == "consumed"


def test_only_owner_or_admin_can_decide(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent.permissions import approval_fingerprint

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    owner, conv_id, message_id = _seed(db, "owner")
    other = db.create_user("approval-other", "pw12345678", "其他用户", "user")
    args = {"command": "pwd"}
    request = db.create_tool_approval_request(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        fingerprint=approval_fingerprint("run_shell", args, ""),
        tool_name="run_shell", arguments=args, reason="需要批准",
    )
    assert db.decide_tool_approval(
        request_id=request["id"], conv_id=conv_id,
        actor_user_id=other["id"], approve=True,
    ) is None
    row = db.decide_tool_approval(
        request_id=request["id"], conv_id=conv_id,
        actor_user_id=owner["id"], approve=False,
    )
    assert row["status"] == "declined"


def test_permission_system_consumes_durable_approval_once(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent.permissions import PermissionSystem, approval_fingerprint

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "strict")
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db, "permission")
    args = {"command": "echo approved"}
    cwd = str(db.conv_files_dir(conv_id))
    request = db.create_tool_approval_request(
        user_id=user["id"], conv_id=conv_id, message_id=message_id,
        fingerprint=approval_fingerprint("run_shell", args, cwd),
        tool_name="run_shell", arguments=args, cwd=cwd, reason="需要批准",
    )
    db.decide_tool_approval(
        request_id=request["id"], conv_id=conv_id,
        actor_user_id=user["id"], approve=True,
    )
    permissions = PermissionSystem()
    assert permissions.check("run_shell", args, user["id"], cwd=cwd, conv_id=conv_id)[0] is True
    assert permissions.check("run_shell", args, user["id"], cwd=cwd, conv_id=conv_id)[0] is False


def test_dispatch_pauses_without_executing_denied_tool(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent.loop import AgentLoop
    from hashmm.agent.permissions import PermissionSystem
    from hashmm.agent.tool_pipeline import TurnState

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "strict")
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db, "dispatch")
    loop = AgentLoop(None, user_id=user["id"], conv_id=conv_id)
    loop.permissions = PermissionSystem()
    loop._approval_message_id = message_id
    executed = []

    async def _must_not_execute(*_args, **_kwargs):
        executed.append(True)
        raise AssertionError("denied tool executed before approval")

    loop._execute_tool = _must_not_execute
    tc = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="run_shell", arguments=json.dumps({"command": "pwd"})),
    )
    rec = SimpleNamespace(add=lambda *_a, **_kw: None)

    async def collect():
        return [event async for event in loop._dispatch_tool_call(
            tc, iteration=1, turn=TurnState(), messages=[], files_generated=[],
            prefetched={}, user_id=user["id"], rec=rec,
        )]

    events = asyncio.run(collect())
    assert executed == []
    assert any(kind == "approval_request" for kind, _ in events)
    assert db.get_active_chats(user["id"])[0]["status"] == "waiting_approval"


def test_dispatch_resumes_same_call_after_approval_exactly_once(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent.loop import AgentLoop
    from hashmm.agent.permissions import PermissionSystem
    from hashmm.agent.tool_pipeline import TurnState

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "strict")
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db, "resume")
    loop = AgentLoop(
        None,
        user_id=user["id"],
        conv_id=conv_id,
        approval_wait_seconds=2,
        approval_poll_seconds=0.01,
    )
    loop.permissions = PermissionSystem()
    loop._approval_message_id = message_id
    executed = []

    async def _execute(name, arguments, actor_user_id):
        executed.append((name, dict(arguments), actor_user_id))
        return {"status": "ok", "stdout": "approved"}

    loop._execute_tool = _execute
    tc = SimpleNamespace(
        id="call-resume",
        function=SimpleNamespace(
            name="run_shell", arguments=json.dumps({"command": "pwd"})
        ),
    )
    rec = SimpleNamespace(add=lambda *_a, **_kw: None)

    async def scenario():
        generator = loop._dispatch_tool_call(
            tc, iteration=1, turn=TurnState(), messages=[], files_generated=[],
            prefetched={}, user_id=user["id"], rec=rec,
        )
        events = []
        while True:
            event = await anext(generator)
            events.append(event)
            if event[0] == "approval_request":
                break
        assert executed == []
        request_id = events[-1][1]["request_id"]
        approved = db.decide_tool_approval(
            request_id=request_id,
            conv_id=conv_id,
            actor_user_id=user["id"],
            approve=True,
        )
        assert approved and approved["status"] == "approved"
        async for event in generator:
            events.append(event)
        return request_id, events

    request_id, events = asyncio.run(scenario())
    assert executed == [("run_shell", {"command": "pwd"}, user["id"])]
    row = db.get_tool_approval_request(
        request_id=request_id,
        conv_id=conv_id,
        actor_user_id=user["id"],
    )
    assert row and row["status"] == "consumed"
    tool_done = next(payload for kind, payload in events if kind == "tool_done")
    assert tool_done["status"] == "done"
    assert tool_done["receipt"]["permission"] == {
        "decision": "approved",
        "authority": "owner_approval",
        "approval_ref": request_id,
    }


def test_http_decision_endpoint_does_not_enumerate_other_users_requests(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api import auth, supabase_sync
    from hashmm.api.routes.conversations import router
    from hashmm.agent.permissions import approval_fingerprint

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    owner, conv_id, message_id = _seed(db, "http-owner")
    other = db.create_user("approval-http-other", "pw12345678", "其他用户", "user")
    args = {"command": "whoami"}
    request = db.create_tool_approval_request(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        fingerprint=approval_fingerprint("run_shell", args, ""),
        tool_name="run_shell", arguments=args, reason="需要批准",
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    owner_token = auth.create_token(owner["id"], owner["username"], "user")
    other_token = auth.create_token(other["id"], other["username"], "user")
    path = f"/api/conversations/{conv_id}/tool-approvals/{request['id']}"

    denied = client.post(path, json={"decision": "approve"},
                         headers={"Authorization": f"Bearer {other_token}"})
    assert denied.status_code == 404
    allowed = client.post(path, json={"decision": "approve"},
                          headers={"Authorization": f"Bearer {owner_token}"})
    assert allowed.status_code == 200
    assert allowed.json()["approval_request"]["status"] == "approved"
