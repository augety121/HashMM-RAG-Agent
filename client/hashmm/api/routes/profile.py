"""用户偏好卡（V230 记忆纪元·端点版）：自由文本偏好，可查可改可删——隐私你做主。
下一版接注入点（system prompt 组装处）后，Agent 每轮自动带上这张卡。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _path(uid: str) -> Path:
    safe = "".join(c for c in uid if c.isalnum() or c in "-_")[:64] or "anon"
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "user_profile" / f"{safe}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_preferences_text(uid: str) -> str:
    """供 agent 主链注入：读用户偏好卡文本（无档/异常回空，绝不拦主流程）。"""
    try:
        p = _path(uid or "")
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return str(d.get("text") or "")[:1200]
    except Exception:
        pass
    return ""


# ── V254 头像（修复断链）：SettingsModal 一直在 POST /api/profile/avatar，
# 后端此前根本没有这个端点——上传按钮点了等于没点。现补齐：
#   POST /api/profile/avatar        原始图片字节直传（≤2MB，png/jpeg/webp）
#   GET  /api/profile/avatar        取自己的头像（无则 404，前端回退首字母头像）
_AVATAR_MAX = 2 * 1024 * 1024
_AVATAR_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}


def _avatar_path(uid: str) -> Path:
    safe = "".join(c for c in uid if c.isalnum() or c in "-_")[:64] or "anon"
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "avatars"
    p.mkdir(parents=True, exist_ok=True)
    return p / safe


@router.post("/avatar")
async def upload_avatar(request: Request):
    user = require_auth(request)
    body = await request.body()
    if not body:
        raise HTTPException(400, "空文件")
    if len(body) > _AVATAR_MAX:
        raise HTTPException(413, "头像不能超过 2MB")
    ext = ""
    for magic, e in _AVATAR_MAGIC.items():
        if body[: len(magic)] == magic:
            ext = e
            break
    if not ext:
        raise HTTPException(400, "仅支持 PNG / JPEG / WebP")
    base = _avatar_path(user.get("uid", ""))
    for old in (base.with_suffix(".png"), base.with_suffix(".jpg"), base.with_suffix(".webp")):
        try:
            if old.exists():
                old.unlink()
        except Exception:
            pass
    fp = base.with_suffix("." + ext)
    fp.write_bytes(body)
    return {"ok": True, "url": "/api/profile/avatar?ts=" + str(int(time.time()))}


@router.get("/avatar")
async def get_avatar(request: Request):
    from fastapi.responses import FileResponse
    user = require_auth(request)
    base = _avatar_path(user.get("uid", ""))
    for ext, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".webp", "image/webp")):
        fp = base.with_suffix(ext)
        if fp.exists():
            return FileResponse(str(fp), media_type=mime,
                                headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "未设置头像")


@router.get("/preferences")
async def get_prefs(request: Request):
    user = require_auth(request)
    p = _path(user.get("uid", ""))
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"text": "", "updated": 0}


@router.put("/preferences")
async def put_prefs(request: Request):
    user = require_auth(request)
    body = await request.json()
    text = str((body or {}).get("text") or "")[:2000]
    p = _path(user.get("uid", ""))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"text": text, "updated": int(time.time())},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True}


@router.delete("/preferences")
async def del_prefs(request: Request):
    user = require_auth(request)
    p = _path(user.get("uid", ""))
    try:
        p.unlink(missing_ok=True)
    except Exception:
        raise HTTPException(500, "删除失败")
    return {"ok": True}
