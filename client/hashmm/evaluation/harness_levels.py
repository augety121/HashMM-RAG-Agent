"""Deterministic Runtime Harness L0-L3 for the public Agent trajectory.

This is a local replay/evaluation contract, not a claim that a benchmark
paper's private environment has been reproduced.  Each level consumes only
the argument-free ``hashmm.agent-harness.v1`` projection.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


def _events(public: Mapping[str, Any]) -> list[dict[str, Any]]:
    trajectory = public.get("trajectory") if isinstance(public, Mapping) else {}
    raw = trajectory.get("events") if isinstance(trajectory, Mapping) else []
    return [dict(item) for item in raw if isinstance(item, Mapping)][:240]


def level_l0_contract(public: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "context", "capabilities", "trajectory", "terminal"}
    missing = sorted(required - set(public.keys())) if isinstance(public, Mapping) else sorted(required)
    safe_fields = {"schema", "context", "capabilities", "trajectory", "children", "terminal", "limitation"}
    forbidden = sorted(set(public.keys()) - safe_fields) if isinstance(public, Mapping) else []
    ok = not missing and not forbidden and public.get("schema") == "hashmm.agent-harness.v1"
    return {"level": "L0", "name": "contract", "status": "passed" if ok else "failed", "missing": missing, "forbidden": forbidden}


def level_l1_replay(public: Mapping[str, Any]) -> dict[str, Any]:
    events = _events(public)
    seq_ok = all(int(item.get("seq") or 0) == index for index, item in enumerate(events, 1))
    types = Counter(str(item.get("type") or "unknown") for item in events)
    terminal = public.get("terminal") if isinstance(public, Mapping) else {}
    ok = bool(events) and seq_ok and isinstance(terminal, Mapping) and bool(terminal.get("reason"))
    return {"level": "L1", "name": "deterministic_replay", "status": "passed" if ok else "failed", "event_count": len(events), "event_types": dict(types), "sequence_ok": seq_ok}


def level_l2_fault_injection(public: Mapping[str, Any]) -> dict[str, Any]:
    events = _events(public)
    terminal_types = {"cancelled", "hard_timeout", "waiting_approval", "waiting_input"}
    terminal_positions = [index for index, item in enumerate(events) if str(item.get("status") or "") in terminal_types]
    # A waiting state may legitimately be followed by an explicit
    # approval/input-consumed event.  What must never happen is a second
    # terminal decision with no intervening lifecycle event.
    sticky = True
    for index in terminal_positions:
        if index + 1 < len(events) and str(events[index + 1].get("type") or "") == "turn_finished":
            sticky = False
            break
    return {"level": "L2", "name": "fault_injection", "status": "passed" if sticky else "failed", "sticky_terminal_ok": sticky, "checked_events": len(events)}


def level_l3_evidence(public: Mapping[str, Any]) -> dict[str, Any]:
    events = _events(public)
    finished = any(str(item.get("type") or "") == "turn_finished" for item in events)
    terminal = public.get("terminal") if isinstance(public, Mapping) else {}
    reason = str(terminal.get("reason") or "") if isinstance(terminal, Mapping) else ""
    valid_receipt_states = {"valid", "ok", "verified"}
    tool_events = [item for item in events if str(item.get("type") or "") == "tool_finished"]
    side_effect_events = []
    invalid_side_effect_receipts = []
    for item in tool_events:
        detail = item.get("detail") if isinstance(item.get("detail"), Mapping) else {}
        side_effect_class = str(detail.get("side_effect_class") or "").strip().lower()
        if side_effect_class in {"write", "destructive", "external_write", "side_effect"}:
            side_effect_events.append(item)
            if str(detail.get("receipt_status") or "").strip().lower() not in valid_receipt_states:
                invalid_side_effect_receipts.append(int(item.get("seq") or 0))
    receipt_events = sum(
        1 for item in tool_events
        if str((item.get("detail") if isinstance(item.get("detail"), Mapping) else {}).get("receipt_status") or "").strip().lower()
        in valid_receipt_states
    )
    completed = reason in {"completed", "completed_with_limits"}
    evidence_ok = not completed or not side_effect_events or not invalid_side_effect_receipts
    ok = finished and bool(reason) and evidence_ok
    return {
        "level": "L3", "name": "evidence_gate",
        "status": "passed" if ok else "failed",
        "finished": finished, "terminal_reason": reason,
        "verified_receipts": receipt_events,
        "required_receipts": len(side_effect_events),
        "invalid_receipt_event_sequences": invalid_side_effect_receipts,
        "model_prose_is_evidence": False,
    }


def evaluate_harness_levels(public: Mapping[str, Any]) -> dict[str, Any]:
    levels = [level_l0_contract(public), level_l1_replay(public), level_l2_fault_injection(public), level_l3_evidence(public)]
    return {"schema": "hashmm.harness-levels.v1", "family": "runtime_harness_l0_l3", "levels": levels, "passed": sum(item["status"] == "passed" for item in levels), "total": len(levels), "claim": "local_public_trajectory_replay_only"}


__all__ = ["level_l0_contract", "level_l1_replay", "level_l2_fault_injection", "level_l3_evidence", "evaluate_harness_levels"]
