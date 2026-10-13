"""V395 user-facing work projection and action inbox."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_presentation_uses_user_language_and_never_invents_percent(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("presentation-owner", "pw12345678")["id"]
    run = work_runtime.create_run(
        user_id=owner,
        kind="team",
        source_id="team-presentation",
        title="整理调研结论",
        status="running",
        snapshot={"roles": ["researcher", "reviewer"]},
    )

    presentation = run["presentation"]
    assert presentation["schema"] == "hashmm.work-presentation.v1"
    assert presentation["category"] == "并行协作"
    assert presentation["status_label"] == "进行中"
    assert presentation["progress"] == {
        "mode": "criteria",
        "completed": 0,
        "total": 3,
        "label": "已满足 0/3 项验收条件",
    }
    assert "percent" not in presentation["progress"]


def test_projection_exposes_real_criteria_deliverables_and_evidence(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("evidence-owner", "pw12345678")["id"]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="artifact-presentation",
        title="生成方案",
        status="running",
    )
    updated = work_runtime.append_event(
        run["id"],
        user_id=owner,
        event_type="delivered",
        status="delivered",
        summary="方案已生成，等待检查",
        snapshot_updates={
            "files": [{"filename": "方案.docx", "type": "docx"}],
            "run_manifest": {
                "task_contract": {
                    "success_criteria": [
                        {"check_id": "a", "label": "结构完整"},
                        {"check_id": "b", "label": "引用充分"},
                    ],
                },
                "completion_gate": {"summary": {"total": 2, "passed": 1}},
                "verification": {"status": "verified"},
                "evidence_graph": {"summary": {"evidence_nodes": 4}},
            },
        },
    )

    assert updated is not None
    presentation = updated["presentation"]
    assert presentation["progress"]["label"] == "已满足 1/2 项验收条件"
    assert presentation["deliverables"] == [{"name": "方案.docx", "type": "docx"}]
    assert presentation["evidence"] == {"status": "verified", "count": 4, "label": "已核验"}
    assert presentation["primary_action"] == "review_delivery"


def test_action_inbox_is_owner_scoped_and_prioritizes_questions(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    alice = db.create_user("inbox-alice", "pw12345678")["id"]
    bob = db.create_user("inbox-bob", "pw12345678")["id"]
    work_runtime.create_run(
        user_id=alice, kind="chat", source_id="delivered",
        title="检查报告", status="delivered",
    )
    work_runtime.create_run(
        user_id=alice, kind="chat", source_id="question",
        title="补充目标", status="waiting_input",
    )
    work_runtime.create_run(
        user_id=bob, kind="chat", source_id="foreign",
        title="其他用户的工作", status="waiting_approval",
    )

    feed = work_runtime.list_runs(alice)
    inbox = feed["action_inbox"]
    assert inbox["schema"] == "hashmm.action-inbox.v1"
    assert inbox["count"] == 2
    assert inbox["high_priority_count"] == 1
    assert [item["type"] for item in inbox["items"]] == ["question", "delivery"]
    assert "其他用户" not in str(inbox)

    # An up-to-date incremental client receives no changed runs, but unresolved
    # user actions must remain visible until they are actually resolved.
    quiet_feed = work_runtime.list_runs(alice, after_cursor=feed["high_water_cursor"])
    assert quiet_feed["items"] == []
    assert [item["type"] for item in quiet_feed["action_inbox"]["items"]] == ["question", "delivery"]
