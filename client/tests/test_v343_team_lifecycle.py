"""V343 team lifecycle, ownership and Chat control-plane regressions."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

from hashmm.agent import team
from hashmm.api.routes.conversations import (
    _external_evidence_contract, _external_tool_trace,
)


def _new(team_id: str, uid: str = "owner") -> None:
    team._team_new(
        team_id, {"uid": uid, "sub": uid}, "核验一份报告", "", "",
        [{"role": "研究员", "task": "找证据"}, {"role": "审校员", "task": "查漏洞"}],
        "parallel",
    )


def _drop(team_id: str) -> None:
    with team._TEAMS_LOCK:
        team._TEAMS.pop(team_id, None)


def test_stop_is_owner_scoped_idempotent_and_explicit():
    team_id = "tm-v343-owner"
    _new(team_id)
    try:
        assert team.request_team_stop(team_id, "attacker") is None
        first = team.request_team_stop(team_id, "owner")
        second = team.request_team_stop(team_id, "owner")
        assert first and first["status"] == "stopping"
        assert second and second["status"] == "stopping"
        assert second["stop_requested"] is True
        assert sum(1 for item in second["trace"] if item["state"] == "stopping") == 1

        stopped = team._team_stopped(team_id, "用户停止")
        assert stopped and stopped["status"] == "stopped"
        assert {role["state"] for role in stopped["roles"]} == {"stop"}
    finally:
        _drop(team_id)


def test_orchestration_record_keeps_team_identity_and_stop_state():
    record = team._orchestration_record(
        "tm-control", "pipeline",
        [{"role": "研究员", "task": "检索", "state": "ok", "ms": 12},
         {"role": "审校员", "task": "复核", "state": "stop", "err": "用户停止"}],
        "stopped", "tm-before",
    )
    assert record["node"] == "orchestration"
    assert record["team_id"] == "tm-control"
    assert record["status"] == "stopped"
    assert record["retry_of"] == "tm-before"
    assert [member["status"] for member in record["members"]] == ["done", "stopped"]


def test_terminal_commit_is_atomic_against_a_stop_request():
    team_id = "tm-v343-commit"
    _new(team_id)
    try:
        assert team.request_team_stop(team_id, "owner")["status"] == "stopping"
        status = team._team_done(team_id, "must not commit", 2, 2)
        state = team.get_team(team_id, "owner")
        assert status == "stopped"
        assert state and state["status"] == "stopped"
        assert state["final"] == ""
    finally:
        _drop(team_id)


class _SlowLLM:
    def quick_call(self, system, user, max_tok=None):
        if "团队汇总者" in system:
            return "不应交付的汇总"
        time.sleep(0.15)
        return "模型调用完成，但停止后必须丢弃。"


def test_running_team_stops_cooperatively_without_fake_summary(monkeypatch):
    from hashmm.api import model_manager

    monkeypatch.setattr(model_manager, "get_active_llm_fn", lambda: (_SlowLLM(), "slow-mock"))
    monkeypatch.setattr(team, "_retrieve_team_evidence", lambda _goal: ("", [], "empty"))

    async def drive():
        result = await team.start_team(
            {"uid": "stop-owner", "sub": "tester"}, "停止语义回归", "",
            roles_override=[{"role": "甲", "task": "慢调用"}, {"role": "乙", "task": "慢调用"}],
            mode="parallel",
        )
        team_id = result["team_id"]
        for _ in range(100):
            state = team.get_team(team_id, "stop-owner")
            if state and any(role["state"] == "run" for role in state["roles"]):
                break
            await asyncio.sleep(0.005)
        requested = team.request_team_stop(team_id, "stop-owner")
        assert requested and requested["status"] == "stopping"
        for _ in range(200):
            state = team.get_team(team_id, "stop-owner")
            if state and state["status"] == "stopped":
                return team_id, state
            await asyncio.sleep(0.01)
        return team_id, team.get_team(team_id, "stop-owner")

    team_id, state = asyncio.run(drive())
    try:
        assert state and state["status"] == "stopped"
        assert not state.get("final")
        assert all(role["state"] == "stop" for role in state["roles"])
        assert any(item["state"] == "stopped" for item in state["trace"])
    finally:
        _drop(team_id)


def test_retry_starts_new_auditable_team_only_from_terminal_owner(monkeypatch):
    team_id = "tm-v343-retry"
    _new(team_id)
    team._team_stopped(team_id)
    captured = {}

    async def fake_start(user, goal, conv_id, roles_override=None, mode="parallel", retry_of=""):
        captured.update(user=user, goal=goal, conv_id=conv_id, roles=roles_override,
                        mode=mode, retry_of=retry_of)
        return {"ok": True, "team_id": "tm-new", "retry_of": retry_of}

    monkeypatch.setattr(team, "start_team", fake_start)
    try:
        assert asyncio.run(team.retry_team({"uid": "attacker"}, team_id)) is None
        result = asyncio.run(team.retry_team({"uid": "owner", "sub": "owner"}, team_id))
        assert result and result["team_id"] == "tm-new"
        assert captured["retry_of"] == team_id
        assert captured["mode"] == "parallel"
        assert [role["role"] for role in captured["roles"]] == ["研究员", "审校员"]
    finally:
        _drop(team_id)


def test_routes_and_chat_surfaces_use_real_lifecycle_endpoints():
    root = Path(__file__).parents[1]
    route = (root / "hashmm" / "api" / "routes" / "team_ops.py").read_text("utf-8")
    right = (root / "frontend-next" / "components" / "RightContextPanel.tsx").read_text("utf-8")
    studio = (root / "frontend-next" / "components" / "desktop" / "AgentsStudioView.tsx").read_text("utf-8")

    assert '@router.post("/{team_id}/stop"' in route
    assert '@router.post("/{team_id}/retry"' in route
    assert 'request_team_stop(team_id, user.get("uid", ""))' in route
    assert "await teamStop(activeTeamId)" in right
    assert "await teamRetry(activeTeamId)" in right
    assert "await teamStop(status.team_id)" in studio
    assert "await teamRetry(status.team_id)" in studio


def test_stopped_canvas_state_contains_no_emoji():
    html = team._canvas_html("停止画布回归", [
        {"role": "研究员", "task": "检索"}, {"role": "审校员", "task": "复核"},
    ])
    assert "st-stop" in html
    assert "已停止" not in html  # 初始画布不能谎报终态
    stopped = team._st(0, "stop")
    assert "class='st st-stop'" in stopped
    assert "id='tm-0'" in stopped
    assert "role='status'" in stopped
    assert "data-state='stop'" in stopped
    assert "aria-label='已停止'" in stopped
    assert stopped.endswith(">已停止</span>")
    assert not any(char in stopped for char in ("🛑", "⏹", "✅", "❌"))


def test_browser_sources_are_bounded_redacted_and_revalidated():
    sources, ledger = _external_evidence_contract(
        "Project was released in 2026 [1].",
        [{
            "citation_id": 99,
            "source_id": "browser-7",
            "filename": "Example facts",
            "section": "https://user:pass@example.com/facts?lang=en&access_token=secret#private",
            "text": "Project was released in 2026. " + ("x" * 2000),
            "score": float("nan"),
            "method": "browser_use",
        }],
    )
    assert len(sources) == 1
    assert sources[0]["id"] == 1
    assert sources[0]["section"] == "https://example.com/facts?lang=en"
    assert "secret" not in str(sources)
    assert "user:pass" not in str(sources)
    assert len(sources[0]["text"]) == 220
    assert ledger["semantic_entailment_verified"] is False
    assert ledger["total_factual_claims"] >= 1


def test_external_evidence_does_not_upgrade_uncited_claims_and_trace_is_whitelisted():
    _, ledger = _external_evidence_contract(
        "Project was released in 2026.",
        [{"filename": "facts", "section": "https://example.com", "text": "Project was released in 2026.",
          "method": "browser_use"}],
    )
    assert ledger["supported_claims"] == 0
    assert ledger["review_required"] is True

    trace = _external_tool_trace([{
        "id": "step-1", "node": "browser:read", "detail": "ok", "status": "failed",
        "elapsed_ms": 15, "members": [{"secret": "must not persist"}], "unknown": "drop",
    }] * 50)
    assert len(trace) == 40
    assert trace[0]["status"] == "error"
    assert trace[0]["elapsed_ms"] == 15
    assert "members" not in trace[0] and "unknown" not in trace[0]


def test_browser_evidence_and_right_panel_modes_are_wired_to_chat():
    root = Path(__file__).parents[1]
    main = (root / "desktop" / "main.js").read_text("utf-8")
    trajectory = (root / "desktop" / "modules" / "browser-trajectory.js").read_text("utf-8")
    route = (root / "hashmm" / "api" / "routes" / "conversations.py").read_text("utf-8")
    right = (root / "frontend-next" / "components" / "RightContextPanel.tsx").read_text("utf-8")
    chat = (root / "frontend-next" / "components" / "ChatArea.tsx").read_text("utf-8")

    assert "browserEvidenceBundle(_browserEvents, afterSeq)" in main
    assert "persisted.meta" in main
    assert 'action !== "read"' in trajectory
    assert 'body["sources"], body["groundings"] = _external_evidence_contract' in route
    assert 'pendingRunMode: "browser"' in right
    assert 'pendingRunMode: "deep"' in right
    assert 'setBrowserMode(isDesktopEnv)' in chat
