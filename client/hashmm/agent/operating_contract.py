"""Server-authored operating contract for one user work run.

The product has many capabilities, but a run must not infer execution authority
from a selected card, model prose or a client-side toggle.  This module compiles
the user-visible work method and the non-negotiable runtime boundaries into one
bounded, deterministic contract.

The contract is descriptive admission state.  It never grants a filesystem,
network, browser or device permission; the corresponding executor still needs
an owner-scoped execution scope, approval and (where applicable) a device lease.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


OPERATING_CONTRACT_SCHEMA = "hashmm.work-operating-contract.v1"
WORK_METHOD_SCHEMA = "hashmm.work-method.v1"

_RETRIEVAL_MODES = frozenset({"auto", "naive", "kg", "mix", "global"})
_EFFORT_MODES = frozenset({"fast", "standard", "max"})
_RUN_MODES = frozenset({"auto", "browser", "deep", "computer"})
_LONG_RUNNING_KINDS = frozenset({
    "loop", "team", "agent_session", "browser", "computer", "remote",
    "artifact", "workflow",
})
_ARTIFACT_KINDS = frozenset({"artifact", "workflow"})


def _text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _fingerprint(value: Any, limit: int = 24) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[: max(12, limit)]


def normalize_work_method(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a closed-enum work method; unknown client values fail to auto."""
    raw = _dict(value)
    retrieval = _text(raw.get("retrieval"), 24).lower()
    effort = _text(raw.get("effort"), 24).lower()
    run_mode = _text(raw.get("run_mode") or raw.get("runMode"), 24).lower()
    return {
        "schema": WORK_METHOD_SCHEMA,
        "retrieval": retrieval if retrieval in _RETRIEVAL_MODES else "auto",
        "effort": effort if effort in _EFFORT_MODES else "standard",
        "run_mode": run_mode if run_mode in _RUN_MODES else "auto",
    }


def requested_capability_path(
    work_method: Mapping[str, Any],
    *,
    inferred_path: str,
) -> str:
    """Map an explicit user mode to a capability path without broadening it."""
    mode = str(work_method.get("run_mode") or "auto")
    if mode == "browser":
        return "browser"
    if mode == "computer":
        return "computer"
    if mode == "deep":
        return "structured"
    return inferred_path if inferred_path in {
        "structured", "browser", "computer",
    } else "structured"


def _route(
    capability_facts: Mapping[str, Any],
    *,
    requested_path: str,
) -> dict[str, Any]:
    facts = _dict(capability_facts)
    selected = _dict(facts.get(requested_path))
    available = bool(selected.get("available") and selected.get("configured"))
    if available:
        state = "ready"
        fallback = "none"
        reason = _text(selected.get("reason"), 280)
    else:
        state = "setup_required"
        fallback = "manual"
        reason = _text(
            selected.get("reason") or "运行时没有证明所需能力可用", 280,
        )
    placement = {
        "browser": "desktop_browser",
        "computer": "desktop_executor",
        "structured": "server",
    }[requested_path]
    return {
        "requested_path": requested_path,
        "state": state,
        "placement": placement,
        "fallback": fallback,
        "requires_confirmation": bool(
            selected.get("requires_confirmation")
            or requested_path in {"browser", "computer"}
        ),
        "reason": reason,
        "authority_granted": False,
    }


def compile_operating_contract(
    *,
    owner_id: str,
    run_id: str,
    kind: str,
    goal: str,
    work_method: Mapping[str, Any] | None,
    capability_facts: Mapping[str, Any],
    task_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the V479-V496 run invariants used by every product surface."""
    method = normalize_work_method(work_method)
    capability_meta = _dict(_dict(capability_facts).get("_meta"))
    inferred_path = _text(capability_meta.get("requested_path"), 24).lower()
    requested_path = requested_capability_path(
        method, inferred_path=inferred_path,
    )
    execution_route = _route(
        capability_facts, requested_path=requested_path,
    )
    contract = _dict(task_contract)
    criteria = [
        item for item in list(contract.get("success_criteria") or [])
        if isinstance(item, Mapping)
    ][:24]
    requires_evidence = any(
        bool(item.get("evidence_required")) for item in criteria
    ) or method["retrieval"] != "auto" or method["run_mode"] == "deep"
    artifact_required = bool(contract.get("artifact_required")) or kind in _ARTIFACT_KINDS
    resumable = kind in _LONG_RUNNING_KINDS

    payload: dict[str, Any] = {
        "schema": OPERATING_CONTRACT_SCHEMA,
        "method": method,
        "route": execution_route,
        "context": {
            "durable_checkpoint": resumable,
            "auto_compaction": True,
            "compaction_trigger": "model_window_pressure",
            "preserve": [
                "goal", "acceptance_criteria", "decisions", "blockers",
                "artifact_refs", "evidence_refs", "execution_receipts",
            ],
            "hidden_reasoning_persisted": False,
        },
        "recovery": {
            "resumable": resumable,
            "resume_from_checkpoint": resumable,
            "replay_external_side_effects": False,
            "requires_receipt_for_completed_action": True,
            "stale_generation_rejected": True,
        },
        "evidence": {
            "required": requires_evidence,
            "owner_scope_required": True,
            "source_revision_required": requires_evidence,
            "citation_ready_required": requires_evidence,
            "graph_provenance_required": (
                method["retrieval"] in {"kg", "mix", "global"}
            ),
            "model_prose_is_evidence": False,
        },
        "artifact": {
            "required": artifact_required,
            "content_addressed_revisions": artifact_required,
            "source_change_invalidates_dependents": artifact_required,
            "publish_requires_fresh_sources": artifact_required,
            "browser_source_link_supported": artifact_required,
        },
        "collaboration": {
            "single_mesh_admission": True,
            "delegation_is_authority": False,
            "independent_verification_required": kind == "team",
            "shared_write_requires_integration": kind == "team",
        },
        "integrations": {
            "provider": {
                "capability_negotiation_required": True,
                "vendor_name_is_not_capability_evidence": True,
                "unsupported_features_fail_closed": True,
            },
            "mcp": {
                "unknown_tools_read_only": False,
                "annotations_required_for_side_effects": True,
                "owner_scope_required": True,
            },
            "hooks": {
                "pre_action_guard_required": True,
                "post_action_receipt_required": True,
                "untrusted_code_disabled": True,
            },
        },
        "continuity": {
            "cache_scope": "owner_backend_object_revision",
            "private_etag": True,
            "stale_while_offline": "read_only",
            "offline_commands_require_idempotency": True,
            "conflicts": "server_revision_wins_then_user_reconcile",
            "mobile_role": "observe_confirm_handoff",
            "desktop_role": "execute_create_inspect",
        },
        "audit": {
            "owner_bound": True,
            "arguments_persisted": False,
            "results_persisted": False,
            "credentials_persisted": False,
            "hashes_and_receipts_only": True,
            "external_content_untrusted": True,
        },
        "completion": {
            "criteria_count": len(criteria),
            "criteria_required": bool(criteria),
            "valid_receipts_required": True,
            "fresh_dependencies_required": True,
            "independent_verification_required": kind == "team",
            "user_acceptance_is_not_execution_evidence": True,
        },
        "facts": {
            "owner_fingerprint": _fingerprint({"owner": _text(owner_id, 200)}, 16),
            "run_id": _text(run_id, 96),
            "kind": _text(kind, 40),
            "goal_hash": _fingerprint({"goal": _text(goal, 8_000)}, 24),
            "capability_revision": _text(
                capability_meta.get("runtime_revision"), 32,
            ),
        },
    }
    payload["revision"] = _fingerprint(payload, 24)
    return payload


def validate_operating_contract(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate safety invariants without trusting the stored revision."""
    contract = _dict(value)
    errors: list[str] = []
    if contract.get("schema") != OPERATING_CONTRACT_SCHEMA:
        errors.append("schema")
    route = _dict(contract.get("route"))
    if route.get("authority_granted") is not False:
        errors.append("route.authority_granted")
    recovery = _dict(contract.get("recovery"))
    if recovery.get("replay_external_side_effects") is not False:
        errors.append("recovery.replay_external_side_effects")
    evidence = _dict(contract.get("evidence"))
    if evidence.get("model_prose_is_evidence") is not False:
        errors.append("evidence.model_prose_is_evidence")
    audit = _dict(contract.get("audit"))
    if not audit.get("hashes_and_receipts_only"):
        errors.append("audit.hashes_and_receipts_only")
    integrations = _dict(contract.get("integrations"))
    mcp = _dict(integrations.get("mcp"))
    if mcp.get("unknown_tools_read_only") is not False:
        errors.append("integrations.mcp.unknown_tools_read_only")
    stored_revision = _text(contract.get("revision"), 24)
    recomputed_source = dict(contract)
    recomputed_source.pop("revision", None)
    revision_valid = stored_revision == _fingerprint(recomputed_source, 24)
    if not revision_valid:
        errors.append("revision")
    return {
        "valid": not errors,
        "errors": errors,
        "revision_valid": revision_valid,
    }


def user_operating_projection(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the small, non-technical projection shown in desktop/App."""
    contract = _dict(value)
    validation = validate_operating_contract(contract)
    if not validation["valid"]:
        return {
            "schema": "hashmm.user-operating-projection.v1",
            "revision": _text(contract.get("revision"), 24),
            "method_label": "自动安排",
            "route_state": "setup_required",
            "route_label": "运行方式需要重新确认",
            "reason": "运行契约校验未通过，系统没有授予任何执行权限",
            "requires_confirmation": True,
            "can_resume": False,
            "evidence_required": True,
        }
    method = _dict(contract.get("method"))
    route = _dict(contract.get("route"))
    recovery = _dict(contract.get("recovery"))
    evidence = _dict(contract.get("evidence"))
    labels = {
        "auto": "自动安排",
        "browser": "浏览器工作",
        "deep": "深度工作",
        "computer": "电脑工作",
    }
    route_state = str(route.get("state") or "setup_required")
    return {
        "schema": "hashmm.user-operating-projection.v1",
        "revision": _text(contract.get("revision"), 24),
        "method_label": labels.get(
            str(method.get("run_mode") or "auto"), "自动安排",
        ),
        "route_state": route_state,
        "route_label": (
            "已准备" if route_state == "ready" else "需要连接可用设备或服务"
        ),
        "reason": _text(route.get("reason"), 220),
        "requires_confirmation": bool(route.get("requires_confirmation")),
        "can_resume": bool(recovery.get("resumable")),
        "evidence_required": bool(evidence.get("required")),
    }
