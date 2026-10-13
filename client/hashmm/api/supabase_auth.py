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
import hashlib
import os
import threading
import time
import urllib.request
import urllib.error

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.supabase_auth")


def update_own_profile(access_token: str, fields: dict) -> bool:
    """Update only the authenticated Supabase user's public profile fields.

    The request uses the caller's access token, so Supabase RLS remains the
    authority. No service-role credential is introduced into this path.
    """
    if not access_token or not enabled() or not isinstance(fields, dict):
        return False
    allowed = {key: value for key, value in fields.items() if key in {"display_name"}}
    if not allowed:
        return False
    claims = _verify_jwks(access_token) or _verify_remote(access_token)
    subject = str((claims or {}).get("sub") or "").strip()
    if not subject:
        return False
    try:
        payload = json.dumps(allowed, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{supabase_url()}/rest/v1/profiles?id=eq.{subject}", data=payload, method="PATCH",
        )
        req.add_header("apikey", publishable_key())
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Prefer", "return=minimal")
        with urllib.request.urlopen(req, timeout=10) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        log_suppressed(logger, exc)
        return False

# JWKS 缓存生命周期（秒）。Supabase 边缘缓存 10min，建议不超过 ~20min 以便密钥轮换/吊销生效。
_JWKS_LIFESPAN = 600
_ALGOS = ["ES256", "RS256"]
_AUD = "authenticated"

_jwks_client = None
# Verified-session continuity cache. Legacy HS256 projects cannot be verified
# with public JWKS and therefore need /auth/v1/user once. After that successful
# verification the immutable token is safe to cache until its own ``exp``
# (capped at one hour). A transient Supabase outage must not turn a previously
# verified desktop session into a fabricated 401 every 60 seconds.
_verify_cache: dict = {}
_verify_locks: dict[str, threading.Lock] = {}
_verify_locks_guard = threading.Lock()
_VERIFY_CACHE_MAX_TTL = 3600.0
_VERIFY_CACHE_NO_EXP_TTL = 900.0
_jwks_for_url = ""


def _verification_lock(token: str) -> threading.Lock:
    """Return a per-token single-flight lock without retaining raw credentials."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _verify_locks_guard:
        lock = _verify_locks.get(digest)
        if lock is None:
            lock = threading.Lock()
            _verify_locks[digest] = lock
        if len(_verify_locks) > 1024:
            # Locks with no waiters are safe to evict from the lookup table;
            # existing holders still retain their object reference.
            for key in list(_verify_locks)[:512]:
                if key != digest and not _verify_locks[key].locked():
                    _verify_locks.pop(key, None)
        return lock


def _unverified_exp(token: str) -> float | None:
    """Read ``exp`` only for cache expiry after the token was verified.

    This value is never used to establish identity or authorization.
    """
    try:
        import base64
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part.encode("ascii")))
        exp = float(payload.get("exp") or 0)
        return exp if exp > 0 else None
    except Exception:
        return None


# ── 配置（DB → 环境变量 → 默认）──
def _setting(key: str, env: str, default: str = "") -> str:
    # Production has exactly one identity authority: the process environment.
    # A stale settings_store row must never silently point one client at a
    # different Supabase project from the JWT verifier used by the backend.
    production_identity = (
        os.environ.get("HASHMM_ENV", "").strip().lower() in {"prod", "production"}
        or os.environ.get("HASHMM_IDENTITY_PROVIDER_REQUIRED", "").strip().lower() == "supabase"
    )
    if production_identity:
        try:
            return os.environ.get(env, "").strip() or default
        except Exception:
            return default
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


def list_all_profiles(access_token: str) -> list[dict] | None:
    """Use the caller's Supabase session to read the admin profile directory.

    The ``list_all_profiles`` RPC performs its own administrator check.  This
    path intentionally uses the user's access token (never a service-role key),
    so mobile and desktop see the same identity directory even when the HashMM
    backend is not configured with ``HASHMM_SUPABASE_SERVICE_KEY``.

    ``None`` means the RPC was unavailable/unauthorised; an empty list is a
    successful response containing no profiles.  Keeping those states distinct
    prevents clients from presenting a failed request as "0 users".
    """
    url = supabase_url()
    key = publishable_key()
    if not url or not key or not access_token:
        return None
    try:
        req = urllib.request.Request(
            f"{url}/rest/v1/rpc/list_all_profiles",
            data=b"{}",
            method="POST",
        )
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        return rows if isinstance(rows, list) else None
    except Exception as e:
        log_suppressed(logger, e)
        return None


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
        # 管理员身份只接受服务端控制来源。Supabase 普通用户通常可以修改自己的
        # user_metadata，因此绝不能把 user_metadata.role 当成授权依据。
        is_admin = ((email and email in admin_emails())
                    or (app_meta.get("role") == "admin"))
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
        jwks_url = f"{url}/auth/v1/.well-known/jwks.json"
        # ★ 兼容旧版 PyJWT：lifespan 参数是较新版本才有的，旧版会 TypeError。
        # 旧环境下若不兜底，整个 JWKS 本地验签会静默失败 → 每个请求退回网络校验 →
        # 桌面端高频轮询打爆 Supabase 限流 → 401 → 掉线。这里退回无参构造保证可用。
        try:
            _jwks_client = PyJWKClient(jwks_url, lifespan=_JWKS_LIFESPAN)
        except TypeError:
            _jwks_client = PyJWKClient(jwks_url)
        _jwks_for_url = url
    return _jwks_client


def decode_with_key(token: str, key, *, verify_aud: bool = True,
                    issuer: str | None = None) -> dict | None:
    """Verify signature, expiry, audience and optionally the project issuer."""
    try:
        import jwt
        opts = {"verify_aud": verify_aud}
        kwargs = {"algorithms": _ALGOS, "options": opts}
        if verify_aud:
            kwargs["audience"] = _AUD
        if issuer:
            kwargs["issuer"] = issuer.rstrip("/")
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
        return decode_with_key(token, signing_key.key, issuer=f"{supabase_url()}/auth/v1")
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
            # V249 修复：原实现把 app_metadata/user_metadata 丢掉了——App 的 Bearer token 一旦
            # 走到这条远程兜底路（如旧版 HS256 项目验不过 JWKS），管理员身份就被抹平成普通用户，
            # 造成"桌面端是管理员、App 不是"。这里原样透传，交给 claims_to_user 统一判。
            return {"sub": u["id"], "email": u.get("email", ""),
                    "role": u.get("role", "authenticated"),
                    "app_metadata": u.get("app_metadata") or {},
                    "user_metadata": u.get("user_metadata") or {}}
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
    is_admin = ((email and email.lower() in admin_emails())
                or (app_meta.get("role") == "admin"))
    role = "admin" if is_admin else "user"
    return {
        "id": f"sb_{sub}" if sub else "",
        "username": email or "supabase",
        "display_name": email.split("@")[0] if email else "用户",
        "role": role,
    }


# ── 对外：验证 token → HashMM 用户 ──
def verify_token(token: str) -> dict | None:
    """验证 Supabase access token，返回 HashMM 用户字典或 None。

    JWKS 验签成功后以 JWT 中由服务端签发的 ``app_metadata`` 为准，不再为每个
    普通用户请求同步调用 ``/auth/v1/user``。管理员角色变更通过刷新会话生效；这避免
    Supabase 抖动把所有已通过本地验签的请求拖进 8 秒网络等待。

    ★ 带 60s 结果缓存：避免打开管理面板等"一瞬间多请求"场景把每个请求都变成一次对
    Supabase /auth/v1/user 的网络校验，并发打爆限流→部分 401→被误登出。
    """
    if not token or not enabled():
        return None
    now = time.time()
    hit = _verify_cache.get(token)
    if hit is not None and now < float(hit[2]):
        return hit[0]
    # Multiple screens start together after login. Without per-token
    # single-flight they all miss the empty cache and synchronously call
    # Supabase /auth/v1/user, causing 5-25 second request piles and sporadic
    # 401s. Different accounts keep independent locks and still verify in
    # parallel.
    with _verification_lock(token):
        now = time.time()
        hit = _verify_cache.get(token)
        if hit is not None and now < float(hit[2]):
            return hit[0]
        claims = _verify_jwks(token)
        user = claims_to_user(claims) if claims else None
        if user is None:
            remote_claims = _verify_remote(token)
            user = claims_to_user(remote_claims) if remote_claims else None
        if user:
            claimed_exp = _unverified_exp(token)
            valid_until = min(claimed_exp or (now + _VERIFY_CACHE_NO_EXP_TTL),
                              now + _VERIFY_CACHE_MAX_TTL)
            if valid_until > now:
                _verify_cache[token] = (user, now, valid_until)
            if len(_verify_cache) > 512:        # 简单容量控制，删最旧的一半
                for k in sorted(_verify_cache, key=lambda k: _verify_cache[k][1])[:256]:
                    _verify_cache.pop(k, None)
        return user
