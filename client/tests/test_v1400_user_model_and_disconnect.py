from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.requests import ClientDisconnect


def test_ordinary_user_can_test_personal_model_without_admin(monkeypatch):
    from hashmm.api.routes import models_user as route
    from hashmm.api import model_manager

    seen = {}
    monkeypatch.setattr(
        route, "require_auth",
        lambda _request: {"uid": "uid-user", "sub": "user@example.com", "role": "user"},
    )
    monkeypatch.setattr(
        route,
        "normalize_model_config",
        lambda value: {"provider_spec": SimpleNamespace(auth="required")},
    )
    monkeypatch.setattr(
        model_manager,
        "test_model_connection",
        lambda config: seen.update(config) or {"ok": True, "latency_ms": 12},
    )

    request = route.MyModelCreate(
        name="mine", provider="openai", base_url="https://api.example.test/v1",
        api_key="secret-not-logged", model_name="example-model",
    )
    result = asyncio.run(route.test_my_model(request, SimpleNamespace()))

    assert result == {"ok": True, "latency_ms": 12}
    assert seen["api_key"] == "secret-not-logged"


def test_client_disconnect_is_not_reported_as_server_error():
    from hashmm.api import server

    request = SimpleNamespace(
        state=SimpleNamespace(request_id="req-499"),
        method="POST",
        url=SimpleNamespace(path="/api/dispatch/runners/heartbeat"),
    )
    response = asyncio.run(server._global_error_handler(request, ClientDisconnect()))

    assert response.status_code == 499
    assert b'"code":499' in response.body
