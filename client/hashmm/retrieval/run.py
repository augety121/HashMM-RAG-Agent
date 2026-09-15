"""Inspectable retrieval-run records for Chat, desktop and App.

This is an execution trace, not an LLM self-evaluation.  It records the actual
queries, result counts, filters and degradation events observed by the server.
Document allow-list values and retrieved text are deliberately excluded.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


RETRIEVAL_RUN_SCHEMA = "hashmm.retrieval-run.v1"


@dataclass
class RetrievalRun:
    query: str
    requested_mode: str = "mix"
    requested_top_k: int = 5
    acl_scoped: bool = False
    run_id: str = field(default_factory=lambda: f"ret_{uuid.uuid4().hex[:16]}")
    started_at: float = field(default_factory=time.time)
    _started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    resolved_mode: str = ""
    route_reason: str = ""
    route_hops: int = 1
    attempts: list[dict[str, Any]] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    expansions: list[dict[str, Any]] = field(default_factory=list)
    degradations: list[dict[str, str]] = field(default_factory=list)

    def route(self, mode: str, *, reason: str = "", hops: int = 1) -> None:
        self.resolved_mode = str(mode or self.requested_mode)[:40]
        self.route_reason = str(reason or "")[:240]
        self.route_hops = max(1, min(int(hops or 1), 8))

    def attempt(self, query: str, results: Any, *, stage: str = "search",
                selected: bool = False) -> None:
        items = list(results or [])
        scores = []
        for item in items:
            try:
                scores.append(float(getattr(item, "score", 0.0) if not isinstance(item, dict)
                                    else item.get("score", 0.0)))
            except (TypeError, ValueError):
                continue
        self.attempts.append({
            "stage": str(stage or "search")[:60],
            "query": str(query or "")[:500],
            "result_count": len(items),
            "top_score": round(max(scores), 6) if scores else None,
            "selected": bool(selected),
        })

    def select_attempt(self, index: int) -> None:
        for i, attempt in enumerate(self.attempts):
            attempt["selected"] = i == index

    def filtered(self, stage: str, before: int, after: int, *, reason: str = "") -> None:
        self.filters.append({
            "stage": str(stage or "filter")[:60],
            "before": max(0, int(before or 0)),
            "after": max(0, int(after or 0)),
            "removed": max(0, int(before or 0) - int(after or 0)),
            "reason": str(reason or "")[:180],
        })

    def degraded(self, stage: str, reason: str) -> None:
        item = {"stage": str(stage or "unknown")[:60], "reason": str(reason or "")[:240]}
        if item not in self.degradations:
            self.degradations.append(item)

    def expanded(self, stage: str, before: int, after: int, *, considered: int = 0,
                 skipped_missing: int = 0, skipped_forbidden: int = 0) -> None:
        """Record deterministic evidence expansion without storing graph data."""
        before_n = max(0, int(before or 0))
        after_n = max(0, int(after or 0))
        self.expansions.append({
            "stage": str(stage or "expansion")[:60],
            "before": before_n,
            "after": after_n,
            "added": max(0, after_n - before_n),
            "considered": max(0, int(considered or 0)),
            "skipped_missing": max(0, int(skipped_missing or 0)),
            "skipped_forbidden": max(0, int(skipped_forbidden or 0)),
        })

    def finish(self, *, evidence_count: int = 0, status: str | None = None,
               total_candidates: int = 0) -> dict[str, Any]:
        count = max(0, int(evidence_count or 0))
        if status is None:
            status = "degraded" if self.degradations else ("completed" if count else "empty")
        allowed_status = {"completed", "empty", "degraded", "failed", "skipped"}
        status = status if status in allowed_status else "failed"
        selected = next((item for item in self.attempts if item.get("selected")), None)
        return {
            "schema": RETRIEVAL_RUN_SCHEMA,
            "run_id": self.run_id,
            "status": status,
            "requested_mode": str(self.requested_mode or "mix")[:40],
            "resolved_mode": self.resolved_mode or str(self.requested_mode or "mix")[:40],
            "route": {"reason": self.route_reason, "hops": self.route_hops},
            "requested_top_k": max(1, min(int(self.requested_top_k or 5), 100)),
            "acl_scoped": bool(self.acl_scoped),
            "attempts": [dict(item) for item in self.attempts[:12]],
            "selected_query": str((selected or {}).get("query") or "")[:500],
            "filters": [dict(item) for item in self.filters[:16]],
            "expansions": [dict(item) for item in self.expansions[:12]],
            "degradations": [dict(item) for item in self.degradations[:16]],
            "evidence_count": count,
            "total_candidates": max(count, int(total_candidates or 0)),
            "elapsed_ms": max(0, int((time.monotonic() - self._started_monotonic) * 1000)),
            "started_at": round(float(self.started_at), 3),
            "model_self_grade": False,
        }
