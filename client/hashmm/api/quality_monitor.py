"""Online quality monitoring (V16 Phase 14).

Offline eval (Phase 8/13) tests a fixed golden set. But the real question is:
"what quality are ACTUAL users getting?" This module samples real conversation
turns, records lightweight quality signals (groundedness ratio, citation count,
source count, latency), and aggregates them into a live dashboard.

Sampling is rate-limited (every Nth turn) so it adds negligible overhead.
Storage is a rolling table; this is monitoring, not an audit log.
"""
from __future__ import annotations

import os
import time
import uuid

from hashmm.utils import get_logger

logger = get_logger("hashmm.quality_monitor")

# Sample 1 in N turns (configurable). 1 = every turn.
_SAMPLE_RATE = int(os.environ.get("HASHMM_QUALITY_SAMPLE_RATE", "5"))
_counter = 0


def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS quality_samples (
                id            TEXT PRIMARY KEY,
                user_id       TEXT DEFAULT '',
                query         TEXT DEFAULT '',
                task_type     TEXT DEFAULT '',
                num_sources   INTEGER DEFAULT 0,
                num_citations INTEGER DEFAULT 0,
                grounded_ratio DOUBLE PRECISION DEFAULT 1.0,
                answer_len    INTEGER DEFAULT 0,
                elapsed_ms    INTEGER DEFAULT 0,
                ts            DOUBLE PRECISION DEFAULT (strftime('%s','now'))
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_qs_ts ON quality_samples(ts DESC)")


def should_sample() -> bool:
    global _counter
    _counter += 1
    return (_counter % max(_SAMPLE_RATE, 1)) == 0


def record_sample(user_id: str, query: str, answer: str, sources: list,
                  task_type: str = "", elapsed_ms: int = 0) -> None:
    """Record a lightweight quality sample (best-effort, never raises)."""
    try:
        _ensure_table()
        from hashmm.generation.groundedness import citation_overlap_check
        from hashmm.api.core.citation_validator import extract_cited_indices
        g = citation_overlap_check(answer or "", sources or [])
        n_cite = len(extract_cited_indices(answer or ""))
        from hashmm.api import database as db
        with db._conn() as c:
            c.execute(
                """INSERT INTO quality_samples
                   (id,user_id,query,task_type,num_sources,num_citations,
                    grounded_ratio,answer_len,elapsed_ms)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex[:12], user_id, (query or "")[:300], task_type,
                 len(sources or []), n_cite, g.get("ratio", 1.0),
                 len(answer or ""), elapsed_ms),
            )
    except Exception as e:
        logger.debug(f"quality sample skipped: {e}")


def dashboard(days: int = 7) -> dict:
    """Aggregate online quality over the last N days for the live dashboard."""
    _ensure_table()
    since = time.time() - days * 86400
    from hashmm.api import database as db
    with db._conn() as c:
        agg = c.execute(
            "SELECT COUNT(*) n, COALESCE(AVG(grounded_ratio),1) gr, "
            "COALESCE(AVG(num_sources),0) src, COALESCE(AVG(num_citations),0) cit, "
            "COALESCE(AVG(elapsed_ms),0) lat, "
            "COALESCE(SUM(CASE WHEN num_sources=0 THEN 1 ELSE 0 END),0) no_src, "
            "COALESCE(SUM(CASE WHEN grounded_ratio<0.5 THEN 1 ELSE 0 END),0) weak "
            "FROM quality_samples WHERE ts >= ?", (since,)).fetchone()
        # daily trend
        daily = c.execute(
            "SELECT CAST((ts/86400) AS INTEGER) day, COUNT(*) n, "
            "COALESCE(AVG(grounded_ratio),1) gr FROM quality_samples "
            "WHERE ts >= ? GROUP BY day ORDER BY day", (since,)).fetchall()

    a = dict(agg)
    n = a["n"] or 0
    return {
        "days": days,
        "samples": n,
        "avg_grounded_ratio": round(a["gr"], 3),
        "avg_sources": round(a["src"], 2),
        "avg_citations": round(a["cit"], 2),
        "avg_latency_ms": round(a["lat"]),
        "answers_without_sources": a["no_src"],
        "weakly_grounded": a["weak"],
        "weak_rate": round(a["weak"] / n, 3) if n else 0.0,
        "daily": [{"day": int(d["day"]), "samples": d["n"],
                   "grounded_ratio": round(d["gr"], 3)} for d in daily],
        "sample_rate": _SAMPLE_RATE,
    }
