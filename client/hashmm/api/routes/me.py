"""Self-service account profile, settings and security-adjacent APIs."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.api.user_settings import (
    SettingConflictError,
    SettingValidationError,
    list_settings,
    update_settings,
)

router = APIRouter(prefix="/api/me", tags=["me"])


def _identity(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("id") or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="登录状态无效")
    return user, owner


@router.get("/profile")
async def get_profile(request: Request):
    user, owner = _identity(request)
    local = db.get_user(owner)
    return {
        "id": owner,
        "username": str((local or {}).get("username") or user.get("sub") or user.get("email") or ""),
        "display_name": str((local or {}).get("display_name") or user.get("display_name") or user.get("sub") or ""),
        "role": str((local or {}).get("role") or user.get("role") or "user"),
        "identity_source": "supabase" if owner.startswith("sb_") else "local",
    }


@router.patch("/profile")
async def patch_profile(request: Request):
    user, owner = _identity(request)
    body = await request.json()
    if not isinstance(body, dict) or set(body) - {"display_name"}:
        raise HTTPException(status_code=400, detail="只允许修改 display_name")
    display_name = str(body.get("display_name") or "").strip()
    if not display_name or len(display_name) > 80:
        raise HTTPException(status_code=422, detail="姓名长度必须为 1 到 80 个字符")
    if owner.startswith("sb_"):
        authorization = request.headers.get("authorization", "")
        access_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        from hashmm.api import supabase_auth
        if not supabase_auth.update_own_profile(access_token, {"display_name": display_name}):
            raise HTTPException(status_code=502, detail="云端账号资料暂时无法更新")
    else:
        if not db.get_user(owner):
            raise HTTPException(status_code=404, detail="账号不存在")
        db.update_user(owner, display_name=display_name)
    db.audit(owner, str(user.get("sub") or owner), "profile.update", "display_name", request.client.host if request.client else "")
    return {"ok": True, "profile": {"id": owner, "display_name": display_name}}


@router.get("/settings")
async def get_settings(request: Request):
    _, owner = _identity(request)
    return {"schema": "hashmm.user-settings.v2", "settings": list_settings(owner)}


@router.patch("/settings")
async def patch_settings(request: Request):
    user, owner = _identity(request)
    body = await request.json()
    changes = body.get("changes") if isinstance(body, dict) else None
    try:
        settings = update_settings(owner, str(user.get("sub") or owner), changes)
    except SettingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SettingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    receipt_id = "set_" + secrets.token_hex(12)
    keys = [str(item.get("key") or "") for item in changes]
    db.audit(owner, str(user.get("sub") or owner), "settings.update", f"receipt={receipt_id}; keys={','.join(keys)}", request.client.host if request.client else "")
    return {
        "ok": True,
        "schema": "hashmm.user-settings.v2",
        "receipt": {"id": receipt_id, "status": "applied", "keys": keys},
        "settings": settings,
    }

