"""v17 Phase 96 — KG global / community retrieval (GraphRAG-style *global* search).

Phase 95 gave entity-local retrieval (good for "雷军是谁"). This adds the *global*
half: for sense-making queries ("小米和网易的业务有哪些差异", "整体上有哪些主题")
the best context is not a single chunk but the **community summaries** already
produced at build time (the 102 summarized communities). We surface the most
relevant community summaries as extra candidates, folded into the same fusion +
rerank path as everything else.

Conventions: **default OFF** (``HASHMM_KG_GLOBAL``); pure function (returns plain
dicts, no pipeline import); **never raises**; the reranker is the final arbiter so
an off-topic summary just gets down-ranked.
"""
from __future__ import annotations

import os
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.community_retrieval")

# Words that signal a "global"/sense-making question (informational only — the
# feature surfaces summaries regardless; this helper is exposed for callers/tests).
_GLOBAL_HINTS = (
    "总结", "汇总", "概况", "概览", "总体", "整体", "趋势", "对比", "比较", "差异",
    "区别", "哪些", "分别", "各自", "主要", "综述", "全部", "所有", "有什么", "异同",
)


def community_global_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_KG_GLOBAL")


def lazy_summaries_enabled() -> bool:
    """v17 Phase 98 (LazyGraphRAG): when ON, the build step DETECTS communities but
    SKIPS the expensive LLM summarization (~3 min + 102 local calls in the live run).
    Community retrieval then falls back to a member-entity-name context at query time
    (no LLM, fast). Default OFF → summaries are produced at build time as before."""
    return os.environ.get("HASHMM_KG_LAZY_SUMMARIES", "").strip().lower() in {"1", "true", "yes", "on"}


def _member_context(info: Any, kg: Any, limit: int = 12) -> str:
    """v17 Phase 98: build a lightweight, LLM-free community context from its top
    member entity names (used when no LLM summary exists — lazy mode)."""
    try:
        if kg is None:
            return ""
        names = []
        for key in (getattr(info, "members", []) or []):
            data = kg.graph.nodes.get(key, {})
            nm = str(data.get("name", "")).strip()
            if nm:
                try:
                    deg = kg.graph.degree(key)
                except Exception:
                    deg = 0
                names.append((deg, nm))
        names.sort(key=lambda x: -x[0])
        top = [nm for _, nm in names[:limit]]
        return ("社区成员：" + "、".join(top)) if top else ""
    except Exception:
        return ""


def _top_k() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_KG_GLOBAL_K", "3")))
    except ValueError:
        return 3


def is_global_query(query: str) -> bool:
    """Heuristic: does the query look like a global/sense-making question?"""
    q = query or ""
    return any(h in q for h in _GLOBAL_HINTS)


def _named_entity_keys(query: str, kg: Any) -> set:
    """Node keys whose display name/alias appears in the query."""
    q = query or ""
    out = set()
    try:
        for key, data in kg.graph.nodes(data=True):
            name = str(data.get("name", "")).strip()
            names = [name] + [str(a) for a in (data.get("aliases", []) or [])]
            if any(nm and len(nm) >= 2 and nm in q for nm in names):
                out.add(key)
    except Exception:
        pass
    return out


def _summary_overlap(query: str, summary: str) -> float:
    """Crude lexical overlap: fraction of query 2-grams present in the summary."""
    q = (query or "").strip()
    s = summary or ""
    if len(q) < 2 or not s:
        return 0.0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in s)
    return hit / len(grams)


def relevant_community_summaries(query: str, kg: Any, comm_mgr: Any,
                                 top_k: int | None = None) -> list[dict]:
    """Top community summaries relevant to the query, scored by (a) how many
    query-named entities are members and (b) lexical overlap with the summary.
    Returns chunk-like dicts (``text`` = summary). Never raises."""
    out: list[dict] = []
    try:
        if comm_mgr is None or not getattr(comm_mgr, "communities", None):
            return out
        top_k = top_k if top_k is not None else _top_k()
        named = _named_entity_keys(query, kg) if kg is not None else set()

        scored = []
        for cid, info in comm_mgr.communities.items():
            summary = getattr(info, "summary", "") or ""
            # v17 Phase 98: lazy fallback — no LLM summary → use member-name context.
            context = summary.strip() or _member_context(info, kg)
            if not context:
                continue
            members = set(getattr(info, "members", []) or [])
            ent_overlap = len(named & members) if named else 0
            lex = _summary_overlap(query, context)
            score = 3.0 * ent_overlap + lex
            if score > 0:
                scored.append((score, cid, info, context))

        scored.sort(key=lambda x: -x[0])
        for score, cid, info, context in scored[:top_k]:
            out.append({
                "text": context,
                "chunk_id": f"community:{cid}",
                "doc_id": f"community:{cid}",
                "filename": "知识图谱社区",
                "page": -1,
                "section": f"社区 #{cid}",
                "_kg_score": round(float(score), 4),
            })
    except Exception as e:  # never raise into the answer path
        log_suppressed(logger, e)
        return []
    return out
