"""磁盘化 BM25 倒排索引（V205 P0-1，图2-③口径）。

为什么再做一个：retrieval_pipeline.BM25Index 的持久化是 pickle 整体快照 +
rank_bm25 内存重建——不可检视、不可增量、装不上 rank_bm25 就整路失效。
这里补一块**透明倒排**：SQLite 存 term→(chunk_id, tf) 倒排表 + 每块 doc_length
+ 元数据，检索时纯 Python 算 BM25（k1=1.5, b=0.75, Robertson idf）。

角色：BM25Index 的镜像与兜底——
  - 每次 add/remove 同步镜像到磁盘（增量，非全量快照）；
  - rank_bm25 缺席、或冷启动 pickle 丢失时，search 走磁盘打分，检索不断路。
铁律：任何失败静默降级（返回空/跳过镜像），绝不影响主链路。
HASHMM_BM25_DISK=0 可整体关闭。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retrieval.bm25_disk")

_K1 = 1.5
_B = 0.75
_LOCK = threading.Lock()


def _enabled() -> bool:
    return (os.environ.get("HASHMM_BM25_DISK", "1") or "1") != "0"


def _db_path() -> Path:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    p = Path(d).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p / "bm25_index.db"


class Bm25Disk:
    """单文件 SQLite 倒排索引。所有方法失败安全。"""

    def __init__(self, path: Path | None = None):
        self.path = path or _db_path()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=5)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        try:
            with self._conn() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS chunks(
                    chunk_id TEXT PRIMARY KEY, doc_id TEXT, length INTEGER,
                    meta TEXT)""")
                c.execute("""CREATE TABLE IF NOT EXISTS postings(
                    term TEXT, chunk_id TEXT, tf INTEGER,
                    PRIMARY KEY(term, chunk_id))""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_post_term ON postings(term)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id)")
        except Exception as e:
            log_suppressed(logger, e, "bm25_disk.schema")

    # ── 写 ──────────────────────────────────────────────────────────────
    def add(self, chunk_id: str, tokens: list[str], meta: dict) -> None:
        """增量写入一个块（幂等：同 chunk_id 覆盖）。"""
        if not _enabled() or not chunk_id or not tokens:
            return
        tf: dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1
        try:
            with _LOCK, self._conn() as c:
                c.execute("DELETE FROM postings WHERE chunk_id=?", (chunk_id,))
                c.execute(
                    "INSERT OR REPLACE INTO chunks(chunk_id, doc_id, length, meta) VALUES(?,?,?,?)",
                    (chunk_id, str(meta.get("doc_id", "")), len(tokens),
                     json.dumps(meta, ensure_ascii=False, default=str)[:4000]))
                c.executemany(
                    "INSERT OR REPLACE INTO postings(term, chunk_id, tf) VALUES(?,?,?)",
                    [(term, chunk_id, n) for term, n in tf.items()])
        except Exception as e:
            log_suppressed(logger, e, "bm25_disk.add")

    def remove_doc(self, doc_id: str) -> int:
        """按文档删除其全部块。返回删除块数。"""
        if not _enabled() or not doc_id:
            return 0
        try:
            with _LOCK, self._conn() as c:
                ids = [r[0] for r in c.execute(
                    "SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
                if not ids:
                    return 0
                q = ",".join("?" * len(ids))
                c.execute(f"DELETE FROM postings WHERE chunk_id IN ({q})", ids)
                c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
                return len(ids)
        except Exception as e:
            log_suppressed(logger, e, "bm25_disk.remove")
            return 0

    def clear(self) -> None:
        try:
            with _LOCK, self._conn() as c:
                c.execute("DELETE FROM postings")
                c.execute("DELETE FROM chunks")
        except Exception as e:
            log_suppressed(logger, e, "bm25_disk.clear")

    # ── 读 ──────────────────────────────────────────────────────────────
    def count(self) -> int:
        try:
            with self._conn() as c:
                return int(c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        except Exception:
            return 0

    def search(self, tokens: list[str], top_k: int = 50) -> list[dict]:
        """纯 Python BM25：返回 [{score, meta}]，按分降序。失败返回空。"""
        if not _enabled() or not tokens:
            return []
        try:
            with self._conn() as c:
                row = c.execute("SELECT COUNT(*), COALESCE(AVG(length),0) FROM chunks").fetchone()
                n_docs, avgdl = int(row[0]), float(row[1] or 0)
                if n_docs == 0 or avgdl <= 0:
                    return []
                scores: dict[str, float] = {}
                seen_terms = set()
                for term in tokens:
                    if term in seen_terms:
                        continue
                    seen_terms.add(term)
                    rows = c.execute(
                        "SELECT p.chunk_id, p.tf, ch.length FROM postings p "
                        "JOIN chunks ch ON ch.chunk_id = p.chunk_id WHERE p.term=?",
                        (term,)).fetchall()
                    df = len(rows)
                    if df == 0:
                        continue
                    idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                    for chunk_id, tf, dl in rows:
                        denom = tf + _K1 * (1 - _B + _B * (dl / avgdl))
                        scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * (tf * (_K1 + 1)) / denom
                if not scores:
                    return []
                top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
                out = []
                for chunk_id, sc in top:
                    m = c.execute("SELECT meta FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
                    meta = {}
                    if m and m[0]:
                        try:
                            meta = json.loads(m[0])
                        except Exception:
                            meta = {}
                    meta.setdefault("chunk_id", chunk_id)
                    out.append({"score": float(sc), "meta": meta})
                return out
        except Exception as e:
            log_suppressed(logger, e, "bm25_disk.search")
            return []

    def stats(self) -> dict:
        try:
            with self._conn() as c:
                n = int(c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                terms = int(c.execute("SELECT COUNT(DISTINCT term) FROM postings").fetchone()[0])
                return {"chunks": n, "terms": terms, "db": str(self.path)}
        except Exception:
            return {"chunks": 0, "terms": 0, "db": str(self.path)}


_store: Bm25Disk | None = None


def get_store() -> Bm25Disk:
    global _store
    if _store is None or str(_store.path) != str(_db_path()):
        _store = Bm25Disk()
    return _store
