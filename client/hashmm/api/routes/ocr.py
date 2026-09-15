"""Authenticated OCR queue status and retry endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.pipeline import ocr_queue
from hashmm.pipeline.resource_pipeline import parse_resource, resource_summary

router = APIRouter(prefix="/api/ocr-jobs", tags=["OCR"])
resource_router = APIRouter(prefix="/api/resources", tags=["resources"])


def _owned_resource(resource_id: str, owner: str):
    with db._conn() as conn:
        return conn.execute(
            "SELECT * FROM ocr_jobs WHERE resource_id=? AND user_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (str(resource_id or "")[:160], str(owner or "")[:160]),
        ).fetchone()


@resource_router.get("/{resource_id}", summary="Get authoritative resource state")
async def get_resource(resource_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    row = _owned_resource(resource_id, owner)
    if row is None:
        raise HTTPException(404, "resource_not_found")
    try:
        resource = parse_resource(row["source_path"], display_name=row["filename"])
    except Exception:
        raise HTTPException(404, "resource_not_found")
    return resource_summary(resource)


@resource_router.post("/{resource_id}/ocr-jobs", summary="Create an owner-scoped OCR attempt")
async def create_resource_ocr_job(resource_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    row = _owned_resource(resource_id, owner)
    if row is None:
        raise HTTPException(404, "resource_not_found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    engine = str(body.get("engine") or row["engine_requested"] or "paddleocr")[:40]
    if engine not in {"paddleocr", "tesseract", "unlimited"}:
        raise HTTPException(422, "unsupported_ocr_engine")
    return ocr_queue.enqueue(
        user_id=owner,
        conv_id=str(row["conv_id"] or ""),
        filename=str(row["filename"] or ""),
        source_path=str(row["source_path"] or ""),
        sha256=str(row["sha256"] or ""),
        resource_id=resource_id,
        resource_revision=int(row["resource_revision"] or 1),
        engine=engine,
        lang=str(body.get("lang") or row["lang"] or "chi_sim+eng")[:40],
        priority=int(body.get("priority") or 0),
    )


@router.get("/capabilities", summary="OCR provider capabilities")
async def ocr_capabilities(request: Request, probe: bool = False):
    require_auth(request)
    return ocr_queue.capabilities(probe=bool(probe))


@router.get("", summary="查看当前账号的 OCR 队列")
async def ocr_jobs(request: Request, conversation_id: str = ""):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    return {"schema": "hashmm.ocr-feed.v2", "items": ocr_queue.list_jobs(owner, conv_id=conversation_id)}


@router.get("/{job_id}", summary="查看一个 OCR 作业")
async def ocr_job(job_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    row = ocr_queue.get_job(job_id, owner)
    if row is None:
        raise HTTPException(404, "ocr_job_not_found")
    return row


@router.post("/{job_id}/retry", summary="重试失败的 OCR 作业")
async def ocr_retry(job_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    row = ocr_queue.retry_job(job_id, owner)
    if row is None:
        raise HTTPException(404, "ocr_job_not_found")
    return row


@router.post("/{job_id}/cancel", summary="Cancel an OCR job")
async def ocr_cancel(job_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("sub") or "")
    row = ocr_queue.cancel_job(job_id, owner)
    if row is None:
        raise HTTPException(404, "ocr_job_not_found")
    return row
