"""v17 Phase 95 — KG-grounded retrieval (GraphRAG-style *local* search).

Turns the 1676-entity graph from a *display* into something that *improves
answers*. Given a query, it finds the entities mentioned, expands one hop in the
graph, and surfaces the source chunks attached to that subgraph as an extra
retrieval signal — to be fused with the existing dense (FAISS) + sparse (BM25)
candidates and re-scored by the cross-encoder reranker.

Why this is safe to add to the answer path:
  * **default OFF** (env ``HASHMM_KG_RETRIEVAL``) → zero behaviour change;
  * candidates are folded into the normal fusion → the **reranker is the final
    arbiter**, so a weak KG candidate just gets down-ranked, never forced in;
  * **never raises** — any error returns an empty candidate list;
  * **pure function** here (returns plain chunk dicts) → no import of the
    retrieval pipeline, no circular deps, fully unit-testable without a GPU.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.kg_retrieval")

_SEED_WEIGHT = 2.0       # entity directly named in the query
_NEIGHBOR_WEIGHT = 1.0   # its 1-hop neighbours
_MIN_NAME_LEN = 2        # ignore 1-char entities as query seeds (too noisy)


def kg_retrieval_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_KG_RETRIEVAL")


def _max_seeds() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_RETRIEVAL_SEEDS", "8")))
    except ValueError:
        return 8


def _top_chunks() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_RETRIEVAL_CHUNKS", "20")))
    except ValueError:
        return 20


def _seed_entities(query: str, kg: Any, max_seeds: int) -> list:
    """Entities whose name or alias appears in the query, preferring high-degree
    (more salient) nodes. Returns a list of node keys."""
    q = query or ""
    hits = []
    for key, data in kg.graph.nodes(data=True):
        name = str(data.get("name", "")).strip()
        names = [name] + [str(a) for a in (data.get("aliases", []) or [])]
        matched = any(nm and len(nm) >= _MIN_NAME_LEN and nm in q for nm in names)
        if matched:
            try:
                deg = kg.graph.degree(key)
            except Exception:
                deg = 0
            hits.append((deg, key))
    hits.sort(key=lambda x: -x[0])
    return [k for _, k in hits[:max_seeds]]


def kg_local_candidates(query: str, kg: Any, corpus: list[dict] | None,
                        max_seeds: int | None = None,
                        top_chunks: int | None = None) -> list[dict]:
    """Return source-chunk dicts (with an added ``_kg_score``) reachable from the
    entities named in ``query`` (seed + 1 hop). Never raises."""
    out: list[dict] = []
    try:
        if not query or kg is None or not corpus:
            return out
        g = kg.graph
        if g.number_of_nodes() == 0:
            return out
        max_seeds = max_seeds if max_seeds is not None else _max_seeds()
        top_chunks = top_chunks if top_chunks is not None else _top_chunks()

        # corpus lookup: by chunk_id (preferred) and by doc_id (fallback)
        by_chunk: dict[str, dict] = {}
        by_doc: dict[str, list] = defaultdict(list)
        for m in corpus:
            cid = str(m.get("chunk_id", "") or "")
            did = str(m.get("doc_id", "") or "")
            if cid:
                by_chunk.setdefault(cid, m)
            if did:
                by_doc[did].append(m)

        seeds = _seed_entities(query, kg, max_seeds)
        if not seeds:
            return out

        # relevant entities = seeds (weight 2) ∪ 1-hop neighbours (weight 1)
        weights: dict[str, float] = {}
        for s in seeds:
            weights[s] = _SEED_WEIGHT
            try:
                for nb in set(list(g.successors(s)) + list(g.predecessors(s))):
                    weights.setdefault(nb, _NEIGHBOR_WEIGHT)
            except Exception:
                pass

        # score chunks by the weight of entities that reference them
        chunk_score: dict[str, float] = defaultdict(float)
        chunk_meta: dict[str, dict] = {}
        for ekey, w in weights.items():
            data = g.nodes.get(ekey, {})
            for sid in (data.get("source_ids", []) or []):
                sid = str(sid)
                metas = []
                if sid in by_chunk:
                    metas = [by_chunk[sid]]
                elif sid in by_doc:
                    metas = by_doc[sid][:5]  # cap doc-level fan-out
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
