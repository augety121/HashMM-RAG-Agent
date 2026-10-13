"""V1000 regressions for durable history and verified cross-Chat continuity."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _fresh(path: str, monkeypatch):
    from hashmm.api import database as db
    # Preserve the process-wide pool for the tests that run after this module.
    # Draining it before monkeypatch restoration leaves an empty Queue object
    # behind and makes the next db._conn() block forever.
    if db._pool is not None and db._pool.empty():
        db._pool = None
    monkeypatch.setattr(db, "DB_PATH", Path(path))
    monkeypatch.setattr(db, "_pool", None)
    db.init_db()
    return db


def test_server_visibility_scope_and_keyset_cursor_are_authoritative(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("continuity-index", "pw12345678")
    project_id = db.create_project(owner["id"], "Project")

    for index in range(5):
        conv_id = f"recent-{index}"
        db.create_conversation(conv_id, owner["id"], conv_id)
        db.create_message(conv_id, "user", f"message {index}")
        with db._conn() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at=?,content_activity_at=? WHERE id=?",
                (1000 + index, 1000 + index, conv_id),
            )
    db.create_conversation("project-chat", owner["id"], "Project chat", project_id=project_id)
    db.create_message("project-chat", "user", "project work")
    db.create_conversation("empty-shell", owner["id"], "Reserved shell")

    first = db.list_conversations(
        owner["id"], limit=2, meaningful_only=True, scope="unassigned",
    )
    assert [row["id"] for row in first] == ["recent-4", "recent-3"]
    assert all(row["has_durable_content"] == 1 for row in first)
    assert all(row["visibility_source"] == "server" for row in first)
    cursor = (
        int(first[-1]["pinned"] or 0),
        float(first[-1]["last_activity_at"]),
        first[-1]["id"],
    )
    second = db.list_conversations(
        owner["id"], limit=10, meaningful_only=True,
        scope="unassigned", cursor=cursor,
    )
    ids = [row["id"] for row in first + second]
    assert ids == ["recent-4", "recent-3", "recent-2", "recent-1", "recent-0"]
    assert len(ids) == len(set(ids))
    assert "project-chat" not in ids
    assert "empty-shell" not in ids

    project_rows = db.list_conversations(
        owner["id"], limit=20, meaningful_only=True,
        scope="project", project_id=project_id,
    )
    assert [row["id"] for row in project_rows] == ["project-chat"]


def test_handoff_is_idempotent_redacted_owner_scoped_and_does_not_carry_permissions(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    from hashmm.api import chat_handoffs
    from hashmm.api.context import ContextBuilder

    owner = db.create_user("continuity-owner", "pw12345678")
    other = db.create_user("continuity-other", "pw12345678")
    project_id = db.create_project(owner["id"], "Verified project")
    db.create_conversation("source-chat", owner["id"], "Source", project_id=project_id)
    db.create_message(
        "source-chat",
        "user",
        "Finish the report; api_key=super-secret-value",
        thinking="private hidden chain must never leave this row",
    )

    first, created = chat_handoffs.create(
        owner["id"], "source-chat", idempotency_key="request-00000001",
    )
    retry, created_retry = chat_handoffs.create(
        owner["id"], "source-chat", idempotency_key="request-00000001",
    )
    assert created is True
    assert first["schema"] == "hashmm.chat-continuation.v2"
    assert created_retry is False
    assert retry["id"] == first["id"]
    assert retry["target_conversation_id"] == first["target_conversation_id"]
    target = db.get_conversation(first["target_conversation_id"])
    assert target["project_id"] == project_id

    serialized = str(first)
    assert "super-secret-value" not in serialized
    assert "private hidden chain" not in serialized
    assert first["payload"]["policy"]["private_reasoning_persisted"] is False
    assert first["payload"]["policy"]["permissions_carried"] is False
    assert all(
        approval.get("permission_carried") is False
        for approval in first["payload"]["pending_approvals"]
    )
    assert db.get_conversation_handoff(first["id"], other["id"]) is None

    context = ContextBuilder(first["target_conversation_id"], owner["id"]).build([], db=db)
    assert "Verified Project Continuity" in context
    assert "do not grant permission" in context
    assert "super-secret-value" not in context
    claimed = db.transition_conversation_handoff(first["id"], owner["id"], "claim")
    assert claimed["status"] == "claimed"
    acknowledged = db.transition_conversation_handoff(first["id"], owner["id"], "acknowledge")
    assert acknowledged["status"] == "acknowledged"
    with pytest.raises(RuntimeError):
        db.transition_conversation_handoff(first["id"], owner["id"], "reject")


def test_handoff_becomes_stale_when_source_revision_changes(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    from hashmm.api import chat_handoffs

    owner = db.create_user("continuity-stale", "pw12345678")
    db.create_conversation("source-stale", owner["id"], "Source")
    db.create_message("source-stale", "user", "Continue the task")
    handoff, _ = chat_handoffs.create(
        owner["id"], "source-stale", idempotency_key="request-stale-01",
    )
    assert chat_handoffs.verify_current_state(handoff)["stale"] is False
    assert db.update_conversation("source-stale", title="Changed after seal")
    state = chat_handoffs.verify_current_state(handoff)
    assert state["stale"] is True
    assert "source_revision_changed" in state["reasons"]


def test_chat_mailbox_is_same_scope_idempotent_and_never_carries_authority(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("mailbox-owner", "pw12345678")
    other = db.create_user("mailbox-other", "pw12345678")
    project_id = db.create_project(owner["id"], "Shared scope")
    db.create_conversation("mail-source", owner["id"], "Source", project_id=project_id)
    db.create_conversation("mail-target", owner["id"], "Target", project_id=project_id)
    db.create_conversation("mail-outside", owner["id"], "Outside")

    first, created = db.create_conversation_mailbox_message(
        owner_id=owner["id"],
        source_conversation_id="mail-source",
        target_conversation_id="mail-target",
        idempotency_key="mail-request-0001",
        payload={
            "summary": "Review evidence; api_key=top-secret",
            "thinking": "private chain",
            "approval": {"arguments": {"path": "private.txt"}},
        },
    )
    retry, created_retry = db.create_conversation_mailbox_message(
        owner_id=owner["id"],
        source_conversation_id="mail-source",
        target_conversation_id="mail-target",
        idempotency_key="mail-request-0001",
        payload={"summary": "duplicate"},
    )
    assert created is True and created_retry is False
    assert retry["id"] == first["id"]
    serialized = str(first)
    assert "top-secret" not in serialized
    assert "private chain" not in serialized
    assert "private.txt" not in serialized
    assert first["policy"]["execution_authority"] is False
    assert db.get_conversation_mailbox(first["id"], other["id"]) is None

    delivered = db.list_conversation_mailbox(owner["id"], "mail-target")
    assert delivered[0]["status"] == "delivered"
    acknowledged = db.transition_conversation_mailbox(first["id"], owner["id"], "acknowledge")
    assert acknowledged["status"] == "acknowledged"
    with pytest.raises(ValueError, match="same_project_scope"):
        db.create_conversation_mailbox_message(
            owner_id=owner["id"],
            source_conversation_id="mail-source",
            target_conversation_id="mail-outside",
            idempotency_key="mail-request-0002",
            payload={"summary": "must fail"},
        )


def test_deletion_tombstone_prevents_offline_resurrection(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("tombstone-owner", "pw12345678")
    other = db.create_user("tombstone-other", "pw12345678")
    db.create_conversation("delete-me", owner["id"], "Delete me")
    db.create_message("delete-me", "user", "durable content")
    revision = int(db.get_conversation("delete-me")["revision"])

    assert db.delete_conversation("delete-me", owner_id=other["id"]) is False
    assert db.get_conversation("delete-me") is not None
    assert db.delete_conversation("delete-me", owner_id=owner["id"]) is True
    assert db.get_conversation("delete-me") is None
    tombstones = db.list_conversation_tombstones(owner["id"])
    assert tombstones[0]["conversation_id"] == "delete-me"
    assert tombstones[0]["revision"] == revision + 1
    assert db.list_conversation_tombstones(other["id"]) == []


def test_tombstone_cursor_does_not_skip_equal_timestamps(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("tombstone-cursor-owner", "pw12345678")
    with db._conn() as conn:
        for conversation_id in ("a", "b", "c"):
            conn.execute(
                "INSERT INTO conversation_tombstones"
                "(conversation_id,owner_id,revision,deleted_at,reason) VALUES(?,?,?,?,?)",
                (conversation_id, owner["id"], 1, 42.0, "test"),
            )
    first = db.list_conversation_tombstones(owner["id"], since=0, limit=2)
    assert [row["conversation_id"] for row in first] == ["a", "b"]
    second = db.list_conversation_tombstones(
        owner["id"], since=42.0, after_id="b", limit=2,
    )
    assert [row["conversation_id"] for row in second] == ["c"]


def test_metadata_edits_do_not_move_recent_content_activity(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("metadata-activity-owner", "pw12345678")
    project_id = db.create_project(owner["id"], "Metadata project")
    db.create_conversation("older-content", owner["id"], "Older")
    db.create_message("older-content", "user", "older durable content")
    db.create_conversation("newer-content", owner["id"], "Newer")
    db.create_message("newer-content", "user", "newer durable content")
    with db._conn() as conn:
        conn.execute(
            "UPDATE conversations SET content_activity_at=1000,updated_at=1000 WHERE id='older-content'"
        )
        conn.execute(
            "UPDATE conversations SET content_activity_at=2000,updated_at=2000 WHERE id='newer-content'"
        )

    assert db.update_conversation("older-content", title="Renamed", pinned=1)
    renamed = db.get_conversation("older-content")
    assert float(renamed["content_activity_at"]) == 1000
    assert float(renamed["metadata_updated_at"]) > 1000
    unassigned = db.list_conversations(
        owner["id"], meaningful_only=True, scope="unassigned", limit=10,
    )
    # Pinning is an explicit navigation priority, but the content timestamp is
    # still truthful and is not rewritten to "today" by the metadata edit.
    assert unassigned[0]["id"] == "older-content"
    assert float(unassigned[0]["last_activity_at"]) == 1000

    assert db.assign_conv_to_project("older-content", project_id, owner["id"])
    assigned = db.get_conversation("older-content")
    assert float(assigned["content_activity_at"]) == 1000
    project_rows = db.list_conversations(
        owner["id"], meaningful_only=True, scope="project", project_id=project_id, limit=10,
    )
    assert float(project_rows[0]["last_activity_at"]) == 1000


def test_task_transition_has_server_lineage_and_completed_with_limits(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    from hashmm.agent import work_runtime
    from hashmm.agent.task_state import public_contract

    owner = db.create_user("task-lineage-owner", "pw12345678")
    project_id = db.create_project(owner["id"], "Task project")
    db.create_conversation("task-chat", owner["id"], "Task", project_id=project_id)
    run = work_runtime.create_run(
        user_id=owner["id"],
        kind="chat",
        source_id="task-turn-1",
        conv_id="task-chat",
        title="Verified task",
        status="draft",
    )
    updated = work_runtime.append_event_once(
        run["id"],
        user_id=owner["id"],
        event_type="admitted",
        status="running",
        summary="Start verified work",
        payload={"owner_id": "attacker", "conversation_id": "other", "turn_id": "turn-1"},
        idempotency_key="task-event-0001",
        expected_revision=run["revision"],
    )
    assert updated["state"] == "applied"
    detail = work_runtime.get_run(run["id"], owner["id"], after_seq=1)
    transition = detail["events"][0]["payload"]["task_transition"]
    assert transition["owner_id"] == owner["id"]
    assert transition["project_id"] == project_id
    assert transition["conversation_id"] == "task-chat"
    assert transition["work_run_id"] == run["id"]
    assert transition["checkpoint_id"]
    assert transition["from_state"] == "draft"
    assert transition["to_state"] == "running"
    assert public_contract("observed")["canonical_state"] == "completed_with_limits"
    assert public_contract("completed_with_limits")["terminal"] is True


def test_two_thousand_chat_keyset_has_no_duplicates_or_omissions(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("pagination-owner", "pw12345678")
    with db._conn() as conn:
        conversations = [
            (f"bulk-{index:04d}", owner["id"], f"Chat {index}", 1000 + index, 1000 + index, 1000 + index)
            for index in range(2005)
        ]
        conn.executemany(
            "INSERT INTO conversations(id,user_id,title,created_at,updated_at,content_activity_at) VALUES (?,?,?,?,?,?)",
            conversations,
        )
        conn.executemany(
            "INSERT INTO messages(id,conv_id,role,content,created_at) VALUES (?,?,?,?,?)",
            [(f"msg-{index:04d}", f"bulk-{index:04d}", "user", "durable", 1000 + index) for index in range(2005)],
        )

    found: list[str] = []
    cursor = None
    while True:
        page = db.list_conversations(
            owner["id"], limit=137, meaningful_only=True, scope="unassigned", cursor=cursor,
        )
        if not page:
            break
        found.extend(row["id"] for row in page)
        last = page[-1]
        cursor = (int(last["pinned"] or 0), float(last["last_activity_at"]), last["id"])
    assert len(found) == 2005
    assert len(set(found)) == 2005
    assert found[0] == "bulk-2004"
    assert found[-1] == "bulk-0000"


def test_cloud_merge_preserves_newer_project_and_obeys_tombstone(tmp_db, monkeypatch):
    db = _fresh(tmp_db, monkeypatch)
    owner = db.create_user("cloud-merge-owner", "pw12345678")
    project_id = db.create_project(owner["id"], "Cloud project")
    db.create_conversation("cloud-chat", owner["id"], "Local", project_id=project_id)
    db.create_message("cloud-chat", "user", "content")
    local = db.get_conversation("cloud-chat")

    # A legacy cloud row has no project/revision metadata and is older.  It
    # must not eject the Chat from its server-owned project.
    assert db.merge_cloud_conversation({
        "id": "cloud-chat", "title": "Old cloud", "updated_at": float(local["updated_at"]) - 10,
        "created_at": float(local["created_at"]), "pinned": False, "archived": False,
    }, owner["id"]) is False
    assert db.get_conversation("cloud-chat")["project_id"] == project_id

    revision = int(db.get_conversation("cloud-chat")["revision"])
    assert db.delete_conversation("cloud-chat", owner_id=owner["id"])
    assert db.merge_cloud_conversation({
        "id": "cloud-chat", "title": "Stale resurrection", "project_id": project_id,
        "revision": revision, "updated_at": float(local["updated_at"]),
        "created_at": float(local["created_at"]), "pinned": False, "archived": False,
    }, owner["id"]) is False
    assert db.get_conversation("cloud-chat") is None
