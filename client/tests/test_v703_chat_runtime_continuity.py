"""V703: one durable Chat run across approvals, projection and sidebar state."""
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


def test_exact_approval_lineage_never_merges_distinct_tool_calls(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent import work_runtime
    from hashmm.agent.permissions import approval_fingerprint

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    user = db.create_user("v703-owner", "pw12345678")
    conv_id = "v703-approval"
    db.create_conversation(conv_id, user["id"], "审批连续性")
    msg_id = db.create_message(conv_id, "assistant", "", status="streaming")
    run = work_runtime.create_run(
        user_id=user["id"], kind="chat", source_id="turn-v703", conv_id=conv_id
    )
    args = {"command": "echo exact"}
    cwd = str(db.conv_files_dir(conv_id))
    fingerprint = approval_fingerprint("run_shell", args, cwd)

    first = db.create_tool_approval_request(
        user_id=user["id"], conv_id=conv_id, message_id=msg_id,
        work_run_id=run["id"], step_id="iteration-1", call_id="tc-1",
        scope={"scope_id": "scope-1", "allowed_tools": ["run_shell"]},
        fingerprint=fingerprint, tool_name="run_shell", arguments=args, cwd=cwd,
    )
    replay = db.create_tool_approval_request(
        user_id=user["id"], conv_id=conv_id, message_id=msg_id,
        work_run_id=run["id"], step_id="iteration-1", call_id="tc-1",
        fingerprint=fingerprint, tool_name="run_shell", arguments=args, cwd=cwd,
    )
    second_call = db.create_tool_approval_request(
        user_id=user["id"], conv_id=conv_id, message_id=msg_id,
        work_run_id=run["id"], step_id="iteration-2", call_id="tc-2",
        fingerprint=fingerprint, tool_name="run_shell", arguments=args, cwd=cwd,
    )

    assert replay["id"] == first["id"]
    assert second_call["id"] != first["id"]
    public = db.public_tool_approval(first)
    assert public["schema"] == "hashmm.tool-approval.v2"
    assert public["work_run_id"] == run["id"]
    assert public["step_id"] == "iteration-1"
    assert public["call_id"] == "tc-1"
    assert "scope" not in public


def test_sse_failure_and_waiting_states_are_not_relabelled_as_delivery(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    user = db.create_user("v703-projector", "pw12345678")
    failed = work_runtime.create_run(
        user_id=user["id"], kind="chat", source_id="v703-failed"
    )
    projector = work_runtime.SSEProjector(failed["id"], user["id"])
    projector.observe(
        'event: done\ndata: {"status":"error","stop_reason":"delivery_incomplete"}\n\n'
    )
    projector.close_if_open()
    failed_detail = work_runtime.get_run(failed["id"], user["id"])
    assert failed_detail["status"] == "blocked"
    assert [event["type"] for event in failed_detail["events"]].count("interrupted") == 0

    waiting = work_runtime.create_run(
        user_id=user["id"], kind="chat", source_id="v703-waiting"
    )
    waiting_projector = work_runtime.SSEProjector(waiting["id"], user["id"])
    waiting_projector.observe(
        'event: done\ndata: {"status":"waiting_approval","stop_reason":"waiting_approval"}\n\n'
    )
    waiting_projector.close_if_open()
    assert work_runtime.get_run(waiting["id"], user["id"])["status"] == "waiting_approval"


def test_sidebar_projection_hides_shells_but_materializes_real_work(tmp_db, monkeypatch):
    from hashmm.api import supabase_sync
    from hashmm.agent import work_runtime

    monkeypatch.setattr(supabase_sync, "enabled", lambda: False)
    db = _fresh_db(tmp_db)
    user = db.create_user("v703-sidebar", "pw12345678")
    db.create_conversation("empty-shell", user["id"], "尚未开始")
    db.create_conversation("message-chat", user["id"], "真实消息")
    db.create_message("message-chat", "user", "开始任务")
    db.create_conversation("run-chat", user["id"], "只有运行记录")
    work_runtime.create_run(
        user_id=user["id"], kind="chat", source_id="v703-real-run",
        conv_id="run-chat",
    )

    all_ids = {item["id"] for item in db.list_conversations(user["id"])}
    visible_ids = {
        item["id"] for item in db.list_conversations(
            user["id"], meaningful_only=True
        )
    }
    assert all_ids == {"empty-shell", "message-chat", "run-chat"}
    assert visible_ids == {"message-chat", "run-chat"}

