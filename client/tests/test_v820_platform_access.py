"""V820 regressions for the durable API access-control plane."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import time

import pytest


def _isolated_db(tmp_db, monkeypatch):
    from hashmm.api import database as db
    db._close_pool()
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_db))
    db.init_db()
    return db


def test_key_secret_is_one_time_owner_scoped_and_revision_guarded(tmp_db, monkeypatch):
    db = _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_access as access

    created = access.create_key("alice", {
        "name": "production", "scopes": ["models:read", "responses:write"],
        "allowed_models": ["model-a"], "quota_limit": 2.0,
    })
    secret = created["secret"]
    assert secret.startswith("hmm_v2_")
    assert "secret" not in access.list_keys("alice")[0]
    assert access.get_key("bob", created["id"]) is None
    with db._conn() as conn:
        stored = dict(conn.execute(
            "SELECT * FROM api_access_keys WHERE id=?", (created["id"],)
        ).fetchone())
    assert secret not in json.dumps(stored, ensure_ascii=False)
    assert len(stored["secret_hash"]) == 64

    updated = access.update_key(
        "alice", created["id"], {"name": "production-2"}, expected_revision=1,
    )
    assert updated and updated["revision"] == 2
    with pytest.raises(access.AccessError) as conflict:
        access.update_key(
            "alice", created["id"], {"name": "stale"}, expected_revision=1,
        )
    assert conflict.value.code == "revision_conflict"
    assert access.update_key(
        "bob", created["id"], {"name": "forbidden"}, expected_revision=2,
    ) is None


def test_auth_scope_resource_ip_expiry_and_immediate_revoke(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_access as access

    created = access.create_key("alice", {
        "name": "restricted", "scopes": ["models:read"],
        "allowed_models": ["model-a"], "allowed_projects": ["project-a"],
        "ip_allowlist": ["127.0.0.0/8"], "ip_denylist": ["127.0.0.2/32"],
        "expires_at": time.time() + 3600,
    })
    context = access.authenticate(created["secret"], source_ip="127.0.0.1")
    assert context and context.owner_id == "alice"
    access.require_scope(context, "models:read")
    access.require_resources(context, model="model-a", project_id="project-a")
    with pytest.raises(access.AccessError) as scope:
        access.require_scope(context, "responses:write")
    assert scope.value.code == "insufficient_scope"
    with pytest.raises(access.AccessError) as model:
        access.require_resources(context, model="model-b")
    assert model.value.code == "resource_not_allowed"
    with pytest.raises(access.AccessError) as denied:
        access.authenticate(created["secret"], source_ip="127.0.0.2")
    assert denied.value.code == "resource_not_allowed"

    revoked = access.revoke_key("alice", created["id"])
    assert revoked and revoked["status"] == "revoked"
    with pytest.raises(access.AccessError) as invalid:
        access.authenticate(created["secret"], source_ip="127.0.0.1")
    assert invalid.value.code == "invalid_api_key"


def test_atomic_rpm_concurrency_and_quota_do_not_oversubscribe(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_access as access

    created = access.create_key("alice", {
        "name": "bounded", "scopes": ["responses:write"],
        "rpm_limit": 3, "concurrent_limit": 2, "quota_limit": 1.0,
    })
    context = access.authenticate(created["secret"], source_ip="127.0.0.1")
    assert context is not None

    def rate(index: int):
        try:
            access.consume_rate_limit(context, now=1_800_000_001.0)
            return "ok"
        except access.AccessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=12) as pool:
        rates = list(pool.map(rate, range(18)))
    assert rates.count("ok") == 3
    assert set(rates) == {"ok", "rate_limit_exceeded"}

    def lease(index: int):
        try:
            value = access.acquire_lease(context, request_id=f"request-{index}")
            return value["id"] if value else "none"
        except access.AccessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=12) as pool:
        leases = list(pool.map(lease, range(12)))
    active_leases = [value for value in leases if value.startswith("lease_")]
    assert len(active_leases) == 2
    assert set(leases) <= set(active_leases) | {"concurrency_limit_exceeded"}
    for lease_id in active_leases:
        assert access.release_lease(context, lease_id)

    def reserve(index: int):
        try:
            value = access.reserve_quota(
                context, amount=0.6, idempotency_key=f"quota-{index}",
            )
            return value["id"] if value else "none"
        except access.AccessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        reservations = list(pool.map(reserve, range(10)))
    accepted = [value for value in reservations if value.startswith("keyreserve_")]
    assert len(accepted) == 1
    assert set(reservations) <= set(accepted) | {"quota_exceeded"}
    assert access.settle_quota(context, accepted[0], actual_amount=0.4)
    assert access.settle_quota(context, accepted[0], actual_amount=0.4)  # idempotent
    key = access.get_key("alice", created["id"])
    assert key and key["quota_used"] == pytest.approx(0.4)


def test_usage_is_attributed_to_key_without_losing_legacy_rows(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_access as access
    from hashmm.api import platform_protocol as protocol

    created = access.create_key("alice", {"name": "usage", "scopes": ["usage:read"]})
    protocol.settle_usage(
        "alice", reservation_id="", run_id="run-managed", step_id="answer",
        provider="hashmm", service="llm", model="model-a", quantity=12, unit="token",
        estimated_cost=0.2, currency="CNY", price_version="test",
        idempotency_key="managed-usage", access_key_id=created["id"],
    )
    protocol.settle_usage(
        "alice", reservation_id="", run_id="run-legacy", step_id="answer",
        provider="hashmm", service="llm", model="model-a", quantity=3, unit="token",
        estimated_cost=0.1, currency="CNY", price_version="test",
        idempotency_key="legacy-usage",
    )
    managed = access.usage_for_key("alice", created["id"])
    assert managed and managed["totals"]["events"] == 1
    assert managed["totals"]["quantity"] == pytest.approx(12)
    assert protocol.usage_summary("alice")["events"] == 2
    breakdown = access.access_key_breakdown("alice")
    assert {row["access_key_id"] for row in breakdown} == {created["id"], ""}


def test_management_and_public_routes_share_revocation_and_scope(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")
    monkeypatch.delenv("HASHMM_API_KEY", raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api.routes import platform_keys, public_api

    monkeypatch.setattr(platform_keys, "require_auth", lambda _request: {
        "uid": "alice", "username": "alice@example.test", "role": "user",
    })
    app = FastAPI()
    app.include_router(platform_keys.router)
    app.include_router(public_api.router)

    with TestClient(app) as client:
        made = client.post("/api/platform/keys", json={
            "name": "models-only", "scopes": ["models:read"],
            "allowed_models": ["hashmm-default"], "rpm_limit": 20,
        })
        assert made.status_code == 201
        key = made.json()
        secret = key["secret"]
        listed = client.get("/api/platform/keys").json()["data"]
        assert len(listed) == 1 and "secret" not in listed[0]

        auth = {"Authorization": f"Bearer {secret}"}
        models = client.get("/v1/models", headers=auth)
        assert models.status_code == 200
        assert models.headers["X-RateLimit-Limit"] == "20"
        denied = client.post(
            "/v1/threads", headers={**auth, "Idempotency-Key": "thread-one"}, json={"title": "x"},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "insufficient_scope"

        revoked = client.delete(f"/api/platform/keys/{key['id']}")
        assert revoked.status_code == 200
        invalid = client.get("/v1/models", headers=auth)
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "invalid_api_key"


def test_managed_response_settles_key_usage_and_failure_releases_admission(tmp_db, monkeypatch):
    db = _isolated_db(tmp_db, monkeypatch)
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")
    monkeypatch.delenv("HASHMM_API_KEY", raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api import platform_access as access
    from hashmm.api import platform_protocol as protocol
    from hashmm.api.routes import public_api

    created = access.create_key("alice", {
        "name": "agent", "scopes": ["responses:read", "responses:write"],
        "quota_limit": 1.0, "concurrent_limit": 1,
    })
    app = FastAPI()
    app.include_router(public_api.router)
    auth = {"Authorization": f"Bearer {created['secret']}"}
    monkeypatch.setattr(public_api, "_execute_text", lambda prompt, history, sources: f"answer: {prompt}")

    with TestClient(app) as client:
        success = client.post(
            "/v1/responses", headers={**auth, "Idempotency-Key": "managed-success"},
            json={"input": "evidence"},
        )
        assert success.status_code == 200
        assert success.json()["status"] == "completed"
        usage = access.usage_for_key("alice", created["id"])
        assert usage and usage["totals"]["events"] == 1
        assert access.get_key("alice", created["id"])["quota_used"] > 0
        with db._conn() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM api_key_leases WHERE key_id=? AND state='active'",
                (created["id"],),
            ).fetchone()[0]
        assert active == 0

        monkeypatch.setattr(
            public_api, "_execute_text",
            lambda *_args: (_ for _ in ()).throw(protocol.ProtocolError("llm_unavailable", "offline", 503)),
        )
        failed = client.post(
            "/v1/responses", headers={**auth, "Idempotency-Key": "managed-failure"},
            json={"input": "will fail"},
        )
        assert failed.status_code == 503
        with db._conn() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM api_key_leases WHERE key_id=? AND state='active'",
                (created["id"],),
            ).fetchone()[0]
            reserved = conn.execute(
                "SELECT COUNT(*) FROM api_key_quota_reservations WHERE key_id=? AND state='reserved'",
                (created["id"],),
            ).fetchone()[0]
            owner_reserved = conn.execute(
                "SELECT COUNT(*) FROM usage_reservations WHERE owner_id='alice' AND state='reserved'"
            ).fetchone()[0]
        assert (active, reserved, owner_reserved) == (0, 0, 0)
