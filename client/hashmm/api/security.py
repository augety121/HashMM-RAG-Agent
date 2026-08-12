"""Security hardening — startup checks and password utilities (V15 Phase 5).

Catches the most common production security mistakes BEFORE they bite:
  - JWT secret left at the default value
  - admin password left at the default 'admin123'
  - running in production mode without these fixed

Behavior:
  - In default (dev) mode: print loud warnings but continue.
  - In production mode (HASHMM_ENV=production): refuse to start if insecure,
    unless HASHMM_ALLOW_INSECURE=1 is explicitly set.
"""
from __future__ import annotations

import os
import re
import secrets

from hashmm.utils import get_logger

logger = get_logger("hashmm.security")

_DEFAULT_SECRET = "hashmm-jwt-secret-change-me"
_DEFAULT_ADMIN_PW = "admin123"

_BANNER = "=" * 64


def is_production() -> bool:
    return os.environ.get("HASHMM_ENV", "dev").lower() in ("prod", "production")


def local_password_auth_enabled() -> bool:
    """Whether HashMM's local password database is an admitted login source."""
    if os.environ.get("HASHMM_ALLOW_LOCAL_LOGIN", "") == "1":
        return True
    try:
        from hashmm.api import supabase_auth
        return not supabase_auth.enabled()
    except Exception:
        # If the identity boundary cannot be determined, retain the stricter
        # local-password audit instead of silently skipping it.
        return True


def _admin_uses_default_password() -> bool:
    """Check whether the admin account still uses the default password."""
    try:
        from hashmm.api import database as db
        from hashmm.api.database import _hash_pw
        with db._conn() as c:
            row = c.execute(
                "SELECT password_hash, salt FROM users WHERE username='admin'"
            ).fetchone()
        if not row:
            return False
        d = dict(row)
        return d["password_hash"] == _hash_pw(_DEFAULT_ADMIN_PW, d["salt"])
    except Exception:
        return False


def audit_for_deploy() -> list[dict]:
    """Pre-deploy security audit (v17 Phase 26 / B4).

    Unlike run_startup_security_check (which only enforces CORS in production and
    logs warnings), this checks ALL hardening items unconditionally and returns a
    structured list — for a CI / pre-deploy *gate* that should block a release with
    insecure defaults regardless of HASHMM_ENV. Empty list = ready to ship.
    """
    issues: list[dict] = []
    secret = os.environ.get("HASHMM_JWT_SECRET", _DEFAULT_SECRET)
    if secret == _DEFAULT_SECRET or len(secret) < 16:
        issues.append({
            "id": "jwt_secret", "severity": "critical",
            "msg": "HASHMM_JWT_SECRET 未设置 / 为默认 / 过短(<16)——任何人都能伪造登录令牌。",
            "fix": f"export HASHMM_JWT_SECRET='{secrets.token_urlsafe(32)}'",
        })
    if local_password_auth_enabled() and _admin_uses_default_password():
        issues.append({
            "id": "admin_password", "severity": "critical",
            "msg": "admin 账号仍使用默认密码 admin123。",
            "fix": "登录后立即改密码，或调用 /api/auth/change-password。",
        })
    cors = os.environ.get("HASHMM_CORS_ORIGINS", "")
    if not cors or cors == "*":
        issues.append({
            "id": "cors", "severity": "high",
            "msg": "HASHMM_CORS_ORIGINS 未限制来源(空或 *)，存在跨站风险。",
            "fix": "export HASHMM_CORS_ORIGINS='https://你的前端域名'",
        })
    public_url = os.environ.get("HASHMM_PUBLIC_URL", "").strip().lower()
    if public_url and not public_url.startswith("https://"):
        issues.append({
            "id": "public_url_https", "severity": "critical",
            "msg": "HASHMM_PUBLIC_URL 是明文 HTTP，不能承载登录令牌或远程控制。",
            "fix": "通过受信任的 HTTPS 网关/Tunnel 暴露服务，并填写 https:// 域名。",
        })
    if os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE", "").lower() in ("1", "true", "yes", "on"):
        if not public_url.startswith("https://"):
            issues.append({
                "id": "remote_transport", "severity": "critical",
                "msg": "已要求安全远程控制，但 HASHMM_PUBLIC_URL 不是 HTTPS。",
                "fix": "为公网入口配置 HTTPS/WSS，并设置 HASHMM_PUBLIC_URL=https://你的域名。",
            })
    if os.environ.get("HASHMM_REMOTE_REQUIRE_TURN", "").lower() in ("1", "true", "yes", "on"):
        from hashmm.api.remote_transport import dynamic_turn_configured
        if not dynamic_turn_configured():
            issues.append({
                "id": "remote_turn", "severity": "critical",
                "msg": "已要求自有 TURN，但短期凭据或 TURN URL 配置不完整。",
                "fix": "设置 HASHMM_TURN_URLS 与至少 32 位 HASHMM_TURN_SHARED_SECRET，并让 coturn 使用同一 shared secret。",
            })
    return issues


def run_startup_security_check() -> list[str]:
    """Run all checks. Returns list of issue strings (empty = all good).

    In production mode, a non-empty list aborts startup (unless overridden).
    """
    issues = []

    # 同步健康（仅告警，绝不阻断启动）：service_role key 缺失/占位 → App↔电脑 的对话/记忆不跨端同步，
    # 且本地 sqlite 一旦损坏重建，历史无法从云端找回（这是"历史消息有时没了"的常见根因）。
    _svc = os.environ.get("HASHMM_SUPABASE_SERVICE_KEY", "")
    _svc_bad = (not _svc) or (_svc.count(".") < 2) or ("粘贴" in _svc) or ("换成" in _svc) or ("service_role_secret" in _svc)
    if os.environ.get("HASHMM_SUPABASE_URL", "") and _svc_bad:
        logger.warning(_BANNER)
        logger.warning("Supabase service_role key 未正确配置（HASHMM_SUPABASE_SERVICE_KEY 为空/占位符）。")
        logger.warning("    后果：App 与电脑端的对话/记忆不会跨端同步；本地库重建后历史无法从云端找回。")
        logger.warning("    修复：Supabase 控制台 Project Settings → API 复制 service_role secret，填进 start-hashmm.sh。")
        logger.warning(_BANNER)

    secret = os.environ.get("HASHMM_JWT_SECRET", _DEFAULT_SECRET)
    if secret == _DEFAULT_SECRET:
        issues.append(
            "JWT 密钥仍是默认值。请设置环境变量 HASHMM_JWT_SECRET 为一个随机长字符串"
            "（否则任何人都能伪造登录令牌）。"
            f" 可用：export HASHMM_JWT_SECRET='{secrets.token_urlsafe(32)}'"
        )

    if local_password_auth_enabled() and _admin_uses_default_password():
        issues.append(
            "admin 账号仍在使用默认密码 admin123。请登录后立即修改密码"
            "（管理后台或调用 /api/auth/change-password）。"
        )

    cors = os.environ.get("HASHMM_CORS_ORIGINS", "")
    if is_production() and (cors == "*" or not cors):
        issues.append(
            "生产模式下 CORS 未限制来源。请设置 HASHMM_CORS_ORIGINS 为你的前端域名。"
        )

    # V306 修 BACK-P0-01：生产模式必须强制鉴权。否则一旦端口被公网转发/反代错配/安全组放开，
    # 匿名请求即可访问共享能力（AutoDL"私有云"不等于天然安全）。
    if is_production() and os.environ.get("HASHMM_REQUIRE_AUTH", "").lower() not in ("1", "true", "yes", "on"):
        issues.append(
            "生产模式未强制鉴权（HASHMM_REQUIRE_AUTH 未开）。请设置 HASHMM_REQUIRE_AUTH=1，"
            "否则匿名调用者可能访问共享能力（尤其端口被公网暴露/反代错配时）。"
        )

    # V306 修 BACK-P0-02：数据加密密钥（HASHMM_SECRET，用于存储的 API Key 等）也必须非默认。
    # 此前只检查 JWT secret，遗漏了数据加密 secret——默认值等同明文。
    data_secret = os.environ.get("HASHMM_SECRET", "")
    if is_production() and (not data_secret or data_secret == "hashmm-default-secret-change-me-in-prod" or len(data_secret) < 16):
        issues.append(
            "生产模式下数据加密密钥无效（HASHMM_SECRET 为空/默认/过短）。请设置一个随机长字符串"
            f"（用于加密存储的凭据）。可用：export HASHMM_SECRET='{secrets.token_urlsafe(32)}'"
        )

    public_url = os.environ.get("HASHMM_PUBLIC_URL", "").strip().lower()
    if is_production() and public_url and not public_url.startswith("https://"):
        issues.append("生产环境 HASHMM_PUBLIC_URL 必须使用 HTTPS；明文 HTTP 禁止承载登录令牌。")
    if is_production() and public_url and os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE", "").lower() not in ("1", "true", "yes", "on"):
        issues.append("生产环境已配置公网地址，但 HASHMM_REQUIRE_SECURE_REMOTE 未启用。")
    if os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE", "").lower() in ("1", "true", "yes", "on"):
        if not public_url.startswith("https://"):
            issues.append("安全远程控制已启用，但 HASHMM_PUBLIC_URL 不是 HTTPS；远程凭证不能通过明文链路传输。")

    if os.environ.get("HASHMM_REMOTE_REQUIRE_TURN", "").lower() in ("1", "true", "yes", "on"):
        from hashmm.api.remote_transport import dynamic_turn_configured
        if not dynamic_turn_configured():
            issues.append(
                "远程生产门要求自有 TURN，但 HASHMM_TURN_URLS 或 HASHMM_TURN_SHARED_SECRET 未正确配置。"
            )

    if issues:
        logger.warning(_BANNER)
        logger.warning("安全检查发现 %d 个问题：", len(issues))
        for i, msg in enumerate(issues, 1):
            logger.warning("  %d. %s", i, msg)
        logger.warning(_BANNER)
        if is_production() and os.environ.get("HASHMM_ALLOW_INSECURE") != "1":
            raise RuntimeError(
                "生产模式拒绝以不安全配置启动。修复上述问题，"
                "或设置 HASHMM_ALLOW_INSECURE=1 强制启动（不推荐）。"
            )
    else:
        logger.info("[Security] 启动安全检查通过 ✓")

    return issues


# ── Password utilities ──

def validate_password_strength(pw: str) -> tuple[bool, str]:
    """Return (ok, reason). Enterprise-reasonable: >=8 chars, letters + digits."""
    if len(pw) < 8:
        return False, "密码至少 8 位"
    if not re.search(r"[A-Za-z]", pw):
        return False, "密码需包含字母"
    if not re.search(r"\d", pw):
        return False, "密码需包含数字"
    if pw == _DEFAULT_ADMIN_PW:
        return False, "不能使用默认密码"
    return True, ""
