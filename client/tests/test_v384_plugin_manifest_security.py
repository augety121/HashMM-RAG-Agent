"""Regression tests for manifest-first, exact-digest plugin loading."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hashmm.api.plugins import PluginManager


def _manifest(plugin_id: str, tool_name: str = "safe_echo", *, entrypoint: str = "__init__.py") -> dict:
    return {
        "schema": "hashmm.plugin.v1",
        "id": plugin_id,
        "version": "1.0.0",
        "description": "test plugin",
        "runtime": {"kind": "python", "entrypoint": entrypoint},
        "permissions": {"filesystem": "none", "network": "none", "side_effects": False},
        "tools": [{
            "name": tool_name,
            "description": "echo",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            "annotations": {
                "read_only": True,
                "destructive": False,
                "idempotent": True,
                "open_world": False,
            },
        }],
    }


def _plugin(root: Path, plugin_id: str = "test_plugin", tool_name: str = "safe_echo") -> Path:
    directory = root / plugin_id
    directory.mkdir(parents=True)
    (directory / "plugin.json").write_text(
        json.dumps(_manifest(plugin_id, tool_name), ensure_ascii=False), encoding="utf-8",
    )
    (directory / "__init__.py").write_text(
        "def register_tools():\n"
        f"    return [{{'name': {tool_name!r}, 'executor': lambda args, ctx: args['value']}}]\n",
        encoding="utf-8",
    )
    return directory


def test_discovery_never_executes_and_untrusted_load_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = _plugin(tmp_path / "plugins")
    sentinel = tmp_path / "executed.txt"
    (directory / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n"
        "def register_tools():\n    return []\n",
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path / "plugins")
    inventory = manager.list_plugins()
    assert inventory[0]["status"] == "review_required"
    assert not sentinel.exists()
    assert manager.load("test_plugin") is False
    assert not sentinel.exists()


def test_exact_trust_loads_and_agent_contract_is_available(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    _plugin(tmp_path / "plugins")
    manager = PluginManager(tmp_path / "plugins")
    item = manager.list_plugins()[0]
    manager.trust("test_plugin", item["sha256"], trusted_by="admin-1")
    assert manager.load("test_plugin") is True
    assert manager.get_executors()["safe_echo"]({"value": "ok"}, {"secret": "drop"}) == "ok"
    assert manager.get_tool_definitions()[0]["function"]["name"] == "safe_echo"
    assert manager.get_tool_annotation("safe_echo")["read_only"] is True
    assert manager.list_plugins()[0]["status"] == "active"
    assert manager.list_plugins()[0]["execution_boundary"] == "subprocess_exact_digest_readonly_no_network_audit_policy"


def test_python_plugin_executes_outside_backend_process(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = _plugin(tmp_path / "plugins", "pid_plugin", "worker_pid")
    (directory / "__init__.py").write_text(
        "import os\n"
        "def register_tools():\n"
        "    return [{'name': 'worker_pid', 'executor': lambda args, ctx: os.getpid()}]\n",
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path / "plugins")
    item = manager.list_plugins()[0]
    manager.trust("pid_plugin", item["sha256"], trusted_by="admin-1")
    assert manager.load("pid_plugin") is True
    assert manager.get_executors()["worker_pid"]({}, {}) != os.getpid()


def test_plugin_crash_is_contained_and_backend_remains_usable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = _plugin(tmp_path / "plugins", "crash_plugin", "crash_now")
    (directory / "__init__.py").write_text(
        "import os\n"
        "def register_tools():\n"
        "    return [{'name': 'crash_now', 'executor': lambda args, ctx: os._exit(23)}]\n",
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path / "plugins")
    item = manager.list_plugins()[0]
    manager.trust("crash_plugin", item["sha256"], trusted_by="admin-1")
    assert manager.load("crash_plugin") is True
    with pytest.raises(Exception, match="异常退出"):
        manager.get_executors()["crash_now"]({}, {})
    assert os.getpid() > 0


def test_chat_can_expose_only_the_plugins_selected_for_that_turn(tmp_path: Path, monkeypatch):
    """Per-Chat selection must narrow tool visibility without changing trust."""
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    _plugin(tmp_path / "plugins", "alpha_plugin", "alpha_echo")
    _plugin(tmp_path / "plugins", "beta_plugin", "beta_echo")
    manager = PluginManager(tmp_path / "plugins")
    for item in manager.list_plugins():
        manager.trust(item["name"], item["sha256"], trusted_by="admin-1")
        assert manager.load(item["name"]) is True

    all_names = {
        item["function"]["name"] for item in manager.get_tool_definitions()
    }
    assert all_names == {"alpha_echo", "beta_echo"}
    assert {
        item["function"]["name"]
        for item in manager.get_tool_definitions({"alpha_plugin"})
    } == {"alpha_echo"}
    assert set(manager.get_executors({"beta_plugin"})) == {"beta_echo"}
    assert manager.get_tool_definitions(set()) == []
    assert manager.get_executors(set()) == {}


def test_changed_active_plugin_is_disabled_immediately(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = _plugin(tmp_path / "plugins")
    manager = PluginManager(tmp_path / "plugins")
    digest = manager.list_plugins()[0]["sha256"]
    manager.trust("test_plugin", digest)
    assert manager.load("test_plugin") is True
    (directory / "__init__.py").write_text(
        "def register_tools():\n    return []\n", encoding="utf-8",
    )
    assert manager.get_executors() == {}


def test_declarative_plugin_has_digest_bound_activation_without_code_trust(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = tmp_path / "plugins" / "declarative"
    directory.mkdir(parents=True)
    manifest = _manifest("declarative")
    manifest["runtime"] = {"kind": "manifest"}
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = PluginManager(tmp_path / "plugins")

    discovered = manager.list_plugins()[0]
    assert discovered["status"] == "configuration_required"
    assert discovered["trusted"] is False
    activated = manager.set_manifest_active(
        "declarative", discovered["sha256"], active=True, actor="admin-1",
    )
    assert activated.status == "active_manifest"
    assert manager.list_plugins()[0]["active"] is True
    assert manager.get_executors() == {}
    with pytest.raises(Exception, match="不进入 Python"):
        manager.trust("declarative", discovered["sha256"])

    manifest["description"] = "changed after activation"
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    changed = manager.list_plugins()[0]
    assert changed["status"] == "manifest_changed"
    assert changed["active"] is False


def test_invalid_plugin_can_be_recoverably_quarantined(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = tmp_path / "plugins" / "broken"
    directory.mkdir(parents=True)
    (directory / "plugin.json").write_text("not-json", encoding="utf-8")
    manager = PluginManager(tmp_path / "plugins")
    assert manager.list_plugins()[0]["status"] == "invalid"
    receipt = manager.quarantine("broken", actor="admin-1")
    assert receipt["quarantined"] is True
    assert not directory.exists()
    destination = tmp_path / "data" / "plugin-quarantine" / receipt["quarantine_id"]
    assert destination.is_dir()
    assert manager.list_plugins() == []


def test_non_source_payload_is_covered_by_exact_package_digest(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    directory = _plugin(tmp_path / "plugins")
    payload = directory / "policy.bin"
    payload.write_bytes(b"reviewed-payload")
    manager = PluginManager(tmp_path / "plugins")
    digest = manager.list_plugins()[0]["sha256"]
    manager.trust("test_plugin", digest)
    assert manager.load("test_plugin") is True

    payload.write_bytes(b"changed-after-review")

    assert manager.get_executors() == {}


def test_path_escape_and_builtin_collision_fail_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    plugins = tmp_path / "plugins"
    escaped = plugins / "escaped"
    escaped.mkdir(parents=True)
    manifest = _manifest("escaped", entrypoint="../outside.py")
    (escaped / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = PluginManager(plugins)
    assert manager.list_plugins()[0]["status"] == "invalid"

    collision = _plugin(plugins, "collision", "kb_search")
    manager = PluginManager(plugins)
    item = next(item for item in manager.list_plugins() if item["name"] == "collision")
    manager.trust("collision", item["sha256"])
    assert manager.load("collision") is False
    collision_item = next(item for item in manager.list_plugins() if item["name"] == "collision")
    assert collision_item["status"] in {"trusted", "collision"}


def test_plugin_routes_require_auth_and_admin():
    source = (Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "system.py").read_text(
        encoding="utf-8"
    )
    listing = source.split('async def list_plugins', 1)[1].split('@router.post("/plugins/{name}/load")', 1)[0]
    loading = source.split('async def load_plugin', 1)[1].split('@router.post("/plugins/{name}/trust")', 1)[0]
    trusting = source.split('async def trust_plugin', 1)[1].split('@router.post("/plugins/{name}/revoke")', 1)[0]
    revoking = source.split('async def revoke_plugin', 1)[1].split('@router.get("/plugins/tools")', 1)[0]
    assert "require_auth(request)" in listing
    assert "require_admin(request)" in loading
    assert "require_admin(request)" in trusting
    assert "require_admin(request)" in revoking
    installing = source.split('async def install_plugin', 1)[1].split('@router.get("/plugins/{name}/diagnostics")', 1)[0]
    diagnosing = source.split('async def plugin_diagnostics', 1)[1].split('@router.get("/plugins/tools")', 1)[0]
    assert "require_admin(request)" in installing
    assert "require_admin(request)" in diagnosing
