from __future__ import annotations

from hashmm.agent import team


def _new(team_id: str = "tm-test") -> None:
    team._team_new(
        team_id,
        {"uid": "owner-1", "sub": "owner@example.com"},
        "完成一个长任务",
        "conv-1",
        "team.html",
        [{"role": "研究员", "task": "核对事实", "agent_id": "researcher"}],
    )


def test_terminal_team_survives_process_local_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    team._reset_team_store_for_tests()
    _new()
    team._team_role("tm-test", 0, "ok", finding="已核对", ms=12)
    assert team._team_done("tm-test", "最终结果", 1, 1) == "done"

    team._reset_team_store_for_tests()
    restored = team.get_team("tm-test", "owner-1")
    assert restored is not None
    assert restored["status"] == "done"
    assert restored["final"] == "最终结果"
    assert restored["roles"][0]["state"] == "ok"
    assert team.get_team("tm-test", "another-user") is None


def test_inflight_team_is_truthfully_interrupted_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    team._reset_team_store_for_tests()
    _new("tm-running")
    team._team_role("tm-running", 0, "run")

    team._reset_team_store_for_tests()
    restored = team.get_team("tm-running", "owner-1")
    assert restored is not None
    assert restored["status"] == "failed"
    assert "后端重启" in restored["recovery_reason"]
    assert restored["roles"][0]["state"] == "fail"
    assert any(item.get("node") == "control:recovery" for item in restored["trace"])


def test_team_subagent_stop_hook_is_added_to_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    team._reset_team_store_for_tests()
    _new("tm-hooks")

    import hashmm.hooks as hooks

    monkeypatch.setattr(hooks, "run_subagent_stop_hooks", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        hooks,
        "get_hook_runs",
        lambda _ctx: [{
            "lifecycle": "SubagentStop",
            "name": "quality-gate",
            "status": "completed",
            "latency_ms": 4,
        }],
    )
    team._run_team_subagent_stop_hooks(
        "tm-hooks", {"role": "研究员", "agent_id": "researcher"},
        "done", "可信结果", "owner-1",
    )

    restored = team.get_team("tm-hooks", "owner-1")
    assert restored is not None
    assert any(
        item.get("node") == "hook:quality-gate" and item.get("state") == "done"
        for item in restored["trace"]
    )
