"""V396-V400 unified work canvas, result receipt and governed decisions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def _manifest(*, causal_status: str = "ready", gate_status: str = "verified") -> dict:
    from hashmm.agent.execution_receipt import build_execution_receipt

    receipt = build_execution_receipt(
        run_id="run-canvas",
        call_id="call-create-report",
        tool_name="create_document",
        arguments={"filename": "研究 报告.docx"},
        result={"ok": True, "filename": "研究 报告.docx"},
        status="completed",
        idempotency_key="create-report-once",
        artifacts=[{
            "filename": "研究 报告.docx",
            "revision": "2",
            "content_hash": "a" * 64,
            "size": 2048,
        }],
        verification=[{"check_id": "artifact_delivery", "status": "passed"}],
    )
    return {
        "task_contract": {
            "goal": "根据我的资料生成一份可核验研究报告",
            "plan": [
                {"text": "整理资料", "status": "completed"},
                {"text": "生成报告", "status": "completed"},
                {"text": "等待用户验收", "status": "running"},
            ],
            "success_criteria": [{
                "check_id": "artifact_delivery",
                "label": "报告文件存在",
                "required": True,
            }],
        },
        "verification": {
            "status": "verified" if gate_status == "verified" else "partial",
            "checks": [{
                "id": "artifact_delivery",
                "status": "passed",
                "detail": "工作区已验证报告文件存在",
            }],
        },
        "causal_work_graph": {
            "schema": "hashmm.causal-work-graph.v1",
            "status": causal_status,
            "nodes": [{
                "id": "source:1",
                "kind": "source_snapshot",
                "label": "访谈记录.md",
                "status": "available",
                "trust": "runtime_observation",
                "revision": 2,
            }],
            "invalidation": {
                "strategy": "content_hash_dependency_closure",
                "stale_node_ids": ["artifact:report"] if causal_status == "stale" else [],
            },
        },
        "execution_receipts": [receipt],
        "completion_gate": {
            "status": gate_status,
            "summary": {"total": 1, "passed": 1},
            "criteria": [{
                "check_id": "artifact_delivery",
                "label": "报告文件存在",
                "status": "passed",
                "authority": "runtime_fact",
            }],
            "failure_modes": [],
            "next_action": "等待用户检查交付内容",
        },
        "context_lifecycle": {
            "contract": "hashmm.context-engine.v2",
            "checkpoint_id": "checkpoint-report-2",
            "generation": 2,
            "compacted": True,
        },
        "execution_frontier": {
            "schema": "hashmm.execution-frontier.v1",
            "frontier_id": "frontier-report",
            "items": [],
        },
    }


def test_work_canvas_unifies_plan_evidence_results_and_receipt(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("canvas-owner", "pw12345678")["id"]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="canvas-report",
        conv_id="conversation-report",
        title="研究报告",
        status="delivered",
        snapshot={
            "files": [{
                "filename": "研究 报告.docx",
                "type": "docx",
                "exists": True,
                "revision": 2,
                "content_hash": "a" * 64,
                "size": 2048,
            }],
            "run_manifest": _manifest(),
        },
    )
    detail = work_runtime.get_run(run["id"], owner)
    assert detail is not None
    canvas = detail["workspace"]

    assert canvas["schema"] == "hashmm.work-canvas.v1"
    assert canvas["overview"]["goal"] == "根据我的资料生成一份可核验研究报告"
    assert [item["status"] for item in canvas["process"]["stages"]] == [
        "done", "done", "running",
    ]
    assert canvas["process"]["checkpoints"][0]["id"] == "checkpoint-report-2"
    assert canvas["evidence"]["summary"] == {
        "sources": 1,
        "checks": 1,
        "receipts": 1,
        "invalid_receipts": 0,
        "stale_nodes": 0,
    }
    result = canvas["results"][0]
    assert result["kind"] == "document"
    assert result["verification"] == "verified"
    assert "%E7%A0%94%E7%A9%B6%20%E6%8A%A5%E5%91%8A.docx" in result["download_url"]
    assert result["version_ref"]
    completion = canvas["completion_receipt"]
    assert completion["status"] == "verified"
    assert completion["can_claim_verified"] is True
    assert completion["integrity"]["model_prose_is_completion_evidence"] is False
    assert canvas["next_actions"]["governance"]["auto_execution_enabled"] is False
    assert canvas["operating"]["schema"] == "hashmm.user-operating-projection.v1"
    assert canvas["operating"]["revision"]
    assert canvas["operating"]["route_state"] in {"ready", "setup_required"}
    assert canvas["integrity"]["projection_only"] is True


def test_delivery_acceptance_is_owner_revision_bound_and_idempotent(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("decision-owner", "pw12345678")["id"]
    other = db.create_user("decision-other", "pw12345678")["id"]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="decision-report",
        status="delivered",
        snapshot={"run_manifest": _manifest(gate_status="delivered_with_limits")},
    )
    hidden = work_runtime.apply_decision(
        run["id"],
        user_id=other,
        decision_id="decision-hidden-1",
        action="accept_delivery",
        expected_revision=run["revision"],
    )
    assert hidden == {"ok": False, "error": "not_found"}

    first = work_runtime.apply_decision(
        run["id"],
        user_id=owner,
        decision_id="decision-accept-1",
        action="accept_delivery",
        expected_revision=run["revision"],
        note="内容符合本次需要",
    )
    assert first["ok"] is True
    assert first["run"]["status"] == "completed"
    assert first["decision"]["status"] == "applied"
    second = work_runtime.apply_decision(
        run["id"],
        user_id=owner,
        decision_id="decision-accept-1",
        action="accept_delivery",
        expected_revision=run["revision"],
        note="不会覆盖首次决定",
    )
    assert second["ok"] is True and second["duplicate"] is True
    assert second["decision"] == first["decision"]

    detail = work_runtime.get_run(run["id"], owner)
    assert detail is not None
    receipt = detail["workspace"]["completion_receipt"]
    assert receipt["status"] == "accepted_with_limits"
    assert receipt["can_claim_complete"] is True
    assert receipt["can_claim_verified"] is False
    assert receipt["user_review"]["authority"] == "actual_user_confirmation"


def test_stale_dependencies_block_acceptance_and_changes_reopen_work(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("decision-stale", "pw12345678")["id"]
    stale = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="stale-report",
        status="delivered",
        snapshot={"run_manifest": _manifest(causal_status="stale")},
    )
    blocked = work_runtime.apply_decision(
        stale["id"],
        user_id=owner,
        decision_id="decision-stale-1",
        action="accept_delivery",
        expected_revision=stale["revision"],
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "verification_blocked"

    fresh = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="changes-report",
        status="delivered",
        snapshot={"run_manifest": _manifest()},
    )
    no_note = work_runtime.apply_decision(
        fresh["id"],
        user_id=owner,
        decision_id="decision-change-0",
        action="request_changes",
        expected_revision=fresh["revision"],
    )
    assert no_note["error"] == "note_required"
    changed = work_runtime.apply_decision(
        fresh["id"],
        user_id=owner,
        decision_id="decision-change-1",
        action="request_changes",
        expected_revision=fresh["revision"],
        note="请补充结论对应的页码",
    )
    assert changed["ok"] is True
    assert changed["run"]["status"] == "waiting_input"
    detail = work_runtime.get_run(fresh["id"], owner)
    assert detail is not None
    assert detail["events"][-1]["type"] == "changes_requested"
    workspace = detail["workspace"]
    assert workspace["completion_receipt"]["status"] == "not_ready"
    assert workspace["change_impact"]["requested"] is True
    assert workspace["change_impact"]["reason"] == "请补充结论对应的页码"
    assert workspace["change_impact"]["integrity"]["model_inferred_impact"] is False


def test_result_invalidation_is_selective_not_run_wide(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("selective-results", "pw12345678")["id"]
    manifest = _manifest(causal_status="stale", gate_status="delivered_with_limits")
    manifest["causal_work_graph"]["nodes"] = [
        {
            "id": "artifact:stale",
            "kind": "artifact",
            "label": "需要更新.docx",
            "status": "stale",
            "trust": "runtime_observation",
            "revision": 2,
        },
        {
            "id": "artifact:fresh",
            "kind": "artifact",
            "label": "仍然有效.pdf",
            "status": "delivered",
            "trust": "runtime_observation",
            "revision": 1,
        },
    ]
    manifest["causal_work_graph"]["invalidation"]["stale_node_ids"] = [
        "artifact:stale",
    ]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="selective-result-run",
        status="delivered",
        snapshot={
            "files": [
                {"filename": "需要更新.docx", "exists": True},
                {"filename": "仍然有效.pdf", "exists": True},
            ],
            "run_manifest": manifest,
        },
    )
    canvas = work_runtime.get_run(run["id"], owner)["workspace"]
    by_name = {item["name"]: item for item in canvas["results"]}
    assert by_name["需要更新.docx"]["verification"] == "stale"
    assert by_name["需要更新.docx"]["evidence_node_id"] == "artifact:stale"
    assert by_name["仍然有效.pdf"]["verification"] == "ready"
    assert by_name["仍然有效.pdf"]["evidence_node_id"] == "artifact:fresh"
    assert canvas["sync"]["invalidated_result_ids"] == [
        by_name["需要更新.docx"]["id"],
    ]


def test_work_canvas_attributes_exact_skill_version_without_exposing_prompt(
    tmp_db,
):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime

    owner = db.create_user("skill-version-owner", "pw12345678")["id"]
    manifest = _manifest()
    manifest["skill_versions"] = [{
        "skill_id": "evidence-review",
        "name": "证据审阅",
        "scope": "personal",
        "prompt_hash": "a" * 64,
        "prompt_text": "private prompt must never be projected",
        "evolution_id": "evo-1",
    }]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="skill-version-run",
        status="delivered",
        snapshot={
            "files": [{"filename": "report.pdf", "exists": True}],
            "run_manifest": work_runtime.project_run_manifest(manifest),
        },
    )

    canvas = work_runtime.get_run(run["id"], owner)["workspace"]
    assert canvas["learning"]["used_versions"] == [{
        "id": "evidence-review",
        "name": "证据审阅",
        "scope": "personal",
        "version_ref": f"skill:evidence-review:sha256:{'a' * 64}",
        "evolution_id": "evo-1",
        "status": "used",
    }]
    governance = canvas["learning"]["governance"]
    assert governance["automatic_promotion_allowed"] is False
    assert governance["paired_replay_required_before_promotion"] is True
    assert "private prompt" not in json.dumps(canvas, ensure_ascii=False)


def test_decision_route_preserves_non_enumerable_owner_boundary(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime
    from hashmm.api.routes import work_runtime as route

    owner = db.create_user("decision-route-owner", "pw12345678")["id"]
    other = db.create_user("decision-route-other", "pw12345678")["id"]
    run = work_runtime.create_run(
        user_id=owner,
        kind="artifact",
        source_id="route-delivery",
        status="delivered",
        snapshot={"run_manifest": _manifest()},
    )

    class Request:
        headers = {}

        async def json(self):
            return {
                "decision_id": "decision-route-1",
                "action": "accept_delivery",
                "expected_revision": run["revision"],
            }

    monkeypatch.setattr(route, "require_auth", lambda _request: {"uid": other})
    with pytest.raises(Exception) as exc:
        asyncio.run(route.work_run_decision(run["id"], Request()))
    assert getattr(exc.value, "status_code", None) == 404

    monkeypatch.setattr(route, "require_auth", lambda _request: {"uid": owner})
    response = asyncio.run(route.work_run_decision(run["id"], Request()))
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["run"]["status"] == "completed"
