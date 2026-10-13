"""v17 Phase 108 — incremental KG build (LightRAG-style).

The full rebuild re-extracts every chunk (~82 min on the live corpus). When you
add a few documents you shouldn't pay that again. This loads the existing graph,
extracts triples **only from chunks not already in the graph**, and merges them in
(``add_entities`` / ``add_relations`` dedup by canonical key — existing nodes gain
the new source_ids, new nodes are added; no facts lost).

The selection + merge logic is pure and unit-tested; the live extraction (local
Qwen) runs on your machine via ``build_incremental_live``. Communities are kept as-is
by default (cheap); pass ``rebuild_comm=True`` to refresh them. Never raises.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.incremental")


def existing_chunk_ids(kg: Any) -> set:
    """Every chunk id already represented in the graph (union of node source_ids)."""
    out: set = set()
    try:
        for _key, data in kg.graph.nodes(data=True):
            for sid in (data.get("source_ids", []) or []):
                if sid:
                    out.add(str(sid))
    except Exception as e:
        log_suppressed(logger, e)
    return out


def select_new_chunks(corpus: list[dict], kg: Any) -> list[dict]:
    """Chunks whose chunk_id is not yet in the graph. If a chunk has no chunk_id,
    it's treated as new (conservative — better to re-extract than to miss)."""
    seen = existing_chunk_ids(kg)
    out = []
    for c in corpus or []:
        cid = str(c.get("chunk_id", "") or "")
        if not cid or cid not in seen:
            out.append(c)
    return out


def merge_graph(target_kg: Any, entities: list, relations: list) -> dict:
    """Merge extracted entities/relations into an existing graph. Returns counts.
    Never raises."""
    before_e = before_r = 0
    try:
        before_e, before_r = target_kg.num_entities, target_kg.num_relations
        if entities:
            target_kg.add_entities(entities)
        if relations:
            target_kg.add_relations(relations)
    except Exception as e:
        log_suppressed(logger, e)
    after_e = getattr(target_kg, "num_entities", before_e)
    after_r = getattr(target_kg, "num_relations", before_r)
    return {"entities_before": before_e, "entities_after": after_e,
            "entities_added": max(0, after_e - before_e),
            "relations_before": before_r, "relations_after": after_r,
            "relations_added": max(0, after_r - before_r)}


def build_incremental(extract_fn: Callable[[list], tuple], corpus: list[dict],
                      load_fn: Callable | None = None, save_fn: Callable | None = None) -> dict:
    """Incrementally extend the saved KG. ``extract_fn(new_chunks) -> (entities,
    relations)``; ``load_fn() -> (kg, comm)``; ``save_fn(kg, comm)``. Injectable for
    tests; defaults use KGStorage. Never raises."""
    t0 = time.time()
    try:
        if load_fn is None or save_fn is None:
            from hashmm.kg.storage import KGStorage
            _store = KGStorage()
            load_fn = load_fn or _store.load
            save_fn = save_fn or _store.save
        kg, comm = load_fn()
        new_chunks = select_new_chunks(corpus, kg)
        if not new_chunks:
            return {"ok": True, "new_chunks": 0, "skipped": len(corpus or []),
                    "entities_added": 0, "relations_added": 0,
                    "note": "没有新分块，KG 未变动", "elapsed_s": round(time.time() - t0, 1)}
        entities, relations = extract_fn(new_chunks)
        counts = merge_graph(kg, entities or [], relations or [])
        save_fn(kg, comm)
        counts.update({"ok": True, "new_chunks": len(new_chunks),
                       "skipped": len(corpus or []) - len(new_chunks),
                       "elapsed_s": round(time.time() - t0, 1)})
        logger.info(f"Incremental KG: +{counts['entities_added']} entities / "
                    f"+{counts['relations_added']} relations from {len(new_chunks)} new chunks")
        return counts
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "error": str(e), "elapsed_s": round(time.time() - t0, 1)}


def build_incremental_live(llm_fn: Callable | None = None, domain: str = "",
                           compact: bool = True, max_workers: int = 8,
                           rebuild_comm: bool = False) -> dict:
    """Live wiring for your machine: corpus from the BM25 index, extraction via the
    LLM triple extractor (give it the local Qwen ``llm_fn`` for free). Never raises."""
    try:
        from hashmm.retrieval_pipeline import RetrievalPipeline
        from hashmm.kg.llm_extractor import LLMTripleExtractor
        from hashmm.kg.storage import KGStorage

        pipe = RetrievalPipeline()
        pipe.load()
        corpus = [{
            "text": m.get("text", ""), "page": m.get("page", -1),
            "filename": m.get("filename", ""), "doc_id": m.get("doc_id", ""),
            "chunk_id": m.get("chunk_id", ""),
        } for m in getattr(pipe.bm25_index, "_corpus", [])]

        def extract_fn(new_chunks):
            usable = [c for c in new_chunks if len(str(c.get("text", "")).strip()) >= 30]
            extractor = LLMTripleExtractor(llm_fn=llm_fn, compact=compact, domain=domain)
            return extractor.extract_from_chunks(usable, max_workers=max_workers)

        store = KGStorage()
        res = build_incremental(extract_fn, corpus, load_fn=store.load, save_fn=store.save)

        if rebuild_comm and res.get("ok") and res.get("new_chunks", 0) > 0:
            try:
                from hashmm.pipeline.ingest import IngestPipeline
                ip = IngestPipeline()
                ip.load_kg()
                if llm_fn:
                    ip.community_mgr.set_llm(llm_fn)
                res["communities"] = ip.rebuild_communities()
            except Exception as e:
                log_suppressed(logger, e)
        return res
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "error": str(e)}
