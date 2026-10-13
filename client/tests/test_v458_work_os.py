"""V439-V458 Work OS product projection, leases and annotation regressions."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_capability_resolver_and_agent_gate_are_fail_closed():
    from hashmm.agent.work_os import evaluate_agent_benefit, resolve_capability_path

    plan = resolve_capability_path({
        "structured": {
            "available": True, "configured": True, "within_scope": False,
            "reason": "连接存在，但当前任务没有授权",
        },
        "browser": {
            "available": True, "configured": True, "within_scope": True,
            "reason": "浏览器会话已就绪",
        },
        "computer": {
            "available": True, "configured": True, "within_scope": True,
        },
    })
    assert plan["selected"] == "browser"
    assert plan["label"] == "需要打开浏览器"
    assert plan["integrity"]["model_selected"] is False
    assert [row["kind"] for row in plan["considered"]] == ["structured", "browser"]

    unmeasured = evaluate_agent_benefit({
        "subtasks": 4, "independent_ratio": 0.9,
        "estimated_latency_saved_ms": 8_000,
        "estimated_extra_tokens": 2_000, "token_budget": 10_000,
        "conflict_risk": 0.05,
    })
    assert unmeasured["eligible"] is False
    measured = evaluate_agent_benefit({
        "measured": True, "subtasks": 4, "independent_ratio": 0.9,
        "estimated_latency_saved_ms": 8_000,
        "estimated_extra_tokens": 2_000, "token_budget": 10_000,
        "conflict_risk": 0.05,
    })
    assert measured["eligible"] is True


def test_project_work_lease_handoff_and_owner_isolation(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("v458-alice", "pw12345678")
    bob = db.create_user("v458-bob", "pw12345678")
    project_id = db.create_project(alice["id"], "年度报告")
    run = runtime.create_run(
        user_id=alice["id"],
        kind="workflow",
        source_id="v458-project-work",
        conv_id="conv-v458",
        project_id=project_id,
        title="完成可核验年度报告",
        execution_target={"kind": "waiting_device"},
        autonomy_level=3,
        snapshot={
            "capability_facts": {
                "structured": {
                    "available": True, "configured": True, "within_scope": True,
                },
            },
            "run_manifest": {
                "task_contract": {
                    "goal": "完成可核验年度报告",
                    "goal_source": "user_message",
                    "constraints": ["不得使用未注明来源的数据"],
                    "success_criteria": [
                        {"check_id": "citations", "label": "关键结论有来源", "required": True},
                    ],
                },
            },
        },
    )
    assert run["project_id"] == project_id
    assert run["execution_target"]["kind"] == "waiting_device"
    assert runtime.list_runs(
        alice["id"], project_id=project_id,
    )["items"][0]["id"] == run["id"]
    assert runtime.list_runs(
        bob["id"], project_id=project_id,
    )["items"] == []

    claimed = runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="remote_device",
        holder_id="desktop-a",
        expected_revision=run["revision"],
        idempotency_key="lease-v458-0001",
        ttl_seconds=90,
    )
    assert claimed["ok"] is True
    assert claimed["lease"]["generation"] == 1
    assert claimed["run"]["execution_target"]["target_id"] == "desktop-a"
    replay = runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="remote_device",
        holder_id="desktop-a",
        expected_revision=run["revision"],
        idempotency_key="lease-v458-0001",
    )
    assert replay["ok"] is True and replay["duplicate"] is True
    assert runtime.acquire_execution_lease(
        run["id"],
        user_id=bob["id"],
        holder_type="remote_device",
        holder_id="desktop-b",
        expected_revision=claimed["run"]["revision"],
        idempotency_key="lease-v458-bob1",
    )["error"] == "not_found"
    assert runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="cloud",
        holder_id="cloud",
        expected_revision=claimed["run"]["revision"],
        idempotency_key="lease-v458-0002",
    )["error"] == "already_leased"
    renewed = runtime.heartbeat_execution_lease(
        run["id"],
        user_id=alice["id"],
        lease_id=claimed["lease"]["id"],
        generation=claimed["lease"]["generation"],
    )
    assert renewed["ok"] is True
    released = runtime.release_execution_lease(
        run["id"],
        user_id=alice["id"],
        lease_id=claimed["lease"]["id"],
        generation=claimed["lease"]["generation"],
    )
    assert released["ok"] is True
    assert released["run"]["execution_target"]["kind"] == "waiting_device"
    assert runtime.get_active_execution_lease(run["id"], alice["id"]) is None


def test_project_assignment_and_updates_are_owner_bound(tmp_db):
    db = _fresh_db(tmp_db)
    alice = db.create_user("v458-project-alice", "pw12345678")
    bob = db.create_user("v458-project-bob", "pw12345678")
    alice_project = db.create_project(alice["id"], "Alice 项目")
    bob_project = db.create_project(bob["id"], "Bob 项目")
    db.create_conversation("conv-project-alice", alice["id"], "Alice 对话")

    assert db.get_project_for_user(alice_project, alice["id"]) is not None
    assert db.get_project_for_user(alice_project, bob["id"]) is None
    assert db.update_project(alice_project, bob["id"], name="越权改名") is False
    assert db.get_project(alice_project)["name"] == "Alice 项目"
    assert db.assign_conv_to_project(
        "conv-project-alice", bob_project, alice["id"],
    ) is False
    assert db.assign_conv_to_project(
        "conv-project-alice", alice_project, alice["id"],
    ) is True
    assert db.get_conversation("conv-project-alice")["project_id"] == alice_project
    from hashmm.agent import work_runtime as runtime
    inherited = runtime.create_run(
        user_id=alice["id"],
        kind="chat",
        source_id="project-inheritance-v458",
        conv_id="conv-project-alice",
        title="沿用项目上下文",
    )
    assert inherited["project_id"] == alice_project


def test_annotation_stales_only_owned_artifact_and_enriches_work_projection(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("v458-annotation-owner", "pw12345678")
    bob = db.create_user("v458-annotation-other", "pw12345678")
    run = runtime.create_run(
        user_id=alice["id"],
        kind="artifact",
        source_id="v458-artifact",
        conv_id="conv-artifact",
        title="制作季度汇报",
        snapshot={
            "capability_facts": {
                "structured": {
                    "available": False, "configured": False, "within_scope": False,
                },
                "browser": {
                    "available": True, "configured": True, "within_scope": True,
                },
            },
            "run_manifest": {
                "task_contract": {
                    "goal": "制作季度汇报",
                    "goal_source": "user_message",
                    "success_criteria": [
                        {"check_id": "fresh-data", "label": "图表数据在有效期内", "required": True},
                    ],
                },
            },
        },
    )
    artifact = runtime.register_artifact_revision(
        run["id"],
        user_id=alice["id"],
        artifact_id="quarterly-ppt",
        content_hash="a" * 64,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        size_bytes=1024,
        locator={"filename": "quarterly.pptx"},
        verification="verified",
        expected_run_revision=run["revision"],
    )
    assert artifact["state"] == "applied"
    current = artifact["run"]
    foreign = runtime.add_artifact_annotation(
        run["id"],
        user_id=bob["id"],
        annotation_id="annotation-bob-0001",
        idempotency_key="annotation-bob-idem1",
        artifact_id="quarterly-ppt",
        artifact_revision=1,
        target={"kind": "slide", "slide": 4},
        note="越权修改",
        expected_revision=current["revision"],
    )
    assert foreign["error"] == "not_found"
    annotated = runtime.add_artifact_annotation(
        run["id"],
        user_id=alice["id"],
        annotation_id="annotation-v458-01",
        idempotency_key="annotation-v458-idem1",
        artifact_id="quarterly-ppt",
        artifact_revision=1,
        target={"kind": "slide", "slide": 4, "region": "chart"},
        note="第 4 页图表数据已经过期，请只更新受影响内容。",
        expected_revision=current["revision"],
    )
    assert annotated["ok"] is True
    duplicate = runtime.add_artifact_annotation(
        run["id"],
        user_id=alice["id"],
        annotation_id="annotation-v458-02",
        idempotency_key="annotation-v458-idem1",
        artifact_id="quarterly-ppt",
        artifact_revision=1,
        target={"kind": "slide", "slide": 5},
        note="重复提交不应制造第二次副作用",
        expected_revision=current["revision"],
    )
    assert duplicate["ok"] is True and duplicate["duplicate"] is True

    detail = runtime.get_run(run["id"], alice["id"])
    assert detail is not None
    assert detail["artifact_revisions"][0]["verification"] == "stale"
    assert len(detail["artifact_annotations"]) == 1
    product = detail["workspace"]["product"]
    assert product["schema"] == "hashmm.work-projection.v2"
    assert product["capability_plan"]["selected"] == "browser"
    assert product["work_twin"]["integrity"]["explicit_edges_only"] is True
    assert product["annotations"][0]["target"]["slide"] == 4
    assert product["autonomy"]["released_level"] == 0
    assert product["integrity"]["model_prose_is_execution_evidence"] is False


def test_autonomy_release_requires_verified_evaluation_receipt():
    from hashmm.agent.work_os import autonomy_profile

    requested = {
        "autonomy_level": 4,
        "snapshot": {"run_manifest": {
            "autonomy_evaluation": {
                "source": "model_claim", "verified": True, "level": 4,
                "suite_hash": "b" * 64,
                "security_failures": 0,
                "duplicate_side_effects": 0,
                "unapproved_high_risk_actions": 0,
            },
        }},
    }
    assert autonomy_profile(requested)["released_level"] == 0
    requested["snapshot"]["run_manifest"]["autonomy_evaluation"]["source"] = "evaluation_service"
    released = autonomy_profile(requested)
    assert released["released_level"] == 4
    assert released["a5_available"] is False


def test_writing_agent_uses_cow_branch_and_only_integrator_promotes(tmp_path, monkeypatch):
    from hashmm.api import database as db
    from hashmm.agent.agent_workspace import (
        branch_manifest, merge_branch, provision_worker_branch,
    )
    from hashmm.agent.execution_scope import (
        build_root_scope, check_execution_scope, derive_child_scope, public_scope,
    )
    from hashmm.api.tool_registry import execute_tool_structured

    monkeypatch.setattr(db, "CONV_FILES_ROOT", tmp_path / "conversation-files")
    parent = build_root_scope(
        owner_id="owner-cow",
        conversation_id="conv-cow",
        run_id="work-cow",
        allowed_tools=["create_file", "kb_search", "spawn_worker"],
        approval_mode="workspace",
        allow_subagents=True,
    )
    unisolated = derive_child_scope(
        parent, role="writer", role_tools=["create_file", "kb_search"],
    )
    assert unisolated is not None
    assert "create_file" not in unisolated["allowed_tools"]

    branch = provision_worker_branch(
        owner_id="owner-cow",
        conversation_id="conv-cow",
        run_id="work-cow",
        role="writer",
        branch_id="writer-branch-1",
    )
    child = derive_child_scope(
        parent,
        role="writer",
        role_tools=["create_file", "kb_search"],
        workspace_branch=branch,
    )
    assert child is not None and "create_file" in child["allowed_tools"]
    assert check_execution_scope(
        child, "create_file", {"filename": "candidate.txt", "content": "candidate"},
        user_id="owner-cow", conversation_id="conv-cow",
    )[0] is True
    assert "root_path" not in public_scope(child)["workspace_branch"]

    result = execute_tool_structured(
        "create_file",
        {"filename": "candidate.txt", "content": "candidate content"},
        {
            "user_id": "owner-cow",
            "conv_id": "conv-cow",
            "permission_prechecked": True,
            "execution_scope": child,
            "cwd": branch["root_path"],
        },
    )
    assert result["file"]["candidate_branch_id"] == "writer-branch-1"
    formal = db.conv_files_dir("conv-cow")
    assert not (formal / "candidate.txt").exists()
    manifest = branch_manifest(branch)
    assert manifest["files"][0]["path"] == "candidate.txt"
    assert merge_branch(
        branch, actor_role="writer", expected_hashes=manifest["files"],
    )["error"] == "integrator_required"
    merged = merge_branch(
        branch, actor_role="integrator", expected_hashes=manifest["files"],
    )
    assert merged["ok"] is True
    assert (formal / "candidate.txt").read_text(encoding="utf-8") == "candidate content"
