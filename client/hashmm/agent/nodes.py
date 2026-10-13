"""LangGraph nodes — one function per state transition.

Design principle: keep classification & planning RULE-BASED first, LLM-driven
only where necessary. This makes the agent:
  1. Cheap to run (most queries don't touch the LLM until the final answer)
  2. Deterministic and testable
  3. Easy to explain in interview ("here's a Python if/else, not a black box")

LLM is used in only TWO places:
  - refine_query: rewrite query when retrieval is poor
  - generate: produce the final answer

Everything else (intent, strategy, top-k, quality check) is rules.
"""

from __future__ import annotations

import time
from typing import Any

from hashmm.agent.state import AgentState
from hashmm.config import HashMMConfig
from hashmm.retrieval.base import BaseRetriever
from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.nodes")


# ── Intent classification (rule-based) ────────────────────────────────

# Words that signal cross-modal intent (user wants an image/figure/etc.)
_CROSS_MODAL_TRIGGERS = {
    "figure", "image", "picture", "diagram", "chart", "graph", "plot",
    "table", "screenshot", "illustration", "visual", "show me", "draw",
    "图", "图片", "图像", "图表", "表格", "示意图", "插图",
}

# Words that signal factual / lookup intent
_FACTUAL_TRIGGERS = {
    "what is", "define", "definition of", "who", "when", "where",
    "什么是", "定义",
}

# Modality keyword → explicit modality filter
_MODALITY_KEYWORDS = {
    "image": "image", "picture": "image", "photo": "image", "图片": "image",
    "figure": "image", "diagram": "image", "illustration": "image",
    "table": "table", "tab.": "table", "tabular": "table", "表格": "table",
    "chart": "chart", "bar chart": "chart", "pie chart": "chart", "图表": "chart",
    "equation": "equation", "formula": "equation", "公式": "equation",
}


def classify_intent(state: AgentState) -> dict:
    """Classify the user query into one of 4 intents. Pure rule-based.

    Heuristic precedence:
        1. If query_image_path provided → cross_modal (image-side query)
        2. If any cross-modal trigger word → cross_modal
        3. If question word + short query → factual
        4. Otherwise → semantic
        5. hybrid is reserved for explicit "compare", "and also", etc.
    """
    q = (state.get("query") or "").lower().strip()

    if state.get("query_image_path"):
        intent = "cross_modal"
        reason = "query has image side"
    elif any(trig in q for trig in _CROSS_MODAL_TRIGGERS):
        intent = "cross_modal"
        reason = "cross-modal trigger word present"
    elif any(q.startswith(trig) for trig in _FACTUAL_TRIGGERS) and len(q) < 80:
        intent = "factual"
        reason = "short fact-check pattern"
    elif " compare " in f" {q} " or " versus " in q or " vs " in q:
        intent = "hybrid"
        reason = "comparison query"
    else:
        intent = "semantic"
        reason = "default — open-ended semantic"

    # Also detect explicit modality filter
    modality = None
    for kw, mod in _MODALITY_KEYWORDS.items():
        if kw in q:
            modality = mod
            break

    logger.info("intent=%s (%s), modality_filter=%s", intent, reason, modality)
    return {
        "intent": intent,
        "modality_filter": modality,
        "trace": [{"node": "classify_intent", "ts": time.time(),
                   "intent": intent, "reason": reason, "modality": modality}],
    }


# ── Retrieval strategy planning (rule-based) ──────────────────────────

def plan_retrieval(state: AgentState) -> dict:
    """Map (intent, modality) → (strategy, top_k).

    Truth table:
        intent       modality  → strategy   top_k
        ─────────────────────────────────────────
        semantic     None      → vector     20
        semantic     non-None  → hybrid     20   (text query + image filter)
        cross_modal  any       → hash       20
        factual      any       → vector     10   (precision over recall)
        hybrid       any       → hybrid     30   (RRF needs more candidates)
    """
    intent = state.get("intent", "semantic")
    modality = state.get("modality_filter")

    if intent == "cross_modal":
        strategy = "hash"
        top_k = 20
    elif intent == "factual":
        strategy = "vector"
        top_k = 10
    elif intent == "hybrid":
        strategy = "hybrid"
        top_k = 30
    else:  # semantic
        strategy = "hybrid" if modality else "vector"
        top_k = 20

    logger.info("strategy=%s top_k=%d", strategy, top_k)
    return {
        "strategy": strategy,
        "top_k": top_k,
        "refine_attempts": state.get("refine_attempts", 0),
        "trace": [{"node": "plan_retrieval", "ts": time.time(),
                   "strategy": strategy, "top_k": top_k}],
    }


# ── Retrieval (calls M3) ───────────────────────────────────────────────

def make_retrieve_node(hash_retriever: BaseRetriever,
                       vector_retriever: BaseRetriever | None = None,
                       hybrid_router: Any | None = None):
    """Factory: returns a node function that has retrievers in closure.

    We close over the retrievers instead of putting them in state because
    they hold large model objects that aren't JSON-serialisable for
    checkpointing.
    """
    def retrieve_node(state: AgentState) -> dict:
        strategy = state.get("strategy", "vector")
        top_k = state.get("top_k", 20)
        query = state["query"]
        modality = state.get("modality_filter")
        image_path = state.get("query_image_path")

        # Pick the right retriever
        if strategy == "hash":
            retriever = hash_retriever
        elif strategy == "vector" and vector_retriever is not None:
            retriever = vector_retriever
        elif strategy == "hybrid" and hybrid_router is not None:
            retriever = hybrid_router
        else:
            # Fallback: hash retriever exists in all configurations
            retriever = hash_retriever

        results = retriever.retrieve(
            query=query, top_k=top_k,
            modality_hint=modality, query_image_path=image_path,
        )

        # Serialise to dicts so they're checkpointable
        retrieved = [
            {
                "chunk_id": r.chunk_id, "modality": r.modality,
                "text": r.text, "image_path": r.image_path,
                "score": r.score, "rank": r.rank, "source": r.source,
                "meta": r.meta,
            }
            for r in results
        ]
        logger.info("retrieved %d items via %s", len(retrieved), strategy)
        return {
            "retrieved": retrieved,
            "trace": [{"node": "retrieve", "ts": time.time(),
                       "strategy": strategy, "n_results": len(retrieved)}],
        }
    return retrieve_node


# ── Quality check (rule-based) ────────────────────────────────────────

def check_quality(state: AgentState) -> dict:
    """Decide whether retrieval is good enough to generate from.

    Heuristics (in priority order):
        1. Empty result → bad
        2. Modality filter requested but no result matches → bad
        3. Top hamming distance > 60 (out of 128 = ~half) → questionable
        4. All results from same doc (echo chamber) → reduced confidence
        5. Otherwise → ok
    """
    retrieved = state.get("retrieved") or []
    modality_filter = state.get("modality_filter")
    refine_attempts = state.get("refine_attempts", 0)

    if not retrieved:
        return _quality(False, "no results", refine_attempts)

    if modality_filter:
        matching = [r for r in retrieved if r["modality"] == modality_filter]
        if not matching:
            return _quality(False, f"no {modality_filter} in top-K", refine_attempts)
        if len(matching) < 2:
            return _quality(False, f"only {len(matching)} {modality_filter} found",
                            refine_attempts)

    # Hamming distance check: top hit too far → bad signal
    top_meta = retrieved[0].get("meta", {})
    top_hamming = top_meta.get("hamming_dist")
    if isinstance(top_hamming, (int, float)) and top_hamming > 60:
        return _quality(False, f"top hamming={top_hamming} too high",
                        refine_attempts)

    # Doc diversity: if all hits from 1 doc AND we have more than 3 results,
    # the query is too narrow; refining helps.
    docs = {r.get("meta", {}).get("doc_id") for r in retrieved}
    if len(retrieved) >= 5 and len(docs) == 1:
        return _quality(False, "all results from single document",
                        refine_attempts)

    return _quality(True, "passes all checks", refine_attempts)


def _quality(ok: bool, reason: str, refine_attempts: int) -> dict:
    logger.info("quality=%s (%s)", ok, reason)
    return {
        "quality_ok": ok,
        "quality_reason": reason,
        "trace": [{"node": "check_quality", "ts": time.time(),
                   "ok": ok, "reason": reason, "attempts": refine_attempts}],
    }


# ── Query refinement (uses LLM if available, else rule-based) ─────────

def make_refine_node(llm_fn=None):
    """Factory: returns a refine node. LLM is optional — without it we use
    a simple heuristic (drop modality keywords + lowercase + take first 60 chars).
    """
    def refine_query(state: AgentState) -> dict:
        original = state["query"]
        attempts = state.get("refine_attempts", 0) + 1

        if llm_fn is not None:
            try:
                prompt = (
                    "Rewrite the following retrieval query to be broader and "
                    "more likely to find relevant content. Return ONLY the "
                    "rewritten query, nothing else.\n\n"
                    f"Original: {original}\n"
                    f"Why it failed: {state.get('quality_reason', '')}\n\n"
                    "Rewritten:"
                )
                refined = llm_fn(prompt).strip().strip('"')
                if not refined or len(refined) > 500:
                    refined = _heuristic_refine(original)
            except Exception as e:
                logger.warning("LLM refine failed: %s — falling back", e)
                refined = _heuristic_refine(original)
        else:
            refined = _heuristic_refine(original)

        logger.info("refine #%d: %r → %r", attempts, original, refined)
        return {
            "query": refined,
            "refine_attempts": attempts,
            "trace": [{"node": "refine_query", "ts": time.time(),
                       "attempt": attempts, "from": original, "to": refined}],
        }
    return refine_query


def _heuristic_refine(query: str) -> str:
    """Drop modality keywords (we already failed once with them); broaden."""
    q = query.lower()
    for kw in _MODALITY_KEYWORDS:
        q = q.replace(kw, "")
    # Drop quotation marks and double spaces
    q = q.replace('"', "").replace("'", "")
    while "  " in q:
        q = q.replace("  ", " ")
    return q.strip() or query


# ── Generation (LLM) ──────────────────────────────────────────────────

def make_generate_node(llm_fn=None):
    """Factory: produce the final answer from retrieved context.

    Without an LLM, returns a templated 'here are the top hits' string —
    useful for tests and when DeepSeek key is unset.
    """
    def generate(state: AgentState) -> dict:
        retrieved = state.get("retrieved", [])
        query = state["query"]

        # Build context with citations
        context_lines = []
        cited_ids = []
        for i, r in enumerate(retrieved[:10]):  # top-10 to LLM context
            doc_id = r.get("meta", {}).get("doc_id", "?")
            page = r.get("meta", {}).get("page_idx", "?")
            tag = f"[{i + 1}]"
            text = (r["text"] or "")[:300]
            context_lines.append(f"{tag} (doc={doc_id[:12]}, page={page}) {text}")
            cited_ids.append(r["chunk_id"])

        context = "\n".join(context_lines)

        if llm_fn is not None:
            try:
                prompt = (
                    "You are answering a question based ONLY on the provided "
                    "context. If the context doesn't answer the question, say "
                    "so. Cite sources using the [N] tags. Be concise.\n\n"
                    f"Context:\n{context}\n\n"
                    f"Question: {query}\n\n"
                    "Answer:"
                )
                answer = llm_fn(prompt).strip()
            except Exception as e:
                logger.warning("LLM generate failed: %s — using template", e)
                answer = _template_answer(query, retrieved)
        else:
            answer = _template_answer(query, retrieved)

        return {
            "answer": answer,
            "sources_cited": cited_ids,
            "trace": [{"node": "generate", "ts": time.time(),
                       "n_context": len(retrieved), "answer_len": len(answer)}],
        }
    return generate


def _template_answer(query: str, retrieved: list[dict]) -> str:
    """Fallback: list top hits as bullets when no LLM is configured."""
    if not retrieved:
        return f"No relevant content found for: {query!r}"
    lines = [f"Top hits for {query!r}:"]
    for i, r in enumerate(retrieved[:5], 1):
        snippet = (r["text"] or "")[:120].replace("\n", " ")
        lines.append(f"  {i}. [{r['modality']}] {snippet}")
    return "\n".join(lines)


# ── Routing function for conditional edge ─────────────────────────────

def route_after_quality(state: AgentState) -> str:
    """After check_quality, decide where to go.

    Returns the name of the next node:
        - "generate"      if quality is ok
        - "refine_query"  if quality bad AND attempts < 2
        - "generate"      if quality bad but attempts == 2 (give up gracefully)
    """
    ok = state.get("quality_ok", True)
    attempts = state.get("refine_attempts", 0)

    if ok:
        return "generate"
    if attempts >= 2:
        logger.info("max refine attempts reached, generating with current results")
        return "generate"
    return "refine_query"
