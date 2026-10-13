"""The AI hotspot radar is a real manifest/automation contract, not a UI-only card."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def test_radar_manifest_is_discoverable_and_manifest_only():
    from hashmm.api.plugins import PluginManager

    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager(root)
    items = {item.name: item for item in manager.discover()}
    radar = items["ai_news_radar"]
    assert radar.runtime == "manifest"
    assert radar.status == "configuration_required"
    assert radar.active is False
    manifest = json.loads((root / "ai_news_radar" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["automations"][0]["action"] == "ai_news_radar"
    assert manifest["automations"][0]["publishes"] is False


def test_radar_skill_declares_read_only_research_boundary():
    skill = (Path(__file__).resolve().parents[1] / "plugins" / "ai_news_radar" / "SKILL.md").read_text(encoding="utf-8")
    assert "不自动发布" in skill
    assert "source_url" in skill
    assert "allowed-tools: [web_search, fetch_url, create_file]" in skill


def test_radar_output_deduplicates_urls_and_keeps_candidates_unverified(monkeypatch):
    from hashmm import scheduler
    from hashmm.api import tool_registry

    def fake_execute(_name, _args, _ctx):
        return {"items": [
            {"title": "Same event", "url": "https://openai.com/news/1"},
            {"title": "Same event", "url": "https://openai.com/news/1"},
        ]}

    monkeypatch.setattr(tool_registry, "execute_tool", fake_execute)
    output = scheduler._action_ai_news_radar({"owner_uid": "radar-owner"})
    structured = output.split("## 去重后的候选", 1)[1].split("### 检索", 1)[0]
    assert structured.count("https://openai.com/news/1") == 1
    assert "待核验" in structured
    assert "没有提取到" not in structured
