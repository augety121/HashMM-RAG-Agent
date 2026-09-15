from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from hashmm.api import database as db
from hashmm.workspace_runtime import store
from hashmm.workspace_runtime.cloudflare_computer import (
    CloudflareComputerClient,
    CloudflareComputerConfig,
    CloudflareComputerError,
    build_workspace_handle,
)


pytestmark = pytest.mark.unit


def _config() -> CloudflareComputerConfig:
    return CloudflareComputerConfig(
        enabled=True,
        base_url="https://runtime.example.workers.dev",
        token="gateway-test-token",
        handle_secret="h" * 40,
        timeout_seconds=5,
    )


def test_workspace_handle_is_stable_opaque_and_owner_scoped():
    left = build_workspace_handle("owner-a", "personal", "s" * 40)
    assert left == build_workspace_handle("owner-a", "personal", "s" * 40)
    assert left.startswith("ws_")
    assert "owner" not in left and "personal" not in left
    assert left != build_workspace_handle("owner-b", "personal", "s" * 40)
    assert left != build_workspace_handle("owner-a", "project-1", "s" * 40)


def test_provider_configuration_never_projects_secret(monkeypatch):
    monkeypatch.setenv("HASHMM_CLOUDFLARE_COMPUTER_ENABLED", "1")
    monkeypatch.setenv("HASHMM_CLOUDFLARE_COMPUTER_URL", "https://example.workers.dev/internal/path")
    monkeypatch.setenv("HASHMM_CLOUDFLARE_COMPUTER_TOKEN", "do-not-leak")
    monkeypatch.setenv("HASHMM_WORKSPACE_HANDLE_SECRET", "x" * 40)
    public = CloudflareComputerConfig.from_env().public()
    assert public["configured"] is True
    assert public["endpoint"] == "https://example.workers.dev"
    assert "do-not-leak" not in str(public)
    assert "internal/path" not in str(public)


def test_provider_rejects_insecure_remote_url():
    config = CloudflareComputerConfig(True, "http://example.com", "token", "x" * 40, 5)
    assert "insecure_or_invalid_url" in config.problems()
    assert CloudflareComputerConfig(True, "http://127.0.0.1:8787", "token", "x" * 40, 5).configured


def test_direct_cloudflare_client_health_and_exec_contract():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/health":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={
                "protocol": "hashmm-cloud-workspace/1.0",
                "ready": True,
                "upstream": "@cloudflare/computer@0.1.0-alpha.1",
            })
        assert request.headers["authorization"] == "Bearer gateway-test-token"
        assert request.url.path.startswith("/v1/workspaces/ws_")
        return httpx.Response(200, json={"exitCode": 0, "stdout": "ok\n", "stderr": "", "truncated": False})

    client = CloudflareComputerClient(_config(), transport=httpx.MockTransport(handler))
    assert client.health()["ready"] is True
    result = client.exec("owner-a", "personal", ["ls", "-la"])
    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"
    assert len(seen) == 2


def test_client_errors_are_classified_without_upstream_body_leak():
    secret = "upstream-secret-must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=secret)

    client = CloudflareComputerClient(_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(CloudflareComputerError) as captured:
        client.exec("owner-a", "personal", ["ls"])
    assert captured.value.code == "upstream_rejected"
    assert secret not in str(captured.value)


def test_execution_receipts_are_idempotent_and_owner_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path) / "v1300.sqlite")
    request = {"run_id": "run-1", "argv": ["ls"], "cwd": "/workspace", "expected_revision": 1}
    first, created = store.create_or_get(
        "owner-a", "personal", "run-1", "cloudflare_computer_worker_shell", "idem-key-123", request,
    )
    replay, replay_created = store.create_or_get(
        "owner-a", "personal", "run-1", "cloudflare_computer_worker_shell", "idem-key-123", request,
    )
    assert created is True and replay_created is False
    assert replay["id"] == first["id"]
    assert store.get(first["id"], "owner-b", "personal") is None
    assert store.get(first["id"], "owner-a", "project-other") is None
    store.update(first["id"], "owner-a", "personal", "completed", result={"exit_code": 0})
    assert store.get(first["id"], "owner-a", "personal")["state"] == "completed"
    assert [event["payload"]["state"] for event in store.events(first["id"], "owner-a")] == ["queued", "completed"]


def test_unconfigured_provider_fails_before_creating_execution(monkeypatch):
    from hashmm.api.routes import workspace_runtime as route

    class FakeRequest:
        headers = {"idempotency-key": "idem-key-123"}

        async def json(self):
            return {"run_id": "run-1", "expected_revision": 1, "argv": ["ls"]}

    class UnconfiguredClient:
        def status(self):
            return {"configured": False}

    monkeypatch.setattr(route, "_owner", lambda request: ({"sub": "owner-a"}, "owner-a"))
    monkeypatch.setattr(route, "resolve_workspace", lambda owner, workspace: {"id": workspace})
    monkeypatch.setattr(route, "get_workspace_run", lambda owner, workspace, run: {
        "id": run, "revision": 1, "state": "ready",
    })
    monkeypatch.setattr(route, "CloudflareComputerClient", UnconfiguredClient)
    monkeypatch.setattr(route.store, "create_or_get", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("unconfigured provider must not create a receipt")
    ))
    with pytest.raises(HTTPException) as captured:
        asyncio.run(route.create_execution("personal", FakeRequest()))
    assert captured.value.status_code == 503
    assert captured.value.detail == "provider_not_configured"
