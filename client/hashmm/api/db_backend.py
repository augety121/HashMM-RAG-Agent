"""Database backend abstraction — SQLite (default) or PostgreSQL (opt-in).

WHY THIS EXISTS
---------------
The app has ~70 call sites that do `c.execute(sql, params)` with SQLite '?'
placeholders and consume rows like `row["col"]` / `dict(row)`. Rewriting all of
them for PostgreSQL would be risky and error-prone. Instead this module lets the
EXISTING code run unchanged against either backend:

  - HASHMM_DB_BACKEND=sqlite   (default) → behaves exactly like before, zero risk
  - HASHMM_DB_BACKEND=postgres           → routes to PostgreSQL, '?' → '%s'
                                            auto-translated, rows are dict-like

For a single-container, read-heavy RAG app, well-tuned SQLite + WAL already
sustains far more than 10k users' typical load. PostgreSQL is the escape hatch
for when sustained concurrent WRITES become the bottleneck — flip one env var,
no code changes.

Design notes:
  - We DO NOT translate SQL dialect beyond placeholders. The schema in
    database.py is SQLite-flavored; a PG schema is provided separately in
    migrations/ and is created by the migration script, not by executescript().
  - The PG connection wrapper exposes .execute()/.executescript()/.commit()/
    .rollback()/.close() and a .row_factory no-op, so callers can't tell the
    difference.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

# Backend selection. Default is sqlite so existing deployments are unaffected.
DB_BACKEND = os.environ.get("HASHMM_DB_BACKEND", "sqlite").lower().strip()
IS_POSTGRES = DB_BACKEND in ("postgres", "postgresql", "pg")

# ── SQLite tuning (applies to the default path) ──
# V103.50: SQLite PRAGMA。重大修复——之前默认开 mmap_size=128MB，在网络/overlay
# 文件系统（AutoDL 的 /root/autodl-tmp 常是这类）上，mmap I/O + 多线程写入是
# `database disk image is malformed` 的已知诱因。日志里反复出现的 DB 损坏正源于此。
# 现在：默认【关闭 mmap】（最安全）；synchronous 升到 FULL 更耐崩。
# 若确认在本地 SSD 上、想要 mmap 性能，可设 HASHMM_SQLITE_MMAP=134217728 开回。
_MMAP = os.environ.get("HASHMM_SQLITE_MMAP", "0").strip()
_SYNC = os.environ.get("HASHMM_SQLITE_SYNC", "FULL").strip().upper()
if _SYNC not in ("OFF", "NORMAL", "FULL", "EXTRA"):
    _SYNC = "FULL"
SQLITE_PRAGMAS = [
    "PRAGMA journal_mode=WAL",      # concurrent reads + single writer
    f"PRAGMA synchronous={_SYNC}",  # FULL：每次事务落盘，最大限度防损坏（默认）
    "PRAGMA busy_timeout=8000",     # wait up to 8s for a lock instead of erroring
    "PRAGMA cache_size=-16000",     # ~16MB page cache (negative = KB)
    "PRAGMA temp_store=MEMORY",     # temp tables/indices in RAM
    "PRAGMA foreign_keys=ON",
    "PRAGMA wal_autocheckpoint=1000",
    f"PRAGMA mmap_size={_MMAP}",    # 默认 0=关闭 mmap（网络/overlay FS 上必须关，否则会损坏）
]

# ── PostgreSQL connection string (only used when IS_POSTGRES) ──
#   Standard libpq env vars also work (PGHOST/PGUSER/...), but we accept one URL.
PG_DSN = os.environ.get(
    "HASHMM_PG_DSN",
    "postgresql://hashmm:hashmm@127.0.0.1:5432/hashmm",
)


# ════════════════════════════════════════════════════════════════════════
# PostgreSQL adapter — makes a psycopg connection behave like sqlite3 enough
# that existing call sites work unchanged.
# ════════════════════════════════════════════════════════════════════════

def _qmark_to_pyformat(sql: str) -> str:
    """Translate SQLite '?' placeholders to PostgreSQL '%s'.

    - '?' OUTSIDE single-quoted string literals → '%s' (a bound parameter).
    - '?' inside string literals is left as-is.
    - EVERY literal '%' (inside or outside literals) → '%%', because psycopg's
      pyformat paramstyle treats '%' as special everywhere in the query string.
    """
    out = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%":
            out.append("%%")  # always escape percent for pyformat
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


# SQL dialect fixups applied only on PostgreSQL. Keep this list small and
# explicit; we prefer to author PG-correct SQL in migrations rather than
# auto-rewrite at runtime.
_PG_FIXUPS = [
    (re.compile(r"strftime\('%s','now'\)", re.I), "extract(epoch from now())"),
    (re.compile(r"\bINSERT OR IGNORE\b", re.I), "INSERT"),  # combined w/ ON CONFLICT in migrations
    (re.compile(r"\bINSERT OR REPLACE\b", re.I), "INSERT"),
]


class _PGCursorResult:
    """Wraps a psycopg cursor to mimic sqlite3's execute().fetchone/fetchall."""

    def __init__(self, cursor):
        self._cur = cursor

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return None  # callers that need IDs generate them explicitly (_uid())

    @property
    def rowcount(self):
        return self._cur.rowcount


class PGConnection:
    """sqlite3-compatible facade over a psycopg connection."""

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None  # no-op; RealDictCursor already returns dicts

    def execute(self, sql: str, params: tuple | list = ()):  # noqa: A003
        # Apply dialect fixups FIRST (e.g. strftime → extract(epoch)), so that
        # their '%' don't get percent-escaped before substitution.
        fixed = sql
        for pat, repl in _PG_FIXUPS:
            fixed = pat.sub(repl, fixed)
        translated = _qmark_to_pyformat(fixed)
        cur = self._raw.cursor()
        cur.execute(translated, tuple(params) if params else None)
        return _PGCursorResult(cur)

    def executescript(self, script: str):
        # Used only by SQLite init; on PG the migration script handles schema.
        cur = self._raw.cursor()
        cur.execute(script)
        return _PGCursorResult(cur)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def make_sqlite_conn(db_path: Path) -> sqlite3.Connection:
    """Create and tune a SQLite connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    for pragma in SQLITE_PRAGMAS:
        try:
            c.execute(pragma)
        except sqlite3.OperationalError:
            pass
    return c


def make_pg_conn():
    """Create a PostgreSQL connection that behaves like sqlite3."""
    try:
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=False)
        return PGConnection(raw)
    except ImportError:
        # psycopg2 fallback
        import psycopg2
        from psycopg2.extras import RealDictCursor
        raw = psycopg2.connect(PG_DSN, cursor_factory=RealDictCursor)
        return PGConnection(raw)


def make_conn(db_path: Path):
    """Factory: return a tuned connection for the active backend."""
    if IS_POSTGRES:
        return make_pg_conn()
    return make_sqlite_conn(db_path)


def backend_name() -> str:
    return "postgres" if IS_POSTGRES else "sqlite"
