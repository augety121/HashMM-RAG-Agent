"""V669 user-facing automation, timezone and owner-isolation regressions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from fastapi import HTTPException


def _fresh_db(tmp_path: Path, monkeypatch):
    from hashmm.api import database as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "automation.sqlite")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(db, "CONV_FILES_ROOT", tmp_path / "data" / "conversations")
    db.init_db()
    return db


def test_daily_automation_uses_users_iana_timezone():
    from hashmm import scheduler

    now = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc).timestamp()
    next_run = scheduler._compute_next_run(
        "daily", 3600, "09:30", now, "Asia/Shanghai"
    )
    actual = datetime.fromtimestamp(next_run, tz=timezone.utc)
    assert actual == datetime(2026, 7, 29, 1, 30, tzinfo=timezone.utc)
    assert scheduler.valid_timezone_name("Asia/Shanghai") is True
    assert scheduler.valid_timezone_name("not/a/timezone") is False


def test_owner_can_edit_schedule_without_rewriting_action_or_other_owner(
    tmp_path, monkeypatch
):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm import scheduler

    action = "v669_read_only"
    scheduler.register_action(action, lambda params: params["scope"])
    task = scheduler.create_task(
        action=action,
        name="旧名称",
        params={
            "owner_uid": "owner-a",
            "scope": "资料库",
            "timezone": "UTC",
            "result_destination": "work_ledger",
        },
        schedule_kind="interval",
        interval_seconds=3600,
        tenant_id="owner-a",
        created_by="alice",
        db=db,
    )

    assert scheduler.update_task_for_tenant(
        task["id"],
        "owner-b",
        name="越权修改",
        schedule_kind="daily",
        interval_seconds=7200,
        daily_at="08:00",
        params={},
        db=db,
    ) == {}

    updated = scheduler.update_task_for_tenant(
        task["id"],
        "owner-a",
        name="每日资料检查",
        schedule_kind="daily",
        interval_seconds=7200,
        daily_at="08:00",
        params={
            "owner_uid": "owner-a",
            "scope": "资料库",
            "timezone": "Asia/Shanghai",
            "result_destination": "work_ledger",
        },
        db=db,
    )
    assert updated["action"] == action
    assert updated["name"] == "每日资料检查"
    assert updated["schedule_kind"] == "daily"
    assert updated["daily_at"] == "08:00"
    params = json.loads(updated["params"])
    assert params["timezone"] == "Asia/Shanghai"
    assert params["result_destination"] == "work_ledger"


def test_user_routine_preferences_reject_invalid_timezone_and_destination():
    from hashmm.api.routes import user_work

    with pytest.raises(HTTPException) as invalid_timezone:
        user_work._preference_input({"timezone": "Moon/Base"})
    assert invalid_timezone.value.status_code == 422

    with pytest.raises(HTTPException) as invalid_destination:
        user_work._preference_input({
            "timezone": "UTC",
            "result_destination": "unverified_channel",
        })
    assert invalid_destination.value.status_code == 422


def test_public_routine_contract_exposes_truthful_delivery_defaults(monkeypatch):
    from hashmm.api.routes import user_work

    monkeypatch.setattr(
        user_work.work_runtime,
        "get_latest_run_by_source_prefix",
        lambda *_args, **_kwargs: None,
    )
    public = user_work._routine_public({
        "id": "routine-1",
        "name": "资料摘要",
        "action": "corpus_digest",
        "params": json.dumps({"timezone": "Asia/Shanghai"}),
        "schedule_kind": "daily",
        "interval_seconds": 3600,
        "daily_at": "09:00",
        "enabled": 1,
        "tenant_id": "owner-a",
    })
    assert public["timezone"] == "Asia/Shanghai"
    assert public["result_destination"] == "work_ledger"
    assert public["permission_mode"] == "read_only"


def test_ledger_destination_clears_stale_conversation_binding():
    from hashmm.api.routes import user_work

    params = {
        "conv_id": "private-conversation",
        "conv_owner_uid": "owner-a",
        "result_destination": "conversation",
    }
    user_work._apply_result_destination(params, "work_ledger")
    assert params == {"result_destination": "work_ledger"}
