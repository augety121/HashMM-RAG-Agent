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


def _secret() -> str:
    return os.environ.get("HASHMM_SECRET", _DEFAULT_SECRET)


def _fernet():
    """Return a Fernet instance, or None if cryptography is unavailable."""
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    # Derive a valid 32-byte urlsafe-base64 key from the operator's secret.
    digest = hashlib.sha256(_secret().encode("utf-8")).digest()
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


def is_legacy_ciphertext(cipher: str) -> bool:
    """True if a stored value is legacy XOR (i.e. should be migrated to Fernet)."""
    return bool(cipher) and not cipher.startswith(_FERNET_PREFIX)


def fernet_available() -> bool:
    return _fernet() is not None
