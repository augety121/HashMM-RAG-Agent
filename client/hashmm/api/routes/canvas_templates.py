"""画布「我的模板」（V226）：把当前画布存为可复用模板，起稿菜单里一键再用。

存储：data/canvas_templates/{uid}.json 侧车（items 列表），进程锁 + 原子写；
上限 20 个/人，单模板 HTML ≤2MB，名称 ≤40 字。删除即从列表移除。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth, require_admin
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.canvas_tpl")
router = APIRouter(prefix="/api/canvas", tags=["canvas"])

_LOCK = threading.Lock()
_CAP = 20
_HTML_CAP = 2_000_000


def _path(uid: str) -> Path:
    safe = "".join(c for c in uid if c.isalnum() or c in "-_")[:64] or "anon"
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "canvas_templates" / f"{safe}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(uid: str) -> list:
    p = _path(uid)
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
    except Exception as e:
        log_suppressed(logger, e)
    return []


def _save(uid: str, items: list) -> None:
    p = _path(uid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


@router.get("/templates")
async def list_templates(request: Request):
    user = require_auth(request)
    items = _load(user.get("uid", ""))
    org = _load("_org")   # V230 组织模板库：全员共享（升入/管理见 promote 与 DELETE）
    return {"items": [{"id": t.get("id"), "name": t.get("name"), "created": t.get("created", 0)}
                      for t in items if t.get("id")],
            "org_items": [{"id": t.get("id"), "name": t.get("name"), "by": t.get("by", "")}
                          for t in org if t.get("id")]}


@router.get("/templates/{tpl_id}")
async def get_template(tpl_id: str, request: Request):
    user = require_auth(request)
    for t in _load(user.get("uid", "")) + _load("_org"):
        if t.get("id") == tpl_id:
            return {"id": tpl_id, "name": t.get("name"), "html": t.get("html", "")}
    raise HTTPException(404, "模板不存在")


@router.post("/templates/{tpl_id}/promote")
async def promote_template(tpl_id: str, request: Request):
    """V230 升为组织模板：复制到 _org 库（全员起稿菜单可见）；删除组织模板需管理员。"""
    user = require_auth(request)
    uid = user.get("uid", "")
    by = (user.get("sub") or uid or "").split("@")[0][:20]
    with _LOCK:
        src = next((t for t in _load(uid) if t.get("id") == tpl_id), None)
        if not src:
            raise HTTPException(404, "模板不存在（只能升自己的模板）")
        org = _load("_org")
        if len(org) >= _CAP:
            raise HTTPException(409, f"组织模板已达上限 {_CAP} 个")
        name = src.get("name", "")
        names = {t.get("name") for t in org}
        base, n = name, 2
        while name in names:
            name = f"{base}-{n}"[:40]; n += 1
        org.append({"id": secrets.token_hex(4), "name": name, "html": src.get("html", ""),
                    "created": int(time.time()), "by": by})
        _save("_org", org)
    return {"ok": True, "name": name}


@router.post("/templates")
async def save_template(request: Request):
    user = require_auth(request)
    body = await request.json()
    name = str((body or {}).get("name") or "").strip()[:40]
    html = str((body or {}).get("html") or "")
    if not name or not html:
        raise HTTPException(400, "name 与 html 必填")
    if len(html.encode("utf-8", "ignore")) > _HTML_CAP:
        raise HTTPException(413, "模板超过 2MB 上限")
    uid = user.get("uid", "")
    with _LOCK:
        items = _load(uid)
        if len(items) >= _CAP:
            raise HTTPException(409, f"模板已达上限 {_CAP} 个，先删几个再存")
        tid = secrets.token_hex(4)
        items.append({"id": tid, "name": name, "html": html, "created": int(time.time())})
        _save(uid, items)
    return {"ok": True, "id": tid, "name": name}


@router.delete("/templates/{tpl_id}")
async def delete_template(tpl_id: str, request: Request):
    user = require_auth(request)
    uid = user.get("uid", "")
    with _LOCK:
        org = _load("_org")
        if any(t.get("id") == tpl_id for t in org):   # 组织模板：仅管理员可删
            require_admin(request)
            _save("_org", [t for t in org if t.get("id") != tpl_id])
            return {"ok": True, "scope": "org"}
        items = [t for t in _load(uid) if t.get("id") != tpl_id]
        _save(uid, items)
    return {"ok": True}


@router.get("/templates/export")
async def export_templates(request: Request):
    """V228 导出：全部模板打包为 JSON 附件（含 html），跨机迁移/备份用。"""
    from fastapi.responses import JSONResponse
    user = require_auth(request)
    items = _load(user.get("uid", ""))
    return JSONResponse(
        {"kind": "hashmm-canvas-templates", "version": 1, "items": items},
        headers={"Content-Disposition": 'attachment; filename="hashmm-canvas-templates.json"'},
    )


@router.post("/templates/import")
async def import_templates(request: Request):
    """V228 导入：合并到我的模板（重名自动加后缀；超上限截断；单个 >2MB 跳过）。"""
    user = require_auth(request)
    body = await request.json()
    incoming = (body or {}).get("items") or []
    if not isinstance(incoming, list):
        raise HTTPException(400, "items 需为数组")
    uid = user.get("uid", "")
    imported, skipped = 0, 0
    with _LOCK:
        items = _load(uid)
        names = {t.get("name") for t in items}
        for t in incoming:
            if len(items) >= _CAP:
                skipped += 1
                continue
            name = str((t or {}).get("name") or "").strip()[:40]
            html = str((t or {}).get("html") or "")
            if not name or not html or len(html.encode("utf-8", "ignore")) > _HTML_CAP:
                skipped += 1
                continue
            base, n = name, 2
            while name in names:
                name = f"{base}-{n}"[:40]
                n += 1
            names.add(name)
            items.append({"id": secrets.token_hex(4), "name": name, "html": html,
                          "created": int(time.time())})
            imported += 1
        _save(uid, items)
    return {"ok": True, "imported": imported, "skipped": skipped, "total": len(items)}
