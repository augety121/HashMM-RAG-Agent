"""Deterministic assurance projection for long-running user work.

V430-V438 are deliberately implemented as one connected contract instead of
nine disconnected dashboards.  The projection reads authoritative runtime
state and exposes why work can resume, continue or be delivered.  It never
executes tools, widens permissions, or treats model prose as proof.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "hashmm.work-assurance.v1"
VERSION_CHAIN = {
    "recovery": "V430",
    "context": "V431",
    "evidence": "V432",
    "artifacts": "V433",
    "delegation": "V434",
    "authority": "V435",
    "provider": "V436",
    "sync": "V437",
    "delivery": "V438",
}
_PASS = {"passed", "verified", "ready", "completed", "done", "accepted"}
_FAIL = {"failed", "blocked", "invalid", "missing", "stale", "rejected"}
_ACTIVE = {"queued", "running", "waiting_input", "waiting_approval", "blocked", "interrupted"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 240) -> str:
    result = str(value or "").replace("\x00", "").strip()
    return result if len(result) <= limit else result[: limit - 1] + "…"


def _number(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _status(value: Any) -> str:
    raw = _text(value, 40).lower()
    if raw in _PASS:
        return "passed"
    if raw in _FAIL:
        return "failed"
    if raw in {"pending", "queued", "running", "unknown", "not_evaluable"}:
        return "pending"
    return "pending"


def _fingerprint(value: Any) -> str:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _context_capsule(snapshot: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(snapshot.get("context_capsule"))
    if direct:
        return direct
    lifecycle = _dict(manifest.get("context_lifecycle"))
    return _dict(lifecycle.get("context_capsule"))


def _provider_contract(snapshot: dict[str, Any], capsule: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(capsule.get("provider_contract"))
    if direct:
        return direct
    return _dict(snapshot.get("provider_contract"))


def _recovery_projection(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    checkpoints: list[dict[str, Any]] = []
    seen: set[str] = set()
    lifecycle = _dict(manifest.get("context_lifecycle"))
    candidates = _list(lifecycle.get("checkpoints"))
    latest = _dict(lifecycle.get("latest_checkpoint"))
    if latest:
        candidates.append(latest)
    lifecycle_checkpoint = _text(lifecycle.get("checkpoint_id"), 160)
    if lifecycle_checkpoint:
        candidates.append({
            "checkpoint_id": lifecycle_checkpoint,
            "created_at": lifecycle.get("checkpoint_created_at"),
            "reason": lifecycle.get("checkpoint_reason") or "context_lifecycle",
            "restorable": True,
        })
    for event in events:
        if _text(event.get("type"), 60) not in {
            "checkpoint", "context_checkpoint", "execution_checkpoint",
        }:
            continue
        payload = _dict(event.get("payload"))
        candidates.append({
            "checkpoint_id": payload.get("checkpoint_id") or event.get("id"),
            "created_at": event.get("created_at"),
            "reason": payload.get("reason") or event.get("summary"),
            "restorable": payload.get("restorable", True),
        })
    for raw in reversed(candidates):
        item = _dict(raw)
        checkpoint_id = _text(item.get("checkpoint_id") or item.get("id"), 160)
        if not checkpoint_id or checkpoint_id in seen:
            continue
        seen.add(checkpoint_id)
        checkpoints.append({
            "id": checkpoint_id,
            "created_at": float(item.get("created_at") or 0),
            "reason": _text(item.get("reason"), 240),
            "restorable": item.get("restorable") is not False,
        })
        if len(checkpoints) >= 8:
            break
    latest_checkpoint = checkpoints[0] if checkpoints else None
    active = _text(run.get("status"), 40) in _ACTIVE
    return {
        "version": VERSION_CHAIN["recovery"],
        "status": "ready" if latest_checkpoint and latest_checkpoint["restorable"] else "unavailable",
        "can_resume": bool(active and latest_checkpoint and latest_checkpoint["restorable"]),
        "latest_checkpoint": latest_checkpoint,
        "checkpoint_count": len(checkpoints),
        "automatic_rollback": False,
    }


def _context_projection(capsule: dict[str, Any]) -> dict[str, Any]:
    budgets = _dict(capsule.get("budgets"))
    metrics = _dict(capsule.get("metrics"))
    used = _number(
        metrics.get("estimated_tokens")
        or metrics.get("used_tokens")
        or capsule.get("estimated_tokens")
    )
    limit = _number(
        budgets.get("max_input_tokens")
        or budgets.get("input_tokens")
        or capsule.get("max_input_tokens")
    )
    remaining = max(0, limit - used) if limit else 0
    pressure = round(used / limit, 4) if limit else None
    compacted = bool(
        capsule.get("compacted")
        or metrics.get("compaction_count")
        or capsule.get("checkpoint_id")
    )
    if limit and used > limit:
        state = "over_budget"
    elif pressure is not None and pressure >= 0.85:
        state = "compact_soon"
    elif limit:
        state = "within_budget"
    else:
        state = "not_reported"
    return {
        "version": VERSION_CHAIN["context"],
        "status": state,
        "estimated_tokens": used,
        "max_input_tokens": limit,
        "remaining_tokens": remaining,
        "pressure": pressure,
        "compaction_observed": compacted,
        "fingerprint": _text(capsule.get("fingerprint"), 96),
        "source_bodies_exposed": False,
    }


def _criteria_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    contract = _dict(manifest.get("task_contract"))
    gate = _dict(manifest.get("completion_gate"))
    gate_criteria = {
        _text(item.get("check_id") or item.get("id"), 120): _dict(item)
        for item in _list(gate.get("criteria"))
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(contract.get("success_criteria"))[:40]):
        item = _dict(raw)
        criterion_id = _text(item.get("id") or item.get("check_id"), 120) or f"criterion-{index + 1}"
        gate_item = gate_criteria.get(criterion_id, {})
        status = _status(gate_item.get("status") or item.get("status"))
        rows.append({
            "id": criterion_id,
            "label": _text(item.get("label") or item.get("name") or item.get("description"), 320)
            or f"验收条件 {index + 1}",
            "required": item.get("required") is not False,
            "status": status,
            "evidence_refs": [
                _text(ref, 160) for ref in _list(
                    gate_item.get("evidence_refs") or gate_item.get("evidence_ids")
                )[:20] if _text(ref, 160)
            ],
        })
    # Some runtime checks are injected by the completion gate rather than the
    # initial task contract. They are still authoritative and must stay visible.
    known = {item["id"] for item in rows}
    for criterion_id, item in gate_criteria.items():
        if not criterion_id or criterion_id in known:
            continue
        rows.append({
            "id": criterion_id,
            "label": _text(item.get("label") or item.get("name"), 320) or criterion_id,
            "required": item.get("required") is not False,
            "status": _status(item.get("status")),
            "evidence_refs": [
                _text(ref, 160) for ref in _list(
                    item.get("evidence_refs") or item.get("evidence_ids")
                )[:20] if _text(ref, 160)
            ],
        })
    required = [item for item in rows if item["required"]]
    passed = [item for item in required if item["status"] == "passed"]
    failed = [item for item in required if item["status"] == "failed"]
    return {
        "version": VERSION_CHAIN["evidence"],
        "status": "blocked" if failed else ("covered" if required and len(passed) == len(required) else "incomplete"),
        "criteria": rows,
        "required": len(required),
        "passed": len(passed),
        "failed": len(failed),
        "coverage": round(len(passed) / len(required), 4) if required else None,
        "model_prose_counts_as_evidence": False,
    }


def _artifact_projection(run: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    revisions = [_dict(item) for item in _list(run.get("artifact_revisions")) if isinstance(item, dict)]
    active_generation = _text(run.get("active_generation_id"), 160)
    stale_results = [
        _text(item.get("id"), 160) for item in results
        if _text(item.get("verification"), 40).lower() == "stale"
    ]
    missing_hashes = [
        _text(item.get("id") or item.get("artifact_id"), 160)
        for item in revisions
        if not _text(item.get("content_hash"), 80)
    ]
    return {
        "version": VERSION_CHAIN["artifacts"],
        "status": "stale" if stale_results else ("ready" if revisions or results else "none"),
        "active_generation_id": active_generation,
        "revision_count": len(revisions),
        "result_count": len(results),
        "stale_result_ids": stale_results,
        "missing_content_hash_ids": missing_hashes,
        "content_addressed": bool(revisions) and not missing_hashes,
    }


def _delegation_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    graph = _dict(snapshot.get("agent_mesh"))
    nodes = [_dict(item) for item in _list(graph.get("nodes")) if isinstance(item, dict)]
    status_counts: dict[str, int] = {}
    running = 0
    blockers: list[str] = []
    for item in nodes:
        state = _text(item.get("status"), 40).lower() or "pending"
        status_counts[state] = status_counts.get(state, 0) + 1
        if state in {"running", "leased"}:
            running += 1
        if state in {"failed", "blocked"}:
            blockers.append(_text(item.get("id"), 160))
    strategy = _dict(graph.get("strategy"))
    return {
        "version": VERSION_CHAIN["delegation"],
        "status": "blocked" if blockers else ("active" if nodes else "not_used"),
        "strategy": _text(strategy.get("strategy") or graph.get("strategy"), 40),
        "agents": len(nodes),
        "running": running,
        "status_counts": status_counts,
        "blocked_node_ids": [item for item in blockers if item][:40],
        "parallel_net_benefit": strategy.get("parallel_net_benefit"),
        "estimated_rounds": (
            strategy.get("estimated_parallel_rounds")
            if _text(strategy.get("strategy"), 40) == "parallel"
            else strategy.get("estimated_serial_rounds")
        ),
        "shared_raw_context": False,
    }


def _authority_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    scope = _dict(manifest.get("execution_scope"))
    approval = _dict(manifest.get("approval_request"))
    receipts = [_dict(item) for item in _list(manifest.get("execution_receipts")) if isinstance(item, dict)]
    irreversible = [
        _text(item.get("receipt_id") or item.get("id"), 160)
        for item in receipts
        if item.get("reversible") is False
    ]
    pending_approval = bool(
        approval and _text(approval.get("status"), 40).lower()
        not in {"approved", "consumed", "rejected", "expired"}
    )
    return {
        "version": VERSION_CHAIN["authority"],
        "status": "approval_required" if pending_approval else "bounded",
        "scope_id": _text(scope.get("scope_id"), 160),
        "approval_mode": _text(scope.get("approval_mode"), 80) or "not_reported",
        "network_mode": _text(scope.get("network_mode"), 80) or "not_reported",
        "pending_approval": pending_approval,
        "receipt_count": len(receipts),
        "irreversible_receipt_ids": [item for item in irreversible if item][:40],
        "widens_scope": False,
    }


def _provider_projection(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {
            "version": VERSION_CHAIN["provider"],
            "status": "not_reported",
            "compatible": None,
            "provider_id": "",
            "missing": [],
        }
    missing = [
        _text(item, 120)
        for item in _list(contract.get("missing"))
        if _text(item, 120)
    ]
    if not missing:
        missing = [
            _text(_dict(item).get("field"), 120)
            for item in _list(contract.get("errors"))
            if _text(_dict(item).get("field"), 120)
        ]
    ok = contract.get("ok") is True and not missing
    provider = _dict(contract.get("provider"))
    return {
        "version": VERSION_CHAIN["provider"],
        "status": "compatible" if ok else "incompatible",
        "compatible": ok,
        "provider_id": _text(provider.get("id") or provider.get("provider"), 120),
        "missing": missing[:40],
        "contract": _text(contract.get("schema"), 80),
    }


def _sync_projection(run: dict[str, Any]) -> dict[str, Any]:
    revision = _number(run.get("revision"))
    event_cursor = _number(run.get("event_cursor"))
    change_cursor = _number(run.get("change_cursor"))
    identity = {
        "run_id": _text(run.get("id"), 160),
        "revision": revision,
        "event_cursor": event_cursor,
        "change_cursor": change_cursor,
        "generation": _text(run.get("active_generation_id"), 160),
    }
    return {
        "version": VERSION_CHAIN["sync"],
        "status": "versioned" if revision > 0 else "not_versioned",
        **identity,
        "fingerprint": _fingerprint(identity),
        "owner_bound": bool(_text(run.get("user_id"), 160)),
        "offline_mutations_are_authoritative": False,
    }


def build_work_assurance(
    run: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    *,
    results: list[dict[str, Any]] | None = None,
    completion_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the V430-V438 assurance chain from bounded runtime facts."""
    snapshot = _dict(run.get("snapshot"))
    manifest = _dict(snapshot.get("run_manifest"))
    capsule = _context_capsule(snapshot, manifest)
    provider_contract = _provider_contract(snapshot, capsule)
    safe_events = [item for item in list(events or []) if isinstance(item, dict)]
    safe_results = [item for item in list(results or []) if isinstance(item, dict)]
    receipt = _dict(completion_receipt)

    recovery = _recovery_projection(run, safe_events, manifest)
    context = _context_projection(capsule)
    evidence = _criteria_projection(manifest)
    artifacts = _artifact_projection(run, safe_results)
    delegation = _delegation_projection(snapshot)
    authority = _authority_projection(manifest)
    provider = _provider_projection(provider_contract)
    sync = _sync_projection(run)

    blockers: list[dict[str, str]] = []
    if evidence["failed"]:
        blockers.append({"code": "required_check_failed", "area": "evidence"})
    if artifacts["stale_result_ids"]:
        blockers.append({"code": "stale_artifact", "area": "artifacts"})
    if delegation["blocked_node_ids"]:
        blockers.append({"code": "agent_branch_blocked", "area": "delegation"})
    if authority["pending_approval"]:
        blockers.append({"code": "approval_pending", "area": "authority"})
    if provider["compatible"] is False:
        blockers.append({"code": "provider_incompatible", "area": "provider"})
    if _number(_dict(receipt.get("summary")).get("invalid_receipts")):
        blockers.append({"code": "invalid_execution_receipt", "area": "delivery"})

    receipt_allows = receipt.get("can_claim_complete") is True
    status = _text(run.get("status"), 40)
    can_deliver = bool(receipt_allows and not blockers)
    if can_deliver:
        state = "verified"
        headline = "成果已具备交付条件"
        detail = "验收条件、证据、产物版本和权限记录均未发现阻断项。"
    elif blockers:
        state = "attention"
        headline = "还有事项需要处理"
        detail = f"发现 {len(blockers)} 项确定性阻断，处理后才能交付。"
    elif status in _ACTIVE:
        state = "working"
        headline = "工作仍在推进"
        detail = "当前没有确定性失败，但完成回执尚未允许声明交付。"
    else:
        state = "not_verified"
        headline = "尚不能确认交付"
        detail = "缺少可验证的完成回执，不能用状态文字替代完成证据。"

    areas = [
        recovery, context, evidence, artifacts, delegation,
        authority, provider, sync,
    ]
    delivery = {
        "version": VERSION_CHAIN["delivery"],
        "status": state,
        "can_deliver": can_deliver,
        "receipt_allows_completion": receipt_allows,
        "blockers": blockers,
    }
    return {
        "schema": SCHEMA,
        "versions": dict(VERSION_CHAIN),
        "user_summary": {
            "state": state,
            "headline": headline,
            "detail": detail,
            "checks_observed": sum(
                1 for item in areas
                if item.get("status") not in {"not_reported", "not_used", "none", "unavailable"}
            ),
            "checks_total": len(areas),
        },
        "recovery": recovery,
        "context": context,
        "evidence": evidence,
        "artifacts": artifacts,
        "delegation": delegation,
        "authority": authority,
        "provider": provider,
        "sync": sync,
        "delivery": delivery,
        "integrity": {
            "construction": "deterministic_runtime_projection",
            "projection_only": True,
            "auto_executes": False,
            "widens_scope": False,
            "model_prose_is_evidence": False,
            "fingerprint": _fingerprint({
                "run": sync["fingerprint"],
                "blockers": blockers,
                "receipt": receipt.get("receipt_id"),
                "results": [item.get("version_ref") for item in safe_results],
            }),
        },
    }


__all__ = ["SCHEMA", "VERSION_CHAIN", "build_work_assurance"]
