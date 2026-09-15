"""V627 durable long-horizon Chat and scheduled-work regressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _fresh_db(tmp_path: Path, monkeypatch):
    from hashmm.api import database as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "v627.sqlite")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(db, "CONV_FILES_ROOT", tmp_path / "data" / "conversations")
    db._pool = None
    db.init_db()
    return db


def test_long_horizon_handoff_is_bounded_redacted_and_excludes_private_reasoning():
    from hashmm.agent.long_horizon import (
        advance_handoff,
        initialise_handoff,
        render_handoff,
    )

    state = initialise_handoff({
        "goal": "完成一个可恢复、可验证的长代码任务",
        "plan": [
            {"id": "inspect", "text": "检查真实入口与依赖", "status": "pending"},
            {"id": "verify", "text": "运行确定性验证", "status": "pending"},
        ],
    }, run_id="run-v627")
    state, trace = advance_handoff(
        state,
        event_type="tool_start",
        status="running",
        summary="检查真实入口 api_key=top-secret-value",
        payload={"step_id": "inspect", "authorization": "Bearer private-token"},
        event_seq=2,
    )
    state, _ = advance_handoff(
        state,
        event_type="tool_done",
        status="completed",
        summary="入口和依赖清单已由工具读取",
        payload={
            "step_id": "inspect",
            "verification_status": "passed",
            "source": "filesystem",
        },
        event_seq=3,
    )

    rendered = render_handoff(state)
    serialised = json.dumps({"state": state, "trace": trace}, ensure_ascii=False)
    assert state["schema"] == "hashmm.long-horizon-handoff.v1"
    assert state["plan"][0]["status"] == "completed"
    assert state["verification"]["model_prose_is_evidence"] is False
    assert state["policy"]["private_reasoning_persisted"] is False
    assert trace["private_reasoning"] is False
    assert "top-secret-value" not in rendered + serialised
    assert "private-token" not in rendered + serialised
    assert "模型自述不是完成证据" in rendered
    assert len(rendered) <= 7_000


def test_work_runtime_projects_plan_evidence_and_trace(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm.agent import work_runtime

    owner = db.create_user("v627-runtime", "safe-pass-123", role="user")
    run = work_runtime.create_run(
        user_id=owner["id"],
        kind="workflow",
        source_id="v627:long-code",
        title="长代码任务",
        snapshot={
            "goal": "检查、修改并验证一个长代码任务",
            "run_manifest": {
                "task_contract": {
                    "goal": "检查、修改并验证一个长代码任务",
                    "plan": [
                        {"id": "inspect", "text": "检查入口", "status": "pending"},
                        {"id": "verify", "text": "运行测试", "status": "pending"},
                    ],
                },
            },
        },
    )
    first = work_runtime.append_event_once(
        run["id"],
        user_id=owner["id"],
        event_type="tool_start",
        status="running",
        summary="开始检查入口",
        payload={"step_id": "inspect", "source": "filesystem"},
        idempotency_key="v627-inspect-start",
    )
    second = work_runtime.append_event_once(
        run["id"],
        user_id=owner["id"],
        event_type="tool_done",
        status="running",
        summary="入口检查通过",
        payload={
            "step_id": "inspect",
            "verification_status": "passed",
            "source": "filesystem",
        },
        idempotency_key="v627-inspect-done",
        expected_revision=first["run"]["revision"],
    )

    detail = work_runtime.get_run(run["id"], owner["id"])
    handoff = detail["snapshot"]["long_horizon_handoff"]
    trace = detail["snapshot"]["task_trace"]
    assert second["state"] == "applied"
    assert handoff["plan"][0]["status"] == "completed"
    assert handoff["verification"]["status"] == "partially_verified"
    assert handoff["evidence"][0]["source"] == "filesystem"
    assert [item["type"] for item in trace["items"]][-2:] == [
        "tool_start", "tool_done",
    ]
    assert all(item["private_reasoning"] is False for item in trace["items"])
    canvas = work_runtime.build_work_canvas(detail, detail["events"])
    assert canvas["process"]["stages"] == [
        {"id": "inspect", "order": 1, "label": "检查入口", "status": "done"},
        {"id": "verify", "order": 2, "label": "运行测试", "status": "pending"},
    ]
    assert work_runtime.get_run(run["id"], "foreign-owner") is None


def test_scheduled_task_is_a_four_stage_owner_bound_long_run(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    from hashmm import scheduler
    from hashmm.agent import work_runtime

    owner = db.create_user("v627-scheduler", "safe-pass-123", role="user")
    action = "v627_deterministic_task"
    scheduler.register_action(action, lambda params: f"已读取 {params['scope']}")
    task = scheduler.create_task(
        action=action,
        name="资料健康检查",
        params={"owner_uid": owner["id"], "scope": "当前资料库"},
        tenant_id=owner["id"],
        created_by="v627-scheduler",
        db=db,
    )

    result = scheduler.run_task_now_for_tenant(task["id"], owner["id"], db=db)
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["resumable"] is False
    assert scheduler.run_task_now_for_tenant(task["id"], "foreign", db=db) == {
        "ok": False,
        "error": "task not found",
    }

    detail = work_runtime.get_run(result["work_run_id"], owner["id"])
    assert detail["status"] == "completed"
    assert detail["snapshot"]["task_type"] == "scheduled_long_task"
    plan = detail["snapshot"]["long_horizon_handoff"]["plan"]
    assert [item["id"] for item in plan] == [
        "schedule-scope",
        "schedule-execute",
        "schedule-verify",
        "schedule-deliver",
    ]
    assert all(item["status"] == "completed" for item in plan)
    trace_types = {
        item["type"] for item in detail["snapshot"]["task_trace"]["items"]
    }
    assert {
        "automation_started", "automation_result", "verification", "step_done",
    } <= trace_types


def test_persistent_compaction_reports_pressure_without_deleting_history():
    from contextlib import contextmanager
    from hashmm.agent.conv_compact import prepare_persistent_history

    rows = [
        {
            "_rowid": index,
            "id": f"m{index}",
            "role": "user" if index % 2 else "assistant",
            "content": (
                ("原始目标：持续完成长代码任务。" if index == 1 else f"第{index}轮。")
                + "边界、计划、证据与验收。" * 260
            ),
            "files": [],
            "created_at": float(index),
        }
        for index in range(1, 45)
    ]

    class FakeDb:
        state = None

        def get_context_compaction(self, _conv_id):
            return self.state

        def get_context_messages_after(self, _conv_id, after_rowid=0):
            return [dict(row) for row in rows if row["_rowid"] > after_rowid]

        def get_recent_messages(self, _conv_id, n=80):
            return [dict(row) for row in rows[-n:]]

        @contextmanager
        def _conn(self):
            class Conn:
                @staticmethod
                def execute(_sql, args):
                    found = any(row["_rowid"] == int(args[0]) for row in rows)

                    class Cursor:
                        @staticmethod
                        def fetchone():
                            return (1,) if found else None
                    return Cursor()
            yield Conn()

        def save_context_compaction(
            self, _conv_id, summary, through_rowid, source_messages,
            estimated_tokens, trigger="auto", expected_through_rowid=None,
        ):
            self.state = {
                "summary": summary,
                "through_rowid": through_rowid,
                "source_messages": source_messages,
                "estimated_tokens": estimated_tokens,
                "compaction_count": 1,
                "last_trigger": trigger,
            }
            return dict(self.state)

    original = [dict(row) for row in rows]
    prepared = prepare_persistent_history(
        FakeDb(), "conv-v627", token_budget=4_000, keep_recent_tokens=2_000,
    )
    telemetry = prepared.compaction
    assert prepared.compacted_now is True
    assert telemetry["schema"] == "hashmm.context-compaction.v1"
    assert telemetry["trigger"] == "automatic"
    assert telemetry["reason"] == "model_window_pressure"
    assert telemetry["tokens_after"] < telemetry["tokens_before"]
    assert telemetry["full_history_deleted"] is False
    assert rows == original
