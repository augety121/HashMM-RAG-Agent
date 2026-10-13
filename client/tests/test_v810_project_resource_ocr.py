"""V810 regressions for project contracts and the durable OCR v2 path."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

pytestmark = pytest.mark.unit


def _fresh(tmp_db: str):
    from hashmm.api import database as db
    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_project_contract_is_frozen_into_chat_run(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.agent import work_runtime
    owner = db.create_user("v810-project-run", "pw12345678")
    project_id = db.create_project(
        owner["id"], "Evidence project", goal="Produce an evidence report",
        deliverable="report.md", success_criteria=["Every claim has a source"],
        permission_mode="read_only",
    )
    db.create_conversation("conv-v810", owner["id"], "Project chat", project_id=project_id)
    run = work_runtime.create_run(
        user_id=owner["id"], kind="chat", source_id="turn-v810",
        conv_id="conv-v810", title="Continue",
    )
    snapshot = run["snapshot"]
    project_contract = snapshot["project_contract"]
    assert project_contract["project_id"] == project_id
    assert project_contract["revision"] == 1
    assert project_contract["permission_mode"] == "read_only"
    task_contract = snapshot["run_manifest"]["task_contract"]
    assert task_contract["project_id"] == project_id
    assert any(
        item["label"] == "Every claim has a source" and item["source"] == "project"
        for item in task_contract["success_criteria"]
    )
    checkpoint = work_runtime.latest_checkpoint(run["id"], owner["id"])
    assert checkpoint["generation"] == 0
    assert checkpoint["state"]["retry_policy"] == "check_execution_receipt_before_replay"


def test_done_projection_cannot_drop_project_acceptance_criteria(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.agent import work_runtime
    owner = db.create_user("v810-project-gate", "pw12345678")
    project_id = db.create_project(
        owner["id"], "Gate", goal="Build", success_criteria=["Run the regression suite"]
    )
    db.create_conversation("conv-gate", owner["id"], "Gate", project_id=project_id)
    run = work_runtime.create_run(
        user_id=owner["id"], kind="chat", source_id="turn-gate",
        conv_id="conv-gate", title="Build",
    )
    work_runtime.append_event(
        run["id"], user_id=owner["id"], event_type="started", status="running"
    )
    projector = work_runtime.SSEProjector(run["id"], owner["id"])
    payload = {
        "status": "delivered",
        "stop_reason": "completed",
        "run_manifest": {
            "task_contract": {
                "schema": "hashmm.task-contract.v1", "run_id": run["id"],
                "goal": "Build", "success_criteria": [],
            },
            "verification": {"checks": []},
        },
    }
    projector.observe("event: done\ndata: " + json.dumps(payload) + "\n\n")
    detail = work_runtime.get_run(run["id"], owner["id"])
    manifest = detail["snapshot"]["run_manifest"]
    assert any(
        item["label"] == "Run the regression suite"
        for item in manifest["task_contract"]["success_criteria"]
    )
    assert manifest["completion_gate"]["can_claim_complete"] is False
    assert manifest["completion_gate"]["summary"]["missing"] >= 1


def test_project_resource_membership_is_owner_scoped(tmp_db):
    db = _fresh(tmp_db)
    owner = db.create_user("v810-resource-owner", "pw12345678")
    other = db.create_user("v810-resource-other", "pw12345678")
    project_id = db.create_project(owner["id"], "Resources")
    membership = db.add_project_resource(
        project_id, owner["id"], resource_id="sha256:" + "a" * 64,
        conv_id="conv", filename="paper.pdf", sha256="a" * 64,
    )
    assert membership and membership["filename"] == "paper.pdf"
    assert db.list_project_resources(project_id, owner["id"])[0]["resource_id"].startswith("sha256:")
    assert db.list_project_resources(project_id, other["id"]) == []
    assert not db.remove_project_resource(project_id, other["id"], membership["resource_id"])


def test_ocr_v2_deduplicates_bytes_but_preserves_conversation_links(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.pipeline import ocr_queue
    owner = db.create_user("v810-ocr-links", "pw12345678")
    source = Path(tmp_db).with_suffix(".png")
    source.write_bytes(b"same immutable bytes")
    first = ocr_queue.enqueue(
        user_id=owner["id"], conv_id="conv-a", filename="a.png", source_path=str(source)
    )
    second = ocr_queue.enqueue(
        user_id=owner["id"], conv_id="conv-b", filename="b.png", source_path=str(source)
    )
    assert first["id"] == second["id"]
    assert first["engine_requested"] == "paddleocr"
    assert "result_path" not in first
    assert ocr_queue.list_jobs(owner["id"], conv_id="conv-a")[0]["id"] == first["id"]
    assert ocr_queue.list_jobs(owner["id"], conv_id="conv-b")[0]["id"] == first["id"]


def test_ocr_rejects_changed_source_before_provider_execution(tmp_db, monkeypatch):
    db = _fresh(tmp_db)
    from hashmm.pipeline import ocr_queue
    owner = db.create_user("v810-ocr-sha", "pw12345678")
    source = Path(tmp_db).with_suffix(".png")
    source.write_bytes(b"original")
    job = ocr_queue.enqueue(
        user_id=owner["id"], filename="scan.png", source_path=str(source), max_attempts=3
    )
    source.write_bytes(b"changed")
    monkeypatch.setattr(
        ocr_queue, "recognize_image",
        lambda *_args, **_kwargs: pytest.fail("provider must not see mutated bytes"),
    )
    result = ocr_queue.process_one()
    assert result["status"] == "failed"
    assert result["error_code"] == "source_changed"


def test_ocr_success_projects_authoritative_resource_state_to_message(tmp_db, monkeypatch):
    db = _fresh(tmp_db)
    from hashmm.pipeline import ocr_queue
    owner = db.create_user("v810-ocr-projection", "pw12345678")
    db.create_conversation("conv-ocr", owner["id"], "OCR")
    source = Path(tmp_db).with_suffix(".png")
    # Magic bytes are sufficient because provider execution is mocked; the
    # test verifies queue projection rather than Pillow's platform DLL.
    source.write_bytes(b"\x89PNG\r\n\x1a\nprojection-test")
    digest = ocr_queue.hashlib.sha256(source.read_bytes()).hexdigest()
    db.create_message("conv-ocr", "user", "read", files=[{
        "filename": "scan.png", "sha256": digest, "parse_state": "needs_ocr",
    }])
    monkeypatch.setattr(ocr_queue, "recognize_image", lambda *_args, **_kwargs: {
        "text": "verified text",
        "blocks": [{
            "type": "text", "content": "verified text", "page": 1,
            "anchor": "p.1", "position": 0, "bbox": [], "confidence": 0.99,
            "char_count": 13, "is_noise": False,
        }],
        "confidence": 0.99,
    })
    job = ocr_queue.enqueue(
        user_id=owner["id"], conv_id="conv-ocr", filename="scan.png",
        source_path=str(source), sha256=digest,
    )
    result = ocr_queue.process_one()
    assert result["id"] == job["id"]
    assert result["status"] == "succeeded"
    assert result["result_state"] == "ready"
    message = db.get_messages("conv-ocr")[-1]
    assert message["files"][0]["parse_state"] == "ready"
    assert message["files"][0]["readable_pages"] == 1


def test_queued_ocr_can_be_cancelled_without_execution(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.pipeline import ocr_queue
    owner = db.create_user("v810-ocr-cancel", "pw12345678")
    source = Path(tmp_db).with_suffix(".png")
    source.write_bytes(b"queued")
    job = ocr_queue.enqueue(user_id=owner["id"], filename="scan.png", source_path=str(source))
    cancelled = ocr_queue.cancel_job(job["id"], owner["id"])
    assert cancelled["status"] == "cancelled"
    assert ocr_queue.process_one() is None


def test_ocr_provider_language_and_sidecar_boundaries(tmp_path, monkeypatch):
    from hashmm.pipeline.ocr_provider import (
        OCRProviderError, capabilities, normalize_language, recognize_document_sidecar,
    )
    assert normalize_language("chi_sim+eng", "paddleocr") == "ch"
    with pytest.raises(OCRProviderError, match="unsupported"):
        normalize_language("unknown-language", "paddleocr")
    snapshot = capabilities(probe=False)
    assert snapshot["schema"] == "hashmm.ocr-capabilities.v2"
    assert snapshot["providers"]["unlimited"]["isolation"] == "http_sidecar"
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.setenv("HASHMM_UNLIMITED_OCR_ENABLED", "1")
    monkeypatch.setenv("HASHMM_UNLIMITED_OCR_URL", "https://example.com")
    with pytest.raises(OCRProviderError) as denied:
        recognize_document_sidecar(source)
    assert denied.value.code == "remote_sidecar_denied"
