"""V430-V438 connected assurance projection regressions."""
from __future__ import annotations

from hashmm.agent.work_assurance import SCHEMA, VERSION_CHAIN, build_work_assurance


def _run(*, provider_ok: bool = True, stale: bool = False) -> dict:
    provider_contract = {
        "schema": "hashmm.provider-contract.v1",
        "ok": provider_ok,
        "provider": {"provider": "test-provider", "model": "test-model"},
        "requirements": {"supports_tools": True},
        "errors": [] if provider_ok else [
            {"code": "missing_capability", "field": "supports_tools"},
        ],
    }
    return {
        "id": "run-assurance",
        "user_id": "owner-assurance",
        "conversation_id": "conv-assurance",
        "status": "delivered",
        "revision": 7,
        "event_cursor": 11,
        "change_cursor": 13,
        "active_generation_id": "generation-2",
        "artifact_revisions": [{
            "id": "revision-report-2",
            "artifact_id": "report",
            "content_hash": "a" * 64,
            "generation_id": "generation-2",
        }],
        "snapshot": {
            "context_capsule": {
                "fingerprint": "capsule-safe",
                "budgets": {"max_input_tokens": 10_000},
                "metrics": {"estimated_tokens": 7_500, "compaction_count": 1},
                "provider_contract": provider_contract,
            },
            "agent_mesh": {
                "strategy": {
                    "strategy": "parallel",
                    "parallel_net_benefit": 1.4,
                    "estimated_parallel_rounds": 2,
                },
                "nodes": [
                    {"id": "research", "status": "completed"},
                    {"id": "review", "status": "completed"},
                ],
            },
            "run_manifest": {
                "task_contract": {
                    "success_criteria": [{
                        "check_id": "artifact_delivery",
                        "label": "交付文件存在",
                        "required": True,
                    }],
                },
                "completion_gate": {
                    "criteria": [{
                        "check_id": "artifact_delivery",
                        "label": "交付文件存在",
                        "required": True,
                        "status": "passed",
                        "evidence_refs": ["artifact:report"],
                    }],
                },
                "context_lifecycle": {
                    "checkpoint_id": "checkpoint-2",
                    "checkpoint_created_at": 100,
                },
                "execution_scope": {
                    "scope_id": "scope-owner",
                    "approval_mode": "ask",
                    "network_mode": "allow_list",
                },
                "execution_receipts": [{
                    "receipt_id": "receipt-create-report",
                    "reversible": True,
                }],
            },
        },
        "_stale": stale,
    }


def _results(stale: bool = False) -> list[dict]:
    return [{
        "id": "artifact:report",
        "version_ref": "artifact:report:sha256:" + "a" * 64,
        "verification": "stale" if stale else "verified",
    }]


def _receipt(can_complete: bool = True) -> dict:
    return {
        "receipt_id": "completion-safe",
        "can_claim_complete": can_complete,
        "summary": {"invalid_receipts": 0},
    }


def test_v430_v438_are_one_deterministic_delivery_chain():
    result = build_work_assurance(
        _run(),
        [{"id": "event-1", "type": "context_checkpoint", "created_at": 101,
          "payload": {"checkpoint_id": "checkpoint-3", "restorable": True}}],
        results=_results(),
        completion_receipt=_receipt(),
    )

    assert result["schema"] == SCHEMA
    assert result["versions"] == VERSION_CHAIN
    assert result["recovery"]["version"] == "V430"
    assert result["recovery"]["latest_checkpoint"]["id"] == "checkpoint-3"
    assert result["context"]["version"] == "V431"
    assert result["context"]["pressure"] == 0.75
    assert result["evidence"]["version"] == "V432"
    assert result["evidence"]["coverage"] == 1.0
    assert result["artifacts"]["version"] == "V433"
    assert result["artifacts"]["content_addressed"] is True
    assert result["delegation"]["version"] == "V434"
    assert result["delegation"]["agents"] == 2
    assert result["authority"]["version"] == "V435"
    assert result["authority"]["widens_scope"] is False
    assert result["provider"]["version"] == "V436"
    assert result["provider"]["compatible"] is True
    assert result["sync"]["version"] == "V437"
    assert result["sync"]["owner_bound"] is True
    assert result["delivery"]["version"] == "V438"
    assert result["delivery"]["can_deliver"] is True
    assert result["user_summary"]["state"] == "verified"
    assert result["integrity"]["model_prose_is_evidence"] is False


def test_delivery_fails_closed_for_stale_artifact_and_provider_mismatch():
    result = build_work_assurance(
        _run(provider_ok=False, stale=True),
        [],
        results=_results(stale=True),
        completion_receipt=_receipt(),
    )

    assert result["delivery"]["can_deliver"] is False
    assert result["user_summary"]["state"] == "attention"
    assert {item["code"] for item in result["delivery"]["blockers"]} == {
        "stale_artifact", "provider_incompatible",
    }
    assert result["provider"]["missing"] == ["supports_tools"]


def test_projection_does_not_persist_context_or_tool_bodies():
    run = _run()
    run["snapshot"]["context_capsule"]["retrieved_text"] = "private source body"
    run["snapshot"]["run_manifest"]["execution_receipts"][0]["arguments"] = {
        "api_key": "do-not-persist",
    }
    result = build_work_assurance(
        run, [], results=_results(), completion_receipt=_receipt(),
    )
    serialized = str(result)

    assert "private source body" not in serialized
    assert "do-not-persist" not in serialized
    assert result["context"]["source_bodies_exposed"] is False


def test_work_canvas_exposes_assurance_without_changing_execution_authority():
    from hashmm.agent.work_runtime import build_work_canvas

    run = _run()
    run.update({
        "kind": "artifact",
        "title": "研究报告",
        "presentation": {},
        "updated_at": 120,
    })
    run["snapshot"]["files"] = [{
        "filename": "report.docx",
        "type": "docx",
        "content_hash": "a" * 64,
        "revision": 2,
        "size": 100,
        "exists": True,
    }]
    run["snapshot"]["run_manifest"]["verification"] = {
        "status": "verified",
        "checks": [{"id": "artifact_delivery", "status": "passed"}],
    }
    run["snapshot"]["run_manifest"]["completion_gate"].update({
        "status": "verified",
        "can_claim_complete": True,
        "can_claim_verified": True,
        "summary": {"total": 1, "passed": 1},
    })

    canvas = build_work_canvas(run, [])

    assert canvas["assurance"]["schema"] == SCHEMA
    assert canvas["assurance"]["integrity"]["auto_executes"] is False
    assert canvas["integrity"]["widens_scope"] is False
