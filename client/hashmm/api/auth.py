"""HashMM-RAG — Token-based authentication.

Uses HMAC-SHA256 for lightweight JWT-like tokens. No external dependencies.

v17 Phase 65 — big-company token revocation:
  - Two token types: short-lived ACCESS + long-lived REFRESH (payload "token_type").
  - Server-side revocation lever: users.token_version. Every token embeds the
    version at issue time ("tv"); verify_token rejects tokens whose tv != the
    user's current version. Bumping the version (logout / password change /
    admin force-logout) instantly invalidates ALL of that user's tokens.
  - A tiny TTL cache avoids a DB read on every request.
Backward compatible: legacy tokens (no token_type, no tv) are treated as access
tokens with tv=0, so sessions issued before this deploy keep working.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, time
from functools import wraps
from fastapi import Request, HTTPException

SECRET = os.environ.get("HASHMM_JWT_SECRET", "hashmm-jwt-secret-change-me")
# Access tokens are short-lived; the refresh token keeps the session alive.
# HASHMM_TOKEN_TTL is still honored (as the access TTL) for backward compat.
# Access token TTL. 桌面客户端目前没有前端 refresh 逻辑，1h 的短 access token 到期后
# /api/auth/me 会 401，客户端(shellserver)据此判定「已登出」→ 逼你重新登录。
# 对「自己跑后端的个人工具」来说，把 access token 直接设成 30 天（与 refresh 同寿命），
# 就不会在正常使用中掉登录。token_version 撤销机制仍然有效（改密码/强制登出照样立即失效）。
# 想要更短寿命可用环境变量 HASHMM_ACCESS_TTL 覆盖。
ACCESS_TTL = int(os.environ.get("HASHMM_ACCESS_TTL",
                                os.environ.get("HASHMM_TOKEN_TTL", 86400 * 30)))   # 默认 30d（原为 1h）
REFRESH_TTL = int(os.environ.get("HASHMM_REFRESH_TTL", 86400 * 30))            # 30d
TOKEN_TTL = ACCESS_TTL  # kept as an alias; some callers import this name

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64d(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


# ── token_version cache (revocation lever) ──────────────────────────────
_tv_cache: dict[str, tuple[int, float]] = {}
_TV_CACHE_TTL = 10.0  # seconds — revocation takes effect within this window

def _current_token_version(uid: str) -> int | None:
    now = time.time()
    hit = _tv_cache.get(uid)
    if hit and now - hit[1] < _TV_CACHE_TTL:
        return hit[0]
    from hashmm.api import database as db  # lazy import avoids module cycle
    v = db.get_user_token_version(uid)
    if v is not None:
        _tv_cache[uid] = (v, now)
    else:
        _tv_cache.pop(uid, None)
    return v

def invalidate_token_cache(uid: str) -> None:
    _tv_cache.pop(uid, None)

def bump_and_revoke(uid: str) -> int:
    """Revoke ALL of a user's tokens (logout-all / password change / admin
    force-logout) by bumping their token_version, then clear the cache so the
    revocation is effective immediately on this process."""
    from hashmm.api import database as db
    v = db.bump_token_version(uid)
    invalidate_token_cache(uid)
    return v


# ── token mint / verify ─────────────────────────────────────────────────
def _make_token(user_id: str, username: str, role: str, token_version: int,
                token_type: str, ttl: int) -> str:
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64e(json.dumps({
        "uid": user_id, "sub": username, "role": role,
        "tv": token_version, "token_type": token_type,
        "exp": int(time.time()) + ttl, "iat": int(time.time()),
    }).encode())
    sig = _b64e(hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

def create_token(user_id: str, username: str, role: str, token_version: int | None = None) -> str:
    """Mint a short-lived ACCESS token (default 1h)."""
    if token_version is None:
        token_version = _current_token_version(user_id) or 0
    return _make_token(user_id, username, role, token_version, "access", ACCESS_TTL)

def create_refresh_token(user_id: str, username: str, role: str, token_version: int | None = None) -> str:
    """Mint a long-lived REFRESH token (default 30d). Used only at /auth/refresh."""
    if token_version is None:
        token_version = _current_token_version(user_id) or 0
    return _make_token(user_id, username, role, token_version, "refresh", REFRESH_TTL)

def verify_token(token: str, expected_type: str = "access") -> dict | None:
    """Verify signature + expiry + token type + revocation (token_version).

    expected_type: 'access' for normal API auth, 'refresh' for /auth/refresh.
    Pass expected_type='' to skip the type check.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        expected = _b64e(hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(payload))
        if data.get("exp", 0) < time.time():
            return None
        # Token type — legacy tokens (no field) are treated as access.
        tok_type = data.get("token_type", "access")
        if expected_type and tok_type != expected_type:
            return None
        # Revocation — token_version must match the user's current version.
        uid = data.get("uid")
        if uid:
            cur = _current_token_version(uid)
            if cur is None:                       # user deleted → reject
                return None
            if int(data.get("tv", 0)) != int(cur):  # revoked / superseded
                return None
        return data
    except Exception:
        return None

def verify_any_token(token: str) -> dict | None:
    """验证一个 access token（字符串）：先 HashMM 自带 JWT，失败且 Supabase 已配置时再验 Supabase。
    供 HTTP（get_current_user）与 WebSocket（远程信令）共用，保证统一身份在两处一致。永不抛错。"""
    if not token:
        return None
    user = verify_token(token, expected_type="access")
    if user:
        return user
    try:
        from hashmm.api import supabase_auth
        if supabase_auth.enabled():
            su = supabase_auth.verify_token(token)
            if su:
                return su
    except Exception:
        pass
    return None


_owner_autobind_done = False
_owner_autobind_last = 0.0


def _maybe_autobind_channel_owner(user: dict | None) -> None:
    """渠道文件投送需要 channel_owner_uid（桌面用同账号轮询），但此前没有任何 UI/接口能设它，
    导致"让微信发我电脑桌面的文件"永远走不到投送管线、退回成普通问答。这里在 owner 用
    Supabase 管理员账号登录 App/桌面端、令牌打到后端时，自动把它绑成本人 uid（仅在未设置时）。
    一旦绑定即停止检查；非管理员 / 非 Supabase 身份直接跳过（零开销）。"""
    global _owner_autobind_done, _owner_autobind_last
    if _owner_autobind_done:
        return
    if not user or user.get("role") != "admin":
        return
    uid = user.get("uid") or ""
    if not uid.startswith("sb_"):      # 桌面端是按 Supabase uid 轮询的，只认 Supabase 身份
        return
    import time as _t
    now = _t.time()
    if now - _owner_autobind_last < 5:  # 限流，避免登录前每个请求都读一次设置
        return
    _owner_autobind_last = now
    try:
        from hashmm.api import settings_store
        cur = (settings_store.get_setting("channel_owner_uid", "") or "").strip()
        if not cur:
            settings_store.set_setting("channel_owner_uid", uid)
        _owner_autobind_done = True
    except Exception:
        pass


def get_current_user(request: Request) -> dict | None:
    """Extract user from Authorization header or query param (access tokens only).

    并存策略：先验 HashMM 自带 JWT；失败且 Supabase 已配置时，再验 Supabase access token
    （统一身份——客户端与 App 共用 Supabase 账号）。未配 Supabase 则行为零变化。
    """
    if getattr(request.state, "auth_checked", False):
        user = getattr(request.state, "auth_user", None)
        if user:
            _maybe_autobind_channel_owner(user)
        return user
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:]
    elif request.query_params.get("token"):
        token = request.query_params.get("token") or ""
    user = verify_any_token(token)
    if user:
        _maybe_autobind_channel_owner(user)
    return user

def require_auth(request: Request) -> dict:
    """Raise 401 if not authenticated."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或令牌已过期")
    return user


def principal_id(user: dict) -> str:
    """Return the canonical, provider-independent owner id.

    HashMM access tokens expose ``uid`` while Supabase-backed principals may
    also expose ``sub``.  Route code must not depend on a provider-specific
    ``id`` key: doing so made valid Supabase sessions fail with ``KeyError``.
    The returned value is intentionally the same owner key used by
    conversations, remote devices and durable runs.
    """
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="未登录或令牌已过期")
    value = user.get("uid") or user.get("sub") or user.get("id")
    if value is None or not str(value).strip():
        raise HTTPException(status_code=401, detail="登录身份缺少稳定用户标识")
    return str(value).strip()


def require_principal_id(request: Request) -> str:
    """Authenticate ``request`` and return its canonical owner id."""
    return principal_id(require_auth(request))

def require_admin(request: Request) -> dict:
    """Raise 403 if not admin."""
    user = require_auth(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def extract_ws_token(ws) -> str:
    """V306: WebSocket 握手取令牌——优先 Authorization: Bearer 头（不进 URL/日志），
    回退 Sec-WebSocket-Protocol，再回退 query ?token=（向后兼容旧客户端）。"""
    try:
        auth = ws.headers.get("authorization") or ws.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:]
        # 有的客户端把令牌放进子协议头：Sec-WebSocket-Protocol: bearer,<token>
        proto = ws.headers.get("sec-websocket-protocol") or ""
        if proto:
            parts = [p.strip() for p in proto.split(",")]
            if len(parts) == 2 and parts[0].lower() in ("bearer", "authorization"):
                return parts[1]
    except Exception:
        pass
    try:
        return ws.query_params.get("token", "") or ""
    except Exception:
        return ""


# ── v17 Phase 63: object-level authorization (fix IDOR on /conversations) ──
#
# Before this, every /conversations/{conv_id} endpoint read/wrote/deleted/
# executed purely by id with NO ownership check — so any caller who knew (or
# guessed) a conv_id could read another user's chat, files, exports, or even
# run code in their workspace. The list endpoint was already user-scoped, but
# direct-by-id access was wide open. That is OWASP API #1 (Broken Object Level
# Authorization). These helpers close it for every by-id endpoint.

def resolve_user_id(request: Request) -> str:
    """The effective owner id for the caller: their uid, else 'anonymous'.

    Mirrors how conversations are created (logged-out users land in the shared
    'anonymous' bucket), so ownership comparisons are consistent end to end.
    """
    user = get_current_user(request)
    return user["uid"] if user else "anonymous"


def _require_auth_enabled() -> bool:
    """Opt-in hard gate. When HASHMM_REQUIRE_AUTH is set, anonymous callers are
    rejected outright (no shared 'anonymous' bucket). Default OFF so existing
    single-user / local deployments keep working unchanged."""
    return os.environ.get("HASHMM_REQUIRE_AUTH", "").lower() in ("1", "true", "yes", "on")


def require_conv_access(request: Request, conv_id: str) -> dict:
    """Authorize the caller for a specific conversation and return its row.

    Rules:
      - HASHMM_REQUIRE_AUTH on + not logged in        → 401
      - conversation missing                          → 404
      - caller is neither the owner nor an admin       → 404 (NOT 403)

    The "not yours" case deliberately returns the same 404 as "missing" so an
    attacker cannot enumerate which conv_ids exist by probing. Admins bypass the
    ownership check (preserves the Phase 61 admin global view).
    """
    user = get_current_user(request)
    if user is None and _require_auth_enabled():
        raise HTTPException(status_code=401, detail="未登录或令牌已过期")
    uid = user["uid"] if user else "anonymous"

    from hashmm.api import database as db  # lazy import avoids module cycle
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    is_admin = bool(user and user.get("role") == "admin")
    if conv.get("user_id") != uid and not is_admin:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


def require_conv_access_or_create(request: Request, conv_id: str, default_title: str = "") -> dict | None:
    """For write-then-create endpoints (e.g. POST /conversations/{id}/stream).

    Unlike require_conv_access (which 404s on a missing conversation), stream
    also has to CREATE a brand-new conversation on first message. So:
      - conversation exists  → caller must be the owner or an admin, else 404
      - conversation missing → create it owned by the caller
    Returns the existing conversation row, or None when a new one was created.
    """
    user = get_current_user(request)
    if user is None and _require_auth_enabled():
        raise HTTPException(status_code=401, detail="未登录或令牌已过期")
    uid = user["uid"] if user else "anonymous"

    from hashmm.api import database as db
    conv = db.get_conversation(conv_id)
    if conv is None:
        db.create_conversation(conv_id, uid, (default_title or "新对话")[:30])
        return None

    is_admin = bool(user and user.get("role") == "admin")
    if conv.get("user_id") != uid and not is_admin:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv
