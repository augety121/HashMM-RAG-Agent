from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.requests import ClientDisconnect

from hashmm.agent import dispatch


def test_runner_presence_is_account_and_device_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    task = dispatch.create_task("desktop", "browser_use", {"goal": "read"}, "alice@example.com")

    claimed = dispatch.poll(
        "desktop",
        created_by="alice@example.com",
        presence_owner="uid-alice",
        device_id="desktop-a",
        display_name="Alice PC",
        app_version="1.2.3",
    )

    assert claimed and claimed["task_id"] == task
    assert dispatch.runners_status(owner="uid-bob", created_by="bob@example.com") == []
    visible = dispatch.runners_status(owner="uid-alice", created_by="alice@example.com")
    assert len(visible) == 1
    assert visible[0]["device_id"] == "desktop-a"
    assert visible[0]["name"] == "Alice PC"
    assert visible[0]["runner"] == "desktop"
    assert visible[0]["version"] == "1.2.3"
    assert visible[0]["online"] is True


def test_runners_route_never_returns_another_accounts_presence(monkeypatch):
    from hashmm.api.routes import dispatch as route

    captured = {}
    monkeypatch.setattr(
        route,
        "require_auth",
        lambda _request: {"uid": "uid-alice", "sub": "alice@example.com", "role": "admin"},
    )

    def fake_status(*, owner, created_by):
        captured.update(owner=owner, created_by=created_by)
        return []

    monkeypatch.setattr(route.dq, "runners_status", fake_status)
    result = asyncio.run(route.runners(SimpleNamespace()))
    assert result == {"runners": []}
    assert captured == {"owner": "uid-alice", "created_by": "alice@example.com"}


def test_admin_poll_is_still_limited_to_its_own_queue(monkeypatch):
    from hashmm.api.routes import dispatch as route

    captured = {}
    monkeypatch.setattr(
        route,
        "require_auth",
        lambda _request: {"uid": "uid-admin", "sub": "admin@example.com", "role": "admin"},
    )

    def fake_poll(runner, created_by, **presence):
        captured.update(runner=runner, created_by=created_by, **presence)
        return None

    monkeypatch.setattr(route.dq, "poll", fake_poll)
    result = asyncio.run(route.poll(
        SimpleNamespace(),
        runner="desktop",
        device_id="pc-1",
        device_name="Office PC",
        app_version="1.0",
    ))
    assert result == {"task": None}
    assert captured["created_by"] == "admin@example.com"
    assert captured["presence_owner"] == "uid-admin"
    assert captured["device_id"] == "pc-1"


def test_presence_heartbeat_does_not_claim_a_task(monkeypatch):
    from hashmm.api.routes import dispatch as route

    captured = {}
    monkeypatch.setattr(
        route, "require_auth",
        lambda _request: {"uid": "uid-alice", "sub": "alice@example.com", "role": "user"},
    )
    monkeypatch.setattr(
        route.dq, "touch_runner",
        lambda **kwargs: captured.update(kwargs) is None,
    )

    class _Request:
        headers = {"content-length": "86"}
        query_params = {}

        async def json(self):
            return {
                "runner": "desktop", "device_id": "pc-stable-id",
                "device_name": "Alice PC", "app_version": "3.7.9",
            }

    result = asyncio.run(route.runner_heartbeat(_Request()))
    assert result["ok"] is True
    assert result["online_ttl_seconds"] == 35
    assert captured == {
        "owner": "uid-alice", "device_id": "pc-stable-id", "runner": "desktop",
        "display_name": "Alice PC", "app_version": "3.7.9",
    }


def test_presence_heartbeat_without_body_uses_query_and_never_reads_json(monkeypatch):
    from hashmm.api.routes import dispatch as route

    captured = {}
    monkeypatch.setattr(
        route, "require_auth",
        lambda _request: {"uid": "uid-alice", "sub": "alice@example.com", "role": "user"},
    )
    monkeypatch.setattr(route.dq, "touch_runner", lambda **kwargs: not captured.update(kwargs))

    class _Request:
        headers = {}
        query_params = {
            "runner": "desktop", "device_id": "stable-device",
            "device_name": "Alice PC", "app_version": "5.0.0",
        }

        async def json(self):
            raise AssertionError("a zero-length heartbeat must not read a request body")

    result = asyncio.run(route.runner_heartbeat(_Request()))
    assert result["ok"] is True
    assert captured["owner"] == "uid-alice"
    assert captured["device_id"] == "stable-device"


def test_disconnected_legacy_heartbeat_does_not_raise_asgi_error(monkeypatch):
    from hashmm.api.routes import dispatch as route

    monkeypatch.setattr(
        route, "require_auth",
        lambda _request: {"uid": "uid-alice", "sub": "alice@example.com", "role": "user"},
    )

    class _Request:
        headers = {"content-length": "80"}
        query_params = {}

        async def json(self):
            raise ClientDisconnect()

    result = asyncio.run(route.runner_heartbeat(_Request()))
    assert result == {"ok": False, "disconnected": True}


def test_remote_status_merges_runner_and_host_by_owner_and_device(monkeypatch):
    from hashmm.api.routes import remote_signal as route

    monkeypatch.setattr(
        route, "get_current_user",
        lambda _request: {"uid": "uid-alice", "sub": "alice@example.com"},
    )
    monkeypatch.setattr(
        route.hub, "hosts",
        lambda uid: [SimpleNamespace(
            id="conn-1", device_id="stable-device", name="Alice PC", platform="Win32",
        )] if uid == "uid-alice" else [],
    )
    captured = {}

    def fake_runners_status(*, owner, created_by):
        captured.update(owner=owner, created_by=created_by)
        return [{
            "device_id": "stable-device", "name": "Alice PC", "runner": "desktop",
            "version": "5.0.0", "online": True, "last_seen": 123.0,
        }]

    monkeypatch.setattr(route.dispatch_queue, "runners_status", fake_runners_status)
    result = asyncio.run(route.remote_status(SimpleNamespace()))

    assert captured == {"owner": "uid-alice", "created_by": "alice@example.com"}
    assert result["count"] == 1
    assert result["desktop_count"] == 1
    assert result["devices"] == [{
        "device_id": "stable-device", "connection_id": "conn-1", "name": "Alice PC",
        "platform": "Win32", "app_version": "5.0.0", "online": True,
        "remote_ready": True, "capabilities": ["dispatch", "remote_view", "remote_control"],
        "last_seen": 123.0,
    }]
