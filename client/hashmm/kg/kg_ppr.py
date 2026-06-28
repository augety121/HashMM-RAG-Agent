"""v17 Phase 97 — PPR multi-hop retrieval (HippoRAG-2 style).

Phase 95 local retrieval only looks 1 hop out. Real multi-hop questions ("雷军
创办的公司发布了什么车") need probability to flow further across the graph. This
runs **Personalized PageRank** (networkx, already installed — zero new deps) with
the query-named entities as seeds, so relevance propagates multi-hop; the
top-ranked entities' source chunks become extra candidates.

Conventions: **default OFF** (``HASHMM_KG_PPR``); pure function; **never raises**;
folded into the normal fusion + rerank path (rerank is the final arbiter).
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.kg_ppr")


def kg_ppr_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_KG_PPR")


def _max_seeds() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_PPR_SEEDS", "8")))
    except ValueError:
        return 8


def _top_entities() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_PPR_ENTITIES", "30")))
    except ValueError:
        return 30


def _top_chunks() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_PPR_CHUNKS", "20")))
    except ValueError:
        return 20


def _build_corpus_index(corpus):
    by_chunk: dict[str, dict] = {}
    by_doc: dict[str, list] = defaultdict(list)
    for m in corpus:
        cid = str(m.get("chunk_id", "") or "")
        did = str(m.get("doc_id", "") or "")
        if cid:
            by_chunk.setdefault(cid, m)
        if did:
            by_doc[did].append(m)
    return by_chunk, by_doc


def ppr_candidates(query: str, kg: Any, corpus: list[dict] | None,
                   max_seeds: int | None = None,
                   top_entities: int | None = None,
                   top_chunks: int | None = None) -> list[dict]:
    """Personalized-PageRank multi-hop retrieval. Seeds = entities named in the
    query; PPR propagates relevance; top entities' chunks are returned as
    candidates (with ``_kg_score``). Never raises."""
    out: list[dict] = []
    try:
        if not query or kg is None or not corpus:
            return out
        g = kg.graph
        if g.number_of_nodes() == 0:
            return out
        import networkx as nx
        from hashmm.kg.kg_retrieval import _seed_entities  # shared seed logic

        max_seeds = max_seeds if max_seeds is not None else _max_seeds()
        top_entities = top_entities if top_entities is not None else _top_entities()
        top_chunks = top_chunks if top_chunks is not None else _top_chunks()

        seeds = _seed_entities(query, kg, max_seeds)
        if not seeds:
            return out
        personalization = {s: 1.0 for s in seeds}

        # Personalized PageRank — multi-hop relevance. Robust to non-convergence.
        try:
            pr = nx.pagerank(g, alpha=0.85, personalization=personalization,
                             max_iter=200, tol=1.0e-6)
        except Exception:
            try:
                pr = nx.pagerank(g, alpha=0.85, personalization=personalization,
                                 max_iter=500, tol=1.0e-4)
            except Exception as e:
                log_suppressed(logger, e)
                return out

        ranked = sorted(pr.items(), key=lambda kv: -kv[1])

        # Restrict to entities reachable from the seeds (undirected connectivity):
        # personalized PageRank leaks a little mass onto isolated/unreachable nodes,
        # so without this an orphan node could surface. Relevance only meaningfully
        # flows within the seeds' connected component(s).
        try:
            ug = g.to_undirected()
            reachable: set = set()
            for s in seeds:
                if ug.has_node(s):
                    reachable |= nx.node_connected_component(ug, s)
            if reachable:
                ranked = [(k, v) for k, v in ranked if k in reachable]
        except Exception as e:
            log_suppressed(logger, e)

        ranked = ranked[:top_entities]
        if not ranked:
            return out
        max_pr = ranked[0][1] or 1.0

        by_chunk, by_doc = _build_corpus_index(corpus)
        chunk_score: dict[str, float] = defaultdict(float)
        chunk_meta: dict[str, dict] = {}
        for ekey, score in ranked:
            data = g.nodes.get(ekey, {})
            w = score / max_pr
            for sid in (data.get("source_ids", []) or []):
                sid = str(sid)
                metas = [by_chunk[sid]] if sid in by_chunk else (by_doc[sid][:5] if sid in by_doc else [])
                for m in metas:
                    ck = str(m.get("chunk_id", "")) or (str(m.get("doc_id", "")) + ":" + m.get("text", "")[:24])
                    chunk_score[ck] += w
                    chunk_meta.setdefault(ck, m)

        if not chunk_score:
            return out
        top = sorted(chunk_score.items(), key=lambda kv: -kv[1])[:top_chunks]
        maxs = top[0][1] or 1.0
        for ck, sc in top:
            m = dict(chunk_meta[ck])
            m["_kg_score"] = round(sc / maxs, 4)
            out.append(m)
    except Exception as e:  # never raise into the answer path
        log_suppressed(logger, e)
        return []
    return out
