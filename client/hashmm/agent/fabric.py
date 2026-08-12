"""Typed, least-authority multi-Agent delegation.

This module is the compatibility layer between older parallel search helpers
and the owner-bound Agent mesh.  Delegation is admitted only when its expected
utility exceeds deterministic coordination cost; a child scope is always an
intersection of the parent scope and role tools.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from hashmm.agent.execution_scope import derive_child_scope, public_scope


FABRIC_SCHEMA = "hashmm.agent-fabric.v1"
DELEGATION_SCHEMA = "hashmm.delegation-contract.v1"


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _id(value: Any, limit: int = 120) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or "").strip())[:limit]


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class DelegationContract:
    delegation_id: str
    role: str
    objective: str
    deliverable: str
    success_criteria: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    input_refs: tuple[str, ...] = ()
    expected_parallel_gain: float = 0.0
    coordination_cost: float = 0.0
    overlap_risk: float = 0.0

    def public(self, child_scope: Mapping[str, Any] | None) -> dict[str, Any]:
        benefit = max(0.0, min(1.0, float(self.expected_parallel_gain)))
        cost = max(0.0, min(1.0, float(self.coordination_cost)))
        overlap = max(0.0, min(1.0, float(self.overlap_risk)))
        utility = round(benefit - cost - overlap * 0.5, 3)
        body = {
            "schema": DELEGATION_SCHEMA,
            "delegation_id": _id(self.delegation_id),
            "role": _id(self.role, 64),
            "objective": _text(self.objective, 800),
            "deliverable": _text(self.deliverable, 400),
            "success_criteria": [
                _text(item, 240) for item in self.success_criteria[:16] if _text(item, 240)
            ],
            "allowed_tools": [_id(item) for item in self.allowed_tools[:80] if _id(item)],
            "input_refs": [_id(item, 160) for item in self.input_refs[:32] if _id(item, 160)],
            "utility": utility,
            "admitted": bool(child_scope and utility > 0),
            "rejection_reason": (
                "" if child_scope and utility > 0
                else "delegation does not beat coordination cost or lacks authority"
            ),
            "execution_scope": public_scope(child_scope),
            "private_reasoning_included": False,
        }
        body["fingerprint"] = _fingerprint(body)
        return body


def admit_delegation(
    parent_scope: Mapping[str, Any] | None,
    contract: DelegationContract,
    *,
    workspace_branch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic admission; never trust a model-authored scope."""
    child = derive_child_scope(
        dict(parent_scope) if isinstance(parent_scope, Mapping) else None,
        role=contract.role,
        role_tools=contract.allowed_tools,
        workspace_branch=dict(workspace_branch) if isinstance(workspace_branch, Mapping) else None,
    )
    return contract.public(child)


def build_delegation_plan(
    *,
    parent_scope: Mapping[str, Any] | None,
    goal: str,
    roles: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(roles)[:8]
    contracts: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    for index, row in enumerate(rows, start=1):
        task = _text(row.get("task") or goal, 800)
        normalized_task = task.lower()
        overlap = 0.95 if normalized_task and normalized_task in seen_tasks else 0.05
        if normalized_task:
            seen_tasks.add(normalized_task)
        benefit = min(0.95, 0.35 + len(rows) * 0.12)
        cost = min(0.8, 0.12 + len(rows) * 0.05)
        contract = DelegationContract(
            delegation_id=_id(row.get("id") or f"delegation-{index}"),
            role=_id(row.get("role") or "specialist", 64),
            objective=task,
            deliverable=_text(row.get("deliverable") or "bounded evidence-backed finding", 400),
            success_criteria=tuple(row.get("success_criteria") or ("cite observations",)),
            allowed_tools=tuple(row.get("allowed_tools") or parent_scope.get("allowed_tools", [])
                                if isinstance(parent_scope, Mapping) else ()),
            input_refs=tuple(row.get("input_refs") or ()),
            expected_parallel_gain=benefit,
            coordination_cost=cost,
            overlap_risk=overlap,
        )
        contracts.append(admit_delegation(parent_scope, contract))
    return {
        "schema": FABRIC_SCHEMA,
        "goal": _text(goal, 800),
        "delegations": contracts,
        "admitted_count": sum(1 for item in contracts if item["admitted"]),
        "rejected_count": sum(1 for item in contracts if not item["admitted"]),
        "private_reasoning_included": False,
    }
