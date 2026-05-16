"""Semantic cache — two-stage lookup using the project's own hash codes.

This is M5's research-novelty piece. Standard semantic caches (GPTCache,
RedisSemanticCache) do float-cosine over N entries: O(N · d). For N=10k and
d=1024 that's tens of milliseconds per lookup.

Our cache uses the SAME hash net trained for retrieval. Two stages:

  Stage 1 (coarse, fast):  query → 128-bit hash code →
                           FAISS IndexBinaryFlat.search(top-K=20)
                           filter by Hamming < HAMMING_THRESHOLD
                           → ~20 candidates in <1 ms even at N=100k

  Stage 2 (precise, slow): cosine_sim(query_emb, cand_emb) for ≤ 20 cands
                           filter by cosine > COSINE_THRESHOLD
                           → 0 or 1 final match

The combined cost is O(N) integer XOR + O(K · d) cosine ≪ pure float scan.
This is the same coarse-then-fine pattern used in image retrieval (PQ + flat
rerank), applied to LLM caching.

Storage layout (all under cfg.semcache_dir):
    codes_{bits}bit.faiss   FAISS IndexBinaryFlat of all 128-bit codes
    embeddings.npy          float32 matrix (n_entries, embed_dim) in RAM
    meta.sqlite             entry metadata, stats, TTL, index_version
    row_to_entry.jsonl      append-only mapping faiss row → entry_id

Crash-safety:
    Two committable surfaces (faiss file + npy file) need atomic updates.
    Strategy: on every write, write to temp paths and atomically rename.
    The SQLite row is the durable truth; if a crash leaves faiss/npy out of
    sync with sqlite, we rebuild them from the DB at next load.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from hashmm.config import HashMMConfig
from hashmm.utils import get_logger

if TYPE_CHECKING:
    import faiss  # only for type hints

logger = get_logger("hashmm.memory.semcache")

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def _read_semcache_ddl() -> str:
    text = _SCHEMA_FILE.read_text(encoding="utf-8")
    marker = "-- SEMANTIC CACHE:"
    a = text.find(marker)
    if a < 0:
        raise RuntimeError("schema.sql is missing SEMANTIC CACHE section")
    return text[a:]


def _normalise_query(q: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation edges. Used for
    exact dedup so 'What is BGE-M3?' and 'what is bge-m3' map to one row."""
    q = (q or "").lower().strip()
    q = re.sub(r"\s+", " ", q)
    q = q.strip(" .?!,;:")
    return q


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically via temp + rename."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_save_np(path: Path, arr: np.ndarray) -> None:
    """Save numpy array atomically. Note: np.save() appends .npy to the
    target unless the path already ends in .npy. To avoid surprise renames,
    we write directly to an open file handle (which bypasses the extension
    heuristic)."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with open(tmp, "wb") as f:
        np.save(f, arr, allow_pickle=False)
    os.replace(tmp, path)


class SemanticCache:
    """Two-stage hash-then-cosine cache.

    Encoders & hash net are injected so the cache doesn't load them itself
    — this lets the agent share already-loaded models with the cache.

    Usage:
        cache = SemanticCache(cfg, text_encoder=enc, hash_net=net)

        # On agent entry
        hit = cache.lookup(query)
        if hit:
            return hit["answer"]   # ms-level, no LLM call

        # After agent generates
        cache.write(query, answer, retrieval, intent, strategy)
    """

    def __init__(
        self,
        cfg: HashMMConfig,
        text_encoder: Any | None = None,
        hash_net: Any | None = None,
        *,
        embed_dim: int | None = None,
    ):
        self.cfg = cfg
        self._text_enc = text_encoder
        self._hash_net = hash_net
        self._embed_dim = embed_dim  # cfg.embedding_dim if None
        self._faiss = None            # lazy import (heavy)
        self._index = None            # type: faiss.IndexBinaryFlat | None
        self._embeddings: np.ndarray | None = None  # shape (n_rows, embed_dim)
        self._conn = self._open_db()
        self._init_schema()
        self._load_indexes()

    # ── DB lifecycle ──────────────────────────────────────────────────

    def _open_db(self) -> sqlite3.Connection:
        path = self.cfg.semcache_db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path), isolation_level="DEFERRED",
            timeout=10.0, check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_read_semcache_ddl())
            # ensure singleton stats row exists
            self._conn.execute(
                "INSERT OR IGNORE INTO cache_stats (id, last_reset_at) "
                "VALUES (1, ?)", (time.time(),),
            )

    # ── Index lifecycle ───────────────────────────────────────────────

    def _ensure_faiss(self):
        if self._faiss is not None:
            return self._faiss
        import faiss as _faiss
        self._faiss = _faiss
        return _faiss

    def _new_index(self):
        faiss = self._ensure_faiss()
        return faiss.IndexBinaryFlat(self.cfg.hash_bits)

    def _load_indexes(self) -> None:
        """Load FAISS + embeddings from disk. If absent or mismatched with
        the SQLite row count, rebuild from DB (using stored codes if you
        have them; in our setup we re-encode if needed)."""
        faiss = self._ensure_faiss()

        # How many entries does the SQLite say we have?
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM cache_entries")
        n_db = cur.fetchone()["n"]

        # Try to load existing faiss + npy.
        idx_ok = False
        if self.cfg.semcache_faiss_path.exists():
            try:
                self._index = faiss.read_index_binary(str(self.cfg.semcache_faiss_path))
                if self._index.ntotal == n_db:
                    idx_ok = True
                else:
                    logger.warning(
                        "semcache faiss has %d rows but DB has %d — rebuilding",
                        self._index.ntotal, n_db,
                    )
            except Exception as e:
                logger.warning("failed to read semcache faiss: %s — rebuilding", e)

        if not idx_ok:
            self._index = self._new_index()

        # Embeddings array
        emb_dim = self._embed_dim or self.cfg.embedding_dim
        if self.cfg.semcache_embeddings_path.exists() and idx_ok:
            try:
                self._embeddings = np.load(self.cfg.semcache_embeddings_path)
                if self._embeddings.shape != (n_db, emb_dim):
                    logger.warning(
                        "semcache embeddings shape mismatch %s vs (%d, %d) — discarding",
                        self._embeddings.shape, n_db, emb_dim,
                    )
                    self._embeddings = None
            except Exception as e:
                logger.warning("failed to load semcache embeddings: %s", e)
                self._embeddings = None

        if self._embeddings is None:
            self._embeddings = np.zeros((0, emb_dim), dtype=np.float32)
            # Inconsistent indexes → clear them and start fresh.
            if self._index.ntotal != 0:
                self._index = self._new_index()

        logger.info(
            "semcache loaded: %d entries, faiss=%d, emb=%s",
            n_db, self._index.ntotal, self._embeddings.shape,
        )

    def _save_indexes(self) -> None:
        """Atomically persist faiss + embeddings."""
        faiss = self._ensure_faiss()

        # FAISS doesn't have a "write to buffer" for binary indexes in all
        # versions; use write_index_binary to a temp file then rename.
        tmp_faiss = self.cfg.semcache_faiss_path.with_suffix(
            f".faiss.tmp.{os.getpid()}"
        )
        faiss.write_index_binary(self._index, str(tmp_faiss))
        os.replace(tmp_faiss, self.cfg.semcache_faiss_path)

        _atomic_save_np(self.cfg.semcache_embeddings_path, self._embeddings)

    # ── Encoding ──────────────────────────────────────────────────────

    def _encode_query(self, query: str) -> tuple[np.ndarray, np.ndarray]:
        """Return (float_embedding [d], hash_code [bits // 8] uint8 packed).

        Both encoders must be provided (in __init__). Caller is responsible
        for ensuring they're in eval mode (load_hash_net() returns eval mode).

        Implementation tries `torch.no_grad()` for production efficiency.
        If torch isn't importable (e.g. unit tests with numpy-only stubs),
        falls back to a plain forward pass.
        """
        if self._text_enc is None or self._hash_net is None:
            raise RuntimeError(
                "SemanticCache needs text_encoder and hash_net injected; "
                "see SemanticCache.__init__"
            )

        try:
            import torch
            ctx = torch.no_grad()
        except ImportError:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            text_emb = self._text_enc([query])  # (1, embed_dim)
            # Tolerant unwrap: torch tensor → .detach().cpu().float().numpy()
            # numpy ndarray → already there
            if hasattr(text_emb, "detach"):
                float_emb = text_emb.detach().cpu().float().numpy()[0]
            elif isinstance(text_emb, np.ndarray):
                float_emb = text_emb[0]
            else:
                # fallback: assume it has a .numpy() chain we can do partially
                float_emb = np.asarray(text_emb)[0]

            # L2-normalise so cosine == dot product
            n = np.linalg.norm(float_emb) + 1e-9
            float_emb = (float_emb / n).astype(np.float32)

            # Hash code via the SAME path retriever uses
            sign = self._hash_net.sign_text(text_emb)  # (1, K) in {-1, +1}
            if hasattr(sign, "detach"):
                sign_np = sign.detach().cpu().numpy().astype(np.int8)[0]
            elif isinstance(sign, np.ndarray):
                sign_np = sign[0].astype(np.int8)
            else:
                sign_np = np.asarray(sign)[0].astype(np.int8)

            # Pack to uint8 bits  (sign>0 → 1)
            bits = (sign_np > 0).astype(np.uint8)
            packed = np.packbits(bits).reshape(1, -1)[0]

        return float_emb, packed

    # ── Lookup ────────────────────────────────────────────────────────

    def lookup(self, query: str) -> dict | None:
        """Return cached entry as dict if cache hit, else None.

        Tracks stats (n_lookups, n_hits, total_lookup_ms) in the DB.
        """
        if not self.cfg.semcache_enabled:
            return None
        if not query or not query.strip():
            return None

        t0 = time.time()
        norm = _normalise_query(query)

        # Exact-match fast path — skips encoding entirely.
        cur = self._conn.execute(
            "SELECT * FROM cache_entries WHERE query_norm = ? AND index_version = ? "
            "LIMIT 1",
            (norm, self.cfg.semcache_index_version),
        )
        row = cur.fetchone()
        if row and not self._is_expired(row):
            elapsed_ms = (time.time() - t0) * 1000
            self._on_hit(dict(row), elapsed_ms)
            return self._row_to_hit(row, elapsed_ms, match_type="exact")

        # Semantic match: stage 1 (hash) + stage 2 (cosine)
        if self._index.ntotal == 0:
            self._record_lookup(t0, hit=False)
            return None

        try:
            float_emb, hash_code = self._encode_query(query)
        except Exception as e:
            logger.warning("semcache encode failed: %s — treating as miss", e)
            self._record_lookup(t0, hit=False)
            return None

        # Stage 1: hash search
        k = min(self.cfg.semcache_stage1_topk, self._index.ntotal)
        # IndexBinaryFlat.search returns (distances int32, ids int64)
        q_bytes = hash_code.reshape(1, -1)
        D, I = self._index.search(q_bytes, k)
        ham_dists = D[0]
        cand_rows = I[0]

        # Filter by Hamming threshold
        mask = ham_dists < self.cfg.semcache_hamming_threshold
        if not mask.any():
            self._record_lookup(t0, hit=False)
            return None
        cand_rows = cand_rows[mask]
        cand_hams = ham_dists[mask]

        # Stage 2: cosine
        cand_embs = self._embeddings[cand_rows]                 # (m, d)
        cosines = cand_embs @ float_emb                          # (m,)
        best_local = int(np.argmax(cosines))
        best_cos = float(cosines[best_local])
        best_row = int(cand_rows[best_local])
        best_ham = int(cand_hams[best_local])

        if best_cos < self.cfg.semcache_cosine_threshold:
            self._record_lookup(t0, hit=False)
            return None

        # Hit! Fetch the metadata row from DB by faiss_row.
        cur = self._conn.execute(
            "SELECT * FROM cache_entries WHERE faiss_row = ? AND index_version = ? "
            "LIMIT 1",
            (best_row, self.cfg.semcache_index_version),
        )
        row = cur.fetchone()
        if not row or self._is_expired(row):
            self._record_lookup(t0, hit=False)
            return None

        elapsed_ms = (time.time() - t0) * 1000
        self._on_hit(dict(row), elapsed_ms)
        return self._row_to_hit(
            row, elapsed_ms, match_type="semantic",
            hamming=best_ham, cosine=best_cos,
        )

    def _is_expired(self, row: sqlite3.Row | dict) -> bool:
        ttl = row["ttl_seconds"]
        if ttl <= 0:
            return False
        return (time.time() - row["created_at"]) > ttl

    def _row_to_hit(self, row, elapsed_ms: float,
                    match_type: str, hamming: int | None = None,
                    cosine: float | None = None) -> dict:
        d = dict(row)
        return {
            "entry_id": d["entry_id"],
            "query": d["query"],
            "answer": d["answer"],
            "retrieval": json.loads(d["retrieval_json"] or "[]"),
            "intent": d["intent"],
            "strategy": d["strategy"],
            "n_hits": d["n_hits"] + 1,  # post-increment (after _on_hit ran)
            "match_type": match_type,
            "hamming": hamming,
            "cosine": cosine,
            "lookup_ms": elapsed_ms,
        }

    def _on_hit(self, row: dict, elapsed_ms: float) -> None:
        """Update stats + bump entry's last_hit_at / n_hits."""
        with self._conn:
            self._conn.execute(
                "UPDATE cache_entries SET last_hit_at = ?, n_hits = n_hits + 1 "
                "WHERE entry_id = ?",
                (time.time(), row["entry_id"]),
            )
            self._conn.execute(
                "UPDATE cache_stats SET n_lookups = n_lookups + 1, "
                "n_hits = n_hits + 1, total_lookup_ms = total_lookup_ms + ? "
                "WHERE id = 1",
                (elapsed_ms,),
            )

    def _record_lookup(self, t0: float, hit: bool) -> None:
        elapsed_ms = (time.time() - t0) * 1000
        with self._conn:
            self._conn.execute(
                "UPDATE cache_stats SET n_lookups = n_lookups + 1, "
                "total_lookup_ms = total_lookup_ms + ? WHERE id = 1",
                (elapsed_ms,),
            )

    # ── Write ─────────────────────────────────────────────────────────

    def write(
        self,
        query: str,
        answer: str,
        retrieval: list[dict] | None = None,
        intent: str | None = None,
        strategy: str | None = None,
        ttl_seconds: float | None = None,
    ) -> int | None:
        """Insert (or update) a cache entry. Returns entry_id, or None if
        the entry couldn't be encoded.

        If a row with the same query_norm exists (and index_version matches),
        it is updated rather than duplicated.
        """
        if not self.cfg.semcache_enabled:
            return None
        if not query or not answer:
            return None

        try:
            float_emb, hash_code = self._encode_query(query)
        except Exception as e:
            logger.warning("semcache write encode failed: %s — skipping", e)
            return None

        norm = _normalise_query(query)
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.cfg.semcache_ttl_seconds

        # Check for existing entry with same query_norm
        cur = self._conn.execute(
            "SELECT entry_id, faiss_row, embed_row FROM cache_entries "
            "WHERE query_norm = ? AND index_version = ?",
            (norm, self.cfg.semcache_index_version),
        )
        existing = cur.fetchone()

        if existing:
            # Update in place — don't touch faiss/embeddings (same code).
            with self._conn:
                self._conn.execute(
                    "UPDATE cache_entries SET answer = ?, retrieval_json = ?, "
                    "  intent = ?, strategy = ?, last_hit_at = ?, ttl_seconds = ? "
                    "WHERE entry_id = ?",
                    (
                        answer[:8000],
                        json.dumps(retrieval or [], ensure_ascii=False),
                        intent, strategy, now, ttl,
                        existing["entry_id"],
                    ),
                )
            return existing["entry_id"]

        # Maybe evict before inserting
        self._maybe_evict()

        # Append: row index in both faiss and embeddings will be the new ntotal.
        new_row = int(self._index.ntotal)
        self._index.add(hash_code.reshape(1, -1))
        self._embeddings = np.vstack([self._embeddings, float_emb.reshape(1, -1)])

        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO cache_entries ("
                " faiss_row, query, query_norm, answer, retrieval_json,"
                " intent, strategy, created_at, last_hit_at, n_hits,"
                " ttl_seconds, index_version, embed_row"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    new_row, query, norm, answer[:8000],
                    json.dumps(retrieval or [], ensure_ascii=False),
                    intent, strategy, now, now,
                    ttl, self.cfg.semcache_index_version, new_row,
                ),
            )
            entry_id = cur.lastrowid
            self._conn.execute(
                "UPDATE cache_stats SET n_writes = n_writes + 1 WHERE id = 1",
            )

        # Persist atomically. Acceptable cost (one disk flush per write).
        self._save_indexes()
        return entry_id

    def _maybe_evict(self) -> None:
        """Cap by max_entries (LRU by last_hit_at)."""
        if self.cfg.semcache_max_entries <= 0:
            return
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM cache_entries")
        n = cur.fetchone()["n"]
        if n < self.cfg.semcache_max_entries:
            return

        # Drop the oldest 10% in one shot to amortise the rebuild cost.
        n_evict = max(1, n // 10)
        cur = self._conn.execute(
            "SELECT entry_id, faiss_row FROM cache_entries "
            "ORDER BY last_hit_at ASC LIMIT ?",
            (n_evict,),
        )
        evict_ids = [row["entry_id"] for row in cur.fetchall()]
        if not evict_ids:
            return
        placeholders = ",".join("?" * len(evict_ids))
        with self._conn:
            self._conn.execute(
                f"DELETE FROM cache_entries WHERE entry_id IN ({placeholders})",
                evict_ids,
            )
            self._conn.execute(
                "UPDATE cache_stats SET n_evictions = n_evictions + ? WHERE id = 1",
                (len(evict_ids),),
            )

        # After bulk delete, the simplest safe path is to fully rebuild
        # the faiss index + embeddings from the remaining rows.
        self._rebuild_from_db()
        logger.info("semcache evicted %d entries", len(evict_ids))

    def _rebuild_from_db(self) -> None:
        """Reconstruct faiss + embeddings to match the current cache_entries
        table. This is called after eviction or when on-disk state is
        inconsistent. We rely on the fact that codes are deterministic given
        (query, hash_net) — so we re-encode rather than store raw bytes."""
        faiss = self._ensure_faiss()
        cur = self._conn.execute(
            "SELECT entry_id, query FROM cache_entries ORDER BY entry_id ASC"
        )
        rows = list(cur.fetchall())

        emb_dim = self._embed_dim or self.cfg.embedding_dim
        new_index = self._new_index()
        new_embs = np.zeros((len(rows), emb_dim), dtype=np.float32)

        if rows and (self._text_enc is None or self._hash_net is None):
            logger.warning(
                "cannot rebuild semcache without encoders; clearing %d entries",
                len(rows),
            )
            with self._conn:
                self._conn.execute("DELETE FROM cache_entries")
            self._index = new_index
            self._embeddings = np.zeros((0, emb_dim), dtype=np.float32)
            self._save_indexes()
            return

        # Re-encode each query and stitch back into both structures.
        with self._conn:
            for new_row, r in enumerate(rows):
                float_emb, hash_code = self._encode_query(r["query"])
                new_index.add(hash_code.reshape(1, -1))
                new_embs[new_row] = float_emb
                self._conn.execute(
                    "UPDATE cache_entries SET faiss_row = ?, embed_row = ? "
                    "WHERE entry_id = ?",
                    (new_row, new_row, r["entry_id"]),
                )

        self._index = new_index
        self._embeddings = new_embs
        self._save_indexes()

    # ── Inspection ────────────────────────────────────────────────────

    def stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT n_lookups, n_hits, n_writes, n_evictions, total_lookup_ms, "
            "       last_reset_at FROM cache_stats WHERE id = 1"
        )
        s = dict(cur.fetchone())
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM cache_entries")
        s["n_entries"] = cur.fetchone()["n"]
        s["hit_rate"] = (s["n_hits"] / s["n_lookups"]) if s["n_lookups"] > 0 else 0.0
        s["avg_lookup_ms"] = (
            (s["total_lookup_ms"] / s["n_lookups"]) if s["n_lookups"] > 0 else 0.0
        )
        return s

    def top_queries(self, n: int = 10) -> list[dict]:
        cur = self._conn.execute(
            "SELECT entry_id, query, n_hits, intent, strategy, last_hit_at "
            "FROM cache_entries ORDER BY n_hits DESC LIMIT ?",
            (n,),
        )
        return [dict(r) for r in cur.fetchall()]

    def purge_expired(self) -> int:
        """Delete all entries past their TTL. Returns number purged."""
        now = time.time()
        cur = self._conn.execute(
            "SELECT entry_id FROM cache_entries "
            "WHERE ttl_seconds > 0 AND (? - created_at) > ttl_seconds",
            (now,),
        )
        expired = [row["entry_id"] for row in cur.fetchall()]
        if not expired:
            return 0
        placeholders = ",".join("?" * len(expired))
        with self._conn:
            self._conn.execute(
                f"DELETE FROM cache_entries WHERE entry_id IN ({placeholders})",
                expired,
            )
        self._rebuild_from_db()
        return len(expired)

    def purge_all(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM cache_entries")
        n = cur.fetchone()["n"]
        with self._conn:
            self._conn.execute("DELETE FROM cache_entries")
        # Reset indexes
        emb_dim = self._embed_dim or self.cfg.embedding_dim
        self._index = self._new_index()
        self._embeddings = np.zeros((0, emb_dim), dtype=np.float32)
        self._save_indexes()
        return n

    def reset_stats(self) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE cache_stats SET n_lookups=0, n_hits=0, n_writes=0, "
                "n_evictions=0, total_lookup_ms=0, last_reset_at=? WHERE id=1",
                (time.time(),),
            )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
