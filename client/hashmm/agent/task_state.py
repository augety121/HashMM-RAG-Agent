"""Single public task state machine shared by Chat, loops and teams."""
from __future__ import annotations

from typing import Any

STATUSES = frozenset({
    "draft", "created", "queued", "running", "waiting_input", "waiting_approval", "paused",
    "retrying", "verifying", "blocked",
    "observed", "delivered", "completed", "completed_with_limits", "failed", "cancelled", "interrupted",
})

_TERMINAL = frozenset({"observed", "completed", "completed_with_limits", "failed", "cancelled", "interrupted"})
_TRANSITIONS = {
    "draft": frozenset({"draft", "queued", "running", "cancelled"}),
    "created": frozenset({"created", "queued", "running", "waiting_input", "waiting_approval", "cancelled", "failed"}),
    "queued": frozenset({"queued", "running", "paused", "retrying", "waiting_input", "waiting_approval", "blocked", "delivered", "completed", "cancelled", "failed", "interrupted"}),
    "running": frozenset({"running", "paused", "retrying", "verifying", "waiting_input", "waiting_approval", "blocked", "delivered", "completed", "failed", "cancelled", "interrupted"}),
    "waiting_input": frozenset({"waiting_input", "running", "paused", "retrying", "cancelled", "failed"}),
    "waiting_approval": frozenset({"waiting_approval", "running", "paused", "retrying", "cancelled", "failed"}),
    "paused": frozenset({"paused", "queued", "running", "retrying", "cancelled", "failed", "interrupted"}),
    "retrying": frozenset({"retrying", "queued", "running", "waiting_input", "waiting_approval", "failed", "cancelled"}),
    "verifying": frozenset({"verifying", "running", "delivered", "completed", "completed_with_limits", "waiting_input", "failed", "cancelled"}),
    "blocked": frozenset({"blocked", "running", "paused", "retrying", "cancelled", "failed"}),
    "delivered": frozenset({"delivered", "verifying", "completed", "retrying", "blocked", "failed"}),
    "observed": frozenset({"observed"}),
    "completed": frozenset({"completed"}),
    "completed_with_limits": frozenset({"completed_with_limits"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
    "interrupted": frozenset({"interrupted"}),
}


def can_transition(current: str, requested: str) -> bool:
    current = str(current or "queued").strip().lower()
    requested = str(requested or current).strip().lower()
    return requested in _TRANSITIONS.get(current, frozenset())


def transition(current: str, requested: str) -> str:
    target = str(requested or current).strip().lower()
    if target not in STATUSES:
        raise ValueError("unknown_task_state")
    if not can_transition(current, target):
        raise ValueError(f"invalid_task_transition:{current}->{target}")
    return target


def public_contract(current: str) -> dict[str, Any]:
    state = str(current or "queued").strip().lower()
    canonical = {
        "created": "draft",
        "retrying": "running",
        "delivered": "verifying",
        "observed": "completed_with_limits",
    }.get(state, state)
    return {
        "schema": "hashmm.task-state.v1",
        "state": state if state in STATUSES else "queued",
        "canonical_state": canonical if canonical in STATUSES else "queued",
        "terminal": state in _TERMINAL,
        "allowed_next": sorted(_TRANSITIONS.get(state, frozenset())),
        "requires_user": state in {"waiting_input", "waiting_approval", "blocked", "delivered"},
    }


__all__ = ["STATUSES", "can_transition", "transition", "public_contract"]
