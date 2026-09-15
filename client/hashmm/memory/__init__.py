"""Memory module (M5): three-layer state for the HashMM-RAG agent.

Layers:
    WorkingMemory   — in-process short-term state (deques + sets)
    SessionStore    — durable conversation history (SQLite WAL)
    SemanticCache   — cross-session answer cache (hash-then-cosine)

Public API:
    from hashmm.memory import WorkingMemory, SessionStore, SemanticCache
"""

from hashmm.memory.episodic import SessionStore
from hashmm.memory.metrics import (
    render_episodic_stats,
    render_semcache_stats,
    render_top_queries,
)
from hashmm.memory.semantic_cache import SemanticCache
from hashmm.memory.working import TurnRecord, WorkingMemory

__all__ = [
    "WorkingMemory",
    "TurnRecord",
    "SessionStore",
    "SemanticCache",
    "render_episodic_stats",
    "render_semcache_stats",
    "render_top_queries",
]
