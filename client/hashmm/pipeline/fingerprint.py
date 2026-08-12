"""文件指纹库（V204，图2-①）—— 语料入库的幂等控制，防止同一文件重复入库。

设计：
- 独立小 SQLite（<DATA_DIR>/file_fingerprints.db），单表，不与业务库耦合；
- SHA256 流式计算（大文件不占内存）；
- 语义：同一 sha256 已成功入库且其 doc 目录仍存在 → 视为重复，入库层直接
  返回既有 doc_id（duplicate=True），跳过解析/切块/向量/KG 全流程；
- doc 目录被人工删除后再次上传 → 指纹失效，自动清除并允许重新入库（自愈）；
- 全部操作永不抛错：指纹层任何故障都退化为"当作没有指纹库"，绝不挡住入库。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.pipeline.fingerprint")

_LOCK = threading.Lock()


def _data_dir() -> Path:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    return Path(d).expanduser().resolve()


def _db_path() -> Path:
    return _data_dir() / "file_fingerprints.db"


def _conn() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=5)
    c.execute(
        "CREATE TABLE IF NOT EXISTS file_fingerprints ("
        " sha256 TEXT PRIMARY KEY,"
        " filename TEXT NOT NULL,"
        " size INTEGER NOT NULL,"
        " doc_id TEXT NOT NULL,"
        " first_seen REAL NOT NULL)"
    )
    return c


def sha256_file(filepath: str | Path) -> str:
    """流式 SHA256；失败返回空串（调用方按无指纹处理）。"""
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except Exception as e:
        log_suppressed(logger, e, "sha256_file")
        return ""


def lookup(sha: str) -> dict | None:
    """查指纹。命中但对应 doc 目录已被删 → 清除该指纹并返回 None（自愈）。"""
    if not sha:
        return None
    try:
        with _LOCK, _conn() as c:
            row = c.execute(
                "SELECT sha256, filename, size, doc_id, first_seen FROM file_fingerprints WHERE sha256=?",
                (sha,),
            ).fetchone()
            if not row:
                return None
            rec = {"sha256": row[0], "filename": row[1], "size": row[2], "doc_id": row[3], "first_seen": row[4]}
            doc_dir = _data_dir() / "docs" / rec["doc_id"]
            legacy_dir = Path("data") / "docs" / rec["doc_id"]
            if not doc_dir.exists() and not legacy_dir.exists():
                c.execute("DELETE FROM file_fingerprints WHERE sha256=?", (sha,))
                logger.info(f"指纹自愈：{rec['filename']} 的 doc 目录已不存在，允许重新入库")
                return None
            return rec
    except Exception as e:
        log_suppressed(logger, e, "fingerprint.lookup")
        return None


def register(sha: str, filename: str, size: int, doc_id: str) -> None:
    """入库成功后登记指纹（UPSERT）。失败只记日志。"""
    if not sha or not doc_id:
        return
    try:
        with _LOCK, _conn() as c:
            c.execute(
                "INSERT INTO file_fingerprints (sha256, filename, size, doc_id, first_seen)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(sha256) DO UPDATE SET filename=excluded.filename,"
                " size=excluded.size, doc_id=excluded.doc_id",
                (sha, filename, int(size), doc_id, time.time()),
            )
    except Exception as e:
        log_suppressed(logger, e, "fingerprint.register")


def forget_doc(doc_id: str) -> None:
    """删除文档时同步清指纹（供 remove_document 调用）。"""
    if not doc_id:
        return
    try:
        with _LOCK, _conn() as c:
            c.execute("DELETE FROM file_fingerprints WHERE doc_id=?", (doc_id,))
    except Exception as e:
        log_suppressed(logger, e, "fingerprint.forget_doc")


def stats() -> dict:
    try:
        with _LOCK, _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM file_fingerprints").fetchone()[0]
            return {"count": int(n), "db": str(_db_path())}
    except Exception:
        return {"count": 0, "db": str(_db_path())}
