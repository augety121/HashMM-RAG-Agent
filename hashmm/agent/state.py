"""LangGraph agent state schema.

The state is the SINGLE source of truth for the agent. All nodes read from and
write to this TypedDict; no node calls another directly. LangGraph handles
routing via conditional edges based on state contents.

Design notes (2026 best practice):
- Use TypedDict, not a flat dict. State schema is the decision that ages
  worst — once 5+ nodes read/write, refactoring is painful because of strict
  type checking.
- Use Annotated[list, add_messages] for the conversation log so multiple
  nodes can append safely.
- Keep retrieval results as a list of dicts (not RetrievedChunk dataclass)
  so they're JSON-serialisable for checkpointing.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

# We don't import langgraph here — its add_messages reducer is optional.
# We use a simple list and let LangGraph default-overwrite semantics work.


# Intent categories the classifier produces. Kept small and orthogonal:
#   - semantic:   ordinary text query, hit text chunks (vector route)
#   - cross_modal: query asks for an image / table / chart (hash route)
#   - hybrid:     mixed — needs RRF fusion
#   - factual:    short fact-check, prefer high-precision exact match
Intent = Literal["semantic", "cross_modal", "hybrid", "factual"]

# Three retrieval strategies that map onto M3's HybridRouter modes.
RetrievalStrategy = Literal["vector", "hash", "hybrid"]


class AgentState(TypedDict, total=False):
    """The complete state of one agent run.

    `total=False` means all fields are optional at init — nodes fill them in
    as they execute. Required field at start is just `query`.
    """

    # ── Input ──────────────────────────────────────────────────────────
    query: str
    """The original user query."""

    query_image_path: str | None
    """Optional image-side query for true multimodal queries."""

    # ── Planning (filled by classify_intent and plan_retrieval nodes) ─
    intent: Intent
    """Classified intent. Drives strategy selection."""

    strategy: RetrievalStrategy
    """Which retriever to use. Chosen from intent + heuristics."""

    modality_filter: str | None
    """If the user asked specifically for image/table/etc., this is set."""

    top_k: int
    """How many to retrieve. May be increased during refine."""

    # ── Retrieval (filled by retrieve node) ───────────────────────────
    retrieved: list[dict]
    """Top-K results as JSON-serialisable dicts.
    Keys: chunk_id, modality, text, image_path, score, rank, source, meta."""

    # ── Quality control (filled by check_quality node) ────────────────
    quality_ok: bool
    """Whether retrieval is good enough to generate from."""

    quality_reason: str
    """Why quality is/isn't ok. Surfaced for debugging + interview demo."""

    refine_attempts: int
    """How many times we've tried refining. Capped at 2."""

    # ── Generation (filled by generate node) ──────────────────────────
    answer: str
    """The final LLM answer."""

    sources_cited: list[str]
    """Chunk IDs the answer actually cites."""

    # ── Diagnostics ────────────────────────────────────────────────────
    trace: Annotated[list[dict], add]
    """Append-only execution log. Each node returns {"trace": [{node, ts, ...}]}
    and LangGraph concatenates them via operator.add. The `Annotated[..., add]`
    declaration is the canonical LangGraph idiom for accumulator state fields
    — without it, the default reducer is REPLACE and only the last node's
    entry survives. Used by the CLI to show the full decision path on-screen."""

    # ── Memory (M5) ────────────────────────────────────────────────────
    session_id: str
    """Episodic session id. If set, turns are persisted via SessionStore.
    If unset, no episodic write happens."""

    cache_hit: bool
    """True if semantic cache served this query. Set by semcache_lookup
    node; consumed by the router (skip classify→retrieve→generate) and
    semcache_write (don't re-write what we read)."""

    skip_cache: bool
    """If True, bypass the semantic cache entirely for this query.
    Set via --no-cache on the CLI."""
