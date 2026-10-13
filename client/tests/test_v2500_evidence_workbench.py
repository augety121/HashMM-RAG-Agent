from __future__ import annotations

import json
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from hashmm.api import provider_fabric as fabric
from hashmm.api.plugins import PluginManager
from hashmm.api.routes import canvas_ops
from hashmm.api.routes import provider_fabric as provider_routes
from hashmm.api import model_manager
from hashmm.model_runtime import ModelRequirements


def test_provider_fabric_encrypts_secret_and_isolates_owner():
    owner = "owner-" + uuid.uuid4().hex
    other = "other-" + uuid.uuid4().hex
    created = fabric.create_connection(owner, {
        "name": "authorised-sub2api-" + uuid.uuid4().hex[:8],
        "kind": "sub2api",
        "base_url": "https://gateway.example.com/v1",
        "wire_api": "chat_completions",
        "api_key": "sk-test-provider-secret",
        "model_alias": "default",
        "upstream_model": "model-owned-by-account",
    })
    assert created["kind"] == "sub2api"
    assert created["has_credential"] is True
    assert "api_key" not in created
    assert "sk-test-provider-secret" not in json.dumps(created)
    assert fabric.get_connection(other, created["id"]) is None
    assert fabric.delete_connection(other, created["id"]) is False
    private = fabric.connection_as_model(owner, created["id"])
    assert private and private["api_key"] == "sk-test-provider-secret"
    assert private["model_name"] == "model-owned-by-account"
    assert fabric.delete_connection(owner, created["id"]) is True


@pytest.mark.parametrize("url", [
    "http://gateway.example.com/v1",
    "https://127.0.0.1/v1",
    "https://10.1.2.3/v1",
    "https://169.254.169.254/latest/meta-data",
    "https://user:secret@example.com/v1",
])
def test_provider_fabric_rejects_unsafe_public_endpoints(url: str):
    with pytest.raises(ValueError):
        fabric.validate_endpoint(url, "sub2api")


def test_provider_fabric_allows_explicit_local_runtime_only():
    assert fabric.validate_endpoint("http://127.0.0.1:11434/v1", "local") == "http://127.0.0.1:11434/v1"


def test_provider_fabric_routes_before_first_token_with_capacity_and_sticky_binding():
    owner = "route-owner-" + uuid.uuid4().hex
    item = fabric.create_connection(owner, {
        "name": "route-pool-" + uuid.uuid4().hex[:8], "kind": "sub2api",
        "base_url": "https://gateway.example.com/v1", "wire_api": "chat_completions",
        "api_key": "sk-route-pool", "model_alias": "answer",
        "upstream_model": "model-a", "max_concurrency": 1, "weight": 100,
    })
    fabric.add_channel(owner, item["id"], {
        "model_alias": "answer", "upstream_model": "model-b",
        "max_concurrency": 1, "weight": 100,
    })
    fabric.record_probe(owner, item["id"], ok=True, latency_ms=12)
    first = fabric.select_route(
        owner, "answer", request_id="request-route-0001", sticky_key="conversation-a",
        connection_id=item["id"], lease_ttl=60,
    )
    second = fabric.select_route(
        owner, "answer", request_id="request-route-0002", sticky_key="conversation-b",
        connection_id=item["id"], lease_ttl=60,
    )
    assert first["channel"]["id"] != second["channel"]["id"]
    assert first["retry_boundary"] == "before_first_token"
    assert first["channel"]["api_key"] == "sk-route-pool"
    assert fabric.release_lease(owner, first["lease_id"], state="completed") is True
    assert fabric.release_lease(owner, second["lease_id"], state="completed") is True
    fabric.delete_connection(owner, item["id"])


def test_provider_fabric_duplicate_request_reuses_own_full_capacity_lease():
    owner = "duplicate-owner-" + uuid.uuid4().hex
    item = fabric.create_connection(owner, {
        "name": "duplicate-route", "kind": "sub2api",
        "base_url": "https://gateway.example.com/v1", "wire_api": "chat_completions",
        "api_key": "sk-duplicate", "model_alias": "answer",
        "upstream_model": "model-a", "max_concurrency": 1,
    })
    first = fabric.select_route(owner, "answer", request_id="same-request-0001", connection_id=item["id"])
    duplicate = fabric.select_route(owner, "answer", request_id="same-request-0001", connection_id=item["id"])
    assert duplicate["lease_id"] == first["lease_id"]
    assert fabric.release_lease(owner, first["lease_id"], state="completed") is True
    fabric.delete_connection(owner, item["id"])


def test_request_time_provider_call_records_attempt_and_releases_lease(monkeypatch):
    owner = "call-owner-" + uuid.uuid4().hex
    subject = "call-subject-" + uuid.uuid4().hex
    item = fabric.create_connection(owner, {
        "name": "call-route", "kind": "sub2api",
        "base_url": "https://gateway.example.com/v1", "wire_api": "chat_completions",
        "api_key": "sk-call", "model_alias": "answer", "upstream_model": "model-a",
        "max_concurrency": 1,
    })
    cfg = fabric.connection_as_model(owner, item["id"])
    assert cfg is not None
    cfg.update({"id": "fabric-model", "created_by": subject, "is_default": 0})
    monkeypatch.setattr(model_manager, "_configured_model_catalog", lambda: [cfg])
    monkeypatch.setattr(model_manager, "make_llm_fn_from_model", lambda _cfg: (lambda prompt: f"ok:{prompt}"))
    fn, _, reason = model_manager.get_llm_for_request(
        ModelRequirements(streaming=False), preferred_model_id="fabric-model",
        owner_subject=subject, owner_id=owner, request_id="chat-request-001",
        sticky_key="conversation-001",
    )
    assert fn is not None and reason == "provider_fabric"
    assert fn("one") == "ok:one"
    assert fn("two") == "ok:two"
    with fabric.db._conn() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM provider_leases WHERE owner_id=? AND state='active'", (owner,),
        ).fetchone()[0]
        attempts = conn.execute(
            "SELECT COUNT(*) FROM provider_request_attempts WHERE owner_id=? AND outcome='success'", (owner,),
        ).fetchone()[0]
    assert active == 0
    assert attempts == 2
    fabric.delete_connection(owner, item["id"])


def test_provider_activation_materialises_real_chat_model(monkeypatch):
    owner = "activate-" + uuid.uuid4().hex
    subject = "subject-" + uuid.uuid4().hex
    item = fabric.create_connection(owner, {
        "name": "chat-route-" + uuid.uuid4().hex[:8], "kind": "sub2api",
        "base_url": "https://gateway.example.com/v1", "wire_api": "chat_completions",
        "api_key": "sk-route-secret", "upstream_model": "account-model",
    })
    monkeypatch.setattr(provider_routes, "_owner", lambda request: ({"uid": owner, "sub": subject}, owner))
    result = asyncio.run(provider_routes.activate_provider_connection(item["id"], object()))
    assert result["ok"] is True and result["preferred"] is True
    model = provider_routes.db.get_model(result["model_id"])
    assert model and model["created_by"] == subject
    assert model["base_url"] == "https://gateway.example.com/v1"
    assert model["model_name"] == "account-model"
    provider_routes.db.delete_model(result["model_id"])
    fabric.delete_connection(owner, item["id"])


def test_plugin_manifest_projects_universal_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "plugins" / "universal_demo"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({
        "schema": "hashmm.plugin.v1",
        "id": "universal_demo",
        "version": "1.0.0",
        "runtime": {"kind": "manifest"},
        "skills": [{"id": "research"}],
        "connectors": ["authorised_api"],
        "hooks": ["before_run"],
        "ui": ["settings_panel"],
        "scheduled_tasks": ["daily_digest"],
        "compatibility": {"hashmm": ">=2.5", "protocol": "v1"},
        "permissions": {"filesystem": "none", "network": "none", "side_effects": False},
    }), encoding="utf-8")
    item = PluginManager(tmp_path / "plugins").list_plugins()[0]
    assert item["skills"] == ["research"]
    assert item["connectors"] == ["authorised_api"]
    assert item["hooks"] == ["before_run"]
    assert item["ui"] == ["settings_panel"]
    assert item["scheduled_tasks"] == ["daily_digest"]
    assert item["compatibility"]["protocol"] == "v1"


def test_plugin_manifest_rejects_duplicate_universal_capability(tmp_path):
    root = tmp_path / "plugins" / "bad_demo"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({
        "schema": "hashmm.plugin.v1", "id": "bad_demo",
        "runtime": {"kind": "manifest"}, "skills": ["same", "same"],
    }), encoding="utf-8")
    item = PluginManager(tmp_path / "plugins").list_plugins()[0]
    assert item["status"] == "invalid"


def test_python_plugin_worker_denies_file_access_outside_package(tmp_path):
    root = tmp_path / "plugins" / "isolated"
    root.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("must-not-leak", encoding="utf-8")
    entry = root / "plugin.py"
    entry.write_text(
        "from pathlib import Path\n"
        f"TARGET = {str(outside)!r}\n"
        "def register_tools():\n"
        "    return [{'name':'probe','executor':lambda args,ctx:Path(TARGET).read_text()}]\n",
        encoding="utf-8",
    )
    worker = Path(canvas_ops.__file__).resolve().parents[1] / "plugin_worker.py"
    request = {
        "action": "execute", "package_root": str(root), "entrypoint": str(entry),
        "digest": "a" * 64, "tool": "probe", "args": {}, "ctx": {},
        "permissions": {"filesystem": "none", "network": "none"},
    }
    result = subprocess.run(
        [sys.executable, "-I", str(worker)], input=json.dumps(request).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert "PermissionError" in payload["error"]


class _Request:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def test_canvas_ops_are_idempotent_and_revision_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_ops.db, "CONV_FILES_ROOT", tmp_path / "conversations")
    monkeypatch.setattr(canvas_ops, "require_auth", lambda request: {"uid": "canvas-owner"})
    monkeypatch.setattr(canvas_ops, "require_conv_access", lambda request, conv_id: {"id": conv_id})
    conv_id = "conv-v2600-" + uuid.uuid4().hex
    base = {"conv_id": conv_id, "filename": "result.html", "op_id": "op-1",
            "kind": "replace", "payload": {"block_id": "summary", "text": "verified"},
            "expected_revision": 0}
    first = asyncio.run(canvas_ops.append_canvas_op(_Request(base)))
    duplicate = asyncio.run(canvas_ops.append_canvas_op(_Request(base)))
    assert first["revision"] == 1 and first["duplicate"] is False
    assert duplicate["revision"] == 1 and duplicate["duplicate"] is True
    stale = dict(base, op_id="op-2")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(canvas_ops.append_canvas_op(_Request(stale)))
    assert exc.value.status_code == 409


def test_canvas_snapshot_requires_sha256_and_current_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_ops.db, "CONV_FILES_ROOT", tmp_path / "conversations")
    monkeypatch.setattr(canvas_ops, "require_auth", lambda request: {"uid": "canvas-owner"})
    monkeypatch.setattr(canvas_ops, "require_conv_access", lambda request, conv_id: {"id": conv_id})
    conv_id = "conv-snap-" + uuid.uuid4().hex
    asyncio.run(canvas_ops.append_canvas_op(_Request({"conv_id": conv_id, "filename": "artifact.html",
        "op_id": "snap-op", "kind": "insert", "payload": {}, "expected_revision": 0})))
    result = asyncio.run(canvas_ops.record_canvas_snapshot(_Request({"conv_id": conv_id, "filename": "artifact.html",
        "revision": 1, "content_hash": "a" * 64})))
    assert result["snapshot"]["revision"] == 1
    assert result["snapshot"]["content_hash"] == "a" * 64
