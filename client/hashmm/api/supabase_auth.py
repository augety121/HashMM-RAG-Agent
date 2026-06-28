"""hashmm/api/supabase_auth.py — Supabase JWT 校验（客户端与 App 共用 Supabase 账号的统一身份基石）。

按 Supabase 官方做法验证用户 access token：
  · 新版项目（sb_publishable_ key 体系）：非对称 ES256/RS256 签名，用 JWKS 端点本地验签
    GET {url}/auth/v1/.well-known/jwks.json —— 只需项目 URL，不需 JWT secret / service_role。
  · 兜底：GET {url}/auth/v1/user 远程校验（任何 token 类型都能验，含旧版 HS256），需带 apikey。

验证通过后把 Supabase claims 映射成 HashMM 用户字典 {uid, sub, role, email, auth_provider}，
与现有自带 JWT **并存**（auth.py 先验自带 JWT，失败再验 Supabase）。

配置在 settings_store（客户端 UI 可配）：supabase_url / supabase_publishable_key /
supabase_admin_emails。未配 supabase_url → enabled()=False → 零变化。永不抛错。
零新后端依赖（PyJWT + cryptography 项目已装）。
"""
from __future__ import annotations

import json
import os
import urllib.request

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.supabase_auth")

# JWKS 缓存生命周期（秒）。Supabase 边缘缓存 10min，建议不超过 ~20min 以便密钥轮换/吊销生效。
_JWKS_LIFESPAN = 600
_ALGOS = ["ES256", "RS256"]
_AUD = "authenticated"

_jwks_client = None
_jwks_for_url = ""


# ── 配置（DB → 环境变量 → 默认）──
def _setting(key: str, env: str, default: str = "") -> str:
    try:
        from hashmm.api import settings_store
        v = settings_store.get_setting(key, "")
        if v:
            return v
    except Exception:
        pass
    try:
        return os.environ.get(env, "") or default
    except Exception:
        return default


def supabase_url() -> str:
    return _setting("supabase_url", "HASHMM_SUPABASE_URL").rstrip("/")


def publishable_key() -> str:
    return _setting("supabase_publishable_key", "HASHMM_SUPABASE_PUBLISHABLE_KEY")


def admin_emails() -> set:
    raw = _setting("supabase_admin_emails", "HASHMM_SUPABASE_ADMIN_EMAILS")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def enabled() -> bool:
    return bool(supabase_url())


# ── claims → HashMM 用户（纯逻辑，可测）──
def claims_to_user(claims: dict) -> dict | None:
    """把 Supabase JWT claims 映射成 HashMM 用户字典。永不抛错。
    uid 加 'sb_' 前缀，避免与 HashMM 自带账号的 uid 冲突；role 默认 user，邮箱在白名单则 admin。"""
    try:
        if not isinstance(claims, dict):
            return None
        sub = (claims.get("sub") or "").strip()
        if not sub:
            return None
        email = (claims.get("email") or "").strip().lower()
        app_meta = claims.get("app_metadata") or {}
        is_admin = (email and email in admin_emails()) or (app_meta.get("role") == "admin")
        role = "admin" if is_admin else "user"
        return {
            "uid": f"sb_{sub}",
            "sub": email or sub,
            "role": role,
            "email": email,
            "auth_provider": "supabase",
        }
    except Exception as e:
        log_suppressed(logger, e)
        return None


# ── JWKS 本地验签（非对称）──
def _get_jwks_client():
    """惰性构造 PyJWKClient；项目 URL 变化时重建。"""
    global _jwks_client, _jwks_for_url
    url = supabase_url()
    if not url:
        return None
    if _jwks_client is None or _jwks_for_url != url:
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", lifespan=_JWKS_LIFESPAN)
        _jwks_for_url = url
    return _jwks_client


def decode_with_key(token: str, key, *, verify_aud: bool = True) -> dict | None:
    """用给定公钥验签并解析 claims（ES256/RS256，校验 aud/exp）。纯逻辑，可用自造密钥单测。"""
    try:
        import jwt
        opts = {"verify_aud": verify_aud}
        kwargs = {"algorithms": _ALGOS, "options": opts}
        if verify_aud:
            kwargs["audience"] = _AUD
        return jwt.decode(token, key, **kwargs)
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _verify_jwks(token: str) -> dict | None:
    try:
        client = _get_jwks_client()
        if client is None:
            return None
        signing_key = client.get_signing_key_from_jwt(token)
        return decode_with_key(token, signing_key.key)
    except Exception as e:
        log_suppressed(logger, e)
        return None


# ── 远程兜底（任何 token 类型）──
def _verify_remote(token: str) -> dict | None:
    """GET {url}/auth/v1/user 远程校验。返回 claims 形（sub/email/role）或 None。"""
    url = supabase_url()
    if not url:
        return None
    try:
        req = urllib.request.Request(f"{url}/auth/v1/user", method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        pk = publishable_key()
        if pk:
            req.add_header("apikey", pk)
        with urllib.request.urlopen(req, timeout=8) as resp:
            u = json.loads(resp.read().decode("utf-8"))
        if isinstance(u, dict) and u.get("id"):
            return {"sub": u["id"], "email": u.get("email", ""), "role": u.get("role", "authenticated")}
    except Exception as e:
        log_suppressed(logger, e)
    return None


# ── 用 Supabase 账号密码登录 / 刷新会话（供客户端登录用 Supabase 账号）──
def password_login(email: str, password: str) -> dict | None:
    """用 Supabase 账号密码登录，换取 session（access_token/refresh_token/user）。失败返回 None。"""
    url = supabase_url()
    pk = publishable_key()
    if not url or not pk:
        return None
    try:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/auth/v1/token?grant_type=password", data=body, method="POST"
        )
        req.add_header("apikey", pk)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("access_token"):
            return data
    except Exception as e:
        log_suppressed(logger, e)
    return None


def refresh_session(refresh_token: str) -> dict | None:
    """用 Supabase refresh_token 换新 session。失败返回 None。"""
    url = supabase_url()
    pk = publishable_key()
    if not url or not pk or not refresh_token:
        return None
    try:
        body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/auth/v1/token?grant_type=refresh_token", data=body, method="POST"
        )
        req.add_header("apikey", pk)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("access_token"):
            return data
    except Exception as e:
        log_suppressed(logger, e)
    return None


def session_to_user(session: dict) -> dict:
    """从 Supabase session 提取 HashMM 用户结构（id 用 sb_<uuid>，admin 看 admin_emails 或 app_metadata.role）。"""
    sb_user = session.get("user") or {}
    sub = sb_user.get("id", "")
    email = (sb_user.get("email") or "").strip()
    app_meta = sb_user.get("app_metadata") or {}
    is_admin = (email and email.lower() in admin_emails()) or (app_meta.get("role") == "admin")
    role = "admin" if is_admin else "user"
    return {
        "id": f"sb_{sub}" if sub else "",
        "username": email or "supabase",
        "display_name": email.split("@")[0] if email else "用户",
        "role": role,
    }


# ── 对外：验证 token → HashMM 用户 ──
def verify_token(token: str) -> dict | None:
    """验证 Supabase access token，返回 HashMM 用户字典或 None。先 JWKS 本地验签，失败再远程兜底。"""
    if not token or not enabled():
        return None
    claims = _verify_jwks(token)
    if claims is None:
        claims = _verify_remote(token)
    if not claims:
        return None
    return claims_to_user(claims)
