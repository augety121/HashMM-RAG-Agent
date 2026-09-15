from __future__ import annotations

import asyncio
import base64
import json


def test_production_identity_settings_ignore_database_override(monkeypatch):
    from hashmm.api import settings_store, supabase_auth

    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv("HASHMM_SUPABASE_URL", "https://environment-project.supabase.co")
    monkeypatch.setattr(
        settings_store,
        "get_setting",
        lambda *_args, **_kwargs: "https://stale-database-project.supabase.co",
    )

    assert supabase_auth.supabase_url() == "https://environment-project.supabase.co"


def test_identity_contract_is_public_and_project_scoped(monkeypatch):
    from hashmm.api import supabase_auth
    from hashmm.api.routes import auth

    monkeypatch.setattr(
        supabase_auth,
        "supabase_url",
        lambda: "https://project-ref-123.supabase.co",
    )
    monkeypatch.setattr(supabase_auth, "publishable_key", lambda: "public-client-key")
    monkeypatch.setenv("HASHMM_PUBLIC_URL", "https://hashmm.example.test")

    response = asyncio.run(auth.identity_contract())
    payload = bytes(response.body).decode("utf-8")

    assert response.headers["cache-control"].startswith("no-store")
    assert '"project_ref":"project-ref-123"' in payload
    assert '"backend_url":"https://hashmm.example.test"' in payload
    assert "public-client-key" not in payload
    assert "service_role" not in payload
    assert "secret" not in payload.lower()


def test_launcher_compares_public_project_references_without_exposing_key():
    from scripts import hashmm_launcher as launcher

    def part(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    service_key = f"{part({'alg': 'none'})}.{part({'ref': 'project-ref-123'})}.signature"
    assert launcher._supabase_project_ref("https://project-ref-123.supabase.co") == "project-ref-123"
    assert launcher._jwt_project_ref(service_key) == "project-ref-123"
    assert launcher._jwt_project_ref("not-a-token") == ""


def test_loop_admission_is_fair_per_owner(monkeypatch):
    from hashmm.agent import loop_engine

    monkeypatch.setenv("HASHMM_LOOP_GLOBAL_CONCURRENCY", "12")
    monkeypatch.setenv("HASHMM_LOOP_OWNER_CONCURRENCY", "2")
    monkeypatch.setattr(loop_engine, "_LOOPS", {
        "a1": {"status": "running", "user": "alice"},
        "a2": {"status": "queued", "user": "alice"},
        "b1": {"status": "running", "user": "bob"},
    })

    assert "当前账号" in loop_engine._admission_error("alice")
    assert loop_engine._admission_error("bob") == ""
    assert loop_engine._admission_error("carol") == ""
