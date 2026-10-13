from __future__ import annotations

import time

from hashmm.agent import dispatch


def test_claim_heartbeat_renews_long_running_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    task_id = dispatch.create_task("desktop", "browser_use", {"goal": "长任务"}, "u@example.com")
    claimed = dispatch.poll("desktop", created_by="u@example.com")
    assert claimed and claimed["task_id"] == task_id
    before = dispatch.get_task(task_id)
    assert before and before["status"] == "claimed"

    time.sleep(0.002)
    assert dispatch.heartbeat(task_id) is True
    after = dispatch.get_task(task_id)
    assert after and after["claimed_at"] > before["claimed_at"]

    assert dispatch.complete(task_id, True, "done") is True
    assert dispatch.heartbeat(task_id) is False


def test_task_heartbeat_can_keep_account_device_presence_online(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    assert dispatch.touch_runner(owner="owner-a", device_id="desktop-a",
                                 display_name="Work PC", app_version="1.0") is True
    mine = dispatch.runners_status(owner="owner-a", created_by="a@example.com")
    other = dispatch.runners_status(owner="owner-b", created_by="b@example.com")
    assert mine[0]["online"] is True
    assert mine[0]["device_id"] == "desktop-a"
    assert other == []
