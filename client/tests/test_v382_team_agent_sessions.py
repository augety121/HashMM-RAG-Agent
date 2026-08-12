from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _new_team(team, team_id: str) -> None:
    team._team_new(
        team_id, {"uid": "owner", "sub": "owner"}, "核验资料", "", "",
        [{"role": "研究员", "task": "找证据"}, {"role": "审校员", "task": "复核"}],
        "parallel",
    )


def _drop_team(team, team_id: str) -> None:
    with team._TEAMS_LOCK:
        team._TEAMS.pop(team_id, None)


def test_worker_announces_session_before_first_model_turn():
    from hashmm.agent.worker import Worker

    announced = []

    class LLM:
        def call_with_tools(self, messages, tools=None):
            assert announced and announced[0]["status"] == "running"
            return SimpleNamespace(message=SimpleNamespace(content="完成", tool_calls=[]))

    result = asyncio.run(Worker(LLM(), role="research", user_id="u1", conv_id="c1").run(
        "核验", on_session=announced.append,
    ))
    assert result["status"] == "completed"
    assert announced[0]["session_id"] == result["session_id"]


def test_team_persists_bounded_session_projection_and_manifest_identity():
    from hashmm.agent import team

    team_id = "tm-v382-session"
    _new_team(team, team_id)
    try:
        team._team_agent_session(team_id, 0, {
            "session_id": "as-session", "scope_id": "scope-child",
            "status": "running", "allowed_tools": ["kb_search", "web_search"],
        })
        team._team_agent_session(team_id, 0, {
            "session_id": "as-session", "scope_id": "scope-child",
            "status": "completed", "allowed_tools": ["kb_search", "web_search"],
        }, {"status": "completed", "tool_calls": 2,
            "steps": ["kb_search({'query': 'private'})", "web_search({'query': 'private'})"]})

        saved = team.get_team(team_id, "owner")
        role = saved["roles"][0]
        assert role["session_id"] == "as-session"
        assert role["scope_id"] == "scope-child"
        assert role["session_status"] == "completed"
        assert role["session_steps"] == ["kb_search", "web_search"]
        assert "private" not in str(role["session_steps"])

        member = team._orchestration_record(
            team_id, "parallel", saved["roles"], "done",
        )["members"][0]
        assert member["session_id"] == "as-session"
        assert member["scope_id"] == "scope-child"
        assert member["tool_calls"] == 2
    finally:
        _drop_team(team, team_id)


def test_owner_stop_interrupts_live_child_session():
    from hashmm.agent import team
    from hashmm.agent.execution_scope import build_root_scope
    from hashmm.agent.session import get_session_registry

    team_id = "tm-v382-stop"
    _new_team(team, team_id)
    registry = get_session_registry()
    row = registry.create(
        role="research", task="long task", owner_id="owner", conversation_id="c1",
        execution_scope=build_root_scope(
            owner_id="owner", conversation_id="c1", run_id=team_id,
            allowed_tools=["kb_search"], approval_mode="read_only", network_mode="deny",
        ), parent_session_id=team_id,
    )
    registry.start(row["session_id"])
    team._team_agent_session(team_id, 0, registry.get(row["session_id"]))
    try:
        assert team.request_team_stop(team_id, "attacker") is None
        stopped = team.request_team_stop(team_id, "owner")
        assert stopped and stopped["status"] == "stopping"
        assert registry.should_stop(row["session_id"]) is True
    finally:
        registry.finish(row["session_id"], "stopped")
        _drop_team(team, team_id)


def test_retrieval_run_records_graph_expansion_without_graph_contents():
    from hashmm.retrieval.run import RetrievalRun

    run = RetrievalRun("relationship query", acl_scoped=True)
    run.expanded(
        "knowledge_graph", 5, 7, considered=6,
        skipped_missing=1, skipped_forbidden=2,
    )
    saved = run.finish(evidence_count=7, total_candidates=12)
    assert saved["expansions"] == [{
        "stage": "knowledge_graph", "before": 5, "after": 7, "added": 2,
        "considered": 6, "skipped_missing": 1, "skipped_forbidden": 2,
    }]
    assert "relationship query" not in str(saved["expansions"])
