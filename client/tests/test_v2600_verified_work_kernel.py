from __future__ import annotations

import json
from pathlib import Path

from hashmm.release import API_VERSION, BACKEND_VERSION, DESKTOP_VERSION, PROTOCOLS, RELEASE, release_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_is_the_version_source_of_truth():
    manifest = release_manifest()
    assert manifest["schema"] == "hashmm.release-manifest.v1"
    assert RELEASE == "V2802"
    assert BACKEND_VERSION == "2.8.2"
    assert API_VERSION == "28.0.2"
    assert DESKTOP_VERSION == "17.0.2"
    assert PROTOCOLS["event"] == "hashmm.event.v1"
    assert json.loads((ROOT / "desktop" / "package.json").read_text("utf-8"))["version"] == DESKTOP_VERSION
    assert json.loads((ROOT / "frontend-next" / "package.json").read_text("utf-8"))["version"] == DESKTOP_VERSION
    assert f'version = "{BACKEND_VERSION}"' in (ROOT / "pyproject.toml").read_text("utf-8")


def test_release_gate_consumes_manifest_instead_of_init_literal():
    verifier = (ROOT / "desktop" / "scripts" / "verify-release.py").read_text("utf-8")
    assert "release-manifest.json" in verifier
    assert "release_data" in verifier
    assert "backend_release(ROOT / \"hashmm\" / \"__init__.py\")" not in verifier


def test_focus_mode_preserves_plan_and_todo_state():
    chat = (ROOT / "frontend-next" / "components" / "ChatArea.tsx").read_text("utf-8")
    assert "{taskPlan && taskPlan.steps.length > 0 && (" in chat
    assert "{todoItems.length > 0 && <TodoCard items={todoItems} />}" in chat
    assert "!focusMode && taskPlan" not in chat
    assert "!focusMode && todoItems" not in chat
