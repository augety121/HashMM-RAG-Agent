"""Working memory — short-term, in-process state.

Scope: a single agent invocation OR a multi-turn session held in memory.
Lifetime: process / chat session. Never persisted.

Three pieces of state:
  - turn_history    : recent (query, intent, strategy, answer) tuples
  - recent_chunks   : chunk_ids seen lately, for cross-turn dedup
  - failed_queries  : queries that quality-failed; skip retrying near-duplicates

The working memory is intentionally small and bounded. Longer-term recall
lives in episodic (cross-session) and semantic_cache (cross-user).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class TurnRecord:
    """One conversation turn, lightweight (≤ 1 KB)."""
    turn_idx: int
    ts: float
    query: str
    intent: str
    strategy: str
    n_results: int
    quality_ok: bool
    answer_preview: str  # first 200 chars

    @classmethod
    def from_state(cls, state: dict, turn_idx: int) -> "TurnRecord":
        ans = state.get("answer", "") or ""
        return cls(
            turn_idx=turn_idx,
            ts=time.time(),
            query=state.get("query", ""),
            intent=state.get("intent", "?"),
            strategy=state.get("strategy", "?"),
            n_results=len(state.get("retrieved", []) or []),
            quality_ok=bool(state.get("quality_ok", False)),
            answer_preview=ans[:200],
        )


class WorkingMemory:
    """Bounded in-memory store. Cheap, thread-unsafe (single agent at a time).

    Args:
        max_turns: ring-buffer cap for turn history.
        max_recent_chunks: cap for chunk_id dedup set.
        max_failed: cap for failed_queries history.
    """

    def __init__(
        self,
        max_turns: int = 10,
        max_recent_chunks: int = 200,
        max_failed: int = 20,
    ):
        self._turns: deque[TurnRecord] = deque(maxlen=max_turns)
        self._recent_chunks: deque[str] = deque(maxlen=max_recent_chunks)
        self._failed_queries: deque[str] = deque(maxlen=max_failed)

    # ── Turn history ──────────────────────────────────────────────────

    def record_turn(self, state: dict) -> TurnRecord:
        rec = TurnRecord.from_state(state, turn_idx=len(self._turns))
        self._turns.append(rec)
        # Track chunk ids seen this turn
        for r in (state.get("retrieved") or []):
            cid = r.get("chunk_id")
            if cid:
                self._recent_chunks.append(cid)
        if not rec.quality_ok and rec.query:
            self._failed_queries.append(rec.query.lower().strip())
        return rec

    def recent_turns(self, n: int = 5) -> list[TurnRecord]:
        """Most recent n turns, newest last."""
        return list(self._turns)[-n:]

    def has_seen_chunk(self, chunk_id: str) -> bool:
        return chunk_id in self._recent_chunks

    def previously_failed(self, query: str) -> bool:
        """Heuristic: exact-match lowercase query in failed log."""
        return (query or "").lower().strip() in self._failed_queries

    def clear(self) -> None:
        self._turns.clear()
        self._recent_chunks.clear()
        self._failed_queries.clear()

    def __len__(self) -> int:
        return len(self._turns)

    def summary(self) -> str:
        """Human-readable one-liner for debug/trace."""
        if not self._turns:
            return "WorkingMemory: empty"
        recent_intents = [t.intent for t in self._turns]
        return (
            f"WorkingMemory: {len(self._turns)} turns, "
            f"intents={recent_intents}, "
            f"recent_chunks={len(self._recent_chunks)}, "
            f"failed={len(self._failed_queries)}"
        )
