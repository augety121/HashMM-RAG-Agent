"""Proof-carrying execution receipts for every observable tool action.

A receipt records what the runtime can prove about an action without persisting
raw arguments, credentials, prompts, or tool output.  It is deliberately
provider-neutral so Chat, AgentLoop, browser/computer workers and mobile
control-plane views can share the same contract.

The receipt is evidence of an observed execution, not evidence that a model's
description of the outside world is true.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePath
from typing import Any, Iterable, Mapping


SCHEMA = "hashmm.execution-receipt.v1"
_SUCCESS = {"ok", "done", "success", "completed", "complete"}
_FAILURE = {"error", "failed", "fail", "denied", "blocked", "stopped", "cancelled"}
_SIDE_EFFECTS = {
    "none", "observe", "local_write", "external_write", "privileged_control",
}
_RISK_FACTORS = (
    "irreversibility", "externality", "sensitivity", "scope", "uncertainty",
)
_OBSERVE_TOOLS = {
    "read_file", "read_file_range", "file_tree", "kb_search", "kg_query",
    "memory_recall", "web_search", "fetch_url", "browser_open", "browser_read",
    "browser_screenshot", "git_status", "git_diff",
}
_LOCAL_WRITE_TOOLS = {
    "create_file", "create_document", "str_replace", "update_todo",
    "remember_preference",
}
_EXTERNAL_WRITE_TOKENS = {
    "send", "publish", "submit", "upload", "message", "email", "calendar",
}
_PRIVILEGED_TOKENS = {
    "execute", "shell", "terminal", "computer", "process", "install", "delete",
    "remove", "git_push",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(value: Any, length: int = 24) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[: max(0, limit)]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _status(raw: Any) -> tuple[str, bool]:
    if isinstance(raw, Mapping):
        value = _clip(raw.get("status"), 32).lower()
        success = raw.get("success")
        if isinstance(success, bool):
            return ("completed" if success else "failed"), success
        if value in _SUCCESS:
            return "completed", True
        if value in _FAILURE:
            return ("denied" if value == "denied" else "failed"), False
        if raw.get("error"):
            return "failed", False
        return "completed", True
    return "completed", True


def _artifact_ref(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    raw_name = _clip(item.get("filename") or item.get("name"), 180)
    # Never expose a caller supplied absolute path through the public receipt.
    name = PurePath(raw_name.replace("\\", "/")).name if raw_name else ""
    identity = (
        item.get("artifact_id") or item.get("id") or item.get("content_hash")
        or item.get("revision") or name
    )
    if not identity:
        return None
    row: dict[str, Any] = {
        "ref": _hash(["artifact", identity], 20),
        "name": name,
    }
    if item.get("content_hash"):
        row["content_hash"] = _clip(item.get("content_hash"), 96)
    if item.get("revision") not in (None, ""):
        row["revision"] = _clip(item.get("revision"), 64)
    if item.get("size") not in (None, ""):
        row["size"] = max(0, int(_number(item.get("size"))))
    return row


def _bounded_refs(values: Iterable[Any] | None, *, limit: int = 32) -> list[str]:
    refs: list[str] = []
    for value in list(values or [])[:limit]:
        if isinstance(value, Mapping):
            value = (
                value.get("ref") or value.get("id") or value.get("source_id")
                or value.get("chunk_id") or value.get("receipt_id")
            )
        clean = _clip(value, 160)
        if clean and clean not in refs:
            refs.append(clean)
    return refs


def calculate_action_risk(
    factors: Mapping[str, Any] | None = None,
    *,
    side_effect_class: str = "none",
) -> dict[str, Any]:
    """Return a transparent deny-first action risk score.

    Each factor is in ``0..4``.  A geometric-style product is intentionally not
    used because a zero in one dimension must not erase serious risk in another.
    External writes and privileged control also carry a floor.
    """
    raw = dict(factors or {})
    normalized = {
        key: min(4, max(0, int(_number(raw.get(key)))))
        for key in _RISK_FACTORS
    }
    weighted = (
        normalized["irreversibility"] * 5
        + normalized["externality"] * 5
        + normalized["sensitivity"] * 4
        + normalized["scope"] * 3
        + normalized["uncertainty"] * 3
    )
    effect = side_effect_class if side_effect_class in _SIDE_EFFECTS else "privileged_control"
    floor = {
        "none": 0, "observe": 0, "local_write": 20,
        "external_write": 45, "privileged_control": 65,
    }[effect]
    score = min(100, max(floor, round(weighted * 1.25)))
    if score >= 70:
        level, approval = "critical", "explicit"
    elif score >= 45:
        level, approval = "high", "explicit"
    elif score >= 20:
        level, approval = "medium", "policy"
    else:
        level, approval = "low", "none"
    return {
        "score": score,
        "level": level,
        "required_approval": approval,
        "factors": normalized,
        "formula": "bounded_weighted_sum_with_side_effect_floor",
    }


def infer_side_effect(
    tool_name: str, arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Conservatively classify a tool action when an executor has no annotation."""
    name = _clip(tool_name, 120).lower()
    args = dict(arguments or {})
    action = _clip(args.get("action") or args.get("operation"), 80).lower()
    tokens = {token for token in name.replace("-", "_").split("_") if token}
    if name in _OBSERVE_TOOLS:
        effect = "observe"
    elif name in _LOCAL_WRITE_TOOLS:
        effect = "local_write"
    elif name in {"browser_act", "browser_action"}:
        effect = (
            "external_write"
            if action in {"click", "type", "fill", "submit", "upload", "download"}
            else "observe"
        )
    elif tokens & _PRIVILEGED_TOKENS or any(
        token in name for token in _PRIVILEGED_TOKENS
    ):
        effect = "privileged_control"
    elif tokens & _EXTERNAL_WRITE_TOKENS or any(
        token in name for token in _EXTERNAL_WRITE_TOKENS
    ):
        effect = "external_write"
    else:
        # Unknown tools are not read-only.
        effect = "privileged_control"
    return {
        "class": effect,
        "external": effect in {"external_write", "privileged_control"},
        "reversible": effect in {"none", "observe", "local_write"},
    }


def build_execution_receipt(
    *,
    run_id: str,
    call_id: str,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    result: Any = None,
    status: str = "",
    started_at: float = 0,
    finished_at: float = 0,
    elapsed_ms: int | float = 0,
    execution_scope: Mapping[str, Any] | None = None,
    executor: Mapping[str, Any] | None = None,
    permission: Mapping[str, Any] | None = None,
    side_effect: Mapping[str, Any] | None = None,
    evidence_refs: Iterable[Any] | None = None,
    artifacts: Iterable[Mapping[str, Any]] | None = None,
    verification: Iterable[Mapping[str, Any]] | None = None,
    risk_factors: Mapping[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Build one bounded, content-addressed execution receipt."""
    scope = dict(execution_scope or {})
    executor_data = dict(executor or {})
    permission_data = dict(permission or {})
    effect_data = dict(side_effect or infer_side_effect(tool_name, arguments))
    tool = _clip(tool_name, 120)
    call = _clip(call_id, 160)
    run = _clip(run_id, 160)
    args = dict(arguments or {})
    args_hash = _hash(args)
    inferred_status, success = _status(result)
    normalized_status = _clip(status, 32).lower() or inferred_status
    if normalized_status in _SUCCESS:
        normalized_status, success = "completed", True
    elif normalized_status in _FAILURE:
        normalized_status, success = (
            "denied" if normalized_status == "denied" else "failed"
        ), False

    effect_class = _clip(effect_data.get("class"), 40).lower() or "none"
    if effect_class not in _SIDE_EFFECTS:
        effect_class = "privileged_control"
    external = bool(effect_data.get("external", effect_class in {
        "external_write", "privileged_control",
    }))
    reversible = bool(effect_data.get("reversible", effect_class in {
        "none", "observe", "local_write",
    }))
    idem = _clip(idempotency_key or effect_data.get("idempotency_key"), 240)
    started = _number(started_at)
    finished = _number(finished_at)
    elapsed = max(0, int(round(_number(elapsed_ms))))
    if not elapsed and started and finished and finished >= started:
        elapsed = max(0, int(round((finished - started) * 1000)))

    artifact_rows = [
        ref for ref in (_artifact_ref(item) for item in list(artifacts or [])[:32])
        if ref is not None
    ]
    verification_rows: list[dict[str, Any]] = []
    for item in list(verification or [])[:24]:
        if not isinstance(item, Mapping):
            continue
        check = _clip(item.get("check_id") or item.get("id"), 100)
        if not check:
            continue
        verification_rows.append({
            "check_id": check,
            "status": _clip(item.get("status"), 32).lower() or "not_evaluable",
            "evidence_ref": _clip(item.get("evidence_ref"), 160),
        })

    result_payload = result if isinstance(result, Mapping) else {"output": result}
    error_code = ""
    if isinstance(result, Mapping):
        error_code = _clip(
            result.get("error_code") or result.get("code")
            or (type(result.get("error")).__name__ if result.get("error") else ""),
            80,
        )
    permission_decision = _clip(permission_data.get("decision"), 32).lower()
    if not permission_decision:
        permission_decision = "denied" if normalized_status == "denied" else "allowed"
    risk = calculate_action_risk(risk_factors, side_effect_class=effect_class)

    core: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run,
        "call_id": call,
        "action": {
            "tool": tool,
            "arguments_hash": args_hash,
            "scope_id": _clip(scope.get("scope_id"), 160),
            "capability_revision": _clip(
                scope.get("capability_revision") or executor_data.get("capability_revision"),
                96,
            ),
        },
        "executor": {
            "kind": _clip(executor_data.get("kind"), 40) or "tool",
            "name": _clip(executor_data.get("name"), 120) or tool,
            "version": _clip(executor_data.get("version"), 80),
            "device_ref": _hash(
                ["device", executor_data.get("device_id")], 16
            ) if executor_data.get("device_id") else "",
        },
        "timing": {
            "started_at": started,
            "finished_at": finished,
            "elapsed_ms": elapsed,
        },
        "permission": {
            "decision": permission_decision,
            "authority": _clip(permission_data.get("authority"), 80) or "execution_scope",
            "approval_ref": _clip(
                permission_data.get("approval_id") or permission_data.get("approval_ref"),
                160,
            ),
        },
        "outcome": {
            "status": normalized_status,
            "success": bool(success),
            "result_hash": _hash(result_payload),
            "error_code": error_code,
        },
        "side_effect": {
            "class": effect_class,
            "external": external,
            "reversible": reversible,
            "idempotency_ref": _hash(["idempotency", idem], 20) if idem else "",
            "compensation": _clip(effect_data.get("compensation"), 160),
            "compensation_status": _clip(
                effect_data.get("compensation_status"), 32,
            ),
        },
        "risk": risk,
        "evidence_refs": _bounded_refs(evidence_refs),
        "artifacts": artifact_rows,
        "verification": verification_rows,
    }
    identity = {
        "run_id": run,
        "call_id": call,
        "tool": tool,
        "arguments_hash": args_hash,
        "scope_id": core["action"]["scope_id"],
        "started_at": started,
    }
    core["receipt_id"] = "rcpt_" + _hash(identity)
    core["integrity"] = {
        "algorithm": "sha256",
        "content_hash": _hash(core, 64),
        "raw_arguments_included": False,
        "raw_result_included": False,
    }
    return core


def validate_execution_receipt(receipt: Any) -> dict[str, Any]:
    """Validate structural and integrity properties without executing anything."""
    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return {"valid": False, "errors": ["not_a_mapping"]}
    item = dict(receipt)
    if item.get("schema") != SCHEMA:
        errors.append("schema")
    if not _clip(item.get("receipt_id"), 160):
        errors.append("receipt_id")
    action = item.get("action") if isinstance(item.get("action"), Mapping) else {}
    if not _clip(action.get("tool"), 120):
        errors.append("tool")
    if len(_clip(action.get("arguments_hash"), 96)) < 16:
        errors.append("arguments_hash")
    outcome = item.get("outcome") if isinstance(item.get("outcome"), Mapping) else {}
    if _clip(outcome.get("status"), 32) not in {"completed", "failed", "denied"}:
        errors.append("terminal_status")
    effect = item.get("side_effect") if isinstance(item.get("side_effect"), Mapping) else {}
    effect_class = _clip(effect.get("class"), 40)
    if effect_class not in _SIDE_EFFECTS:
        errors.append("side_effect_class")
    if effect_class in {"external_write", "privileged_control"} and not effect.get(
        "idempotency_ref"
    ):
        errors.append("idempotency_ref")
    integrity = item.get("integrity") if isinstance(item.get("integrity"), Mapping) else {}
    expected = _clip(integrity.get("content_hash"), 96)
    check = dict(item)
    check.pop("integrity", None)
    if not expected or expected != _hash(check, 64):
        errors.append("content_hash")
    return {"valid": not errors, "errors": errors}


def public_execution_receipts(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Keep only valid, bounded receipts for manifests and cross-device state."""
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in list(values or [])[:160]:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        receipt_id = _clip(item.get("receipt_id"), 160)
        if not receipt_id or receipt_id in seen:
            continue
        # Rebuild through JSON to detach caller-owned nested structures.
        detached = json.loads(_canonical(item))
        receipts.append(detached)
        seen.add(receipt_id)
    return receipts


__all__ = [
    "SCHEMA", "build_execution_receipt", "validate_execution_receipt",
    "public_execution_receipts", "calculate_action_risk", "infer_side_effect",
]
