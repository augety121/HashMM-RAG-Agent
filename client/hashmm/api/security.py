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
    if _admin_uses_default_password():
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
    return issues


def run_startup_security_check() -> list[str]:
    """Run all checks. Returns list of issue strings (empty = all good).

    In production mode, a non-empty list aborts startup (unless overridden).
    """
    issues = []

    secret = os.environ.get("HASHMM_JWT_SECRET", _DEFAULT_SECRET)
    if secret == _DEFAULT_SECRET:
        issues.append(
            "JWT 密钥仍是默认值。请设置环境变量 HASHMM_JWT_SECRET 为一个随机长字符串"
            "（否则任何人都能伪造登录令牌）。"
            f" 可用：export HASHMM_JWT_SECRET='{secrets.token_urlsafe(32)}'"
        )

    if _admin_uses_default_password():
        issues.append(
            "admin 账号仍在使用默认密码 admin123。请登录后立即修改密码"
            "（管理后台或调用 /api/auth/change-password）。"
        )

    cors = os.environ.get("HASHMM_CORS_ORIGINS", "")
    if is_production() and (cors == "*" or not cors):
        issues.append(
            "生产模式下 CORS 未限制来源。请设置 HASHMM_CORS_ORIGINS 为你的前端域名。"
        )

    if issues:
        logger.warning(_BANNER)
        logger.warning("⚠️  安全检查发现 %d 个问题：", len(issues))
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
