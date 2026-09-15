"""LangGraph nodes for memory integration (M5).

Three new nodes plug into the M4 graph:

  semcache_lookup    BEFORE classify_intent
                     If HIT, fill answer + retrieved + cited_ids from cache
                     and skip the rest of the graph.

  semcache_write     AFTER generate
                     Persist (query, answer, retrieval, intent, strategy)
                     to the semantic cache for future hits.

  episodic_write     END of run
                     Persist the turn to SQLite session history.

Routing:
  After semcache_lookup, a router decides:
    HIT  → END (we already have an answer)
    MISS → classify_intent (continue normal flow)

All three nodes are no-ops when the corresponding memory layer is None,
so the agent works with any subset of M5 components enabled.
"""

from __future__ import annotations

import time
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.memory_nodes")


# ── semcache_lookup ───────────────────────────────────────────────────


def make_semcache_lookup_node(semcache):
    """Factory. `semcache` may be None to disable the layer."""

    def lookup_node(state: dict) -> dict:
        if semcache is None or state.get("skip_cache"):
            return {
                "cache_hit": False,
                "trace": [{"node": "semcache_lookup", "ts": time.time(),
                           "skipped": True}],
            }

        query = state.get("query", "")
        hit = semcache.lookup(query)
        if hit is None:
            logger.info("semcache MISS for query=%r", query[:50])
            return {
                "cache_hit": False,
                "trace": [{"node": "semcache_lookup", "ts": time.time(),
                           "hit": False}],
            }

        # Hit! Pre-fill the state so generate_node can short-circuit.
        logger.info(
            "semcache HIT (match=%s ham=%s cos=%.3f lookup=%.2fms)",
            hit["match_type"], hit.get("hamming"),
            hit.get("cosine") or 1.0, hit["lookup_ms"],
        )
        return {
            "cache_hit": True,
            "answer": hit["answer"],
            "retrieved": hit["retrieval"],
            "sources_cited": [r["chunk_id"] for r in hit["retrieval"]
                              if isinstance(r, dict) and r.get("chunk_id")],
            "intent": hit.get("intent") or "cached",
            "strategy": hit.get("strategy") or "cache",
            "quality_ok": True,
            "trace": [{"node": "semcache_lookup", "ts": time.time(),
                       "hit": True, "match_type": hit["match_type"],
                       "hamming": hit.get("hamming"),
                       "cosine": hit.get("cosine"),
                       "lookup_ms": hit["lookup_ms"]}],
        }

    return lookup_node


def route_after_cache_lookup(state: dict) -> str:
    """If we found a cache hit, jump straight to END (via episodic_write).
    Else continue with the normal flow at classify_intent."""
    if state.get("cache_hit"):
        return "episodic_write"
    return "classify_intent"


# ── semcache_write ────────────────────────────────────────────────────


def make_semcache_write_node(semcache):
    """Factory. After generate, write the turn into the cache.

    Skipped if: cache disabled, was a hit (no point re-writing), or
    quality failed (don't poison cache with bad answers).
    """

    def write_node(state: dict) -> dict:
        if semcache is None or state.get("skip_cache"):
            return {"trace": [{"node": "semcache_write", "ts": time.time(),
                               "skipped": True}]}
        if state.get("cache_hit"):
            return {"trace": [{"node": "semcache_write", "ts": time.time(),
                               "skipped": "was_hit"}]}
        if not state.get("quality_ok"):
            return {"trace": [{"node": "semcache_write", "ts": time.time(),
                               "skipped": "quality_failed"}]}

        try:
            entry_id = semcache.write(
                query=state.get("query", ""),
                answer=state.get("answer", ""),
                retrieval=state.get("retrieved") or [],
                intent=state.get("intent"),
                strategy=state.get("strategy"),
            )
            return {"trace": [{"node": "semcache_write", "ts": time.time(),
                               "entry_id": entry_id}]}
        except Exception as e:
            logger.warning("semcache write failed: %s", e)
            return {"trace": [{"node": "semcache_write", "ts": time.time(),
                               "error": str(e)[:200]}]}

    return write_node


# ── episodic_write ────────────────────────────────────────────────────


def make_episodic_write_node(session_store, working_mem=None):
    """Factory. At end of agent run, persist the turn to SQLite.

    Both session_store and the session_id (in state) must be present, else
    this is a no-op. We pull session_id from AgentState["session_id"];
    if absent, we skip.
    """

    def episodic_node(state: dict) -> dict:
        if session_store is None:
            return {"trace": [{"node": "episodic_write", "ts": time.time(),
                               "skipped": "no_store"}]}

        sid = state.get("session_id")
        if not sid:
            return {"trace": [{"node": "episodic_write", "ts": time.time(),
                               "skipped": "no_session_id"}]}

        try:
            turn_id = session_store.record_turn(
                sid, state, cache_hit=state.get("cache_hit", False),
            )
            if working_mem is not None:
                working_mem.record_turn(state)
            return {"trace": [{"node": "episodic_write", "ts": time.time(),
                               "turn_id": turn_id}]}
        except Exception as e:
            logger.warning("episodic write failed: %s", e)
            return {"trace": [{"node": "episodic_write", "ts": time.time(),
                               "error": str(e)[:200]}]}

    return episodic_node
