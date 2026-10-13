"""V499-V518 owner-facing workspace, routines and active-turn attachments."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _fresh_db(tmp_path: Path, monkeypatch):
    from hashmm.api import database as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workspace.sqlite")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(db, "CONV_FILES_ROOT", tmp_path / "data" / "conversations")
    db.init_db()
    return db


def test_project_goal_brief_is_owner_scoped_and_revisioned(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    owner = db.create_user("brief-owner", "safe-pass-123", role="user")
    other = db.create_user("brief-other", "safe-pass-123", role="user")
    project_id = db.create_project(
        owner["id"],
        "客户调研",
        goal="找出流失的主要原因",
        deliverable="一份可审阅的调研报告",
        success_criteria=["引用访谈证据", "给出三项优先行动"],
        permission_mode="read_only",
    )

    project = db.get_project_for_user(project_id, owner["id"])
    assert project["goal"] == "找出流失的主要原因"
    assert project["success_criteria"] == ["引用访谈证据", "给出三项优先行动"]
    assert project["permission_mode"] == "read_only"
    assert project["revision"] == 1
    assert db.get_project_for_user(project_id, other["id"]) is None

    assert db.update_project(
        project_id,
        owner["id"],
        deliverable="报告与行动清单",
        success_criteria=["数字有来源"],
    )
    updated = db.get_project_for_user(project_id, owner["id"])
    assert updated["revision"] == 2
    assert updated["deliverable"] == "报告与行动清单"
    assert updated["success_criteria"] == ["数字有来源"]

    db.create_conversation("conv-brief", owner["id"], "项目对话")
    assert db.assign_conv_to_project("conv-brief", project_id, owner["id"])
    from hashmm.api.context import ContextBuilder
    context = ContextBuilder("conv-brief", owner["id"]).build([], db=db)
    assert "当前项目目标书" in context
    assert "报告与行动清单" in context
    assert "数字有来源" in context
    assert "不能扩大文件、网络或工具权限" in context
    assert ContextBuilder("conv-brief", other["id"]).build([], db=db) == ""


def test_user_routine_mutations_cannot_cross_owner(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm import scheduler

    task = scheduler.create_task(
        action="daily_brief",
        name="我的简报",
        params={"owner_uid": "owner-a"},
        tenant_id="owner-a",
        created_by="alice",
        db=db,
    )
    assert scheduler.get_task_for_tenant(task["id"], "owner-a", db=db)
    assert scheduler.get_task_for_tenant(task["id"], "owner-b", db=db) == {}
    assert scheduler.set_enabled_for_tenant(task["id"], "owner-b", False, db=db) is False
    assert scheduler.delete_task_for_tenant(task["id"], "owner-b", db=db) is False
    assert scheduler.run_task_now_for_tenant(task["id"], "owner-b", db=db) == {
        "ok": False,
        "error": "task not found",
    }
    assert scheduler.set_enabled_for_tenant(task["id"], "owner-a", False, db=db) is True


def test_unified_user_search_never_returns_another_owners_rows(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm.api.routes import user_work

    owner = db.create_user("search-owner", "safe-pass-123", role="user")
    other = db.create_user("search-other", "safe-pass-123", role="user")
    own_project = db.create_project(owner["id"], "火星计划", goal="检索目标")
    db.create_project(other["id"], "火星秘密", goal="不可见")
    db.create_conversation("conv-own", owner["id"], "火星对话")
    db.create_message("conv-own", "user", "分析火星资料")
    db.create_conversation("conv-other", other["id"], "火星隐私")
    db.create_message("conv-other", "user", "不能泄露")
    monkeypatch.setattr(
        user_work,
        "require_auth",
        lambda _request: {"uid": owner["id"], "sub": "search-owner", "role": "user"},
    )

    result = asyncio.run(
        user_work.search_user_work(SimpleNamespace(), q="火星", limit=30)
    )
    ids = {item["id"] for item in result["items"]}
    assert own_project in ids
    assert "conv-own" in ids
    assert "conv-other" not in ids
    assert all("秘密" not in item["title"] for item in result["items"])


def test_user_overview_filters_projects_and_honors_etag(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm.api.routes import user_work

    owner = db.create_user("overview-owner", "safe-pass-123", role="user")
    other = db.create_user("overview-other", "safe-pass-123", role="user")
    own_project = db.create_project(owner["id"], "Owner project", goal="Owner goal")
    foreign_project = db.create_project(other["id"], "Foreign project", goal="Private")
    monkeypatch.setattr(
        user_work,
        "require_auth",
        lambda _request: {"uid": owner["id"], "sub": "overview-owner", "role": "user"},
    )
    monkeypatch.setattr(user_work.scheduler, "list_tasks", lambda _owner: [])
    monkeypatch.setattr(user_work.scheduler, "scheduler_enabled", lambda: True)
    monkeypatch.setattr(user_work.scheduler, "list_actions", lambda: ["daily_brief"])

    response = asyncio.run(
        user_work.user_work_overview(
            SimpleNamespace(headers={}), project_id=own_project
        )
    )
    payload = json.loads(response.body)
    assert payload["selected_project_id"] == own_project
    assert [item["id"] for item in payload["projects"]] == [own_project]
    assert payload["work"]["schema"] == "hashmm.work-feed.v1"
    assert response.headers["etag"]

    cached = asyncio.run(
        user_work.user_work_overview(
            SimpleNamespace(headers={"if-none-match": response.headers["etag"]}),
            project_id=own_project,
        )
    )
    assert cached.status_code == 304
    assert cached.body == b""

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            user_work.user_work_overview(
                SimpleNamespace(headers={}), project_id=foreign_project
            )
        )
    assert exc.value.status_code == 404


def test_active_turn_attachment_is_server_verified_and_persisted(tmp_path, monkeypatch):
    from hashmm.api.active_runs import registry
    from hashmm.api.routes import conversations as routes

    root = tmp_path / "conv"
    root.mkdir()
    payload = b"verified attachment"
    (root / "notes.txt").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    registry.clear()
    monkeypatch.setattr(
        routes, "require_conv_access",
        lambda _request, conv_id: {"id": conv_id, "user_id": "owner"},
    )
    monkeypatch.setattr(
        routes, "get_current_user",
        lambda _request: {"uid": "owner", "sub": "alice", "role": "user"},
    )
    monkeypatch.setattr(routes.db, "conv_files_dir", lambda _conv_id: root)
    writes = []
    monkeypatch.setattr(
        routes.db,
        "create_message",
        lambda conv_id, role, content, **kwargs: (
            writes.append((conv_id, role, content, kwargs)) or "message-file"
        ),
    )
    monkeypatch.setattr(routes.db, "audit", lambda *_args: None)
    run = registry.reserve("conv-file", "owner", "长任务", turn_id="turn-file")
    run.mark_steerable()
    body = routes.TurnSteerRequest(
        content="把附件加入比较",
        client_message_id="client-file",
        attachments=[{"filename": "notes.txt", "sha256": digest}],
    )

    result = asyncio.run(
        routes.steer_active_turn("conv-file", "turn-file", body, object())
    )
    assert result["accepted"] is True
    assert writes[0][3]["files"][0]["sha256"] == digest
    drained = run.drain_steering()
    assert drained[0]["attachments"][0]["filename"] == "notes.txt"

    bad = routes.TurnSteerRequest(
        content="错误摘要",
        client_message_id="client-bad",
        attachments=[{"filename": "notes.txt", "sha256": "0" * 64}],
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes.steer_active_turn("conv-file", "turn-file", bad, object())
        )
    assert exc.value.status_code == 409
    registry.clear()
