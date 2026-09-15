"""v17 Phase 30 — retrieval quality: per-document capping + near-duplicate dedup.

A common, principled RAG quality issue: the top-k chunks are dominated by ONE
document (or near-duplicate passages), starving the context of breadth — which
hurts comparison/multi-source questions. Capping chunks per document and dropping
near-duplicates improves coverage WITHOUT touching the eval (no gaming): it changes
the real retrieved context, and whether it helps must be confirmed on the real
golden/C-MTEB eval + the regression gate (Phase 26). Disable if it regresses.

Pure + offline-testable. Conservative defaults (cap=3) so it rarely changes small
result sets; order is otherwise preserved (highest-scored kept first).
"""
from __future__ import annotations

from typing import Iterable


def _doc_key(item) -> str:
    if isinstance(item, dict):
        return item.get("filename") or item.get("doc_id") or item.get("id") or ""
    return getattr(item, "filename", None) or getattr(item, "doc_id", "") or ""


def _text_of(item) -> str:
    if isinstance(item, dict):
        return item.get("text", "") or ""
    return getattr(item, "text", "") or ""


def _shingle(text: str, n: int = 24) -> str:
    return (text or "").strip()[:n]


def cap_per_document(results: Iterable, max_per_doc: int = 3) -> list:
    """Keep at most `max_per_doc` chunks from any single document (order preserved).

    Assumes results are already ranked best-first (so the kept ones are the best
    from each doc). Improves source breadth for comparison/multi-hop questions.
    """
    if max_per_doc <= 0:
        return list(results)
    seen: dict = {}
    out = []
    for r in results:
        k = _doc_key(r)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= max_per_doc:
            out.append(r)
    return out


def dedup_near_duplicates(results: Iterable, shingle_n: int = 24) -> list:
    """Drop chunks whose leading text duplicates an already-kept chunk."""
    seen: set = set()
    out = []
    for r in results:
        sig = _shingle(_text_of(r), shingle_n)
        if sig and sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out


def diversify(results: Iterable, max_per_doc: int = 3, dedup: bool = True) -> list:
    """Combined: near-duplicate dedup then per-document cap. Order preserved."""
    rs = list(results)
    if dedup:
        rs = dedup_near_duplicates(rs)
    return cap_per_document(rs, max_per_doc=max_per_doc)
