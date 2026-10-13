"""V347: durable, cross-device user-input request lifecycle."""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_waiting_input_survives_reload_and_is_resolved_by_next_user_turn(
    tmp_db, monkeypatch,
):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    user_id = "v347-user"
    conv_id = "v347-conversation"
    db.create_conversation(conv_id, user_id, "需要补充的长任务")

    request_id = db.create_message(
        conv_id,
        "assistant",
        "请确认希望我采用哪一种交付格式？",
        status="waiting_input",
        suggestions=["完整报告", "精简摘要"],
    )

    reloaded = db.get_messages(conv_id)
    request = next(message for message in reloaded if message["id"] == request_id)
    assert request["status"] == "waiting_input"
    assert request["suggestions"] == ["完整报告", "精简摘要"]
    assert db.get_active_chats(user_id) == [{
        "conv_id": conv_id,
        "msg_id": request_id,
        "created_at": request["created_at"],
        "status": "waiting_input",
        "title": "需要补充的长任务",
    }]

    db.create_message(conv_id, "user", "完整报告", status="complete")

    resolved = next(message for message in db.get_messages(conv_id) if message["id"] == request_id)
    assert resolved["status"] == "resolved"
    assert resolved["suggestions"] == ["完整报告", "精简摘要"]
    assert db.get_active_chats(user_id) == []


def test_input_request_contract_is_bounded_and_addressable():
    from hashmm.api.streaming import _input_request

    request = _input_request(
        "message-1",
        "conversation-1",
        "请选择下一步",
        ["A", "A", "B", "C", "D"],
        reason="test",
    )

    assert request == {
        "schema": "hashmm.input-request.v1",
        "request_id": "message-1",
        "conversation_id": "conversation-1",
        "kind": "clarification",
        "reason": "test",
        "question": "请选择下一步",
        "options": ["A", "B", "C"],
        "status": "waiting_input",
    }
