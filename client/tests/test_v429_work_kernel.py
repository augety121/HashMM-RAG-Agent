"""V429 WorkKernel event source and control uncertainty regressions."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_control_command_writes_one_idempotent_event_chain(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db)
    from hashmm.agent import loop_engine, work_control, work_runtime

    user = db.create_user("kernel-events", "pw12345678")
    run = work_runtime.create_run(
        user_id=user["id"], kind="loop", source_id="kernel-loop", status="running",
    )
    monkeypatch.setattr(loop_engine, "pause_loop", lambda _source, _owner: True)
    result = asyncio.run(work_control.execute_command(
        run["id"],
        user={"uid": user["id"]},
        command_id="cmd-kernel-events",
        action="pause",
        expected_revision=run["revision"],
    ))
    assert result["ok"] is True
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT event_type,idempotency_key,expected_revision "
            "FROM work_events WHERE run_id=? ORDER BY seq",
            (run["id"],),
        ).fetchall()
    control = [row for row in rows if row["event_type"] == "control_requested"]
    assert len(control) == 1
    assert control[0]["idempotency_key"] == "work-control:cmd-kernel-events:control_requested"
    assert int(control[0]["expected_revision"]) == run["revision"]


def test_event_persistence_failure_never_dispatches_executor(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db)
    from hashmm.agent import loop_engine, work_control, work_runtime

    user = db.create_user("kernel-fail-closed", "pw12345678")
    run = work_runtime.create_run(
        user_id=user["id"], kind="loop", source_id="kernel-loop-fail", status="running",
    )
    calls = {"pause": 0}

    def pause(_source, _owner):
        calls["pause"] += 1
        return True

    monkeypatch.setattr(loop_engine, "pause_loop", pause)
    monkeypatch.setattr(
        work_runtime,
        "append_event_once",
        lambda *_args, **_kwargs: {"state": "not_found"},
    )
    result = asyncio.run(work_control.execute_command(
        run["id"],
        user={"uid": user["id"]},
        command_id="cmd-kernel-fail",
        action="pause",
        expected_revision=run["revision"],
    ))
    assert result["ok"] is False
    assert result["error"] == "event_persist_failed"
    assert calls["pause"] == 0


def test_side_effect_idempotency_is_scoped_to_owner_and_conversation():
    from hashmm.agent.loop import _side_effect_idempotency_key

    args = {"filename": "report.md", "content": "same bytes"}
    same_retry = _side_effect_idempotency_key(
        "create_file", args, user_id="owner-a", conv_id="conv-a",
    )

    assert same_retry == _side_effect_idempotency_key(
        "create_file", args, user_id="owner-a", conv_id="conv-a",
    )
    assert same_retry != _side_effect_idempotency_key(
        "create_file", args, user_id="owner-b", conv_id="conv-a",
    )
    assert same_retry != _side_effect_idempotency_key(
        "create_file", args, user_id="owner-a", conv_id="conv-b",
    )
