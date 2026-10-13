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
import hashlib
import os
import secrets
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from hashmm.api import database as db
from hashmm.api import auth as _auth
from hashmm.api.auth import (
    create_token, create_refresh_token, verify_token, require_auth,
)
from hashmm.api.schemas import LoginRequest, RegisterRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


class WebViewCodeRequest(BaseModel):
    refresh_token: str = ""


class WebViewConsumeRequest(BaseModel):
    code: str


def _no_store(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload, status_code=status_code,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


def _issue_pair(user_id: str, username: str, role: str) -> dict:
    """Mint an access + refresh token pair for a freshly-authenticated user."""
    return {
        "token": create_token(user_id, username, role),
        "refresh_token": create_refresh_token(user_id, username, role),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    from hashmm.api import supabase_auth
    from hashmm.api.security import local_password_auth_enabled
    # Supabase 启用后，默认只允许 Supabase 账号登录（不允许本地 admin/密码账号）。
    # 应急：设环境变量 HASHMM_ALLOW_LOCAL_LOGIN=1 可临时恢复本地账号登录。
    allow_local = local_password_auth_enabled()

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
            session = await run_in_threadpool(
                supabase_auth.password_login, req.username, req.password,
            )
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
    from hashmm.api.security import local_password_auth_enabled
    if not local_password_auth_enabled():
        raise HTTPException(403, "统一账号模式已启用，请使用 Supabase 注册账号")
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
            session = await run_in_threadpool(supabase_auth.refresh_session, rt)
            if session and session.get("access_token"):
                return {
                    "token": session["access_token"],
                    "refresh_token": session.get("refresh_token", ""),
                    "user": supabase_auth.session_to_user(session),
                }
    except Exception:
        pass
    raise HTTPException(401, "刷新令牌无效或已过期，请重新登录")


@router.post("/webview/code")
async def issue_webview_code(req: WebViewCodeRequest, request: Request):
    """Mint a 60-second, single-use App -> WebView bootstrap capability."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    access_token = auth_header[7:].strip()
    user = _auth.verify_any_token(access_token)
    if not user or not user.get("uid"):
        raise HTTPException(401, "Authentication required")
    refresh_token = str(req.refresh_token or "")
    if len(access_token) > 16_384 or len(refresh_token) > 16_384:
        raise HTTPException(400, "Invalid session credential")

    from hashmm.secrets_crypto import encrypt_secret
    code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    now = time.time()
    with db._conn() as conn:
        conn.execute(
            "DELETE FROM webview_auth_codes WHERE expires_at<? OR consumed_at>0",
            (now - 300,),
        )
        conn.execute(
            "INSERT INTO webview_auth_codes "
            "(code_hash,owner_id,access_cipher,refresh_cipher,expires_at,consumed_at,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                code_hash, str(user["uid"]), encrypt_secret(access_token),
                encrypt_secret(refresh_token), now + 60, 0, now,
            ),
        )
    return _no_store({"code": code, "expires_in": 60})


@router.post("/webview/consume")
async def consume_webview_code(req: WebViewConsumeRequest):
    """Atomically consume an opaque capability and return its account session."""
    raw_code = str(req.code or "").strip()
    if not raw_code or len(raw_code) > 256:
        raise HTTPException(401, "Invalid or expired bootstrap code")
    digest = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT owner_id,access_cipher,refresh_cipher FROM webview_auth_codes "
            "WHERE code_hash=? AND consumed_at=0 AND expires_at>=?",
            (digest, now),
        ).fetchone()
        if row is None:
            raise HTTPException(401, "Invalid or expired bootstrap code")
        claimed = conn.execute(
            "UPDATE webview_auth_codes SET consumed_at=? "
            "WHERE code_hash=? AND consumed_at=0 AND expires_at>=?",
            (now, digest, now),
        )
        if claimed.rowcount != 1:
            raise HTTPException(401, "Invalid or expired bootstrap code")

    from hashmm.secrets_crypto import decrypt_secret
    access_token = decrypt_secret(str(row["access_cipher"] or ""))
    refresh_token = decrypt_secret(str(row["refresh_cipher"] or ""))
    user = _auth.verify_any_token(access_token)
    if not user or str(user.get("uid") or "") != str(row["owner_id"] or ""):
        raise HTTPException(401, "Invalid or expired bootstrap code")
    return _no_store({
        "token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "id": str(user.get("uid") or ""),
            "username": str(user.get("sub") or ""),
            "display_name": str(user.get("sub") or ""),
            "role": str(user.get("role") or "user"),
        },
    })


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


@router.get("/identity-contract")
async def identity_contract():
    """Return the public, non-secret identity contract used by this backend.

    Clients use it before committing a cloud session.  This makes a Supabase
    project mismatch diagnosable without exposing keys, tokens or user data.
    """
    from hashmm import RELEASE, __version__
    from hashmm.api import supabase_auth

    provider_url = supabase_auth.supabase_url().rstrip("/")
    host = (urlparse(provider_url).hostname or "").lower()
    project_ref = host.split(".", 1)[0] if host.endswith(".supabase.co") else ""
    provider_ready = bool(provider_url and supabase_auth.publishable_key())
    public_url = os.environ.get("HASHMM_PUBLIC_URL", "").strip().rstrip("/")
    revision_material = "|".join(
        ["supabase", provider_url, project_ref, "authenticated", public_url, RELEASE]
    )
    return _no_store({
        "schema": "hashmm.identity-contract.v1",
        "provider": "supabase",
        "provider_ready": provider_ready,
        "project_ref": project_ref,
        "issuer": f"{provider_url}/auth/v1" if provider_url else "",
        "audience": "authenticated",
        "backend_url": public_url,
        "config_revision": hashlib.sha256(revision_material.encode("utf-8")).hexdigest()[:16],
        "release": RELEASE,
        "backend_version": __version__,
    })


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
