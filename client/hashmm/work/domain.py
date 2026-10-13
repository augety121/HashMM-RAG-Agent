"""Canonical, provider-independent Work domain for HashMM V519+.

This module is deliberately pure.  It does not call an LLM, a tool, Supabase
or SQLite, so the state machine can be used consistently by the server,
desktop cache and App cache without allowing a client to claim execution
success.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping


WORKSPACE_SCHEMA = "hashmm.workspace.v2"
RUN_SCHEMA = "hashmm.workspace-run.v2"
CONTRACT_SCHEMA = "hashmm.outcome-contract.v2"
COMMAND_SCHEMA = "hashmm.workspace-command.v2"
EVIDENCE_SCHEMA = "hashmm.evidence-graph.v2"


class WorkState(StrEnum):
    """User-facing lifecycle independent of a concrete executor."""

    DRAFT = "draft"
    PLANNED = "planned"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    REVIEW = "review"
    ACCEPTED = "accepted"
    CHANGE_REQUESTED = "change_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    OBSERVED = "observed"


_LEGACY_TO_CANONICAL: dict[str, WorkState] = {
    "queued": WorkState.READY,
    "running": WorkState.RUNNING,
    "waiting_input": WorkState.WAITING_USER,
    "waiting_approval": WorkState.WAITING_APPROVAL,
    "blocked": WorkState.BLOCKED,
    "delivered": WorkState.REVIEW,
    "completed": WorkState.COMPLETED,
    "failed": WorkState.FAILED,
    "cancelled": WorkState.CANCELLED,
    "interrupted": WorkState.INTERRUPTED,
    # Imported historical rows are not upgraded to verified completion.
    "observed": WorkState.OBSERVED,
}
_CANONICAL_TO_LEGACY: dict[WorkState, str] = {
    WorkState.DRAFT: "queued",
    WorkState.PLANNED: "queued",
    WorkState.READY: "queued",
    WorkState.RUNNING: "running",
    WorkState.WAITING_USER: "waiting_input",
    WorkState.WAITING_APPROVAL: "waiting_approval",
    WorkState.BLOCKED: "blocked",
    WorkState.REVIEW: "delivered",
    WorkState.ACCEPTED: "completed",
    WorkState.CHANGE_REQUESTED: "waiting_input",
    WorkState.COMPLETED: "completed",
    WorkState.FAILED: "failed",
    WorkState.CANCELLED: "cancelled",
    WorkState.INTERRUPTED: "interrupted",
    WorkState.OBSERVED: "observed",
}

# Domain transitions describe allowed lifecycle movement.  The public command
# service still applies stronger executor/evidence checks before persisting it.
_TRANSITIONS: dict[WorkState, frozenset[WorkState]] = {
    WorkState.DRAFT: frozenset({WorkState.PLANNED, WorkState.CANCELLED}),
    WorkState.PLANNED: frozenset({WorkState.READY, WorkState.CANCELLED}),
    WorkState.READY: frozenset({
        WorkState.RUNNING, WorkState.WAITING_APPROVAL, WorkState.BLOCKED,
        WorkState.CANCELLED,
    }),
    WorkState.RUNNING: frozenset({
        WorkState.WAITING_USER, WorkState.WAITING_APPROVAL, WorkState.BLOCKED,
        WorkState.REVIEW, WorkState.FAILED, WorkState.CANCELLED,
        WorkState.INTERRUPTED,
    }),
    WorkState.WAITING_USER: frozenset({
        WorkState.RUNNING, WorkState.CANCELLED, WorkState.INTERRUPTED,
    }),
    WorkState.WAITING_APPROVAL: frozenset({
        WorkState.RUNNING, WorkState.CANCELLED, WorkState.INTERRUPTED,
    }),
    WorkState.BLOCKED: frozenset({
        WorkState.RUNNING, WorkState.CANCELLED, WorkState.INTERRUPTED,
    }),
    WorkState.REVIEW: frozenset({
        WorkState.ACCEPTED, WorkState.CHANGE_REQUESTED, WorkState.CANCELLED,
    }),
    WorkState.ACCEPTED: frozenset({WorkState.COMPLETED}),
    WorkState.CHANGE_REQUESTED: frozenset({
        WorkState.RUNNING, WorkState.CANCELLED,
    }),
    WorkState.FAILED: frozenset({WorkState.READY, WorkState.CANCELLED}),
    WorkState.INTERRUPTED: frozenset({WorkState.READY, WorkState.CANCELLED}),
    WorkState.COMPLETED: frozenset({WorkState.CHANGE_REQUESTED}),
    WorkState.CANCELLED: frozenset(),
    WorkState.OBSERVED: frozenset(),
}


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def canonical_state(status: Any) -> WorkState:
    """Translate a durable v1 status without silently claiming completion."""
    text = str(status or "").strip().lower()
    if text in _LEGACY_TO_CANONICAL:
        return _LEGACY_TO_CANONICAL[text]
    try:
        return WorkState(text)
    except ValueError:
        return WorkState.BLOCKED


def legacy_status(state: WorkState | str) -> str:
    try:
        parsed = state if isinstance(state, WorkState) else WorkState(str(state))
    except ValueError:
        return "blocked"
    return _CANONICAL_TO_LEGACY[parsed]


def transition_allowed(source: WorkState | str, target: WorkState | str) -> bool:
    try:
        current = source if isinstance(source, WorkState) else WorkState(str(source))
        desired = target if isinstance(target, WorkState) else WorkState(str(target))
    except ValueError:
        return False
    return desired in _TRANSITIONS.get(current, frozenset())


def available_transitions(source: WorkState | str) -> tuple[str, ...]:
    try:
        current = source if isinstance(source, WorkState) else WorkState(str(source))
    except ValueError:
        return ()
    return tuple(item.value for item in sorted(
        _TRANSITIONS.get(current, frozenset()), key=lambda state: state.value,
    ))


@dataclass(frozen=True)
class SuccessCriterion:
    id: str
    label: str
    required: bool = True
    status: str = "open"

    @classmethod
    def from_value(cls, value: Any, index: int) -> "SuccessCriterion":
        if isinstance(value, Mapping):
            return cls(
                id=_text(
                    value.get("id") or value.get("check_id") or f"criterion-{index}",
                    96,
                ),
                label=_text(
                    value.get("label") or value.get("text") or value.get("goal"),
                    300,
                ),
                required=bool(value.get("required", True)),
                status=_text(value.get("status") or "open", 32),
            )
        return cls(
            id=f"criterion-{index}",
            label=_text(value, 300),
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "required": self.required,
            "status": self.status,
        }


@dataclass(frozen=True)
class OutcomeContract:
    goal: str
    deliverable: str
    criteria: tuple[SuccessCriterion, ...]
    permission_mode: str
    source: str
    user_confirmed: bool

    @classmethod
    def from_values(
        cls,
        *,
        goal: Any,
        deliverable: Any = "",
        criteria: Iterable[Any] | None = None,
        permission_mode: Any = "ask",
        source: str = "user",
        user_confirmed: bool = False,
    ) -> "OutcomeContract":
        clean_goal = _text(goal, 2_000)
        if not clean_goal:
            raise ValueError("outcome contract requires a goal")
        mode = str(permission_mode or "ask").strip().lower()
        if mode not in {"ask", "read_only", "trusted_workspace"}:
            mode = "ask"
        normalized = tuple(
            item for item in (
                SuccessCriterion.from_value(value, index)
                for index, value in enumerate(list(criteria or [])[:24], start=1)
            )
            if item.label
        )
        return cls(
            goal=clean_goal,
            deliverable=_text(deliverable, 1_200),
            criteria=normalized,
            permission_mode=mode,
            source=_text(source, 32) or "user",
            user_confirmed=bool(user_confirmed),
        )

    @classmethod
    def from_run(cls, run: Mapping[str, Any]) -> "OutcomeContract":
        snapshot = run.get("snapshot") if isinstance(run.get("snapshot"), Mapping) else {}
        manifest = (
            snapshot.get("run_manifest")
            if isinstance(snapshot.get("run_manifest"), Mapping) else {}
        )
        task = (
            manifest.get("task_contract")
            if isinstance(manifest.get("task_contract"), Mapping) else {}
        )
        operating = (
            manifest.get("operating_contract")
            if isinstance(manifest.get("operating_contract"), Mapping) else {}
        )
        goal = task.get("goal") or manifest.get("goal") or run.get("title") or "一项工作"
        deliverable = (
            task.get("deliverable")
            or task.get("expected_artifact")
            or snapshot.get("deliverable")
            or ""
        )
        criteria = task.get("success_criteria") or snapshot.get("success_criteria") or []
        method = (
            operating.get("work_method")
            if isinstance(operating.get("work_method"), Mapping) else {}
        )
        return cls.from_values(
            goal=goal,
            deliverable=deliverable,
            criteria=criteria if isinstance(criteria, list) else [],
            permission_mode=(
                operating.get("permission_mode")
                or method.get("permission_mode")
                or snapshot.get("permission_mode")
                or "ask"
            ),
            source="runtime",
            user_confirmed=bool(task.get("user_confirmed")),
        )

    def public(self) -> dict[str, Any]:
        return {
            "schema": CONTRACT_SCHEMA,
            "goal": self.goal,
            "deliverable": self.deliverable,
            "criteria": [item.public() for item in self.criteria],
            "permission_mode": self.permission_mode,
            "source": self.source,
            "user_confirmed": self.user_confirmed,
        }
