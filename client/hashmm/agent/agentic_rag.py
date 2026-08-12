"""v17 Phase 102 — Agentic-RAG orchestrator (LangGraph, with a safe fallback).

Composes the pieces built so far into one self-correcting flow:

    plan (classify query, Phase 100)
      → retrieve (injected search_fn; KG strategies via the pipeline)
      → evaluate + correct (CRAG, Phase 101)
      → generate (injected generate_fn; wraps the answer LLM)

Built on ``langgraph.StateGraph`` when the package is available (your container has
1.2.0); otherwise an **identical** pure-Python sequential runner is used, so the
logic is the same with or without the dependency. All node work is via **injected
functions** (search_fn / generate_fn / llm_fn) → fully unit-testable without a GPU,
a live index, or an LLM. Opt-in (``HASHMM_AGENTIC_RAG``); does not touch the
existing answer path until explicitly wired.
"""
from __future__ import annotations

import os
from typing import Any, Callable, TypedDict

from hashmm.utils import get_logger, log_suppressed
from hashmm.agent.crag import correct as crag_correct
from hashmm.kg.kg_router import classify_query

logger = get_logger("hashmm.agent.agentic_rag")


def agentic_rag_enabled() -> bool:
    return os.environ.get("HASHMM_AGENTIC_RAG", "").strip().lower() in {"1", "true", "yes", "on"}


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except Exception:
        return False


class _State(TypedDict, total=False):
    query: str
    intent: str
    results: list
    answer: str
    sources: list
    trace: list
    corrected: bool
    errors: list[dict]


# ── individual node functions (shared by both the LangGraph and fallback paths) ──
def _node_plan(state: dict) -> dict:
    state["intent"] = classify_query(state.get("query", ""))
    state.setdefault("trace", []).append(f"plan: intent={state['intent']}")
    return state


def _make_retrieve(search_fn: Callable[[str], list]):
    def _node_retrieve(state: dict) -> dict:
        try:
            state["results"] = search_fn(state.get("query", "")) or []
        except Exception as e:
            log_suppressed(logger, e)
            state["results"] = []
            state.setdefault("errors", []).append({
                "stage": "retrieve",
                "type": type(e).__name__,
                "message": str(e)[:240],
            })
        state.setdefault("trace", []).append(f"retrieve: {len(state['results'])} hits")
        return state
    return _node_retrieve


def _make_correct(search_fn: Callable[[str], list], llm_fn: Callable | None):
    def _node_correct(state: dict) -> dict:
        results, info = crag_correct(state.get("query", ""), state.get("results", []),
                                     search_fn, llm_fn=llm_fn)
        state["results"] = results
        state["corrected"] = bool(info.get("corrected"))
        state.setdefault("trace", []).append(
            f"crag: label={info.get('label')} corrected={state['corrected']}"
            + (f" via={info.get('accepted')}" if info.get("accepted") else ""))
        return state
    return _node_correct


def _make_generate(generate_fn: Callable[[str, list], Any]):
    def _node_generate(state: dict) -> dict:
        try:
            ans = generate_fn(state.get("query", ""), state.get("results", []))
        except Exception as e:
            log_suppressed(logger, e)
            ans = ""
            state.setdefault("errors", []).append({
                "stage": "generate",
                "type": type(e).__name__,
                "message": str(e)[:240],
            })
        if isinstance(ans, dict):
            state["answer"] = ans.get("answer", "")
            state["sources"] = ans.get("sources", state.get("results", []))
        else:
            state["answer"] = str(ans)
            state["sources"] = state.get("results", [])
        state.setdefault("trace", []).append(f"generate: {len(state.get('answer',''))} chars")
        return state
    return _node_generate


def build_graph(search_fn: Callable[[str], list],
                generate_fn: Callable[[str, list], Any],
                llm_fn: Callable | None = None):
    """Return a compiled LangGraph app if langgraph is installed, else None
    (the caller then uses the sequential fallback). Never raises."""
    if not langgraph_available():
        return None
    try:
        from langgraph.graph import StateGraph, START, END
        g = StateGraph(_State)
        g.add_node("plan", _node_plan)
        g.add_node("retrieve", _make_retrieve(search_fn))
        g.add_node("correct", _make_correct(search_fn, llm_fn))
        g.add_node("generate", _make_generate(generate_fn))
        g.add_edge(START, "plan")
        g.add_edge("plan", "retrieve")
        g.add_edge("retrieve", "correct")
        g.add_edge("correct", "generate")
        g.add_edge("generate", END)
        return g.compile()
    except Exception as e:
        log_suppressed(logger, e)
        return None


def run_agentic_rag(query: str, search_fn: Callable[[str], list],
                    generate_fn: Callable[[str, list], Any],
                    llm_fn: Callable | None = None) -> dict:
    """Run the agentic-RAG flow. Uses LangGraph when available, otherwise an
    identical sequential fallback. Never raises — returns a best-effort dict."""
    state: dict = {"query": query or "", "trace": []}
    try:
        app = build_graph(search_fn, generate_fn, llm_fn)
        if app is not None:
            try:
                out = app.invoke(state)
                return dict(out)
            except Exception as e:
                log_suppressed(logger, e)  # fall through to sequential
        # sequential fallback — same nodes, same order
        state = _node_plan(state)
        state = _make_retrieve(search_fn)(state)
        state = _make_correct(search_fn, llm_fn)(state)
        state = _make_generate(generate_fn)(state)
        return state
    except Exception as e:
        log_suppressed(logger, e)
        state.setdefault("answer", "")
        state.setdefault("errors", []).append({
            "stage": "orchestrator",
            "type": type(e).__name__,
            "message": str(e)[:240],
        })
        return state
