"""图片资源库 REST（V205 P1-5，图2-④）——列表 / 文本搜图 / 取原图 / 删除。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_admin, require_auth
from hashmm.retrieval import image_store as ist
from hashmm.utils import get_logger

logger = get_logger("hashmm.api.routes.images")

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("", summary="最近入库的图片")
async def list_images(request: Request, limit: int = 50):
    user = require_auth(request)
    return {"items": ist.list_images(limit, owner=user["uid"]), **ist.stats(owner=user["uid"])}


@router.get("/search", summary="文本搜图（caption/tags/filename）")
async def search_images(request: Request, q: str = "", top_k: int = 5):
    user = require_auth(request)
    if not q.strip():
        return {"items": []}
    return {"items": ist.search_text(q, top_k=top_k, owner=user["uid"])}


@router.get("/{image_id}/file", summary="取原图")
async def image_file(image_id: str, request: Request):
    user = require_auth(request)
    rec = ist.get_image(image_id, owner=user["uid"])
    if not rec:
        raise HTTPException(404, "图片不存在")
    from pathlib import Path
    from fastapi.responses import FileResponse
    p = Path(rec["path"])
    if not p.is_file():
        raise HTTPException(410, "图片文件已丢失")
    return FileResponse(str(p), filename=rec["filename"])


@router.delete("/{image_id}", summary="删除图片（管理员）")
async def delete_image(image_id: str, request: Request):
    admin = require_admin(request)
    rec = ist.get_image(image_id, owner=None)
    if not rec:
        raise HTTPException(404, "图片不存在")
    ok = ist.remove_image(image_id, owner=None)
    db.audit(admin["uid"], admin["sub"], "image_delete", rec["filename"])
    return {"ok": ok}
