"""Auth routes — login / register / me / password / refresh / logout.

v17 Phase 65 — big-company token lifecycle:
  - login/register return a short-lived ACCESS token + a long-lived REFRESH token.
  - /refresh exchanges a valid refresh token for a fresh access (and rotates the
    refresh token).
  - /logout revokes ALL of the user's tokens server-side (bumps token_version),
    so logging out actually invalidates the session everywhere — not just on the
    client.
  - changing the password revokes all existing sessions, then issues fresh
    tokens for the current device.
"""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException

from hashmm.api import database as db
from hashmm.api import auth as _auth
from hashmm.api.auth import (
    create_token, create_refresh_token, verify_token, require_auth,
)
from hashmm.api.schemas import LoginRequest, RegisterRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_pair(user_id: str, username: str, role: str) -> dict:
    """Mint an access + refresh token pair for a freshly-authenticated user."""
    return {
        "token": create_token(user_id, username, role),
        "refresh_token": create_refresh_token(user_id, username, role),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    import os
    from hashmm.api import supabase_auth
    # Supabase 启用后，默认只允许 Supabase 账号登录（不允许本地 admin/密码账号）。
    # 应急：设环境变量 HASHMM_ALLOW_LOCAL_LOGIN=1 可临时恢复本地账号登录。
    allow_local = (os.environ.get("HASHMM_ALLOW_LOCAL_LOGIN", "") == "1") or not supabase_auth.enabled()

    if allow_local:
        user = db.verify_user(req.username, req.password)
        if user:
            pair = _issue_pair(user["id"], user["username"], user["role"])
            db.audit(user["id"], user["username"], "login",
                     f"IP: {request.client.host if request.client else 'unknown'}")
            return {
                **pair,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user["display_name"],
                    "role": user["role"],
                },
            }

    # Supabase 账号登录（客户端 / Web / App 共用同一套账号）
    if supabase_auth.enabled():
        try:
            session = supabase_auth.password_login(req.username, req.password)
            if session and session.get("access_token"):
                sb_user = supabase_auth.session_to_user(session)
                try:
                    db.audit(sb_user["id"], sb_user["username"], "login", "Supabase 账号登录")
                except Exception:
                    pass
                return {
                    "token": session["access_token"],
                    "refresh_token": session.get("refresh_token", ""),
                    "user": sb_user,
                }
        except Exception:
            pass
    raise HTTPException(401, "账号或密码错误")


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    if len(req.username) < 2 or len(req.password) < 4:
        raise HTTPException(400, "用户名至少2字符，密码至少4字符")
    user = db.create_user(req.username, req.password, req.display_name)
    if not user:
        raise HTTPException(409, "用户名已存在")
    pair = _issue_pair(user["id"], user["username"], user["role"])
    db.audit(user["id"], user["username"], "register", "新用户注册")
    return {**pair, "user": user}


@router.post("/refresh")
async def refresh(request: Request):
    """Exchange a valid REFRESH token for a fresh access token (and rotate the
    refresh token). Accepts the refresh token in the JSON body
    ({"refresh_token": ...}) or the Authorization: Bearer header."""
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    rt = body.get("refresh_token") or ""
    if not rt:
        auth_h = request.headers.get("Authorization", "")
        if auth_h.startswith("Bearer "):
            rt = auth_h[7:]
    data = verify_token(rt, expected_type="refresh")
    if data:
        uid, uname, role = data["uid"], data["sub"], data.get("role", "user")
        return _issue_pair(uid, uname, role)
    # HashMM 刷新令牌无效 → 尝试 Supabase refresh_token（客户端用 Supabase 账号登录的情形）
    try:
        from hashmm.api import supabase_auth
        if supabase_auth.enabled():
            session = supabase_auth.refresh_session(rt)
            if session and session.get("access_token"):
                return {
                    "token": session["access_token"],
                    "refresh_token": session.get("refresh_token", ""),
                    "user": supabase_auth.session_to_user(session),
                }
    except Exception:
        pass
    raise HTTPException(401, "刷新令牌无效或已过期，请重新登录")


@router.post("/logout")
async def logout(request: Request):
    """Revoke ALL of the user's tokens server-side. Idempotent."""
    user = require_auth(request)
    _auth.bump_and_revoke(user["uid"])
    db.audit(user["uid"], user.get("sub", ""), "logout", "")
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    user = require_auth(request)
    full = db.get_user(user["uid"])
    return full or user


@router.get("/supabase-config")
async def supabase_config():
    """公开端点：返回 Supabase 公开配置（URL + Publishable Key 均可公开），供前端创建客户端、
    决定是否显示 Supabase 登录入口。未配置则 enabled=False，前端隐藏入口。"""
    try:
        from hashmm.api import supabase_auth
        return {
            "enabled": supabase_auth.enabled(),
            "url": supabase_auth.supabase_url(),
            "publishable_key": supabase_auth.publishable_key(),
        }
    except Exception:
        return {"enabled": False, "url": "", "publishable_key": ""}


@router.post("/password")
async def change_pw(req: PasswordChangeRequest, request: Request):
    user = require_auth(request)
    # v15 Phase 5: enforce password strength
    from hashmm.api.security import validate_password_strength
    ok, reason = validate_password_strength(req.new_password)
    if not ok:
        raise HTTPException(400, reason)
    db.change_password(user["uid"], req.new_password)
    # Revoke every existing session (other devices / stolen tokens), then issue
    # a fresh pair so the current device stays logged in.
    _auth.bump_and_revoke(user["uid"])
    pair = _issue_pair(user["uid"], user["sub"], user.get("role", "user"))
    db.audit(user["uid"], user["sub"], "change_password", "")
    return {"ok": True, **pair}
