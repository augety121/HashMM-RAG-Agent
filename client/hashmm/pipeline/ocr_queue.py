"""Durable, owner-scoped OCR queue with leases and page-level quality state."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hashmm.api import database as db
from hashmm.pipeline.ocr_provider import (
    OCRProviderError, capabilities, recognize_document_sidecar, recognize_image,
)

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_ALLOWED = {"queued", "running", "succeeded", "failed", "cancelled"}
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
_LEASE_SECONDS = max(30, min(int(os.environ.get("HASHMM_OCR_LEASE_SECONDS", "180") or 180), 1800))


def _clip(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _val(row: Any, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _public(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    total = max(0, int(_val(row, "total_pages", 0) or 0))
    processed = max(0, int(_val(row, "processed_pages", 0) or 0))
    return {
        "schema": "hashmm.ocr-job.v2",
        "id": _clip(_val(row, "id"), 96),
        "resource_id": _clip(_val(row, "resource_id"), 160),
        "resource_revision": max(1, int(_val(row, "resource_revision", 1) or 1)),
        "filename": _clip(_val(row, "filename"), 240),
        "sha256": _clip(_val(row, "sha256"), 64),
        "engine_requested": _clip(_val(row, "engine_requested", _val(row, "engine")), 40),
        "engine_resolved": _clip(_val(row, "engine_resolved", _val(row, "engine")), 40),
        "lang": _clip(_val(row, "lang"), 40),
        "status": _clip(_val(row, "status"), 24),
        "result_state": _clip(_val(row, "result_state"), 24),
        "priority": int(_val(row, "priority", 0) or 0),
        "total_pages": total,
        "processed_pages": processed,
        "failed_pages": max(0, int(_val(row, "failed_pages", 0) or 0)),
        "progress": max(0.0, min(float(_val(row, "progress", 0) or 0), 1.0)),
        "attempts": int(_val(row, "attempts", 0) or 0),
        "max_attempts": int(_val(row, "max_attempts", 3) or 3),
        "next_attempt_at": float(_val(row, "next_attempt_at", 0) or 0),
        "error_code": _clip(_val(row, "error_code"), 80),
        "error": _clip(_val(row, "error"), 500),
        "cancel_requested": bool(_val(row, "cancel_requested", 0)),
        "created_at": float(_val(row, "created_at", 0) or 0),
        "updated_at": float(_val(row, "updated_at", 0) or 0),
    }


def _link_job(conn: Any, job_id: str, owner: str, conv_id: str, project_id: str, filename: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ocr_job_links "
        "(job_id,user_id,conv_id,project_id,filename) VALUES (?,?,?,?,?)",
        (job_id, owner, _clip(conv_id, 160), _clip(project_id, 120), _clip(filename, 240)),
    )


def enqueue(
    *,
    user_id: str,
    filename: str,
    source_path: str,
    sha256: str = "",
    conv_id: str = "",
    project_id: str = "",
    resource_id: str = "",
    resource_revision: int = 1,
    engine: str = "",
    lang: str = "chi_sim+eng",
    priority: int = 0,
    max_attempts: int = 3,
) -> dict[str, Any]:
    owner = _clip(user_id, 160)
    path = Path(source_path).resolve()
    if not owner or not path.is_file():
        raise ValueError("OCR job requires an owner and an existing source file")
    digest = _clip(sha256, 64)
    if len(digest) != 64:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    selected = _clip(engine or os.environ.get("HASHMM_OCR_ENGINE") or "paddleocr", 40).lower()
    rid = _clip(resource_id or f"sha256:{digest}", 160)
    now, job_id = time.time(), "ocr_" + uuid.uuid4().hex
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO ocr_jobs "
            "(id,user_id,conv_id,filename,source_path,sha256,resource_id,resource_revision,"
            "engine,engine_requested,engine_resolved,lang,priority,status,attempts,max_attempts,"
            "next_attempt_at,error,error_code,result_path,result_state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,sha256,engine,lang) DO UPDATE SET "
            "filename=excluded.filename,source_path=excluded.source_path,"
            "resource_id=excluded.resource_id,resource_revision=excluded.resource_revision,"
            "priority=MAX(ocr_jobs.priority,excluded.priority),updated_at=excluded.updated_at",
            (
                job_id, owner, _clip(conv_id, 160), _clip(filename or path.name, 240),
                str(path), digest, rid, max(1, int(resource_revision or 1)),
                selected, selected, "", _clip(lang, 40) or "chi_sim+eng",
                max(-10, min(int(priority or 0), 10)), "queued", 0,
                max(1, min(int(max_attempts or 3), 8)), now, "", "", "", "", now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM ocr_jobs WHERE user_id=? AND sha256=? AND engine=? AND lang=?",
            (owner, digest, selected, _clip(lang, 40) or "chi_sim+eng"),
        ).fetchone()
        _link_job(conn, str(row["id"]), owner, conv_id, project_id, filename or path.name)
    return _public(row)


def get_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_jobs WHERE id=? AND user_id=?",
            (_clip(job_id, 96), _clip(user_id, 160)),
        ).fetchone()
    return _public(row) if row is not None else None


def list_jobs(user_id: str, *, conv_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    owner, conversation = _clip(user_id, 160), _clip(conv_id, 160)
    bounded = max(1, min(int(limit or 50), 200))
    with db._conn() as conn:
        if conversation:
            rows = conn.execute(
                "SELECT DISTINCT j.* FROM ocr_jobs j "
                "LEFT JOIN ocr_job_links l ON l.job_id=j.id AND l.user_id=j.user_id "
                "WHERE j.user_id=? AND (l.conv_id=? OR j.conv_id=?) "
                "ORDER BY j.updated_at DESC LIMIT ?",
                (owner, conversation, conversation, bounded),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ocr_jobs WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (owner, bounded),
            ).fetchall()
    return [_public(row) for row in rows]


def retry_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    now = time.time()
    with db._conn() as conn:
        conn.execute(
            "UPDATE ocr_jobs SET status='queued',next_attempt_at=?,error='',error_code='',"
            "cancel_requested=0,lease_owner='',lease_token='',lease_expires_at=0,updated_at=? "
            "WHERE id=? AND user_id=? AND status IN ('failed','cancelled')",
            (now, now, _clip(job_id, 96), _clip(user_id, 160)),
        )
        row = conn.execute(
            "SELECT * FROM ocr_jobs WHERE id=? AND user_id=?",
            (_clip(job_id, 96), _clip(user_id, 160)),
        ).fetchone()
    return _public(row) if row is not None else None


def cancel_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_jobs WHERE id=? AND user_id=?",
            (_clip(job_id, 96), _clip(user_id, 160)),
        ).fetchone()
        if row is None:
            return None
        if row["status"] == "queued":
            conn.execute(
                "UPDATE ocr_jobs SET status='cancelled',cancel_requested=1,updated_at=? "
                "WHERE id=? AND user_id=?",
                (now, row["id"], _clip(user_id, 160)),
            )
        elif row["status"] == "running":
            conn.execute(
                "UPDATE ocr_jobs SET cancel_requested=1,updated_at=? WHERE id=? AND user_id=?",
                (now, row["id"], _clip(user_id, 160)),
            )
        row = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (row["id"],)).fetchone()
    return _public(row)


def _claim_next() -> dict[str, Any] | None:
    now, token = time.time(), uuid.uuid4().hex
    with db._conn() as conn:
        conn.execute(
            "UPDATE ocr_jobs SET status='cancelled',lease_owner='',lease_token='',"
            "lease_expires_at=0,updated_at=? WHERE status='running' "
            "AND cancel_requested=1 AND lease_expires_at<=?",
            (now, now),
        )
        conn.execute(
            "UPDATE ocr_jobs SET status='queued',next_attempt_at=?,lease_owner='',lease_token='',"
            "lease_expires_at=0,updated_at=? WHERE status='running' AND cancel_requested=0 "
            "AND lease_expires_at>0 AND lease_expires_at<=?",
            (now, now, now),
        )
        row = conn.execute(
            "SELECT * FROM ocr_jobs WHERE status='queued' AND cancel_requested=0 "
            "AND next_attempt_at<=? ORDER BY priority DESC,created_at ASC LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            return None
        changed = conn.execute(
            "UPDATE ocr_jobs SET status='running',attempts=attempts+1,"
            "lease_owner=?,lease_token=?,lease_expires_at=?,heartbeat_at=?,updated_at=? "
            "WHERE id=? AND status='queued' AND cancel_requested=0",
            (_WORKER_ID, token, now + _LEASE_SECONDS, now, now, row["id"]),
        ).rowcount
        if not changed:
            return None
        claimed = conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (row["id"],)).fetchone()
        result = _public(claimed)
        result.update({
            "_owner_id": _clip(claimed["user_id"], 160),
            "_source_path": _clip(claimed["source_path"], 1024),
            "_lease_token": token,
        })
        return result


def _heartbeat(job_id: str, token: str, *, processed: int, total: int, failed: int) -> None:
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM ocr_jobs WHERE id=? AND lease_token=? AND status='running'",
            (job_id, token),
        ).fetchone()
        if row is None:
            raise OCRProviderError("lease_lost", "OCR lease is no longer owned", retryable=True)
        if bool(row["cancel_requested"]):
            raise OCRProviderError("cancelled", "OCR job was cancelled", retryable=False)
        progress = min(1.0, processed / max(total, 1))
        conn.execute(
            "UPDATE ocr_jobs SET total_pages=?,processed_pages=?,failed_pages=?,progress=?,"
            "heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=? AND lease_token=?",
            (total, processed, failed, progress, now, now + _LEASE_SECONDS, now, job_id, token),
        )


def _looks_blank(path: Path) -> bool:
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as image:
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            extrema = gray.getextrema()
            return bool(extrema and extrema[1] - extrema[0] < 12 and stat.mean[0] > 242)
    except Exception:
        return False


def _write_resource_cache(
    path: Path,
    *,
    text: str,
    digest: str,
    filename: str,
    blocks: list[dict[str, Any]],
    page_count: int,
    failed_pages: list[int],
    blank_pages: list[int],
    parser_used: str,
) -> tuple[str, str]:
    from hashmm.pipeline import resource_pipeline as rp
    readable = sorted({int(item.get("page") or 0) for item in blocks if int(item.get("page") or 0) > 0})
    total = max(int(page_count or 0), len(readable) + len(blank_pages) + len(failed_pages), 1)
    state = "partial" if failed_pages else "ready"
    issues = ([f"OCR failed on pages: {','.join(map(str, failed_pages[:40]))}"] if failed_pages else [])
    target = rp._cache_path(digest)
    previous_revision = 1
    if target.is_file():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
            if str(previous.get("sha256") or "") == digest:
                previous_revision = max(1, int(previous.get("resource_revision") or 1))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            previous_revision = 1
    record = {
        "schema": rp.SCHEMA,
        "parser_version": rp.PARSER_VERSION,
        "resource_id": f"sha256:{digest}",
        "resource_revision": previous_revision + 1,
        "sha256": digest,
        "filename": filename,
        "source_path": str(path),
        "size": path.stat().st_size,
        "detected_type": rp.detect_format(path),
        "parse_state": state,
        "page_count": total,
        "readable_pages": len(readable),
        "readable_ratio": len(readable) / total,
        "accounted_pages": total - len(failed_pages),
        "failed_pages": failed_pages,
        "blank_pages": blank_pages,
        "parser_used": parser_used,
        "text_chars": len(text),
        "text": text,
        "blocks": blocks,
        "quality": {
            "extractable_ratio": len(readable) / total,
            "accounted_ratio": (total - len(failed_pages)) / total,
            "issues": issues,
        },
        "warnings": issues,
    }
    rp._atomic_json(target, record)
    return str(target), state


def _project_message_state(job_id: str, owner: str, result: dict[str, Any]) -> None:
    """Refresh persisted attachment projections without trusting client state."""
    with db._conn() as conn:
        links = conn.execute(
            "SELECT conv_id,filename FROM ocr_job_links WHERE job_id=? AND user_id=?",
            (job_id, owner),
        ).fetchall()
        for link in links:
            conv_id, filename = str(link["conv_id"] or ""), str(link["filename"] or "")
            if not conv_id or not filename:
                continue
            rows = conn.execute(
                "SELECT id,files FROM messages WHERE conv_id=? AND role='user' AND files IS NOT NULL",
                (conv_id,),
            ).fetchall()
            for row in rows:
                try:
                    files = json.loads(row["files"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                changed = False
                for item in files if isinstance(files, list) else []:
                    if isinstance(item, dict) and str(item.get("filename") or "") == filename:
                        item.update({
                            "parse_state": result.get("parse_state"),
                            "resource_revision": result.get("resource_revision", 1),
                            "page_count": result.get("page_count", 0),
                            "readable_pages": result.get("readable_pages", 0),
                            "readable_ratio": result.get("readable_ratio", 0.0),
                            "accounted_pages": result.get("accounted_pages", 0),
                            "failed_pages": result.get("failed_pages", []),
                            "blank_pages": result.get("blank_pages", []),
                            "parser_used": result.get("parser_used", ""),
                            "warnings": result.get("warnings", []),
                        })
                        changed = True
                if changed:
                    conn.execute(
                        "UPDATE messages SET files=? WHERE id=?",
                        (json.dumps(files, ensure_ascii=False, separators=(",", ":")), row["id"]),
                    )


def _ocr_pdf(path: Path, job: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    try:
        import fitz
    except ImportError as exc:
        raise OCRProviderError("renderer_missing", "PDF OCR requires PyMuPDF", retryable=False) from exc
    from hashmm.pipeline import resource_pipeline as rp
    temp_root = rp._data_root() / "ocr_work"
    temp_root.mkdir(parents=True, exist_ok=True)
    dpi = max(144, min(int(os.environ.get("HASHMM_OCR_PDF_DPI", "300") or 300), 400))
    document = fitz.open(str(path))
    blocks: list[dict[str, Any]] = []
    page_texts: list[str] = []
    failed_pages: list[int] = []
    blank_pages: list[int] = []
    try:
        total = len(document)
        if total <= 0:
            raise OCRProviderError("empty_document", "PDF contains no pages")
        _heartbeat(job["id"], job["_lease_token"], processed=0, total=total, failed=0)
        with TemporaryDirectory(prefix="pdf-", dir=str(temp_root)) as work:
            for index in range(total):
                page_no = index + 1
                pixmap = document[index].get_pixmap(
                    matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False
                )
                image_path = Path(work) / f"page-{page_no}.png"
                pixmap.save(str(image_path))
                try:
                    page_result = recognize_image(
                        image_path,
                        engine=job["engine_requested"],
                        language=job["lang"],
                        page=page_no,
                        device=os.environ.get("HASHMM_OCR_DEVICE", "gpu"),
                    )
                    page_text = str(page_result.get("text") or "").strip()
                    if page_text:
                        page_texts.append(f"[p.{page_no}]\n{page_text}")
                        blocks.extend(page_result.get("blocks") or [])
                    elif _looks_blank(image_path):
                        blank_pages.append(page_no)
                    else:
                        failed_pages.append(page_no)
                except OCRProviderError as exc:
                    if exc.code in {"cancelled", "lease_lost"}:
                        raise
                    failed_pages.append(page_no)
                _heartbeat(
                    job["id"], job["_lease_token"], processed=page_no,
                    total=total, failed=len(failed_pages),
                )
    finally:
        document.close()
    if not blocks and failed_pages:
        raise OCRProviderError("empty_output", "OCR produced no readable PDF pages", retryable=True)
    text = "\n\n".join(page_texts)
    result_path, state = _write_resource_cache(
        path,
        text=text,
        digest=job["sha256"],
        filename=job["filename"],
        blocks=blocks,
        page_count=total,
        failed_pages=failed_pages,
        blank_pages=blank_pages,
        parser_used=f"ocr-{job['engine_requested']}-pdf-{dpi}dpi",
    )
    return result_path, state, json.loads(Path(result_path).read_text(encoding="utf-8"))


def _ocr_pdf_resource(
    path: Path,
    *,
    digest: str,
    filename: str,
    lang: str,
    engine: str,
) -> str:
    """Backward-compatible deterministic helper used by the V704 harness.

    Product execution goes through leased ``_ocr_pdf`` above.  This bounded
    helper intentionally has no queue side effects so older page-anchor tests
    and benchmark callers can continue to validate rendering in isolation.
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF OCR requires PyMuPDF") from exc
    from hashmm.benchmark.ocr import ocr_image
    from hashmm.pipeline import resource_pipeline as rp
    temp_root = rp._data_root() / "ocr_work"
    temp_root.mkdir(parents=True, exist_ok=True)
    document = fitz.open(str(path))
    blocks: list[dict[str, Any]] = []
    texts: list[str] = []
    failed: list[int] = []
    blank: list[int] = []
    try:
        page_count = len(document)
        with TemporaryDirectory(prefix="pdf-", dir=str(temp_root)) as work:
            for index in range(page_count):
                page_no = index + 1
                pixmap = document[index].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
                image_path = Path(work) / f"page-{page_no}.png"
                pixmap.save(str(image_path))
                text = str(ocr_image(image_path, lang=lang, engine=engine) or "").strip()
                if text:
                    texts.append(f"[p.{page_no}]\n{text}")
                    blocks.append({
                        "type": "text", "content": text, "page": page_no,
                        "anchor": f"p.{page_no}", "position": len(blocks),
                        "char_count": len(text), "is_noise": False,
                    })
                elif _looks_blank(image_path):
                    blank.append(page_no)
                else:
                    failed.append(page_no)
    finally:
        document.close()
    if not blocks:
        raise RuntimeError("OCR engine returned no text for any PDF page")
    result_path, _ = _write_resource_cache(
        Path(path), text="\n\n".join(texts), digest=digest, filename=filename,
        blocks=blocks, page_count=page_count, failed_pages=failed,
        blank_pages=blank, parser_used=f"ocr-{engine}-pdf-300dpi",
    )
    return result_path


def process_one() -> dict[str, Any] | None:
    job = _claim_next()
    if not job:
        return None
    jid, owner, token = job["id"], job["_owner_id"], job["_lease_token"]
    try:
        source = Path(job["_source_path"]).resolve()
        if not source.is_file():
            raise OCRProviderError("source_missing", "OCR source file is missing")
        actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_digest != job["sha256"]:
            raise OCRProviderError("source_changed", "OCR source bytes no longer match the admitted SHA-256")
        from hashmm.pipeline import resource_pipeline as rp
        detected = rp.detect_format(source)
        if detected == "pdf" and job["engine_requested"] == "unlimited":
            _heartbeat(jid, token, processed=0, total=1, failed=0)
            parsed = recognize_document_sidecar(
                source, language=job["lang"], sha256=job["sha256"]
            )
            if not parsed.get("blocks") and not parsed.get("blank_pages"):
                raise OCRProviderError(
                    "empty_output", "Unlimited OCR produced no accounted pages", retryable=True
                )
            page_count = max(1, int(parsed.get("page_count") or 0))
            result_path, result_state = _write_resource_cache(
                source,
                text=str(parsed.get("text") or ""),
                digest=job["sha256"],
                filename=job["filename"],
                blocks=parsed.get("blocks") or [],
                page_count=page_count,
                failed_pages=parsed.get("failed_pages") or [],
                blank_pages=parsed.get("blank_pages") or [],
                parser_used=(
                    "ocr-unlimited-sidecar"
                    + (f"@{parsed.get('model_revision')}" if parsed.get("model_revision") else "")
                ),
            )
            resource = json.loads(Path(result_path).read_text(encoding="utf-8"))
            _heartbeat(
                jid, token, processed=page_count, total=page_count,
                failed=len(parsed.get("failed_pages") or []),
            )
        elif detected == "pdf":
            result_path, result_state, resource = _ocr_pdf(source, job)
        elif detected == "image":
            _heartbeat(jid, token, processed=0, total=1, failed=0)
            page = recognize_image(
                source, engine=job["engine_requested"], language=job["lang"], page=1,
                device=os.environ.get("HASHMM_OCR_DEVICE", "gpu"),
            )
            text = str(page.get("text") or "").strip()
            failed, blank = [], []
            if not text:
                if _looks_blank(source):
                    blank = [1]
                else:
                    raise OCRProviderError("empty_output", "OCR produced no image text", retryable=True)
            result_path, result_state = _write_resource_cache(
                source, text=text, digest=job["sha256"], filename=job["filename"],
                blocks=page.get("blocks") or [], page_count=1,
                failed_pages=failed, blank_pages=blank,
                parser_used=f"ocr-{job['engine_requested']}-image",
            )
            resource = json.loads(Path(result_path).read_text(encoding="utf-8"))
            _heartbeat(jid, token, processed=1, total=1, failed=0)
        else:
            parsed = rp.parse_resource(source, display_name=job["filename"], force=True)
            if parsed.get("parse_state") != "ready":
                raise OCRProviderError("unsupported_resource", f"Resource state is {parsed.get('parse_state')}")
            result_path, result_state, resource = str(rp._cache_path(job["sha256"])), "ready", parsed
        now = time.time()
        with db._conn() as conn:
            changed = conn.execute(
                "UPDATE ocr_jobs SET status='succeeded',result_path=?,result_state=?,"
                "engine_resolved=?,resource_revision=?,error='',error_code='',progress=1,lease_owner='',lease_token='',"
                "lease_expires_at=0,heartbeat_at=?,updated_at=? WHERE id=? AND lease_token=?",
                (
                    result_path, result_state, job["engine_requested"],
                    max(1, int(resource.get("resource_revision") or 1)),
                    now, now, jid, token,
                ),
            ).rowcount
        if changed:
            _project_message_state(jid, owner, resource)
    except OCRProviderError as exc:
        now = time.time()
        attempts, maximum = int(job.get("attempts") or 1), int(job.get("max_attempts") or 3)
        cancelled = exc.code == "cancelled"
        terminal = cancelled or not exc.retryable or attempts >= maximum
        status = "cancelled" if cancelled else ("failed" if terminal else "queued")
        with db._conn() as conn:
            conn.execute(
                "UPDATE ocr_jobs SET status=?,error=?,error_code=?,next_attempt_at=?,"
                "lease_owner='',lease_token='',lease_expires_at=0,updated_at=? "
                "WHERE id=? AND lease_token=?",
                (
                    status, _clip(str(exc), 500), _clip(exc.code, 80),
                    now + min(300, 2 ** min(attempts, 8)), now, jid, token,
                ),
            )
    except Exception as exc:
        now = time.time()
        attempts, maximum = int(job.get("attempts") or 1), int(job.get("max_attempts") or 3)
        terminal = attempts >= maximum
        with db._conn() as conn:
            conn.execute(
                "UPDATE ocr_jobs SET status=?,error=?,error_code='internal_error',next_attempt_at=?,"
                "lease_owner='',lease_token='',lease_expires_at=0,updated_at=? "
                "WHERE id=? AND lease_token=?",
                (
                    "failed" if terminal else "queued",
                    _clip(f"{type(exc).__name__}: {exc}", 500),
                    now + min(300, 2 ** min(attempts, 8)), now, jid, token,
                ),
            )
    return get_job(jid, owner) or {"id": jid}


def _worker() -> None:
    while not _STOP.is_set():
        try:
            if process_one() is None:
                _STOP.wait(2.0)
        except Exception:
            _STOP.wait(2.0)


def start_worker() -> bool:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return False
    _STOP.clear()
    _THREAD = threading.Thread(target=_worker, name="hashmm-ocr", daemon=True)
    _THREAD.start()
    return True


def stop_worker() -> None:
    _STOP.set()


__all__ = [
    "capabilities", "cancel_job", "enqueue", "get_job", "list_jobs",
    "process_one", "retry_job", "start_worker", "stop_worker",
]
