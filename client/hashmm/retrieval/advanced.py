"""Advanced retrieval techniques (V16 Phase 12) — accuracy boosters.

These are the highest-ROI accuracy improvements for RAG, implemented as small
composable functions the retrieval pipeline can opt into:

  1. HyDE (Hypothetical Document Embeddings): ask the LLM to draft a plausible
     answer, then retrieve with THAT (it's lexically closer to real documents
     than the question). Big recall win when question wording != doc wording.

  2. Multi-Query: generate several paraphrases / sub-questions, search each.

  3. RRF (Reciprocal Rank Fusion): merge multiple result lists robustly by rank
     (not score), so different queries' results combine without scale issues.

  4. Post-filter: after reranking, drop results far below the top score, so the
     LLM isn't fed weak/irrelevant context that makes it drift.

All are OFF by default and turned on via env or explicit args, so existing
behavior is unchanged unless requested. Each degrades gracefully without an LLM.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval.advanced")


def hyde_enabled() -> bool:
    return os.environ.get("HASHMM_HYDE", "0") == "1"


def multiquery_enabled() -> bool:
    return os.environ.get("HASHMM_MULTIQUERY", "0") == "1"


# ════════════════════════════════════════════════════════════════════════
# HyDE
# ════════════════════════════════════════════════════════════════════════

_HYDE_SYSTEM = (
    "你是一个知识助手。针对用户的问题，写一段简短的、看起来像是来自专业文档的"
    "假设性答案（2-4 句，包含可能的关键术语和数字）。不要说“我不知道”，"
    "即使不确定也要写出一个合理的、术语丰富的段落，用于检索。"
)


def generate_hyde(query: str, llm_fn: Any) -> str:
    """Generate a hypothetical answer passage for HyDE retrieval.

    v17 Phase 71: result is cached per query (reasoning-level cache) so repeated
    / multi-hop retrieval doesn't re-pay the LLM call. Empty results (transient
    LLM failures) are not cached, so they're retried next time.
    """
    if not llm_fn:
        return ""

    def _compute() -> str:
        try:
            if hasattr(llm_fn, "quick_call"):
                out = llm_fn.quick_call(_HYDE_SYSTEM, query, 200)
            elif callable(llm_fn):
                out = llm_fn(f"{_HYDE_SYSTEM}\n\n问题：{query}")
            else:
                return ""
            return (out or "").strip()[:600]
        except Exception as e:
            logger.debug(f"HyDE generation failed: {e}")
            return ""

    from hashmm import pipeline_cache as _pc
    return _pc.get_or_compute("hyde", {"q": query}, _compute,
                              should_cache=lambda v: bool(v))


# ════════════════════════════════════════════════════════════════════════
# Multi-Query
# ════════════════════════════════════════════════════════════════════════

_MQ_SYSTEM = (
    "把用户的问题改写成 3 个角度不同、措辞不同的检索查询，覆盖同义表达和不同侧面。"
    "每行一个，不要编号，不要解释。"
)


def generate_multiqueries(query: str, llm_fn: Any, n: int = 3) -> list[str]:
    """Generate N alternative queries. Always includes the original.

    v17 Phase 71: cached per (query, n). Only cached when the LLM actually
    produced extra queries (len > 1), so a transient failure that yields just
    the original isn't cached and is retried next time.
    """
    if not llm_fn:
        return [query]

    def _compute() -> list[str]:
        queries = [query]
        try:
            if hasattr(llm_fn, "quick_call"):
                out = llm_fn.quick_call(_MQ_SYSTEM, query, 200)
            elif callable(llm_fn):
                out = llm_fn(f"{_MQ_SYSTEM}\n\n问题：{query}")
            else:
                return queries
            for line in (out or "").splitlines():
                line = line.strip().lstrip("0123456789.、-) ").strip()
                if line and line not in queries:
                    queries.append(line)
                if len(queries) >= n + 1:
                    break
        except Exception as e:
            logger.debug(f"multi-query generation failed: {e}")
        return queries

    from hashmm import pipeline_cache as _pc
    return _pc.get_or_compute("multiquery", {"q": query, "n": n}, _compute,
                              should_cache=lambda v: isinstance(v, list) and len(v) > 1)


# ════════════════════════════════════════════════════════════════════════
# RRF fusion
# ════════════════════════════════════════════════════════════════════════

def rrf_fuse(result_lists: list[list[Any]], k: int = 60,
             key: Callable[[Any], str] | None = None) -> list[Any]:
    """Reciprocal Rank Fusion of multiple ranked result lists.

    score(d) = sum over lists of 1/(k + rank_in_list(d)).
    `key` extracts a dedup identity from a result (default: text[:120]).
    Returns results sorted by fused score (highest first).
    """
    if key is None:
        def key(r):
            t = getattr(r, "text", None)
            if t is None and isinstance(r, dict):
                t = r.get("text", "")
            return (t or "")[:120]

    scores: dict[str, float] = {}
    rep: dict[str, Any] = {}
    for results in result_lists:
        for rank, r in enumerate(results):
            kid = key(r)
            if not kid:
                continue
            scores[kid] = scores.get(kid, 0.0) + 1.0 / (k + rank + 1)
            if kid not in rep:
                rep[kid] = r
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [rep[kid] for kid, _ in ordered]


# ════════════════════════════════════════════════════════════════════════
# Post-filter
# ════════════════════════════════════════════════════════════════════════

def post_filter(results: list[Any], rel_threshold: float = 0.3,
                min_keep: int = 1, max_keep: int = 8) -> list[Any]:
    """Drop results whose score is far below the top result.

    Keeps results with score >= top_score * rel_threshold, but always keeps at
    least `min_keep` and at most `max_keep`. Avoids feeding weak context that
    makes the LLM drift, while never returning empty when something was found.
    """
    if not results:
        return results

    def _score(r):
        s = getattr(r, "score", None)
        if s is None and isinstance(r, dict):
            s = r.get("score", 0)
        return float(s or 0)

    scored = sorted(results, key=_score, reverse=True)
    top = _score(scored[0])
    if top <= 0:
        return scored[:max_keep]
    cutoff = top * rel_threshold
    kept = [r for r in scored if _score(r) >= cutoff]
    if len(kept) < min_keep:
        kept = scored[:min_keep]
    return kept[:max_keep]
