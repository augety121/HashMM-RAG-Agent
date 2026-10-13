#!/usr/bin/env python3
"""07 — Inspect the semantic cache.

Usage:
    # Just show stats and top queries
    python scripts/07_semcache_stats.py

    # Also show top-N most-hit cached queries
    python scripts/07_semcache_stats.py --top 20

    # Purge entries older than their TTL
    python scripts/07_semcache_stats.py --purge-expired

    # Reset the hit-rate counters (entries themselves stay)
    python scripts/07_semcache_stats.py --reset-stats

    # Wipe the whole cache
    python scripts/07_semcache_stats.py --purge-all
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.panel import Panel

from hashmm.config import HashMMConfig
from hashmm.memory import (
    SemanticCache,
    render_semcache_stats,
    render_top_queries,
    SessionStore,
    render_episodic_stats,
)

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=10,
                    help="show top N hottest cached queries (default 10)")
    ap.add_argument("--purge-expired", action="store_true",
                    help="delete entries past their TTL")
    ap.add_argument("--purge-all", action="store_true",
                    help="wipe the entire semantic cache (irreversible)")
    ap.add_argument("--reset-stats", action="store_true",
                    help="zero the lookup/hit/write counters")
    ap.add_argument("--episodic-only", action="store_true",
                    help="only show episodic stats, skip semcache")
    args = ap.parse_args()

    cfg = HashMMConfig()

    # Episodic always cheap to open
    sess = SessionStore(cfg.episodic_db_path)
    console.print(Panel(render_episodic_stats(sess.stats()),
                        title="Episodic memory", border_style="cyan"))
    sess.close()

    if args.episodic_only:
        return

    # Semantic cache — opening it may not need encoders for stats only,
    # but writes (purge_expired) need to rebuild faiss. We pass None encoders
    # and rebuild_from_db handles that by clearing if rebuild needed.
    cache = SemanticCache(cfg)

    if args.purge_all:
        n = cache.purge_all()
        console.print(f"[yellow]purged all: {n} entries removed[/yellow]")

    if args.purge_expired:
        n = cache.purge_expired()
        console.print(f"[yellow]purged expired: {n} entries[/yellow]")

    if args.reset_stats:
        cache.reset_stats()
        console.print("[yellow]stats reset[/yellow]")

    console.print(Panel(render_semcache_stats(cache.stats()),
                        title="Semantic cache", border_style="magenta"))

    if args.top > 0:
        top = cache.top_queries(args.top)
        console.print(render_top_queries(top))

    cache.close()


if __name__ == "__main__":
    main()
