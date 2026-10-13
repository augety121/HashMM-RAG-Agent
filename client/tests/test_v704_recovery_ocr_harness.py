"""V704 regressions: OCR queue, unified state transitions and L0-L3 replay."""
from __future__ import annotations

from pathlib import Path
import json
import sys
import types

import pytest

pytestmark = pytest.mark.unit


def _fresh(tmp_db: str):
    from hashmm.api import database as db
    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def test_ocr_job_is_durable_and_retryable(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.pipeline import ocr_queue

    user = db.create_user("v704-ocr", "pw12345678")
    source = Path(tmp_db).with_suffix(".png")
    source.write_bytes(b"not-a-real-image")
    first = ocr_queue.enqueue(user_id=user["id"], filename="scan.png", source_path=str(source))
    again = ocr_queue.enqueue(user_id=user["id"], filename="renamed.png", source_path=str(source), sha256=first["sha256"])
    assert again["id"] == first["id"]
    assert ocr_queue.process_one()["status"] in {"queued", "failed"}
    failed = ocr_queue.get_job(first["id"], user["id"])
    assert failed["attempts"] == 1
    retried = ocr_queue.retry_job(first["id"], user["id"])
    assert retried["status"] == "queued"


def test_pdf_ocr_queue_preserves_page_anchors(tmp_path, monkeypatch):
    from hashmm.pipeline import ocr_queue
    from hashmm.pipeline import resource_pipeline as rp
    from hashmm.benchmark import ocr as benchmark_ocr

    source = tmp_path / "scan.pdf"
    source.write_bytes(b"fake-pdf")
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "vault"))

    class Pixmap:
        def __init__(self, page: int): self.page = page
        def save(self, target: str): Path(target).write_bytes(str(self.page).encode())
    class Page:
        def __init__(self, page: int): self.page = page
        def get_pixmap(self, **_kwargs): return Pixmap(self.page)
    class Document:
        def __len__(self): return 2
        def __getitem__(self, index: int): return Page(index + 1)
        def close(self): pass
    fake_fitz = types.SimpleNamespace(open=lambda _path: Document(), Matrix=lambda x, y: (x, y))
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setattr(benchmark_ocr, "ocr_image", lambda image, **_kwargs: f"page text {Path(image).stem}")

    digest = rp.sha256_file(source)
    result_path = ocr_queue._ocr_pdf_resource(
        source, digest=digest, filename="scan.pdf", lang="eng", engine="tesseract",
    )
    record = json.loads(Path(result_path).read_text(encoding="utf-8"))
    assert record["parse_state"] == "ready"
    assert record["page_count"] == 2
    assert record["readable_pages"] == 2
    assert [block["anchor"] for block in record["blocks"]] == ["p.1", "p.2"]
    assert "[p.1]" in record["text"] and "[p.2]" in record["text"]


def test_task_state_rejects_terminal_resurrection():
    from hashmm.agent.task_state import can_transition, public_contract
    assert can_transition("running", "waiting_approval")
    assert not can_transition("completed", "running")
    assert public_contract("waiting_approval")["requires_user"] is True


def test_run_checkpoint_survives_reload_and_is_owner_bound(tmp_db):
    db = _fresh(tmp_db)
    from hashmm.agent import work_runtime
    owner = db.create_user("v704-checkpoint", "pw12345678")
    other = db.create_user("v704-other", "pw12345678")
    run = work_runtime.create_run(user_id=owner["id"], kind="chat", source_id="checkpointed")
    saved = work_runtime.save_checkpoint(run["id"], user_id=owner["id"], reason="waiting_approval", state={"tool_name": "run_shell", "status": "waiting_approval"})
    assert saved and saved["generation"] == 1
    assert work_runtime.latest_checkpoint(run["id"], other["id"]) is None
    detail = work_runtime.get_run(run["id"], owner["id"])
    assert detail["checkpoints"][0]["id"] == saved["id"]


def test_harness_levels_are_public_and_argument_free():
    from hashmm.agent.harness import AgentRunKernel
    kernel = AgentRunKernel(
        owner_id="owner", conversation_id="conv", goal="goal",
        execution_scope={"schema": "hashmm.execution-scope.v1", "allowed_tools": []},
        tool_schemas=[], executors={}, max_iterations=2, max_tool_calls=2,
        max_search_calls=0, max_exec_calls=0, max_workers=0,
    )
    kernel.finish("completed")
    public = kernel.public()
    assert public["levels"]["schema"] == "hashmm.harness-levels.v1"
    assert {item["level"] for item in public["levels"]["levels"]} == {"L0", "L1", "L2", "L3"}
    assert all("args" not in event and "prompt" not in event for event in public["trajectory"]["events"])


def test_l3_rejects_completed_side_effect_without_verified_receipt():
    from hashmm.evaluation.harness_levels import level_l3_evidence
    base = {
        "terminal": {"reason": "completed"},
        "trajectory": {"events": [
            {"seq": 1, "type": "tool_finished", "detail": {
                "side_effect_class": "external_write", "receipt_status": "missing",
            }},
            {"seq": 2, "type": "turn_finished", "status": "completed"},
        ]},
    }
    failed = level_l3_evidence(base)
    assert failed["status"] == "failed"
    assert failed["required_receipts"] == 1
    base["trajectory"]["events"][0]["detail"]["receipt_status"] = "verified"
    passed = level_l3_evidence(base)
    assert passed["status"] == "passed"
    assert passed["verified_receipts"] == 1
