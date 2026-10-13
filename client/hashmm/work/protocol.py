"""Canonical Work protocol shared by Chat, long tasks, desktop and App.

The protocol deliberately separates four facts which were historically mixed
inside free-form Agent messages:

* :class:`WorkSpec` describes what the user asked for.
* :class:`ActionRecord` describes an admitted executor operation.
* :class:`ObservationRecord` describes what the runtime actually observed.
* :class:`EvidenceReceipt` links a claim to verifiable evidence.

Only bounded, redacted projections belong in durable storage.  Model prose is
never promoted to an observation or evidence receipt.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


PROTOCOL_SCHEMA = "hashmm.work-protocol.v4"
WORK_SPEC_SCHEMA = "hashmm.work-spec.v1"
ACTION_SCHEMA = "hashmm.work-action.v1"
OBSERVATION_SCHEMA = "hashmm.work-observation.v1"
EVIDENCE_RECEIPT_SCHEMA = "hashmm.evidence-receipt.v1"
ARTIFACT_REF_SCHEMA = "hashmm.artifact-ref.v1"

_SECRET = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+)"
)


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    text = _SECRET.sub("[REDACTED]", text)
    return text[:limit]


def _identifier(value: Any, limit: int = 120) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or "").strip())[:limit]


def canonical_hash(value: Any, length: int = 64) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    revision: int = 0
    content_hash: str = ""
    media_type: str = "application/octet-stream"
    locator: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def public(self) -> dict[str, Any]:
        locator = {
            _identifier(key, 64): _text(value, 240)
            for key, value in dict(self.locator or {}).items()
            if key in {"store", "bucket", "object_id", "relative_path", "url_id"}
        }
        return {
            "schema": ARTIFACT_REF_SCHEMA,
            "artifact_id": _identifier(self.artifact_id, 160),
            "revision": max(0, int(self.revision or 0)),
            "content_hash": re.sub(
                r"[^a-fA-F0-9]", "", str(self.content_hash or "")
            )[:64].lower(),
            "media_type": _text(self.media_type, 120),
            "locator": locator,
        }


@dataclass(frozen=True)
class WorkSpec:
    goal: str
    deliverable: str = ""
    criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    permission_mode: str = "ask"
    project_id: str = ""

    def public(self) -> dict[str, Any]:
        mode = str(self.permission_mode or "ask").lower()
        if mode not in {"ask", "read_only", "trusted_workspace"}:
            mode = "ask"
        body = {
            "schema": WORK_SPEC_SCHEMA,
            "goal": _text(self.goal, 2_000),
            "deliverable": _text(self.deliverable, 1_200),
            "criteria": [_text(item, 300) for item in self.criteria[:24] if _text(item, 300)],
            "constraints": [
                _text(item, 300) for item in self.constraints[:24] if _text(item, 300)
            ],
            "permission_mode": mode,
            "project_id": _identifier(self.project_id, 160),
        }
        if not body["goal"]:
            raise ValueError("work spec requires a goal")
        body["fingerprint"] = canonical_hash(body)
        return body


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    kind: str
    summary: str
    actor: str
    status: str = "admitted"
    idempotency_key: str = ""
    side_effect: str = "none"
    approval_id: str = ""
    artifact_refs: tuple[ArtifactRef, ...] = ()

    def public(self) -> dict[str, Any]:
        side_effect = str(self.side_effect or "unknown").lower()
        if side_effect not in {
            "none", "read", "write", "execute", "network", "external", "unknown",
        }:
            side_effect = "unknown"
        body = {
            "schema": ACTION_SCHEMA,
            "action_id": _identifier(self.action_id),
            "kind": _identifier(self.kind, 64),
            "summary": _text(self.summary, 500),
            "actor": _identifier(self.actor, 120),
            "status": _identifier(self.status, 32),
            "idempotency_key": _identifier(self.idempotency_key, 160),
            "side_effect": side_effect,
            "approval_id": _identifier(self.approval_id, 120),
            "artifacts": [item.public() for item in self.artifact_refs[:24]],
        }
        body["fingerprint"] = canonical_hash(body)
        return body


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    action_id: str
    source: str
    summary: str
    status: str
    measured: Mapping[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        measured = {
            _identifier(key, 64): value
            for key, value in dict(self.measured or {}).items()
            if isinstance(value, (bool, int, float)) and len(str(key)) <= 64
        }
        body = {
            "schema": OBSERVATION_SCHEMA,
            "observation_id": _identifier(self.observation_id),
            "action_id": _identifier(self.action_id),
            "source": _identifier(self.source, 96),
            "summary": _text(self.summary, 500),
            "status": _identifier(self.status, 32),
            "measured": measured,
        }
        body["fingerprint"] = canonical_hash(body)
        return body


@dataclass(frozen=True)
class EvidenceReceipt:
    receipt_id: str
    claim: str
    observation_ids: tuple[str, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    verification: str = "pending"
    verifier: str = "runtime"

    def public(self) -> dict[str, Any]:
        verification = str(self.verification or "pending").lower()
        if verification not in {
            "pending", "reported", "verified", "failed", "stale", "missing",
        }:
            verification = "pending"
        body = {
            "schema": EVIDENCE_RECEIPT_SCHEMA,
            "receipt_id": _identifier(self.receipt_id),
            "claim": _text(self.claim, 500),
            "observation_ids": [
                _identifier(item) for item in self.observation_ids[:32] if _identifier(item)
            ],
            "artifacts": [item.public() for item in self.artifact_refs[:24]],
            "verification": verification,
            "verifier": _identifier(self.verifier, 96),
        }
        body["fingerprint"] = canonical_hash(body)
        return body


def build_work_envelope(
    *,
    owner_id: str,
    run_id: str,
    spec: WorkSpec,
    actions: Iterable[ActionRecord] = (),
    observations: Iterable[ObservationRecord] = (),
    evidence: Iterable[EvidenceReceipt] = (),
) -> dict[str, Any]:
    """Build a portable public envelope; source bodies and secrets stay out."""
    owner = _identifier(owner_id, 160)
    run = _identifier(run_id, 120)
    if not owner or not run:
        raise ValueError("work envelope requires owner_id and run_id")
    body = {
        "schema": PROTOCOL_SCHEMA,
        "owner_bound": True,
        "owner_ref": canonical_hash(owner, 24),
        "run_id": run,
        "spec": spec.public(),
        "actions": [item.public() for item in list(actions)[:128]],
        "observations": [item.public() for item in list(observations)[:128]],
        "evidence": [item.public() for item in list(evidence)[:128]],
        "source_bodies_included": False,
        "private_reasoning_included": False,
    }
    body["fingerprint"] = canonical_hash(body)
    return body
