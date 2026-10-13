"""Query-time Graph Engineering for evidence-preserving RAG.

This module does not ask a model to invent a graph.  It joins the query-local
KG matches back to the original indexed chunks, applies a strict result budget,
and keeps the concrete node/edge support as provenance.  The design is inspired
by dynamic event/entity retrieval, while staying compatible with HashMM's
existing KnowledgeGraph and vector-index storage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class GraphExpansion:
    results: list
    added_chunk_ids: list[str] = field(default_factory=list)
    considered: int = 0
    skipped_missing: int = 0
    skipped_forbidden: int = 0


def enabled() -> bool:
    """Graph evidence expansion is on by default and has an explicit kill switch."""
    return os.environ.get("HASHMM_GRAPH_ENGINEERING", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }


def evidence_limit(default: int = 4) -> int:
    try:
        value = int(os.environ.get("HASHMM_GRAPH_EVIDENCE_LIMIT", str(default)))
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, 12))


def _metadata_index(corpus: Iterable[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for raw in corpus or []:
        if not isinstance(raw, dict):
            continue
        chunk_id = str(raw.get("chunk_id") or "").strip()
        if chunk_id and chunk_id not in index:
            index[chunk_id] = raw
    return index


def expand_graph_evidence(
    results: Iterable,
    corpus: Iterable[dict],
    evidence: Iterable[dict],
    *,
    limit: int | None = None,
    acl=None,
    principal: str | None = None,
) -> GraphExpansion:
    """Append bounded, original graph-backed chunks to retrieval results.

    Ranking is deterministic and only consumes persisted KG support.  Access
    control remains the caller's responsibility and must be re-applied after
    expansion because a graph neighbor can belong to another document.
    """
    original = list(results or [])
    if not enabled():
        return GraphExpansion(results=original)
    cap = evidence_limit() if limit is None else max(0, min(int(limit), 12))
    if cap == 0:
        return GraphExpansion(results=original)

    lookup = _metadata_index(corpus)
    seen = {str(getattr(item, "chunk_id", "") or "") for item in original}
    ranked: list[tuple[float, int, str, dict, dict]] = []
    considered = 0
    missing = 0
    forbidden = 0
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id in seen:
            continue
        considered += 1
        meta = lookup.get(chunk_id)
        if not meta or not str(meta.get("text") or "").strip():
            missing += 1
            continue
        doc_identity = str(
            meta.get("doc_title") or meta.get("filename") or meta.get("source")
            or meta.get("doc_id") or ""
        )
        if acl is not None and not acl.can_access(principal, doc_identity):
            forbidden += 1
            continue
        support_count = len(item.get("entities") or []) + len(item.get("relations") or [])
        try:
            score = max(0.0, min(1.0, float(item.get("score") or 0.0)))
        except (TypeError, ValueError):
            score = 0.0
        ranked.append((-score, -support_count, chunk_id, meta, item))
    ranked.sort(key=lambda value: value[:3])

    from hashmm.retrieval_pipeline import SearchResult

    added: list[str] = []
    expanded = list(original)
    for negative_score, _support, chunk_id, meta, support in ranked[:cap]:
        graph_score = -negative_score
        result = SearchResult(
            text=str(meta.get("text") or ""),
            score=graph_score,
            doc_id=str(meta.get("doc_id") or ""),
            filename=str(meta.get("doc_title") or meta.get("filename") or meta.get("source") or ""),
            page=int(meta.get("page", -1) or -1),
            section=str(meta.get("section") or meta.get("section_path") or ""),
            chunk_id=chunk_id,
            source_type="graph_evidence",
            modality=str(meta.get("modality") or "text"),
        )
        # Dynamic attributes are intentionally presentation-only and never used
        # as proof; evidence labels came from the persisted KG search result.
        result.graph_support = {
            "entities": list(support.get("entities") or [])[:8],
            "relations": list(support.get("relations") or [])[:8],
        }
        expanded.append(result)
        seen.add(chunk_id)
        added.append(chunk_id)

    return GraphExpansion(
        results=expanded,
        added_chunk_ids=added,
        considered=considered,
        skipped_missing=missing,
        skipped_forbidden=forbidden,
    )
