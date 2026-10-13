"""V428 regressions for the V390-V425 durable work projection."""
from __future__ import annotations


def test_loop_runtime_admission_failure_is_visible_and_not_claimed_synced(monkeypatch):
    from hashmm.agent import loop_engine, work_runtime

    def fail_create(**_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(work_runtime, "create_run", fail_create)
    loop = {"id": "g-runtime", "user": "alice", "conv_id": "c1", "goal": "inspect"}
    loop_engine._runtime_create(loop)
    assert loop["work_runtime_state"] == "degraded"
    assert "database unavailable" in loop["work_runtime_error"]
    assert "work_run_id" not in loop


def test_loop_runtime_admission_requires_a_real_run_id(monkeypatch):
    from hashmm.agent import loop_engine, work_runtime

    monkeypatch.setattr(work_runtime, "create_run", lambda **_kwargs: {"status": "queued"})
    loop = {"id": "g-no-id", "user": "alice", "conv_id": "c1", "goal": "inspect"}
    loop_engine._runtime_create(loop)
    assert loop["work_runtime_state"] == "degraded"
    assert "no run id" in loop["work_runtime_error"]
    assert "work_run_id" not in loop


def test_loop_runtime_admission_is_idempotent_after_link(monkeypatch):
    from hashmm.agent import loop_engine, work_runtime

    calls = {"count": 0}

    def create(**_kwargs):
        calls["count"] += 1
        return {"id": "wr-1"}

    monkeypatch.setattr(work_runtime, "create_run", create)
    loop = {"id": "g-linked", "user": "alice", "conv_id": "c1", "goal": "inspect"}
    loop_engine._runtime_create(loop)
    loop_engine._runtime_create(loop)
    assert loop["work_runtime_state"] == "linked"
    assert calls["count"] == 1


def test_loop_runtime_events_are_idempotent_and_expose_projection_failures(monkeypatch):
    from hashmm.agent import loop_engine, work_runtime

    captured = []

    def append_once(run_id, **kwargs):
        captured.append((run_id, kwargs))
        return {"state": "applied"}

    monkeypatch.setattr(work_runtime, "append_event_once", append_once)
    loop = {
        "id": "g-events",
        "user": "alice",
        "work_run_id": "wr-events",
        "generation": 3,
        "runtime_event_seq": 0,
        "work_runtime_state": "linked",
    }
    loop_engine._runtime_event(loop, "started", "started", status="running")
    loop_engine._runtime_event(loop, "progress", "progress", status="running")

    assert [item[1]["idempotency_key"] for item in captured] == [
        "loop:g-events:3:1:started",
        "loop:g-events:3:2:progress",
    ]
    assert loop["work_runtime_state"] == "linked"
    assert loop["work_runtime_error"] == ""

    def reject(*_args, **_kwargs):
        return {"state": "not_found"}

    monkeypatch.setattr(work_runtime, "append_event_once", reject)
    loop_engine._runtime_event(loop, "finished", "finished", status="completed")
    assert loop["work_runtime_state"] == "degraded"
    assert "rejected" in loop["work_runtime_error"]
