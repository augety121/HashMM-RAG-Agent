import asyncio

import pytest
from fastapi import HTTPException


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _isolate_remote_sync(monkeypatch):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)


def test_computer_task_creates_owned_conversation_message_and_persisted_request(monkeypatch):
    from hashmm.api.routes import conversations as routes

    _isolate_remote_sync(monkeypatch)
    created = []
    messages = []
    requests = []
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "user-1", "role": "user"})
    monkeypatch.setattr(
        routes,
        "require_conv_access_or_create",
        lambda request, conv_id, title: created.append((request, conv_id, title)),
    )
    monkeypatch.setattr(routes.db, "create_message", lambda conv_id, role, content: messages.append((conv_id, role, content)))
    monkeypatch.setattr(
        routes.db,
        "create_file_request",
        lambda req_id, user_id, conv_id, query, target="desktop": requests.append(
            (req_id, user_id, conv_id, query, target)
        ),
    )

    request = _Request({"task": "调研产品并给出来源", "kind": "browser_use"})
    result = asyncio.run(routes.computer_task_from_app("conv-1", request))

    assert created == [(request, "conv-1", "电脑任务")]
    assert messages == [("conv-1", "user", "【浏览器调研】调研产品并给出来源")]
    assert requests[0][1:] == ("user-1", "conv-1", "[[AGENT]] 调研产品并给出来源", "desktop")
    assert result["ok"] is True
    assert result["request_id"] == requests[0][0]
    assert result["status"] == "pending"
    assert result["kind"] == "browser_use"
    assert result["wire_kind"] == "agent"


def test_computer_use_public_kind_keeps_legacy_runner_prefix(monkeypatch):
    from hashmm.api.routes import conversations as routes

    _isolate_remote_sync(monkeypatch)
    requests = []
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "user-1", "role": "user"})
    monkeypatch.setattr(routes, "require_conv_access_or_create", lambda *_args: None)
    monkeypatch.setattr(routes.db, "create_message", lambda *_args: None)
    monkeypatch.setattr(
        routes.db,
        "create_file_request",
        lambda req_id, user_id, conv_id, query, target="desktop": requests.append(
            (req_id, user_id, conv_id, query, target)
        ),
    )

    result = asyncio.run(routes.computer_task_from_app(
        "conv-1", _Request({"task": "列出下载目录", "kind": "computer_use"})
    ))

    assert requests[0][3] == "[[CU]] 列出下载目录"
    assert result["kind"] == "computer_use"
    assert result["wire_kind"] == "cu"


def test_computer_request_status_is_owner_checked(monkeypatch):
    from hashmm.api.routes import conversations as routes

    checked = []
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "user-1", "role": "user"})
    monkeypatch.setattr(routes, "require_conv_access", lambda request, conv_id: checked.append((request, conv_id)))
    monkeypatch.setattr(
        routes.db,
        "list_file_requests_for_conversation",
        lambda uid, conv_id, limit: [{"id": "r1", "status": "processing"}],
    )
    request = _Request({})

    result = asyncio.run(routes.conversation_computer_requests("conv-1", request, limit=10))

    assert checked == [(request, "conv-1")]
    assert result == {"requests": [{"id": "r1", "status": "processing"}]}


def test_computer_request_patch_is_scoped_to_authenticated_owner(monkeypatch):
    from hashmm.api.routes import conversations as routes

    calls = []
    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "user-1", "role": "user"})
    monkeypatch.setattr(
        routes.db,
        "update_file_request",
        lambda req_id, uid, status: calls.append((req_id, uid, status)) or True,
    )

    result = asyncio.run(routes.patch_file_request("request-1", _Request({"status": "processing"})))

    assert calls == [("request-1", "user-1", "processing")]
    assert result == {"ok": True}


def test_computer_request_patch_hides_missing_or_foreign_request(monkeypatch):
    from hashmm.api.routes import conversations as routes

    monkeypatch.setattr(routes, "get_current_user", lambda _request: {"uid": "user-1", "role": "user"})
    monkeypatch.setattr(routes.db, "update_file_request", lambda _req_id, _uid, _status: False)

    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.patch_file_request("foreign-request", _Request({"status": "done"})))

    assert error.value.status_code == 404
