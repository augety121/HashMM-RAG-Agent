"""Owner-scoped Open Knowledge Format import/export routes."""
from __future__ import annotations

import asyncio
import io
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.okf import (
    MAX_ARCHIVE_BYTES,
    apply_preview,
    export_pack,
    inspect_archive,
    list_packs,
)

router = APIRouter(prefix="/api/okf", tags=["okf"])


def _identity(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    owner_id = str(user.get("uid") or "").strip()
    if not owner_id:
        raise HTTPException(status_code=401, detail="登录状态无效")
    return user, owner_id


@router.get("/packs")
async def read_packs(request: Request):
    _, owner_id = _identity(request)
    return {"packs": await asyncio.to_thread(list_packs, owner_id)}


@router.post("/preview")
async def preview_pack(
    request: Request,
    file: UploadFile = File(...),
):
    _, owner_id = _identity(request)
    filename = str(file.filename or "knowledge-pack.zip")
    if not filename.lower().endswith((".zip", ".okf")):
        raise HTTPException(status_code=422, detail="请选择 ZIP 或 .okf 知识包")
    payload = await file.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="知识包超过 25 MB")
    try:
        preview = await asyncio.to_thread(inspect_archive, owner_id, filename, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "preview": preview}


@router.post("/apply/{preview_id}")
async def import_pack(preview_id: str, request: Request):
    user, owner_id = _identity(request)
    try:
        pack = await asyncio.to_thread(apply_preview, owner_id, preview_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="预检记录不存在或已过期") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"知识索引暂不可用：{str(exc)[:180]}") from exc
    db.audit(
        owner_id,
        str(user.get("sub") or owner_id),
        "okf_import",
        f"{pack['id']}:{pack['concept_count']} concepts",
    )
    return {"ok": True, "pack": pack}


@router.get("/packs/{pack_id}/export")
async def download_pack(pack_id: str, request: Request):
    _, owner_id = _identity(request)
    try:
        filename, payload = await asyncio.to_thread(export_pack, owner_id, pack_id)
    except LookupError as exc:
        # Missing and foreign packs intentionally share the same response.
        raise HTTPException(status_code=404, detail="知识包不存在") from exc
    encoded = filename.encode("utf-8").hex()
    encoded_name = quote(filename, safe="")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=hashmm-knowledge-pack.zip; "
                f"filename*=UTF-8''{encoded_name}"
            ),
            "X-HashMM-Filename-Hex": encoded,
        },
    )
