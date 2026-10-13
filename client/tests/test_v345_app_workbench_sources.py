from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_recent_runs_come_from_owner_scoped_chat_manifests(monkeypatch):
    from hashmm.api.routes import feed

    messages = [
        {"id": "u1", "role": "user", "content": "检查仓库并修复测试", "created_at": 10},
        {
            "id": "a1",
            "role": "assistant",
            "content": "完成",
            "created_at": 20,
            "run_manifest": {
                "run_id": "run-1",
                "task_type": "code",
                "execution_mode": "agent",
                "model": "model-a",
                "stage_latency_ms": {"total": 1234},
                "termination": {"reason": "completed", "iterations": 2},
                "tokens": {"input": 100, "output": 50},
                "verification": {"status": "passed", "failed_checks": []},
            },
        },
    ]
    monkeypatch.setattr(feed.db, "get_messages", lambda conv_id, limit=200: messages)

    rows = feed._recent_user_runs([{"id": "c1", "title": "真实任务"}])

    assert len(rows) == 1
    assert rows[0]["conv_id"] == "c1"
    assert rows[0]["title"] == "检查仓库并修复测试"
    assert rows[0]["elapsed_ms"] == 1234
    assert rows[0]["tokens"] == 150


def test_context_inspect_owner_checks_explicit_conversation(monkeypatch):
    from hashmm.api.routes import context_inspect as route
    from hashmm.api import database as db

    checked = []
    monkeypatch.setattr(route, "require_auth", lambda request: {"uid": "u1"})
    monkeypatch.setattr(
        route,
        "require_conv_access",
        lambda request, conv_id: checked.append(conv_id) or {"id": conv_id, "title": "我的会话"},
    )
    monkeypatch.setattr(
        db,
        "get_latest_messages",
        lambda conv_id, limit=200: [{"role": "user", "content": "最后问题"}],
    )
    monkeypatch.setattr(
        route,
        "collect_blocks",
        lambda uid, conv_id="", query="": {"ok": True, "conv_id": conv_id, "blocks": [], "total_chars": 0, "tips": [], "seen": (uid, query)},
    )
    request = SimpleNamespace(query_params={"conv_id": "c1"})

    out = asyncio.run(route.context_inspect(request, conv_id="c1"))

    assert checked == ["c1"]
    assert out["conv_title"] == "我的会话"
    assert out["query"] == "最后问题"
    assert out["resolved_latest"] is False


def test_context_inspect_defaults_to_latest_owned_chat(monkeypatch):
    from hashmm.api.routes import context_inspect as route
    from hashmm.api import database as db

    monkeypatch.setattr(route, "require_auth", lambda request: {"uid": "u1"})
    monkeypatch.setattr(db, "list_conversations", lambda uid, limit=1: [{"id": "latest", "title": "最近会话"}])
    monkeypatch.setattr(db, "get_messages", lambda conv_id, limit=200: [])
    monkeypatch.setattr(
        route,
        "collect_blocks",
        lambda uid, conv_id="", query="": {"ok": True, "conv_id": conv_id, "blocks": [], "total_chars": 0, "tips": []},
    )
    request = SimpleNamespace(query_params={})

    out = asyncio.run(route.context_inspect(request))

    assert out["conv_id"] == "latest"
    assert out["conv_title"] == "最近会话"
    assert out["resolved_latest"] is True
