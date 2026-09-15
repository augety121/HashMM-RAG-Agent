"""Memory metrics rendering helpers.

Pure formatting — no I/O or computation. Takes stats dicts from the
SessionStore / SemanticCache and renders them as rich tables or plain text.
"""

from __future__ import annotations

import time
from typing import Any


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def render_episodic_stats(stats: dict) -> str:
    """Plain-text summary of SessionStore.stats()."""
    lines = [
        "Episodic memory",
        f"  DB path     : {stats.get('db_path')}",
        f"  Size on disk: {_fmt_bytes(stats.get('db_size_bytes', 0))}",
        f"  Sessions    : {stats.get('n_sessions', 0)}",
        f"  Turns total : {stats.get('n_turns', 0)}",
    ]
    return "\n".join(lines)


def render_semcache_stats(stats: dict) -> str:
    """Plain-text summary of SemanticCache.stats()."""
    hit_rate = stats.get("hit_rate", 0.0)
    lines = [
        "Semantic cache",
        f"  Entries        : {stats.get('n_entries', 0)}",
        f"  Lookups        : {stats.get('n_lookups', 0)}",
        f"  Hits           : {stats.get('n_hits', 0)}  ({hit_rate * 100:.1f} %)",
        f"  Writes         : {stats.get('n_writes', 0)}",
        f"  Evictions      : {stats.get('n_evictions', 0)}",
        f"  Avg lookup     : {stats.get('avg_lookup_ms', 0.0):.2f} ms",
        f"  Stats reset at : {_fmt_ts(stats.get('last_reset_at', 0))}",
    ]
    return "\n".join(lines)


def render_top_queries(top: list[dict]) -> str:
    if not top:
        return "(no entries)"
    lines = ["Top cached queries (by hit count):"]
    for i, e in enumerate(top, 1):
        q = (e.get("query") or "")[:80]
        n = e.get("n_hits", 0)
        intent = e.get("intent") or "?"
        when = _fmt_ts(e.get("last_hit_at", 0))
        lines.append(f"  {i:2d}. [{n:3d}] ({intent:11s}) {q}   last_hit={when}")
    return "\n".join(lines)
