"""Secret encryption for stored credentials (API keys) — Fernet, with safe
migration from the legacy XOR scheme.

Why this module exists
----------------------
API keys were stored XOR-"encrypted" in the settings DB. XOR with a known/default
key is effectively plaintext — an enterprise-deployment red line. This module
replaces it with Fernet (AES-128-CBC + HMAC authentication) while keeping three
hard guarantees:

  1. **No lost keys on upgrade.** ``decrypt_secret`` transparently reads BOTH new
     Fernet ciphertext AND legacy XOR ciphertext, so existing rows keep working
     immediately after deploy — before any migration runs.
  2. **Graceful degradation.** If the ``cryptography`` package is unavailable,
     we fall back to the legacy XOR scheme and log a loud warning, rather than
     crashing the server. (On a properly provisioned host, Fernet is used.)
  3. **Key from environment.** The Fernet key is derived from ``HASHMM_SECRET``
     (same env var as before) via SHA-256 → urlsafe-base64, so operators set one
     secret and get a valid 32-byte Fernet key. A weak/default secret is flagged
     by the existing security_check.

Ciphertext format
-----------------
New values are prefixed ``f1:`` so the decryptor can tell Fernet from legacy XOR
(which has no prefix). This makes detection unambiguous and migration idempotent.
"""
from __future__ import annotations

import base64
import hashlib
import os

from hashmm.utils import get_logger

logger = get_logger("hashmm.secrets")

_FERNET_PREFIX = "f1:"
_DEFAULT_SECRET = "hashmm-default-secret-change-me-in-prod"


def _is_production() -> bool:
    """生产判定：HASHMM_REQUIRE_AUTH 开 或 HASHMM_ENV=production。生产下密钥缺陷从严。"""
    if os.environ.get("HASHMM_REQUIRE_AUTH", "").lower() in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("HASHMM_ENV", "").lower() in ("prod", "production")


def _secret() -> str:
    s = os.environ.get("HASHMM_SECRET", _DEFAULT_SECRET)
    # V306 修 REM-11：生产环境拒绝使用默认弱密钥（等同明文），直接失败而非静默降级。
    if _is_production() and (not s or s == _DEFAULT_SECRET):
        raise RuntimeError(
            "生产环境未设置 HASHMM_SECRET（或仍为默认值）。密钥加密不能用默认弱密钥——"
            "请设置一个高强度随机 HASHMM_SECRET 后再启动。"
        )
    return s


def _fernet():
    """Return a Fernet instance, or None if cryptography is unavailable."""
    return _fernet_for_secret(_secret())


def _fernet_for_secret(secret: str):
    """Build a Fernet instance for an explicit operator secret."""
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    # Derive a valid 32-byte urlsafe-base64 key from the operator's secret.
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# ── Legacy XOR (kept ONLY for decrypting pre-existing rows) ──────────────────

def _xor_encrypt(plain: str) -> str:
    if not plain:
        return ""
    k = _secret()
    xored = bytes(a ^ b for a, b in zip(plain.encode(), (k * ((len(plain) // len(k)) + 1)).encode()))
    return base64.b64encode(xored).decode()


def _xor_decrypt(cipher: str) -> str:
    if not cipher:
        return ""
    k = _secret()
    try:
        data = base64.b64decode(cipher)
        return "".join(chr(a ^ b) for a, b in zip(data, (k * ((len(data) // len(k)) + 1)).encode()))
    except Exception:
        return ""


# ── Public API ───────────────────────────────────────────────────────────────

def encrypt_secret(plain: str) -> str:
    """Encrypt a secret. Uses Fernet when available (prefixed ``f1:``); falls
    back to legacy XOR with a warning if cryptography is missing."""
    if not plain:
        return ""
    f = _fernet()
    if f is None:
        # V306 修 REM-11：生产环境缺 cryptography → 不允许 XOR 降级（XOR≈明文），直接失败。
        if _is_production():
            raise RuntimeError(
                "生产环境缺少 cryptography 库，禁止降级为 XOR（等同明文）。"
                "请安装 cryptography 以启用 Fernet(AEAD) 加密后再启动。"
            )
        logger.warning(
            "cryptography unavailable — storing secret with legacy XOR (NOT secure). "
            "Install 'cryptography' to enable Fernet encryption."
        )
        return _xor_encrypt(plain)
    token = f.encrypt(plain.encode("utf-8")).decode("ascii")
    return _FERNET_PREFIX + token


def decrypt_secret(cipher: str) -> str:
    """Decrypt a secret, transparently handling BOTH Fernet (``f1:``) and legacy
    XOR ciphertext. Returns "" on any failure (never raises)."""
    if not cipher:
        return ""
    if cipher.startswith(_FERNET_PREFIX):
        f = _fernet()
        if f is None:
            logger.error("Fernet ciphertext found but cryptography is unavailable; cannot decrypt.")
            return ""
        try:
            return f.decrypt(cipher[len(_FERNET_PREFIX):].encode("ascii")).decode("utf-8")
        except Exception as e:
            logger.error(f"Fernet decryption failed: {e}")
            return ""
    # No prefix → legacy XOR ciphertext (pre-upgrade row).
    return _xor_decrypt(cipher)


def rewrap_legacy_fernet_ciphertext(cipher: str) -> str | None:
    """Re-encrypt authenticated Fernet data after an operator-key rotation.

    A new ciphertext is returned only when the current key cannot decrypt the
    value and one supported previous key authenticates it. Early launchers did
    not persist ``HASHMM_SECRET``, so the historical default is a deliberate
    one-time candidate. ``HASHMM_SECRET_PREVIOUS`` supports explicit rotations.
    No plaintext or key material is logged.
    """
    if not cipher or not cipher.startswith(_FERNET_PREFIX):
        return None
    encoded = cipher[len(_FERNET_PREFIX):].encode("ascii")
    current_secret = _secret()
    current = _fernet_for_secret(current_secret)
    if current is None:
        return None
    try:
        current.decrypt(encoded)
        return None
    except Exception:
        pass

    candidates = []
    configured_previous = os.environ.get("HASHMM_SECRET_PREVIOUS", "").strip()
    if configured_previous:
        candidates.append(configured_previous)
    if _DEFAULT_SECRET != current_secret:
        candidates.append(_DEFAULT_SECRET)

    for previous_secret in candidates:
        if not previous_secret or previous_secret == current_secret:
            continue
        previous = _fernet_for_secret(previous_secret)
        if previous is None:
            continue
        try:
            plain = previous.decrypt(encoded).decode("utf-8")
        except Exception:
            continue
        return _FERNET_PREFIX + current.encrypt(plain.encode("utf-8")).decode("ascii")
    return None


def is_legacy_ciphertext(cipher: str) -> bool:
    """True if a stored value is legacy XOR (i.e. should be migrated to Fernet)."""
    return bool(cipher) and not cipher.startswith(_FERNET_PREFIX)


def fernet_available() -> bool:
    return _fernet() is not None
