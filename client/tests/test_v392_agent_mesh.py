"""V392 persistent Agent Mesh, mailbox and independent delivery gate."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import pytest


pytestmark = pytest.mark.regression
ROOT = Path(__file__).resolve().parents[1]


def _fresh_db(tmp_db: str, monkeypatch):
    from hashmm.api import database as db

    monkeypatch.setattr(db, "DB_PATH", Path(tmp_db))
    monkeypatch.setattr(db, "_pool", None)
    db.init_db()
    return db


def _scope(owner: str, run_id: str):
    from hashmm.agent.execution_scope import build_root_scope

    return build_root_scope(
        owner_id=owner,
        conversation_id=f"conv-{run_id}",
        run_id=run_id,
        allowed_tools=["kb_search", "spawn_worker"],
        approval_mode="read_only",
        network_mode="deny",
        allow_subagents=True,
        max_tool_calls=8,
        max_workers=3,
    )


def test_agent_mesh_is_persistent_dependency_aware_and_terminal_sticky(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import mesh

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    graph = mesh.create_team_graph(
        owner_id=owner,
        team_id=team_id,
        goal="并行检索并核验一份报告",
        roles=[
            {"role": "research", "task": "检索事实"},
            {"role": "analysis", "task": "交叉分析"},
        ],
        mode="parallel",
    )
    assert graph["schema"] == "hashmm.agent-mesh.v1"
    assert graph["summary"]["total"] == 6
    assert mesh.get_team_graph(owner_id="other", team_id=team_id) is None

    first = f"{team_id}:role:1"
    second = f"{team_id}:role:2"
    synthesis = f"{team_id}:synthesis"
    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=first,
        status="completed", result="事实 A",
    )
    assert next(
        node for node in mesh.get_team_graph(owner_id=owner, team_id=team_id)["nodes"]
        if node["task_id"] == synthesis
    )["status"] == "blocked"
    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=second,
        status="failed", result="来源不可达",
    )
    synthesis_node = next(
        node for node in mesh.get_team_graph(owner_id=owner, team_id=team_id)["nodes"]
        if node["task_id"] == synthesis
    )
    assert synthesis_node["status"] == "ready"
    assert synthesis_node["upstream_failures"] == 1

    # Terminal task state is sticky: a retry creates a new generation/team,
    # it cannot rewrite historical failure as success.
    saved = mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=second,
        status="completed", result="伪造成功",
    )
    assert saved and saved["status"] == "failed"


def test_agent_mesh_auto_strategy_is_deterministic_and_conflict_aware():
    from hashmm.agent import mesh

    parallel = mesh.choose_mesh_strategy(
        goal="Compare independent market and product evidence",
        roles=[
            {"role": "research", "task": "Search primary market sources"},
            {"role": "analysis", "task": "Compare product benchmarks"},
            {"role": "verification", "task": "Verify cited evidence"},
        ],
        requested_mode="auto",
    )
    assert parallel["schema"] == "hashmm.agent-mesh-decision.v1"
    assert parallel["resolved_mode"] == "parallel"
    assert parallel["parallel_benefit"] > parallel["coordination_cost"]

    writers = [
        {"role": "writer", "task": "Edit the shared document"},
        {"role": "reviewer", "task": "Modify and publish the same document"},
    ]
    first = mesh.choose_mesh_strategy(
        goal="Prepare one approved document", roles=writers, requested_mode="auto",
    )
    replay = mesh.choose_mesh_strategy(
        goal="Prepare one approved document", roles=writers, requested_mode="auto",
    )
    assert first == replay
    assert first["resolved_mode"] == "pipeline"
    assert first["shared_write_risk"] == 1.0

    explicit = mesh.choose_mesh_strategy(
        goal="Prepare one approved document", roles=writers,
        requested_mode="parallel",
    )
    assert explicit["resolved_mode"] == "parallel"
    assert explicit["reason_codes"] == ["user_selected_topology"]


def test_mailbox_is_owner_scoped_idempotent_and_lease_acknowledged(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import mesh
    from hashmm.agent.session import AgentSessionRegistry

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    sessions = AgentSessionRegistry(max_records=32, persist=True)
    recipient = sessions.create(
        role="research", task="核验", owner_id=owner,
        conversation_id="conv-mail", execution_scope=_scope(owner, team_id),
    )
    first = mesh.send_message(
        owner_id=owner, team_id=team_id,
        recipient_session_id=recipient["session_id"],
        sender_kind="user", message_type="correction",
        body="只使用已打开的来源；api_key=should-not-persist",
        idempotency_key="client-correction-1",
    )
    duplicate = mesh.send_message(
        owner_id=owner, team_id=team_id,
        recipient_session_id=recipient["session_id"],
        sender_kind="user", message_type="correction",
        body="只使用已打开的来源；api_key=should-not-persist",
        idempotency_key="client-correction-1",
    )
    assert duplicate["message_id"] == first["message_id"]
    assert mesh.lease_messages(
        owner_id="other", recipient_session_id=recipient["session_id"],
    ) == []

    leased = mesh.lease_messages(
        owner_id=owner, recipient_session_id=recipient["session_id"],
        lease_seconds=30,
    )
    assert len(leased) == 1
    assert leased[0]["status"] == "leased"
    assert "should-not-persist" not in leased[0]["body"]
    assert "[已脱敏]" in leased[0]["body"]
    assert mesh.ack_message(
        owner_id=owner,
        message_id=leased[0]["message_id"],
        lease_token=leased[0]["lease_token"],
    )
    assert not mesh.ack_message(
        owner_id=owner,
        message_id=leased[0]["message_id"],
        lease_token=leased[0]["lease_token"],
    )
    summary = mesh.mailbox_summary(owner_id=owner, team_id=team_id)
    assert summary["pending"] == 0
    assert summary["counts"]["acked"] == 1


def test_expired_mail_is_redelivered_then_dead_lettered(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import mesh
    from hashmm.agent.session import AgentSessionRegistry

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    sessions = AgentSessionRegistry(max_records=32, persist=True)
    recipient = sessions.create(
        role="analysis", task="处理消息", owner_id=owner,
        conversation_id="conv-redelivery", execution_scope=_scope(owner, team_id),
    )
    message = mesh.send_message(
        owner_id=owner, team_id=team_id,
        recipient_session_id=recipient["session_id"],
        sender_kind="agent", message_type="result", body="候选结果",
        idempotency_key="redelivery-1",
    )
    for attempt in range(3):
        leased = mesh.lease_messages(
            owner_id=owner, recipient_session_id=recipient["session_id"],
            lease_seconds=10,
        )
        assert len(leased) == 1
        assert leased[0]["attempts"] == attempt + 1
        with db._conn() as conn:
            conn.execute(
                "UPDATE agent_mailbox_messages SET lease_expires_at=? "
                "WHERE message_id=?",
                (time.time() - 1, message["message_id"]),
            )
    assert mesh.lease_messages(
        owner_id=owner, recipient_session_id=recipient["session_id"],
    ) == []
    summary = mesh.mailbox_summary(owner_id=owner, team_id=team_id)
    assert summary["counts"]["dead"] == 1


def test_session_tree_is_owner_scoped_and_shares_team_work_runtime(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import work_runtime
    from hashmm.agent.session import AgentSessionRegistry

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    run = work_runtime.create_run(
        user_id=owner, kind="team", source_id=team_id,
        conv_id="conv-tree", title="持久团队", status="running",
    )
    sessions = AgentSessionRegistry(max_records=32, persist=True)
    root = sessions.create(
        role="coordinator", task="协调", owner_id=owner,
        conversation_id="conv-tree", execution_scope=_scope(owner, team_id),
        work_run_id=run["id"],
    )
    child = sessions.create(
        role="research", task="检索", owner_id=owner,
        conversation_id="conv-tree", execution_scope=_scope(owner, team_id),
        parent_session_id=root["session_id"], work_run_id=run["id"],
    )
    tree = sessions.tree(root["session_id"], owner_id=owner)
    assert tree and [node["session_id"] for node in tree["nodes"]] == [
        root["session_id"], child["session_id"],
    ]
    assert tree["edges"] == [{
        "from": root["session_id"],
        "to": child["session_id"],
        "relation": "delegated_to",
    }]
    assert sessions.tree(root["session_id"], owner_id="other") is None
    projected = work_runtime.get_run(run["id"], owner, after_seq=0, limit=20)
    assert projected and projected["id"] == run["id"]
    assert sum(event["type"] == "agent_session_admitted" for event in projected["events"]) == 2


def test_restart_reconciles_team_mesh_and_work_runtime(tmp_db, tmp_path, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "team-state"))
    from hashmm.agent import mesh, work_runtime
    from hashmm.agent import team as team_module

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    roles = [{"role": "research", "task": "仍在运行", "state": "run"}]
    graph = mesh.create_team_graph(
        owner_id=owner, team_id=team_id, goal="重启恢复",
        roles=roles, mode="parallel",
    )
    mesh.transition_task(
        owner_id=owner, team_id=team_id,
        task_id=f"{team_id}:role:1", status="running",
    )
    run = work_runtime.create_run(
        user_id=owner, kind="team", source_id=team_id,
        conv_id="conv-restart", title="重启恢复", status="running",
    )
    path = team_module._teams_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "items": {
            team_id: {
                "team_id": team_id,
                "uid": owner,
                "goal": "重启恢复",
                "conv_id": "conv-restart",
                "file": "",
                "mode": "parallel",
                "status": "running",
                "roles": roles,
                "trace": [],
                "evidence": {"sources": [], "groundings": {}},
                "work_run_id": run["id"],
                "agent_mesh": graph,
                "created": time.time() - 10,
            },
        },
    }, ensure_ascii=False), "utf-8")
    team_module._reset_team_store_for_tests()
    try:
        recovered = team_module.get_team(team_id, owner)
        assert recovered and recovered["status"] == "failed"
        assert recovered["recovery_reason"]
        assert {
            node["status"] for node in recovered["agent_mesh"]["nodes"]
        } <= {"completed", "failed", "cancelled", "interrupted"}
        projected = work_runtime.get_run(
            run["id"], owner, after_seq=0, limit=20,
        )
        assert projected and projected["status"] == "interrupted"
        assert projected["snapshot"]["safe_to_replay_side_effects"] is False
    finally:
        team_module._reset_team_store_for_tests()


def test_worker_consumes_user_correction_before_model_turn(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import mesh
    from hashmm.agent.worker import Worker

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"

    class LLM:
        def __init__(self):
            self.messages = []

        def call_with_tools(self, messages, tools=None):
            self.messages = [dict(item) for item in messages]
            return SimpleNamespace(
                message=SimpleNamespace(content="已按最新要求复核", tool_calls=[]),
            )

    llm = LLM()
    worker = Worker(
        llm, role="research", user_id=owner, conv_id="conv-worker",
        parent_scope=_scope(owner, team_id), team_id=team_id,
        max_iterations=1,
    )

    def send_correction(session):
        mesh.send_message(
            owner_id=owner, team_id=team_id,
            recipient_session_id=session["session_id"],
            sender_kind="user", message_type="correction",
            body="不要使用二手摘要，只接受原始来源。",
            idempotency_key="worker-correction-1",
        )

    result = asyncio.run(worker.run("核验数字", on_session=send_correction))
    assert result["status"] == "completed"
    joined = "\n".join(str(item.get("content") or "") for item in llm.messages)
    assert "用户在子任务执行过程中补充或纠正了要求" in joined
    assert "不要使用二手摘要" in joined
    summary = mesh.mailbox_summary(owner_id=owner, team_id=team_id)
    assert summary["counts"]["acked"] == 1


def test_delivery_node_and_independent_verifier_are_required_for_completion(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import mesh
    from hashmm.agent import team as team_module

    owner = f"owner-{uuid.uuid4().hex}"
    team_id = f"team-{uuid.uuid4().hex}"
    roles = [{"role": "research", "task": "核验", "state": "ok", "tool_calls": 0}]
    mesh.create_team_graph(
        owner_id=owner, team_id=team_id, goal="交付核验结果",
        roles=roles, mode="parallel",
    )
    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=f"{team_id}:role:1",
        status="completed", result="完成",
    )
    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=f"{team_id}:synthesis",
        status="completed", result="汇总",
    )
    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=f"{team_id}:verification",
        status="completed", result="通过",
    )
    snapshot = {
        "team_id": team_id,
        "uid": owner,
        "goal": "交付核验结果",
        "conv_id": "conv-gate",
        "mode": "parallel",
        "status": "done",
        "final": "最终结果",
        "roles": roles,
        "evidence": {"sources": [], "groundings": {}},
        "execution_receipts": [],
        "independent_verification": {
            "verdict": "passed",
            "summary": "独立核验通过",
        },
    }
    team_module._refresh_team_graph_locked(snapshot)
    assert snapshot["completion_gate"]["can_claim_verified"] is False
    assert any(
        item["check_id"] == "agent_mesh_delivery"
        and item["status"] != "passed"
        for item in snapshot["completion_gate"]["criteria"]
    )

    mesh.transition_task(
        owner_id=owner, team_id=team_id, task_id=f"{team_id}:delivery",
        status="completed", result="已写回 Chat",
    )
    team_module._refresh_team_graph_locked(snapshot)
    assert snapshot["completion_gate"]["can_claim_verified"] is True


def test_team_completion_rejects_receipt_from_another_run(tmp_db, monkeypatch):
    _fresh_db(tmp_db, monkeypatch)
    from hashmm.agent import team as team_module
    from hashmm.agent.execution_receipt import build_execution_receipt

    team_id = f"team-{uuid.uuid4().hex}"
    receipt = build_execution_receipt(
        run_id="another-team-run",
        call_id="call-1",
        tool_name="read_file",
        arguments={"path": "README.md"},
        result={"status": "completed"},
    )
    snapshot = {
        "team_id": team_id,
        "uid": f"owner-{uuid.uuid4().hex}",
        "goal": "交付核验结果",
        "conv_id": "conv-wrong-run-receipt",
        "mode": "parallel",
        "status": "done",
        "final": "最终结果",
        "roles": [{
            "role": "research", "task": "核验", "state": "ok", "tool_calls": 1,
        }],
        "evidence": {"sources": [], "groundings": {}},
        "execution_receipts": [receipt],
        "independent_verification": {
            "verdict": "passed", "summary": "独立核验通过",
        },
    }

    team_module._refresh_team_graph_locked(snapshot)

    assert snapshot["completion_gate"]["can_claim_verified"] is False
    receipt_check = next(
        item for item in snapshot["completion_gate"]["criteria"]
        if item["check_id"] == "execution_receipt_integrity"
    )
    assert receipt_check["status"] == "failed"
    assert "错属运行 1 个" in receipt_check["detail"]


def test_agent_control_routes_and_desktop_controls_are_wired():
    routes = (ROOT / "hashmm/api/routes/team_ops.py").read_text("utf-8")
    assert '@router.get("/{team_id}/tree"' in routes
    assert '@router.post("/{team_id}/agents/{session_id}/message"' in routes
    assert '@router.post("/{team_id}/agents/{session_id}/stop"' in routes
    assert "_PUBLIC_TEAM_FIELDS" in routes
    assert '"execution_scope"' not in routes.split("_PUBLIC_TEAM_FIELDS", 1)[1].split("}", 1)[0]
    panel = (
        ROOT / "frontend-next/components/RightContextPanel.tsx"
    ).read_text("utf-8")
    subagents = (
        ROOT / "frontend-next/components/SubAgentPanel.tsx"
    ).read_text("utf-8")
    assert "teamAgentMessage" in panel
    assert "teamAgentStop" in panel
    assert "Agent 任务网" in panel
    assert "执行中纠正" in subagents
    assert "持久邮箱" in subagents
