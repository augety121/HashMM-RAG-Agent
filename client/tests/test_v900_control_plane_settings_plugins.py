"""Control-plane/settings/plugin regressions for the post-V820 major iteration."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest


def _isolated_database(path: str, monkeypatch):
    from hashmm.api import database as db
    if db._pool is not None:
        while not db._pool.empty():
            db._pool.get_nowait().close()
    monkeypatch.setattr(db, "DB_PATH", Path(path))
    monkeypatch.setattr(db, "_pool", None)
    db.init_db()
    return db


def test_user_settings_are_owner_scoped_revisioned_and_atomic(tmp_db, monkeypatch):
    _isolated_database(tmp_db, monkeypatch)
    from hashmm.api.user_settings import (
        SettingConflictError,
        SettingValidationError,
        list_settings,
        update_settings,
    )

    defaults = {item["key"]: item for item in list_settings("owner-a")}
    assert defaults["personalization.answer_style"]["effective_value"] == "analytical"
    assert defaults["personalization.answer_style"]["revision"] == 0

    updated = update_settings("owner-a", "owner-a", [{
        "key": "personalization.answer_style", "value": "factual", "revision": 0,
    }])
    current = {item["key"]: item for item in updated}
    assert current["personalization.answer_style"]["effective_value"] == "factual"
    assert current["personalization.answer_style"]["revision"] == 1
    assert {item["key"]: item for item in list_settings("owner-b")}["personalization.answer_style"]["revision"] == 0

    with pytest.raises(SettingConflictError):
        update_settings("owner-a", "owner-a", [{
            "key": "personalization.answer_style", "value": "creative", "revision": 0,
        }])
    assert {item["key"]: item for item in list_settings("owner-a")}["personalization.answer_style"]["effective_value"] == "factual"

    # A late conflict must roll back earlier writes in the same batch.
    with pytest.raises(SettingConflictError):
        update_settings("owner-a", "owner-a", [
            {"key": "personalization.custom_prompt", "value": "must roll back", "revision": 0},
            {"key": "personalization.answer_style", "value": "creative", "revision": 0},
        ])
    rolled_back = {item["key"]: item for item in list_settings("owner-a")}
    assert rolled_back["personalization.custom_prompt"]["revision"] == 0
    assert rolled_back["personalization.custom_prompt"]["effective_value"] == ""
    with pytest.raises(SettingValidationError):
        update_settings("owner-a", "owner-a", [{
            "key": "general.followup_mode", "value": "queue", "revision": 0,
        }])
    followup = {item["key"]: item for item in list_settings("owner-a")}
    assert followup["general.followup_mode"]["effective_value"] == "steer"


def test_user_settings_http_surface_returns_receipt_and_conflict(tmp_db, monkeypatch):
    _isolated_database(tmp_db, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api.routes import me

    def fake_auth(request):
        owner = request.headers.get("x-test-owner", "")
        return {"uid": owner, "sub": owner, "role": "user"}

    monkeypatch.setattr(me, "require_auth", fake_auth)
    app = FastAPI()
    app.include_router(me.router)
    client = TestClient(app)

    initial = client.get("/api/me/settings", headers={"x-test-owner": "owner-a"})
    assert initial.status_code == 200
    changed = client.patch("/api/me/settings", headers={"x-test-owner": "owner-a"}, json={"changes": [{
        "key": "agent.approval_mode", "value": "workspace", "revision": 0,
    }]})
    assert changed.status_code == 200
    assert changed.json()["receipt"]["status"] == "applied"

    stale = client.patch("/api/me/settings", headers={"x-test-owner": "owner-a"}, json={"changes": [{
        "key": "agent.approval_mode", "value": "ask", "revision": 0,
    }]})
    assert stale.status_code == 409
    other = client.get("/api/me/settings", headers={"x-test-owner": "owner-b"}).json()
    item = next(setting for setting in other["settings"] if setting["key"] == "agent.approval_mode")
    assert item["effective_value"] == "ask"
    assert item["revision"] == 0


def test_runtime_secret_settings_are_encrypted_at_rest(tmp_db, monkeypatch):
    db = _isolated_database(tmp_db, monkeypatch)
    monkeypatch.setenv("HASHMM_SECRET_KEY", "test-only-strong-secret-for-settings")
    from hashmm.api import settings_store

    settings_store.set_setting("serper_api_key", "secret-serper-value")
    with db._conn() as conn:
        stored = conn.execute("SELECT value FROM app_settings WHERE key=?", ("serper_api_key",)).fetchone()["value"]
    assert stored.startswith("enc:")
    assert "secret-serper-value" not in stored
    assert settings_store.get_setting("serper_api_key") == "secret-serper-value"
    visible = next(item for item in settings_store.list_settings() if item["key"] == "serper_api_key")
    assert visible["configured"] is True
    assert "secret-serper-value" not in visible["display"]

    with db._conn() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,0) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("bing_api_key", "legacy-plaintext-secret"),
        )
    assert settings_store.get_setting("bing_api_key") == "legacy-plaintext-secret"
    with db._conn() as conn:
        migrated = conn.execute("SELECT value FROM app_settings WHERE key=?", ("bing_api_key",)).fetchone()["value"]
    assert migrated.startswith("enc:")
    assert "legacy-plaintext-secret" not in migrated


def _plugin_zip(plugin_id: str = "uploaded_plugin", *, extra: dict[str, bytes] | None = None) -> bytes:
    manifest = {
        "schema": "hashmm.plugin.v1", "id": plugin_id, "version": "1.0.0",
        "runtime": {"kind": "python", "entrypoint": "__init__.py"},
        "permissions": {"filesystem": "none", "network": "none", "side_effects": False},
        "tools": [{
            "name": "uploaded_echo", "description": "echo",
            "parameters": {"type": "object", "properties": {}},
            "annotations": {"read_only": True, "destructive": False, "idempotent": True, "open_world": False},
        }],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{plugin_id}/plugin.json", json.dumps(manifest))
        archive.writestr(f"{plugin_id}/__init__.py", "def register_tools():\n    return [{'name':'uploaded_echo','executor':lambda args,ctx:'ok'}]\n")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return stream.getvalue()


def test_plugin_zip_is_staged_untrusted_and_never_executes(tmp_path, monkeypatch):
    from hashmm.api.plugins import PluginManager
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    manager = PluginManager(tmp_path / "plugins")
    package = _plugin_zip()
    result = manager.install_zip(package, installed_by="admin")
    assert result["ok"] is True
    assert result["execution_started"] is False
    assert result["trust_required"] is True
    assert result["plugin"]["trusted"] is False
    assert result["plugin"]["active"] is False
    diagnostics = manager.diagnostics("uploaded_plugin")
    assert diagnostics["permissions"]["network"] == "none"
    assert diagnostics["tools"][0]["annotations"]["read_only"] is True
    assert manager.get_executors() == {}


def test_plugin_zip_rejects_traversal_and_requires_explicit_upgrade(tmp_path, monkeypatch):
    from hashmm.api.plugins import PluginManager, PluginValidationError
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    manager = PluginManager(tmp_path / "plugins")
    package = _plugin_zip()
    manager.install_zip(package)
    with pytest.raises(PluginValidationError, match="同名插件已存在"):
        manager.install_zip(package)

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../outside.txt", "bad")
    with pytest.raises(PluginValidationError, match="目录穿越"):
        manager.install_zip(stream.getvalue())
    assert not (tmp_path / "outside.txt").exists()


def test_plugin_upgrade_revokes_trust_unloads_tools_and_quarantines_previous_bytes(tmp_path, monkeypatch):
    from hashmm.api.plugins import PluginManager
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    manager = PluginManager(tmp_path / "plugins")
    package = _plugin_zip()
    installed = manager.install_zip(package)
    manager.trust("uploaded_plugin", installed["package_sha256"], trusted_by="admin")
    assert manager.load("uploaded_plugin") is True
    assert set(manager.get_executors()) == {"uploaded_echo"}

    upgraded = manager.install_zip(package, replace=True, installed_by="admin")

    assert upgraded["action"] == "upgraded"
    assert upgraded["previous_quarantined"] is True
    assert upgraded["execution_started"] is False
    assert upgraded["plugin"]["trusted"] is False
    assert upgraded["plugin"]["active"] is False
    assert manager.get_executors() == {}
    quarantine = tmp_path / "data" / "plugin-quarantine"
    assert len([item for item in quarantine.iterdir() if item.is_dir()]) == 1


def test_plugin_zip_rejects_duplicate_casefolded_and_windows_ads_paths(tmp_path, monkeypatch):
    from hashmm.api.plugins import PluginManager, PluginValidationError
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    manager = PluginManager(tmp_path / "plugins")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("demo/plugin.json", "{}")
        archive.writestr("DEMO/PLUGIN.JSON", "{}")
    with pytest.raises(PluginValidationError, match="重复或大小写冲突"):
        manager.install_zip(duplicate.getvalue())

    ads = io.BytesIO()
    with zipfile.ZipFile(ads, "w") as archive:
        archive.writestr("demo/plugin.json:payload", "{}")
    with pytest.raises(PluginValidationError, match="跨平台不安全路径"):
        manager.install_zip(ads.getvalue())
