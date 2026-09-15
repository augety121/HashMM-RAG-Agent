"""Retrieval Analytics — tracks every retrieval for quality monitoring.

Records: query, top_score, strategy, elapsed_ms, num_sources, kg_hit,
         retrieval_mode, zero_result. Stored in SQLite for dashboard queries.

API:
    GET /api/admin/retrieval-analytics  → aggregated stats
"""
from __future__ import annotations

import time
from pathlib import Path

from hashmm.utils import get_logger

logger = get_logger("hashmm.analytics")

_DB_PATH = Path("data/retrieval_analytics.db")
_initialized = False


def _ensure_db():
    global _initialized
    if _initialized:
        return
    import sqlite3
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS retrieval_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        query TEXT NOT NULL,
        top_score REAL DEFAULT 0,
        strategy TEXT DEFAULT '',
        elapsed_ms INTEGER DEFAULT 0,
        num_sources INTEGER DEFAULT 0,
        kg_entities INTEGER DEFAULT 0,
        kg_relations INTEGER DEFAULT 0,
        retrieval_mode TEXT DEFAULT 'mix',
        zero_result INTEGER DEFAULT 0,
        cache_hit INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_ts ON retrieval_log(ts)""")
    conn.commit()
    conn.close()
    _initialized = True


def record_retrieval(
    query: str,
    top_score: float = 0,
    strategy: str = "",
    elapsed_ms: int = 0,
    num_sources: int = 0,
    kg_entities: int = 0,
    kg_relations: int = 0,
    retrieval_mode: str = "mix",
    cache_hit: bool = False,
) -> None:
    """Record a single retrieval event."""
    try:
        _ensure_db()
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute(
            """INSERT INTO retrieval_log 
               (ts, query, top_score, strategy, elapsed_ms, num_sources,
                kg_entities, kg_relations, retrieval_mode, zero_result, cache_hit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), query[:200], top_score, strategy, elapsed_ms,
             num_sources, kg_entities, kg_relations, retrieval_mode,
             1 if num_sources == 0 else 0, 1 if cache_hit else 0),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Analytics record failed: {e}")


def get_analytics(hours: int = 24) -> dict:
    """Get aggregated retrieval analytics for the last N hours."""
    try:
        _ensure_db()
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - hours * 3600

        total = conn.execute(
            "SELECT COUNT(*) as n FROM retrieval_log WHERE ts > ?", (cutoff,)
        ).fetchone()["n"]

        if total == 0:
            conn.close()
            return {"total": 0, "hours": hours}

        stats = conn.execute("""
            SELECT 
                COUNT(*) as total,
                AVG(top_score) as avg_score,
                AVG(elapsed_ms) as avg_latency,
                SUM(zero_result) as zero_results,
                SUM(cache_hit) as cache_hits,
                SUM(CASE WHEN strategy='grounded' THEN 1 ELSE 0 END) as grounded,
                SUM(CASE WHEN strategy='augmented' THEN 1 ELSE 0 END) as augmented,
                SUM(CASE WHEN strategy='direct' THEN 1 ELSE 0 END) as direct,
                SUM(CASE WHEN kg_entities > 0 THEN 1 ELSE 0 END) as kg_hits,
                AVG(num_sources) as avg_sources
            FROM retrieval_log WHERE ts > ?
        """, (cutoff,)).fetchone()

        # Zero-result queries
        zero_queries = conn.execute("""
            SELECT query, ts FROM retrieval_log 
            WHERE ts > ? AND zero_result = 1
            ORDER BY ts DESC LIMIT 10
        """, (cutoff,)).fetchall()

        # Score distribution
        score_dist = conn.execute("""
            SELECT 
                CASE 
                    WHEN top_score >= 3 THEN 'high (≥3)'
                    WHEN top_score >= 1 THEN 'medium (1-3)'
                    WHEN top_score >= 0 THEN 'low (0-1)'
                    ELSE 'negative (<0)'
                END as bucket,
                COUNT(*) as count
            FROM retrieval_log WHERE ts > ? AND top_score != 0
            GROUP BY bucket ORDER BY count DESC
        """, (cutoff,)).fetchall()

        conn.close()

        return {
            "total": stats["total"],
            "hours": hours,
            "avg_score": round(stats["avg_score"] or 0, 2),
            "avg_latency_ms": round(stats["avg_latency"] or 0),
            "avg_sources": round(stats["avg_sources"] or 0, 1),
            "zero_result_rate": f"{(stats['zero_results'] or 0) / max(stats['total'], 1) * 100:.1f}%",
            "cache_hit_rate": f"{(stats['cache_hits'] or 0) / max(stats['total'], 1) * 100:.1f}%",
            "strategy_distribution": {
                "grounded": stats["grounded"] or 0,
                "augmented": stats["augmented"] or 0,
                "direct": stats["direct"] or 0,
            },
            "kg_hit_rate": f"{(stats['kg_hits'] or 0) / max(stats['total'], 1) * 100:.1f}%",
            "score_distribution": [dict(r) for r in score_dist],
            "zero_result_queries": [
                {"query": r["query"], "ts": r["ts"]}
                for r in zero_queries
            ],
        }
    except Exception as e:
        logger.warning(f"Analytics query failed: {e}")
        return {"error": str(e)}
