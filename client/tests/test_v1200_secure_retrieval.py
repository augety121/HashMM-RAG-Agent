from __future__ import annotations

import uuid
import asyncio
import threading

import pytest

pytestmark = pytest.mark.unit


def test_retrieval_run_is_durable_owner_scoped_and_corroboration_is_bounded():
    from hashmm.retrieval_fabric import RetrievalFabric, SearchRequest
    from hashmm.retrieval_fabric import store

    def fake(provider, owner, query, count, options):
        assert owner.startswith("v1200-owner-")
        return [{"title": "同一份官方公告", "url": "https://example.com/news?utm_source=test",
                 "snippet": f"由 {provider} 返回的摘要", "published_at": "2026-08-08T00:00:00Z"}]

    owner = "v1200-owner-" + uuid.uuid4().hex
    other = "v1200-other-" + uuid.uuid4().hex
    result = RetrievalFabric(provider_search=fake).run(owner, SearchRequest(
        query="检索基座测试", mode="verified", providers=["brave", "baidu"], max_results=5,
    ))
    assert result["state"] == "completed"
    assert store.get_run(result["id"], other) is None
    evidence = result["result"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["url"] == "https://example.com/news"
    assert evidence[0]["verification_status"] == "corroborated_listing"
    assert result["result"]["disclaimer"].startswith("互证状态")
    events = store.list_events(result["id"], owner)
    assert any(item["event_type"] == "provider_completed" for item in events)


def test_retrieval_provider_failure_yields_partial_without_leaking_exception():
    from hashmm.retrieval_fabric import RetrievalFabric, SearchRequest

    def fake(provider, owner, query, count, options):
        if provider == "exa":
            raise RuntimeError("secret-token-should-not-leak 429")
        return [{"title": "Result", "url": "https://example.org/a", "snippet": "Evidence"}]

    result = RetrievalFabric(provider_search=fake).run(
        "v1200-partial-" + uuid.uuid4().hex,
        SearchRequest(query="partial", providers=["brave", "exa"]),
    )
    assert result["state"] == "partial"
    dumped = str(result)
    assert "secret-token-should-not-leak" not in dumped
    assert result["result"]["provider_failures"] == [{"provider": "exa", "code": "rate_limited"}]


def test_direct_model_credential_sync_is_permanently_disabled(monkeypatch):
    from hashmm.api import supabase_sync
    calls = []
    monkeypatch.setattr(supabase_sync, "_bg", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert supabase_sync.push_direct_llm({
        "base_url": "https://model.example/v1", "model_name": "m", "api_key": "sk-secret",
    }) is None
    assert calls == []


def test_supabase_issuer_is_checked_when_requested():
    import time
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from hashmm.api.supabase_auth import decode_with_key

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    claims = {"sub": "u", "aud": "authenticated", "iss": "https://tenant.supabase.co/auth/v1",
              "exp": int(time.time()) + 60}
    token = jwt.encode(claims, private, algorithm="ES256")
    assert decode_with_key(token, public, issuer=claims["iss"])
    assert decode_with_key(token, public, issuer="https://other.supabase.co/auth/v1") is None


def test_search_api_background_returns_durable_run_before_execution(monkeypatch):
    from hashmm.api.routes import retrieval_fabric as route

    executed = threading.Event()

    class FakeFabric:
        def create(self, owner, spec):
            assert owner == "owner-v1200"
            return {"id": "sr_background", "state": "queued"}

        def execute(self, run_id, owner, spec):
            assert (run_id, owner) == ("sr_background", "owner-v1200")
            executed.set()
            return {"id": run_id, "state": "completed"}

    class FakeRequest:
        async def json(self):
            return {"query": "background evidence", "background": True}

    fake = FakeFabric()
    monkeypatch.setattr(route, "_identity", lambda request: ({"sub": "user"}, "owner-v1200"))
    monkeypatch.setattr(route, "get_retrieval_fabric", lambda: fake)
    monkeypatch.setattr(route.db, "audit", lambda *args: None)

    async def scenario():
        result = await route.create_search_run(FakeRequest())
        assert result == {"id": "sr_background", "state": "queued"}
        assert await asyncio.to_thread(executed.wait, 2)

    asyncio.run(scenario())
