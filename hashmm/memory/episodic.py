"""Episodic memory — durable session + turn history (SQLite).

Lifetime: persists across processes, machines (if the DB file is shared).
Scope: keyed by session_id; user_id is reserved for multi-user expansion.

Design choices:
  * Stdlib `sqlite3` — zero deps. Embedded DB just works in containers.
  * WAL journal mode — readers don't block the single writer.
  * Foreign keys enabled — cascade delete a session → its turns disappear.
  * All writes wrapped in `with self._conn:` (transaction).
  * Schema loaded from schema.sql, not hard-coded — one source of truth.

What we store per turn:
  query, intent, strategy, n_results, quality_ok, cache_hit,
  answer, full trace[] as JSON, cited chunk_ids as JSON.

What we DON'T store:
  Raw retrieval chunks (they live in the chunks.jsonl + faiss index;
  re-fetchable by id). This keeps the DB small.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hashmm.utils import get_logger

logger = get_logger("hashmm.memory.episodic")

# Load the relevant DDL slice from schema.sql once at import time.
_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def _read_episodic_ddl() -> str:
    """Extract just the EPISODIC section of schema.sql.

    The file has two sections separated by a banner comment. We grab from
    the EPISODIC banner up to (not including) the SEMANTIC CACHE banner.
    """
    text = _SCHEMA_FILE.read_text(encoding="utf-8")
    marker_a = "-- EPISODIC:"
    marker_b = "-- SEMANTIC CACHE:"
    a = text.find(marker_a)
    b = text.find(marker_b)
    if a < 0 or b < 0:
        raise RuntimeError("schema.sql is missing expected section markers")
    return text[a:b]


class SessionStore:
    """Episodic memory backend.

    Usage:
        store = SessionStore(cfg.episodic_db_path)
        sid = store.start_session(user_id="alice")
        store.record_turn(sid, agent_state)
        ...
        history = store.get_recent_turns(sid, n=5)

    Thread/process-safety:
        SQLite + WAL handles concurrent reads fine. For writes, the DB
        serialises them; in-process we use one connection per SessionStore
        instance, which is safe as long as a SessionStore isn't shared
        between threads (each thread should have its own).
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            isolation_level="DEFERRED",  # explicit transactions via context manager
            timeout=10.0,                 # block up to 10s on lock contention
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # PRAGMAs need to run outside a transaction
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_read_episodic_ddl())

    # ── Sessions ──────────────────────────────────────────────────────

    def start_session(self, user_id: str = "default",
                      meta: dict | None = None) -> str:
        """Create a new session and return its id."""
        sid = uuid.uuid4().hex[:16]
        now = time.time()
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (session_id, user_id, created_at, updated_at, n_turns, meta_json) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (sid, user_id, now, now, json.dumps(meta or {})),
            )
        logger.info("started session %s (user=%s)", sid, user_id)
        return sid

    def session_exists(self, session_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        return cur.fetchone() is not None

    def list_sessions(self, user_id: str = "default",
                      limit: int = 20) -> list[dict]:
        cur = self._conn.execute(
            "SELECT session_id, created_at, updated_at, n_turns "
            "FROM sessions WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def delete_session(self, session_id: str) -> int:
        """Delete a session and all its turns (cascade). Returns rows deleted."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            return cur.rowcount

    # ── Turns ─────────────────────────────────────────────────────────

    def record_turn(self, session_id: str, state: dict,
                    cache_hit: bool = False) -> int:
        """Persist one agent turn. Returns the new turn_id.

        We expect `state` to be the final AgentState after `generate`. Pulls
        out the salient fields and writes them transactionally.
        """
        if not self.session_exists(session_id):
            raise ValueError(f"unknown session_id: {session_id!r}")

        now = time.time()
        with self._conn:  # transaction
            # Get current turn count
            cur = self._conn.execute(
                "SELECT n_turns FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            turn_idx = row["n_turns"] if row else 0

            cur = self._conn.execute(
                "INSERT INTO turns ("
                " session_id, turn_idx, ts, query, intent, strategy,"
                " n_results, quality_ok, cache_hit, answer, trace_json, cited_ids_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, turn_idx, now,
                    state.get("query", ""),
                    state.get("intent"),
                    state.get("strategy"),
                    len(state.get("retrieved") or []),
                    1 if state.get("quality_ok") else 0,
                    1 if cache_hit else 0,
                    (state.get("answer") or "")[:8000],  # cap for sanity
                    json.dumps(state.get("trace") or [], ensure_ascii=False),
                    json.dumps(state.get("sources_cited") or [], ensure_ascii=False),
                ),
            )
            turn_id = cur.lastrowid

            # Bump session counters
            self._conn.execute(
                "UPDATE sessions SET n_turns = n_turns + 1, updated_at = ? "
                "WHERE session_id = ?",
                (now, session_id),
            )

        return turn_id

    def get_recent_turns(self, session_id: str, n: int = 5) -> list[dict]:
        """Return the most recent n turns, OLDEST FIRST (chronological)."""
        cur = self._conn.execute(
            "SELECT turn_idx, ts, query, intent, strategy, n_results, "
            "       quality_ok, cache_hit, answer, trace_json, cited_ids_json "
            "FROM turns WHERE session_id = ? "
            "ORDER BY turn_idx DESC LIMIT ?",
            (session_id, n),
        )
        rows = list(cur.fetchall())
        rows.reverse()  # back to chronological
        out = []
        for r in rows:
            d = dict(r)
            d["quality_ok"] = bool(d["quality_ok"])
            d["cache_hit"] = bool(d["cache_hit"])
            d["trace"] = json.loads(d.pop("trace_json") or "[]")
            d["cited_ids"] = json.loads(d.pop("cited_ids_json") or "[]")
            out.append(d)
        return out

    def get_all_turns(self, session_id: str) -> list[dict]:
        """All turns in a session, oldest first. For replay / debugging."""
        cur = self._conn.execute(
            "SELECT turn_idx, ts, query, intent, strategy, n_results, "
            "       quality_ok, cache_hit, answer, trace_json, cited_ids_json "
            "FROM turns WHERE session_id = ? ORDER BY turn_idx ASC",
            (session_id,),
        )
        out = []
        for r in cur.fetchall():
            d = dict(r)
            d["quality_ok"] = bool(d["quality_ok"])
            d["cache_hit"] = bool(d["cache_hit"])
            d["trace"] = json.loads(d.pop("trace_json") or "[]")
            d["cited_ids"] = json.loads(d.pop("cited_ids_json") or "[]")
            out.append(d)
        return out

    # ── Maintenance ───────────────────────────────────────────────────

    def vacuum(self) -> None:
        """Compact the DB file. Cheap; call after big deletes."""
        self._conn.execute("VACUUM")

    def stats(self) -> dict:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM sessions")
        n_sessions = cur.fetchone()["n"]
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM turns")
        n_turns = cur.fetchone()["n"]
        return {"n_sessions": n_sessions, "n_turns": n_turns,
                "db_path": str(self.db_path),
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
