"""Deterministic, model-independent retrieval result contracts.

The contract makes the RAG boundary inspectable without asking an LLM whether
its own evidence was sufficient.  Counts and source readiness are derived from
the actual retrieval objects; the semantic answer evaluator remains separate.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


RETRIEVAL_CONTRACT_SCHEMA = "hashmm.retrieval-result.v1"


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def build_retrieval_contract(
    *,
    query: str,
    rewritten_query: str = "",
    requested_top_k: int = 5,
    total_candidates: int = 0,
    candidate_top_k: int = 0,
    results: Iterable[Any] = (),
    elapsed_ms: int = 0,
    strategy: str = "",
    rerank_method: str = "",
    filtered_count: int = 0,
    graph: dict[str, Any] | None = None,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = list(results or [])
    methods = Counter(str(_get(item, "method", _get(item, "source_type", "retrieval")) or "retrieval")
                      for item in items)
    citation_ready = sum(
        1 for item in items
        if str(_get(item, "text", _get(item, "content", "")) or "").strip()
        and str(_get(item, "filename", _get(item, "source", "")) or "").strip()
    )
    docs = {
        str(_get(item, "doc_id", "") or _get(item, "filename", "") or _get(item, "source", "")).strip()
        for item in items
    }
    docs.discard("")
    requested = max(1, min(int(requested_top_k or 5), 100))
    total = max(len(items), int(total_candidates or 0))
    contract = {
        "schema": RETRIEVAL_CONTRACT_SCHEMA,
        "query": str(query or "")[:500],
        "rewritten_query": str(rewritten_query or "")[:500],
        "requested_top_k": requested,
        "candidate_top_k": max(0, int(candidate_top_k or 0)),
        "total_candidates": total,
        "evidence_count": len(items),
        "unique_documents": len(docs),
        "citation_ready_count": citation_ready,
        "citation_ready": bool(items) and citation_ready == len(items),
        "evidence_status": "empty" if not items else "available",
        "has_more": total > len(items),
        "filtered_count": max(0, int(filtered_count or 0)),
        "strategy": str(strategy or "")[:80],
        "rerank_method": str(rerank_method or "")[:80],
        "source_methods": dict(sorted(methods.items())),
        "elapsed_ms": max(0, int(elapsed_ms or 0)),
        "graph": {
            key: max(0, int((graph or {}).get(key) or 0))
            for key in ("considered", "added", "missing", "forbidden")
        },
    }
    if isinstance(run, dict):
        contract["run"] = dict(run)
    return contract


def contract_summary(contract: Any) -> str:
    if not isinstance(contract, dict):
        return ""
    return (
        f"检索证据 {int(contract.get('evidence_count') or 0)} 条 / "
        f"候选 {int(contract.get('total_candidates') or 0)} 条；"
        f"可引用 {int(contract.get('citation_ready_count') or 0)} 条；"
        f"重排 {str(contract.get('rerank_method') or '未启用')}"
    )
