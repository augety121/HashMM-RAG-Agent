"""hashmm/api/routes/profile.py — 用户头像上传/读取（经后端中转，三端共用）。

App 只装了 supabase auth/postgrest/realtime（无 storage 模块），客户端走 Web；与其各端引入
不同的存储 SDK，不如统一走后端：上传存到 DATA_ROOT/avatars/{uid}.jpg，读取直接服务该文件。
鉴权：上传需有效登录令牌（按令牌里的 uid 落盘，互不覆盖）；读取按 uid 服务（头像本身非敏感，
且 <img> 取不到 Authorization 头，故读取不强制鉴权）。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from hashmm.api.auth import require_auth
from hashmm.api.database import DATA_ROOT

router = APIRouter(prefix="/api/profile", tags=["profile"])

_AVATAR_DIR = DATA_ROOT / "avatars"
_MAX_AVATAR_BYTES = 4_000_000     # 4MB
_SAFE_UID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _avatar_path(uid: str) -> Path:
    if not _SAFE_UID.match(uid or ""):
        raise HTTPException(status_code=400, detail="非法 uid")
    return _AVATAR_DIR / f"{uid}.jpg"


@router.post("/avatar")
async def upload_avatar(request: Request):
    """上传当前登录用户的头像（请求体为 raw JPEG 字节）。"""
    user = require_auth(request)
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="空文件")
    if len(body) > _MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="头像过大（上限 4MB）")
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    _avatar_path(uid).write_bytes(body)
    return {"ok": True, "uid": uid, "url": f"/api/profile/avatar/{uid}"}


@router.get("/avatar/{uid}")
async def get_avatar(uid: str):
    """读取指定 uid 的头像（找不到返回 404）。"""
    p = _avatar_path(uid)
    if not p.exists():
        raise HTTPException(status_code=404, detail="无头像")
    return Response(
        content=p.read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"},
    )
