#!/usr/bin/env python3
"""06 — Run the LangGraph agent end-to-end on a query (M4 + M5).

Usage:
    # Default — agent uses semantic cache + episodic store
    python scripts/06_agent_query.py --query "what is BGE-M3"

    # Bypass cache for this query
    python scripts/06_agent_query.py --query "..." --no-cache

    # Resume a previous session (history written to that session_id)
    python scripts/06_agent_query.py --query "..." --session abc123def

    # Disable all memory (M4-only behaviour)
    python scripts/06_agent_query.py --query "..." --no-memory

Outputs:
    - Cache hit/miss banner (with hamming/cosine if hit)
    - Decision trace
    - Final answer with [N] citations
    - Source chunks
"""

from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hashmm.agent import build_agent
from hashmm.agent.llm import make_llm_fn
from hashmm.config import HashMMConfig
from hashmm.memory import SemanticCache, SessionStore, WorkingMemory
from hashmm.retrieval.hash_retriever import HashRetriever
from hashmm.utils import get_logger

logger = get_logger("scripts.06_agent")
console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True, help="user query")
    ap.add_argument("--image", default=None, help="optional image query path")
    ap.add_argument("--no-llm", action="store_true",
                    help="disable LLM (heuristic fallbacks)")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass semantic cache for this query only")
    ap.add_argument("--no-memory", action="store_true",
                    help="disable all M5 memory (cache + episodic)")
    ap.add_argument("--session", default=None,
                    help="session_id to resume; if absent, a new one is created")
    args = ap.parse_args()

    cfg = HashMMConfig()
    hash_retriever = HashRetriever(cfg)

    # Build LLM
    llm_fn = None if args.no_llm else make_llm_fn(cfg)
    console.print(f"[{'green' if llm_fn else 'yellow'}]"
                  f"{'✓' if llm_fn else '!'}[/] LLM "
                  f"{'enabled' if llm_fn else 'disabled (heuristics)'}")

    # Build memory layers (unless --no-memory)
    semcache = session_store = working = None
    if not args.no_memory:
        # Share the retriever's already-loaded encoders with the cache.
        retriever_net = hash_retriever._ensure_net()  # noqa: SLF001 - intentional
        text_encoder = hash_retriever._ensure_text_encoder()  # noqa: SLF001
        semcache = SemanticCache(
            cfg, text_encoder=text_encoder, hash_net=retriever_net,
        )
        session_store = SessionStore(cfg.episodic_db_path)
        working = WorkingMemory(max_turns=cfg.working_mem_max_turns)
        console.print(f"[green]✓[/green] Memory: semcache={'on' if cfg.semcache_enabled else 'OFF'}"
                      f" episodic=on")

    # Manage session id
    session_id = args.session
    if session_store is not None:
        if session_id:
            if not session_store.session_exists(session_id):
                console.print(
                    f"[yellow]session {session_id} not found, starting new[/yellow]"
                )
                session_id = session_store.start_session()
        else:
            session_id = session_store.start_session()
        console.print(f"[dim]session: {session_id}[/dim]")

    # Compile agent
    agent = build_agent(
        hash_retriever=hash_retriever,
        vector_retriever=None,
        hybrid_router=None,
        llm_fn=llm_fn,
        semantic_cache=semcache,
        session_store=session_store,
        working_memory=working,
    )

    console.print(Panel(
        f"[bold]Query:[/bold] {args.query!r}\n"
        f"[bold]Image:[/bold] {args.image or '(none)'}",
        title="Agent input"))

    initial = {
        "query": args.query,
        "query_image_path": args.image,
        "session_id": session_id or "",
        "skip_cache": args.no_cache,
        "cache_hit": False,
    }
    result = agent.invoke(initial)

    # ── Cache banner ──────────────────────────────────────────────────
    cache_step = next(
        (s for s in (result.get("trace") or [])
         if s.get("node") == "semcache_lookup"),
        None,
    )
    if cache_step:
        if cache_step.get("hit"):
            ham = cache_step.get("hamming")
            cos = cache_step.get("cosine")
            ham_str = f"{ham}" if ham is not None else "—"
            cos_str = f"{cos:.3f}" if cos is not None else "1.000"
            console.print(Panel(
                f"[bold green]CACHE HIT[/bold green] ({cache_step['match_type']}) "
                f"hamming={ham_str} "
                f"cosine={cos_str} "
                f"lookup={cache_step.get('lookup_ms', 0):.2f}ms",
                border_style="green",
            ))
        elif not cache_step.get("skipped"):
            console.print("[dim]cache miss — generated fresh[/dim]")

    # ── Trace table ───────────────────────────────────────────────────
    trace = result.get("trace") or []
    if trace:
        tbl = Table(title="Agent decision trace", show_header=True)
        tbl.add_column("#", width=3)
        tbl.add_column("node", width=18)
        tbl.add_column("details", overflow="fold")
        for i, step in enumerate(trace):
            node = step.get("node", "?")
            details = {k: v for k, v in step.items() if k not in ("node", "ts")}
            tbl.add_row(str(i), node,
                        json.dumps(details, ensure_ascii=False)[:200])
        console.print(tbl)

    # ── Final answer ──────────────────────────────────────────────────
    answer = result.get("answer", "(no answer)")
    console.print(Panel(answer, title="Answer", border_style="green"))

    # ── Sources ───────────────────────────────────────────────────────
    sources = result.get("sources_cited") or []
    retrieved = result.get("retrieved") or []
    if sources and retrieved:
        id_to_chunk = {r.get("chunk_id"): r for r in retrieved
                       if isinstance(r, dict)}
        src_tbl = Table(title=f"Sources ({len(sources)})", show_header=True)
        src_tbl.add_column("[N]", width=4)
        src_tbl.add_column("modality", width=10)
        src_tbl.add_column("doc / page", width=22)
        src_tbl.add_column("snippet", overflow="fold")
        for i, cid in enumerate(sources[:10], 1):
            r = id_to_chunk.get(cid)
            if not r:
                continue
            meta = r.get("meta") or {}
            doc = (meta.get("doc_id") or "?")[:14]
            page = meta.get("page_idx", "?")
            snip = (r.get("text") or "")[:100]
            src_tbl.add_row(f"[{i}]", r.get("modality", "?"),
                            f"{doc}/p{page}", snip)
        console.print(src_tbl)

    # Cleanup
    if semcache:
        semcache.close()
    if session_store:
        session_store.close()


if __name__ == "__main__":
    main()
