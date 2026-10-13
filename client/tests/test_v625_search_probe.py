from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException


def _load_route():
    """Load the route module without importing the aggregate routes package."""
    path = Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "search_integrations.py"
    spec = importlib.util.spec_from_file_location("hashmm_v625_search_integrations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_search_probe_uses_the_same_owner_scoped_adapter_as_chat(monkeypatch):
    from hashmm.retrieval_fabric import providers
    route = _load_route()

    observed: dict[str, object] = {}
    monkeypatch.setattr(route, "_owner", lambda _request: ({"sub": "alice"}, "owner-alice"))
    monkeypatch.setattr(
        route,
        "get_search_integration",
        lambda owner, provider: {
            "configured": owner == "owner-alice" and provider == "doubao",
            "enabled": True,
        },
    )
    monkeypatch.setattr(route.db, "audit", lambda *args: observed.setdefault("audit", args))

    def fake_search(provider, owner, query, count, options):
        observed.update(provider=provider, owner=owner, query=query, count=count, options=options)
        return [{"title": "result"}]

    monkeypatch.setattr(providers, "search", fake_search)
    result = asyncio.run(route.test_search_integration("doubao", object()))

    assert result["ok"] is True
    assert result["result_count"] == 1
    assert observed["provider"] == "doubao"
    assert observed["owner"] == "owner-alice"
    assert "api_key" not in repr(result)


def test_search_probe_requires_a_saved_enabled_integration(monkeypatch):
    route = _load_route()

    monkeypatch.setattr(route, "_owner", lambda _request: ({"sub": "alice"}, "owner-alice"))
    monkeypatch.setattr(route, "get_search_integration", lambda *_args: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.test_search_integration("doubao", object()))
    assert exc.value.status_code == 409
    assert "保存并启用" in str(exc.value.detail)
