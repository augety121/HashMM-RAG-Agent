"""LangGraph state machine for HashMM-RAG (M4 + M5).

Compiles a StateGraph that wires the M4 nodes together with the conditional
refine loop, AND threads in the M5 memory layers if provided:

  - semantic_cache (SemanticCache): pre-pended to short-circuit on hit
  - session_store  (SessionStore):  appended to persist turns
  - working_memory (WorkingMemory): updated alongside session_store

All memory args are optional. With none, behavior matches v0.1.9 exactly.
"""

from __future__ import annotations

from typing import Any, Callable

from hashmm.agent.memory_nodes import (
    make_episodic_write_node,
    make_semcache_lookup_node,
    make_semcache_write_node,
    route_after_cache_lookup,
)
from hashmm.agent.nodes import (
    check_quality,
    classify_intent,
    make_generate_node,
    make_refine_node,
    make_retrieve_node,
    plan_retrieval,
    route_after_quality,
)
from hashmm.agent.state import AgentState
from hashmm.retrieval.base import BaseRetriever
from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.graph")


def build_agent(
    hash_retriever: BaseRetriever,
    vector_retriever: BaseRetriever | None = None,
    hybrid_router: Any | None = None,
    llm_fn: Callable[[str], str] | None = None,
    *,
    semantic_cache: Any | None = None,
    session_store: Any | None = None,
    working_memory: Any | None = None,
):
    """Build and compile the LangGraph agent (M4 core + M5 memory).

    All memory args are optional. With none, behavior matches v0.1.9.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as e:
        raise ImportError(
            "langgraph is not installed. `pip install langgraph>=0.2.50 "
            "langchain-core>=0.3`"
        ) from e

    graph = StateGraph(AgentState)

    # ── M4 core nodes ──────────────────────────────────────────────────
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("plan_retrieval", plan_retrieval)
    graph.add_node("retrieve",
                   make_retrieve_node(hash_retriever, vector_retriever, hybrid_router))
    graph.add_node("check_quality", check_quality)
    graph.add_node("refine_query", make_refine_node(llm_fn))
    graph.add_node("generate", make_generate_node(llm_fn))

    # ── M5 memory nodes (no-op when their resource is None) ────────────
    graph.add_node("semcache_lookup",
                   make_semcache_lookup_node(semantic_cache))
    graph.add_node("semcache_write",
                   make_semcache_write_node(semantic_cache))
    graph.add_node("episodic_write",
                   make_episodic_write_node(session_store, working_memory))

    # ── Edges ──────────────────────────────────────────────────────────
    # Entry: cache lookup first
    graph.set_entry_point("semcache_lookup")

    # Conditional after lookup: HIT → episodic_write → END
    #                          MISS → classify_intent (normal path)
    graph.add_conditional_edges(
        "semcache_lookup",
        route_after_cache_lookup,
        {
            "classify_intent": "classify_intent",
            "episodic_write": "episodic_write",
        },
    )

    # Normal path
    graph.add_edge("classify_intent", "plan_retrieval")
    graph.add_edge("plan_retrieval", "retrieve")
    graph.add_edge("retrieve", "check_quality")
    graph.add_conditional_edges(
        "check_quality",
        route_after_quality,
        {
            "generate": "generate",
            "refine_query": "refine_query",
        },
    )
    graph.add_edge("refine_query", "plan_retrieval")

    # Post-generate: write to caches, then end
    graph.add_edge("generate", "semcache_write")
    graph.add_edge("semcache_write", "episodic_write")
    graph.add_edge("episodic_write", END)

    compiled = graph.compile()
    logger.info(
        "agent graph compiled: M4 (6 nodes) + M5 (3 memory) = 9 nodes, "
        "2 conditional_edges. semcache=%s session_store=%s",
        "on" if semantic_cache else "off",
        "on" if session_store else "off",
    )
    return compiled


def ascii_diagram() -> str:
    return """
    ┌────────────────────────┐
    │   semcache_lookup      │  M5: hash-then-cosine cache
    └─────┬──────────────┬───┘
          │ HIT          │ MISS
          ↓              ↓
          │      ┌────────────────────────┐
          │      │   classify_intent      │
          │      └──────────┬─────────────┘
          │                 ↓
          │      ┌────────────────────────┐
          │      │   plan_retrieval       │ ←──┐
          │      └──────────┬─────────────┘    │
          │                 ↓                  │
          │      ┌────────────────────────┐    │
          │      │      retrieve          │    │
          │      └──────────┬─────────────┘    │
          │                 ↓                  │
          │      ┌────────────────────────┐    │
          │      │   check_quality        │    │
          │      └────┬───────────────┬───┘    │
          │           │ ok            │ bad    │
          │           ↓               ↓        │
          │      ┌─────────┐  ┌──────────────┐ │
          │      │ generate│  │ refine_query │ │
          │      └────┬────┘  └──────┬───────┘ │
          │           ↓              └─────────┘
          │      ┌────────────────────────┐
          │      │  semcache_write        │  M5: persist (q, a, retrieval)
          │      └──────────┬─────────────┘
          │                 ↓
          └──────────→ ┌────────────────────────┐
                       │  episodic_write        │  M5: SQLite session log
                       └──────────┬─────────────┘
                                  ↓
                                 END
    """
