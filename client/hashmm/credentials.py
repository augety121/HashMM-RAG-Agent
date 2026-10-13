"""per-connector 凭据仓（V205 P2-8，对齐 Qoder「MCP 凭据 + 机器身份」）。

集中管理连接器（MCP server / 云 API / Webhook…）的密钥：
  - 写入即加密（secrets_crypto：Fernet 优先，缺库降级 XOR 并告警）；
  - 列表只回掩码（前4后2），明文只在服务端解析时出现；
  - Agent/工具配置里写 `${cred:connector_name}` 占位符，执行前
    `resolve_placeholders()` 服务端替换——密钥不进 prompt、不进日志、不进前端；
  - 每次读用更新 last_used，配合 db.audit 形成"谁在什么时候用了哪把钥匙"。
HASHMM_DATA_DIR 定位存储；任何失败安全降级（返回 None/原文）。
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from hashmm.secrets_crypto import (
    decrypt_secret,
    encrypt_secret,
    rewrap_legacy_fernet_ciphertext,
)
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.credentials")

_LOCK = threading.Lock()
_PLACEHOLDER = re.compile(r"\$\{cred:([A-Za-z0-9_\-.]+)\}")


def _db() -> sqlite3.Connection:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    p = Path(d).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p / "credentials.db"), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS credentials(
        connector TEXT PRIMARY KEY, enc_value TEXT, note TEXT,
        created REAL, updated REAL, last_used REAL)""")
    return c


def set_credential(connector: str, value: str, note: str = "") -> bool:
    name = (connector or "").strip()
    if not name or not value:
        return False
    try:
        enc = encrypt_secret(value)
        now = time.time()
        with _LOCK, _db() as c:
            c.execute(
                "INSERT INTO credentials(connector, enc_value, note, created, updated, last_used) "
                "VALUES(?,?,?,?,?,0) "
                "ON CONFLICT(connector) DO UPDATE SET enc_value=excluded.enc_value, "
                "note=excluded.note, updated=excluded.updated",
                (name, enc, (note or "")[:200], now, now))
        return True
    except Exception as e:
        log_suppressed(logger, e, "credentials.set")
        return False


def get_credential(connector: str) -> str | None:
    """取明文（服务端内部用）。命中更新 last_used。"""
    name = (connector or "").strip()
    if not name:
        return None
    try:
        with _LOCK, _db() as c:
            r = c.execute("SELECT enc_value FROM credentials WHERE connector=?", (name,)).fetchone()
            if not r:
                return None
            cipher = str(r[0] or "")
            rewrapped = rewrap_legacy_fernet_ciphertext(cipher)
            if rewrapped:
                c.execute(
                    "UPDATE credentials SET enc_value=?, updated=? "
                    "WHERE connector=? AND enc_value=?",
                    (rewrapped, time.time(), name, cipher),
                )
                cipher = rewrapped
            c.execute("UPDATE credentials SET last_used=? WHERE connector=?", (time.time(), name))
        plain = decrypt_secret(cipher)
        return plain or None
    except Exception as e:
        log_suppressed(logger, e, "credentials.get")
        return None


def delete_credential(connector: str) -> bool:
    try:
        with _LOCK, _db() as c:
            cur = c.execute("DELETE FROM credentials WHERE connector=?", ((connector or "").strip(),))
            return cur.rowcount > 0
    except Exception as e:
        log_suppressed(logger, e, "credentials.delete")
        return False


def _mask(plain: str) -> str:
    if not plain:
        return ""
    if len(plain) <= 8:
        return plain[0] + "***"
    return plain[:4] + "…" + plain[-2:]


def list_credentials() -> list[dict]:
    """列表（只回掩码，明文不出服务端）。"""
    out: list[dict] = []
    try:
        with _db() as c:
            rows = c.execute("SELECT connector, enc_value, note, created, updated, last_used "
                             "FROM credentials ORDER BY connector").fetchall()
        for name, enc, note, created, updated, last_used in rows:
            try:
                rewrapped = rewrap_legacy_fernet_ciphertext(str(enc or ""))
                if rewrapped:
                    with _LOCK, _db() as c:
                        c.execute(
                            "UPDATE credentials SET enc_value=?, updated=? "
                            "WHERE connector=? AND enc_value=?",
                            (rewrapped, time.time(), name, enc),
                        )
                    enc = rewrapped
                masked = _mask(decrypt_secret(enc))
            except Exception:
                masked = "<解密失败>"
            out.append({"connector": name, "masked": masked, "note": note,
                        "created": created, "updated": updated, "last_used": last_used})
    except Exception as e:
        log_suppressed(logger, e, "credentials.list")
    return out


def resolve_placeholders(text: str) -> str:
    """把 `${cred:name}` 占位符替换为明文（服务端执行前一步）。未命中原样保留。"""
    if not text or "${cred:" not in text:
        return text

    def _sub(m: "re.Match[str]") -> str:
        v = get_credential(m.group(1))
        return v if v is not None else m.group(0)

    try:
        return _PLACEHOLDER.sub(_sub, text)
    except Exception as e:
        log_suppressed(logger, e, "credentials.resolve")
        return text
