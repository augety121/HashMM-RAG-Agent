from __future__ import annotations

import io
import json


def test_usage_overview_normalizes_admin_summary(monkeypatch):
    from hashmm.api import usage

    monkeypatch.setattr(usage, "summary", lambda days: {
        "days": days,
        "total_requests": 4,
        "total_tokens_in": 120,
        "total_tokens_out": 80,
        "total_cost": 0.25,
        "currency": "CNY",
        "by_model": [{"model": "m", "n": 4, "tok": 200, "cost": 0.25}],
        "by_user": [{"username": "alice", "n": 4, "tok": 200, "cost": 0.25}],
    })

    out = usage.overview_for({"uid": "admin", "role": "admin"}, 30)
    assert out["contract"] == "hashmm.usage-overview.v1"
    assert out["scope"] == "team"
    assert out["requests"] == 4
    assert out["tokens"] == 200
    assert out["by_model"][0]["requests"] == 4
    assert out["by_user"][0]["username"] == "alice"


def test_usage_overview_keeps_regular_user_private(monkeypatch):
    from hashmm.api import usage

    seen = {}
    def fake_user(uid, days):
        seen.update(uid=uid, days=days)
        return {"requests": 2, "tokens": 90, "tokens_in": 60, "tokens_out": 30,
                "cost": 0.1, "currency": "CNY", "by_model": []}
    monkeypatch.setattr(usage, "user_quota_used", fake_user)

    out = usage.overview_for({"uid": "sb_user", "role": "user"}, 9999)
    assert seen == {"uid": "sb_user", "days": 365}
    assert out["scope"] == "personal"
    assert out["by_user"] == []


def test_supabase_profile_rpc_uses_caller_token(monkeypatch):
    from hashmm.api import supabase_auth

    monkeypatch.setattr(supabase_auth, "supabase_url", lambda: "https://project.example")
    monkeypatch.setattr(supabase_auth, "publishable_key", lambda: "publishable")
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps([{"id": "u1", "email": "a@example.com"}]).encode()

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["apikey"] = req.headers.get("Apikey")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(supabase_auth.urllib.request, "urlopen", fake_urlopen)
    rows = supabase_auth.list_all_profiles("user-token")
    assert rows == [{"id": "u1", "email": "a@example.com"}]
    assert captured["url"].endswith("/rest/v1/rpc/list_all_profiles")
    assert captured["auth"] == "Bearer user-token"
    assert captured["apikey"] == "publishable"


def test_supabase_profile_rpc_distinguishes_failure_from_empty(monkeypatch):
    from hashmm.api import supabase_auth

    monkeypatch.setattr(supabase_auth, "supabase_url", lambda: "")
    monkeypatch.setattr(supabase_auth, "publishable_key", lambda: "")
    assert supabase_auth.list_all_profiles("token") is None


def test_admin_user_wire_normalizes_legacy_timestamp_and_allow_lists_fields():
    from hashmm.api.admin_contract import admin_user_wire

    row = admin_user_wire({
        "id": "local-1",
        "username": "legacy",
        "created_at": 1712345678.5,
        "password_hash": "must-never-leak",
    })

    assert row["created_at"] == "1712345678.5"
    assert row["identity_source"] == "local"
    assert row["role"] == "user"
    assert "password_hash" not in row
