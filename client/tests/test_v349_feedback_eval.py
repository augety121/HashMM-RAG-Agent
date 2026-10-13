"""V349: structured production feedback -> human-reviewed held-out eval cases."""
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


def _seed(db, suffix: str = "a"):
    user = db.create_user(f"feedback-{suffix}", "pw12345678", "反馈用户", "user")
    conv_id = f"feedback-conv-{suffix}"
    db.create_conversation(conv_id, user["id"], "质量回灌")
    db.create_message(conv_id, "user", "服务器里的真实问题")
    message_id = db.create_message(
        conv_id, "assistant", "服务器里的失败回答", status="complete",
        tool_calls=[{"name": "kb_search", "arguments": {"query": "真实问题"}}],
        sources=[{"id": "doc-1", "filename": "evidence.md", "score": 0.2}],
        groundings={"status": "insufficient"},
        run_manifest={"schema": "hashmm.run-manifest.v2", "stop_reason": "done"},
        tokens_in=12, tokens_out=34,
    )
    return user, conv_id, message_id


def test_negative_feedback_captures_authoritative_run_and_is_pending(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db)
    case = db.record_message_feedback(
        user_id=user["id"], conv_id=conv_id, message_id=message_id,
        rating="down", reason_code="retrieval_miss", comment="没有召回制度原文",
    )

    assert case["schema"] == "hashmm.feedback-case.v1"
    assert case["query"] == "服务器里的真实问题"
    assert case["answer"] == "服务器里的失败回答"
    assert case["reason_code"] == "retrieval_miss"
    assert case["status"] == "pending"
    assert case["evidence"]["tool_calls"][0]["name"] == "kb_search"
    assert case["evidence"]["sources"][0]["id"] == "doc-1"
    assert case["evidence"]["tokens_out"] == 34
    assert db.get_messages(conv_id)[-1]["feedback"] == "down"


def test_feedback_is_owner_bound_and_positive_withdraws_candidate(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    owner, conv_id, message_id = _seed(db, "owner")
    other = db.create_user("feedback-other", "pw12345678", "其他用户", "user")
    assert db.record_message_feedback(
        user_id=other["id"], conv_id=conv_id, message_id=message_id,
        rating="down", reason_code="other",
    ) is None
    first = db.record_message_feedback(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        rating="down", reason_code="incorrect",
    )
    second = db.record_message_feedback(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        rating="up",
    )
    assert second["id"] == first["id"]
    assert second["status"] == "positive"
    assert db.list_feedback_cases(status="pending") == []
    assert db.get_messages(conv_id)[-1]["feedback"] == "up"


def test_review_builder_never_promotes_failed_answer_as_reference():
    from hashmm.evaluation.feedback_loop import build_reviewed_eval_case

    candidate = {
        "id": "abc", "query": "真实问题", "answer": "这是错误答案",
        "reason_code": "unsupported", "reason_label": "证据不足",
        "comment": "引用对不上",
    }
    with pytest.raises(ValueError):
        build_reviewed_eval_case(candidate, "")
    case = build_reviewed_eval_case(candidate, "人工核验后的正确答案")
    assert case["id"] == "feedback_abc"
    assert case["reference_answer"] == "人工核验后的正确答案"
    assert case["split"] == "heldout"
    assert case["must_cite"] is True and case["min_sources"] == 1
    assert "这是错误答案" not in str(case)


def test_http_feedback_ignores_forged_query_and_answer(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api import auth, supabase_sync
    from hashmm.api.routes.conversations import router

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    monkeypatch.setattr("hashmm.api.routes.conversations._apply_feedback_learning", lambda *_: None)
    db = _fresh_db(tmp_db)
    user, conv_id, message_id = _seed(db, "http")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    token = auth.create_token(user["id"], user["username"], "user")
    response = client.post(
        f"/api/conversations/{conv_id}/messages/{message_id}/review",
        json={
            "rating": "down", "reason_code": "wrong_tool", "comment": "参数不对",
            "query": "客户端伪造的问题", "message_content": "客户端伪造的答案",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    case = response.json()["feedback_case"]
    assert case["query"] == "服务器里的真实问题"
    assert case["answer"] == "服务器里的失败回答"
    assert "客户端伪造" not in str(case)


def test_admin_approval_requires_reference_and_adds_heldout_case(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api import auth, supabase_sync
    from hashmm.api.routes.admin import router
    from hashmm.evaluation import RAGEvaluator

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    owner, conv_id, message_id = _seed(db, "admin")
    candidate = db.record_message_feedback(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        rating="down", reason_code="incomplete", comment="交付物不存在",
    )
    admin = db.create_user("feedback-reviewer", "pw12345678", "管理员", "admin")
    captured = []
    monkeypatch.setattr(RAGEvaluator, "add_case", lambda _self, case: captured.append(case) or case)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    token = auth.create_token(admin["id"], admin["username"], "admin")
    path = f"/api/admin/eval/feedback-candidates/{candidate['id']}"

    invalid = client.post(path, json={"decision": "approve", "reference_answer": ""},
                          headers={"Authorization": f"Bearer {token}"})
    assert invalid.status_code == 400
    approved = client.post(
        path, json={"decision": "approve", "reference_answer": "应生成并验证真实交付物"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approved.status_code == 200, approved.text
    assert captured[0]["split"] == "heldout"
    assert captured[0]["category"] == "task_completion"
    assert db.get_feedback_case(candidate["id"])["status"] == "approved"


def test_feedback_review_claim_is_exclusive_and_recoverable(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    owner, conv_id, message_id = _seed(db, "claim")
    candidate = db.record_message_feedback(
        user_id=owner["id"], conv_id=conv_id, message_id=message_id,
        rating="down", reason_code="incorrect",
    )
    first = db.claim_feedback_case(case_id=candidate["id"], reviewer="admin-a")
    assert first and first["status"] == "reviewing"
    assert db.claim_feedback_case(case_id=candidate["id"], reviewer="admin-b") is None
    assert db.release_feedback_case_claim(case_id=candidate["id"], reviewer="admin-b") is False
    assert db.release_feedback_case_claim(case_id=candidate["id"], reviewer="admin-a") is True
    second = db.claim_feedback_case(case_id=candidate["id"], reviewer="admin-b")
    assert second and second["status"] == "reviewing"
