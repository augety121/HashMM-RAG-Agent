import asyncio
import threading
import time
import pytest


def _conversation(conv_id="conv-1"):
    return {
        "id": conv_id,
        "user_id": "sb_user-1",
        "title": "one",
        "pinned": 0,
        "updated_at": 10.0,
        "last_activity_at": 10.0,
    }


def test_conversation_list_read_never_pulls_or_backfills_supabase(monkeypatch):
    from hashmm.api.routes import conversations as routes
    from hashmm.api import supabase_sync

    monkeypatch.setattr(routes, "get_current_user", lambda request: {"uid": "sb_user-1"})
    monkeypatch.setattr(routes.db, "list_conversations", lambda *a, **k: [_conversation()])
    monkeypatch.setattr(supabase_sync, "enabled", lambda: True)
    monkeypatch.setattr(
        supabase_sync, "pull_conversations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read contacted Supabase")),
    )
    monkeypatch.setattr(
        supabase_sync, "backfill_conversations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read wrote Supabase")),
    )

    result = asyncio.run(routes.list_convs(object()))
    assert [row["id"] for row in result["conversations"]] == ["conv-1"]


def test_jwks_verified_user_does_not_require_remote_user_lookup(monkeypatch):
    from hashmm.api import supabase_auth

    token = "v1600.local.jwt"
    supabase_auth._verify_cache.clear()
    monkeypatch.setattr(supabase_auth, "enabled", lambda: True)
    monkeypatch.setattr(
        supabase_auth,
        "_verify_jwks",
        lambda value: {
            "sub": "user-1",
            "email": "person@example.com",
            "role": "authenticated",
            "app_metadata": {},
        },
    )
    monkeypatch.setattr(
        supabase_auth, "_verify_remote",
        lambda value: (_ for _ in ()).throw(AssertionError("remote lookup called")),
    )

    user = supabase_auth.verify_token(token)
    assert user and user["uid"] == "sb_user-1"
    assert user["role"] == "user"


def test_supabase_mirror_coalesces_duplicate_pending_writes(monkeypatch):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: True)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow(row):
        calls.append(row["id"])
        entered.set()
        release.wait(2)

    row = {"id": "same-entity"}
    supabase_sync._bg(slow, row)
    assert entered.wait(1)
    supabase_sync._bg(slow, row)
    time.sleep(0.05)
    release.set()
    time.sleep(0.05)
    assert calls == ["same-entity"]


def test_fast_liveness_and_readiness_do_not_probe_dependencies(monkeypatch):
    from starlette.responses import Response
    from hashmm.api.core.services import ServiceRegistry
    from hashmm.api.routes import system

    response = Response()
    assert asyncio.run(system.liveness_check(response)) == {"status": "alive"}
    monkeypatch.setattr(ServiceRegistry, "status", "ready")
    response = Response()
    result = asyncio.run(system.fast_readiness_check(response))
    assert response.status_code == 200
    assert result["ready"] is True


def test_identity_verification_runs_off_event_loop(monkeypatch):
    from hashmm.api import auth
    from hashmm.api.middleware import AsyncIdentityMiddleware

    loop_thread = threading.get_ident()
    verifier_threads = []

    def verify(token):
        verifier_threads.append(threading.get_ident())
        return {"uid": "sb_user-1", "role": "user"}

    monkeypatch.setattr(auth, "verify_any_token", verify)
    captured = {}

    async def inner(scope, receive, send):
        captured.update(scope["state"])

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/conversations",
        "raw_path": b"/api/conversations",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer token")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 6006),
        "scheme": "http",
        "http_version": "1.1",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(AsyncIdentityMiddleware(inner)(scope, receive, send))
    assert captured["auth_user"]["uid"] == "sb_user-1"
    assert verifier_threads and verifier_threads[0] != loop_thread


def test_client_message_id_is_idempotent_and_collision_safe():
    from hashmm.api import database as db

    conv_id = "v1600-idempotent-conv"
    db.create_conversation(conv_id, "title", "sb_user-1")
    first = db.create_message(
        conv_id, "user", "hello", message_id="phone-message-1",
    )
    second = db.create_message(
        conv_id, "user", "hello", message_id="phone-message-1",
    )
    assert first == second == "phone-message-1"
    assert db.count_messages(conv_id) == 1
    with pytest.raises(ValueError, match="message_id_collision"):
        db.create_message(
            conv_id, "user", "different", message_id="phone-message-1",
        )
