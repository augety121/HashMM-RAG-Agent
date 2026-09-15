"""Bounded, auditable handoff state for long-running HashMM work.

The model's private reasoning is deliberately not part of this contract.
Desktop, App and resumed Chat sessions receive only user-owned goals, explicit
constraints, plan state, runtime events, evidence receipts, artifacts and open
work.  This makes a long run resumable without treating model prose as proof.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping


SCHEMA = "hashmm.long-horizon-handoff.v1"
TRACE_SCHEMA = "hashmm.task-trace.v1"

_SECRET = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|token|password|secret|authorization|cookie)"
    r"\s*[:=]\s*\S+)"
)
_SUCCESS = {"ok", "pass", "passed", "success", "completed", "complete", "verified", "ready"}
_FAILURE = {"error", "failed", "denied", "cancelled", "interrupted", "blocked"}
_WAITING = {"waiting_input", "waiting_approval", "blocked", "interrupted", "failed"}
_STEP_START = {"step_start", "tool_start", "execution_started", "automation_started"}
_STEP_DONE = {
    "step_done", "tool_done", "verification", "verified", "artifact",
    "artifact_ready", "automation_result",
}


def _text(value: Any, limit: int = 500) -> str:
    text = _SECRET.sub("[已脱敏]", str(value or "").replace("\x00", "").strip())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _unique_text(values: Any, *, limit: int = 24, item_limit: int = 360) -> list[str]:
    result: list[str] = []
    for value in list(values or []):
        if isinstance(value, Mapping):
            value = (
                value.get("text") or value.get("label") or value.get("summary")
                or value.get("name") or value.get("id") or ""
            )
        item = _text(value, item_limit)
        if item and item not in result:
            result.append(item)
    return result[-limit:]


def _normalise_plan(values: Any) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, raw in enumerate(list(values or [])[:32]):
        item = _mapping(raw)
        text = _text(item.get("text") or item.get("label") or raw, 300)
        if not text:
            continue
        status = str(item.get("status") or "pending").lower()
        if status not in {"pending", "running", "completed", "skipped", "blocked", "failed"}:
            status = "pending"
        plan.append({
            "id": _text(item.get("id") or item.get("check_id") or f"step-{index + 1}", 96),
            "text": text,
            "status": status,
        })
    return plan


def _normalise_evidence(values: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in list(values or [])[-40:]:
        item = _mapping(raw)
        label = _text(
            item.get("label") or item.get("name") or item.get("summary")
            or item.get("tool") or item.get("id"),
            240,
        )
        if not label:
            continue
        status = _text(item.get("status") or item.get("result") or "recorded", 32).lower()
        source = _text(item.get("source") or item.get("kind") or "runtime_event", 48)
        row = {"label": label, "status": status, "source": source}
        if row not in rows:
            rows.append(row)
    return rows[-40:]


def initialise_handoff(
    task_contract: Mapping[str, Any] | None,
    *,
    run_id: str = "",
    goal: str = "",
) -> dict[str, Any]:
    """Create the first durable handoff from server-owned admission facts."""
    contract = _mapping(task_contract)
    criteria = _normalise_plan(contract.get("success_criteria"))
    plan = _normalise_plan(contract.get("plan"))
    return {
        "schema": SCHEMA,
        "run_id": _text(run_id or contract.get("run_id"), 96),
        "goal": _text(goal or contract.get("goal"), 600),
        "current_intent": _text(goal or contract.get("goal"), 400),
        "constraints": [],
        "plan": plan,
        "success_criteria": criteria,
        "decisions": [],
        "evidence": [],
        "artifacts": [],
        "blockers": [],
        "next_actions": [item["text"] for item in plan if item["status"] == "pending"][:5],
        "verification": {
            "status": "pending",
            "evidence_required": True,
            "model_prose_is_evidence": False,
        },
        "cursor": {"event_seq": 1, "last_event": "admitted", "status": "queued"},
        "policy": {
            "private_reasoning_persisted": False,
            "visible_reasoning": "plan_decision_summary_action_evidence_acceptance",
            "resume_from_runtime_facts_only": True,
        },
    }


def _event_label(event_type: str, summary: str, payload: Mapping[str, Any]) -> str:
    return _text(
        summary
        or payload.get("label")
        or payload.get("step")
        or payload.get("name")
        or event_type.replace("_", " "),
        300,
    )


def _match_plan(plan: list[dict[str, Any]], payload: Mapping[str, Any], label: str) -> int:
    wanted = _text(
        payload.get("step_id") or payload.get("plan_id") or payload.get("id"), 96,
    )
    if wanted:
        for index, item in enumerate(plan):
            if item.get("id") == wanted:
                return index
    normal = re.sub(r"\s+", "", label).lower()
    if normal:
        for index, item in enumerate(plan):
            candidate = re.sub(r"\s+", "", str(item.get("text") or "")).lower()
            if candidate and (candidate in normal or normal in candidate):
                return index
    return -1


def advance_handoff(
    current: Mapping[str, Any] | None,
    *,
    event_type: str,
    summary: str = "",
    status: str = "",
    payload: Mapping[str, Any] | None = None,
    event_seq: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project one admitted runtime event into handoff + user-visible trace."""
    handoff = copy.deepcopy(_mapping(current))
    if handoff.get("schema") != SCHEMA:
        handoff = initialise_handoff({}, run_id=str(handoff.get("run_id") or ""))
    safe_event = re.sub(r"[^a-z0-9_.-]", "_", str(event_type or "progress").lower())[:64]
    safe_status = _text(status or "running", 32).lower()
    safe_payload = _mapping(payload)
    label = _event_label(safe_event, summary, safe_payload)
    plan = _normalise_plan(handoff.get("plan"))

    plan_index = _match_plan(plan, safe_payload, label)
    if safe_event in _STEP_START:
        if plan_index < 0 and label:
            plan.append({"id": f"runtime-{max(1, event_seq)}", "text": label, "status": "running"})
        elif plan_index >= 0:
            plan[plan_index]["status"] = "running"
        handoff["current_intent"] = label
    elif safe_event in _STEP_DONE:
        if plan_index >= 0:
            plan[plan_index]["status"] = (
                "failed" if safe_status in _FAILURE else "completed"
            )
        evidence_status = _text(
            safe_payload.get("verification_status")
            or safe_payload.get("result_status")
            or safe_status
            or "recorded",
            32,
        ).lower()
        evidence = _normalise_evidence(handoff.get("evidence"))
        if label:
            evidence.append({
                "label": label,
                "status": evidence_status,
                "source": _text(safe_payload.get("source") or safe_event, 48),
            })
        handoff["evidence"] = _normalise_evidence(evidence)

    if safe_event in {"decision", "plan_decision", "approval_decision", "steer"} and label:
        decisions = list(handoff.get("decisions") or [])
        decision = {
            "summary": label,
            "source": "user" if safe_event in {"approval_decision", "steer"} else "runtime_event",
            "event_seq": max(0, int(event_seq or 0)),
        }
        if decision not in decisions:
            decisions.append(decision)
        handoff["decisions"] = decisions[-24:]

    artifact = safe_payload.get("artifact") or safe_payload.get("filename")
    artifact_rows = list(handoff.get("artifacts") or [])
    if isinstance(artifact, Mapping):
        artifact_row = {
            "id": _text(artifact.get("id") or artifact.get("artifact_id"), 120),
            "name": _text(artifact.get("filename") or artifact.get("name"), 240),
            "status": _text(artifact.get("status") or safe_status or "recorded", 32),
        }
    else:
        artifact_row = {
            "id": "",
            "name": _text(artifact, 240),
            "status": _text(safe_status or "recorded", 32),
        }
    if artifact_row["name"] and artifact_row not in artifact_rows:
        artifact_rows.append(artifact_row)
    handoff["artifacts"] = artifact_rows[-30:]

    blockers = _unique_text(handoff.get("blockers"))
    if safe_status in _WAITING and label and label not in blockers:
        blockers.append(label)
    elif safe_status not in _WAITING and label in blockers:
        blockers.remove(label)
    handoff["blockers"] = blockers[-20:]

    handoff["plan"] = plan[-32:]
    pending = [
        item["text"] for item in handoff["plan"]
        if item["status"] in {"pending", "running", "blocked", "failed"}
    ]
    if safe_status in {"failed", "interrupted"}:
        pending.insert(0, "检查失败证据并从最近检查点安全续接")
    handoff["next_actions"] = _unique_text(pending, limit=6, item_limit=300)

    verification = _mapping(handoff.get("verification"))
    evidence_statuses = {
        str(item.get("status") or "").lower()
        for item in list(handoff.get("evidence") or []) if isinstance(item, Mapping)
    }
    if safe_status in _FAILURE:
        verification["status"] = "failed"
    elif safe_status in {"completed", "delivered"}:
        verification["status"] = (
            "verified" if evidence_statuses & _SUCCESS else "evidence_required"
        )
    elif evidence_statuses & _SUCCESS:
        verification["status"] = "partially_verified"
    else:
        verification.setdefault("status", "pending")
    verification.update({
        "evidence_required": True,
        "model_prose_is_evidence": False,
    })
    handoff["verification"] = verification
    handoff["cursor"] = {
        "event_seq": max(0, int(event_seq or 0)),
        "last_event": safe_event,
        "status": safe_status,
    }

    trace_item = {
        "schema": TRACE_SCHEMA,
        "seq": max(0, int(event_seq or 0)),
        "type": safe_event,
        "status": safe_status,
        "summary": label,
        "evidence": safe_event in _STEP_DONE,
        "private_reasoning": False,
    }
    return handoff, trace_item


def render_handoff(handoff: Mapping[str, Any] | None, *, limit: int = 7_000) -> str:
    """Render a provider-safe checkpoint while keeping uncertainty explicit."""
    state = _mapping(handoff)
    parts = ["[会话压缩检查点 / durable context checkpoint]"]
    prior = _text(state.get("prior_checkpoint"), 2_000)
    if prior:
        parts.extend(["【此前检查点】", prior.replace(parts[0], "").strip()])
    goal = _text(state.get("goal"), 600)
    if goal:
        parts.extend(["【原始/阶段目标】", f"- {goal}"])
    current = _text(state.get("current_intent"), 400)
    if current and current != goal:
        parts.extend(["【当前工作焦点】", f"- {current}"])
    sections = (
        ("【明确约束】", _unique_text(state.get("constraints"), limit=18)),
        ("【计划与状态】", [
            f"[{_text(item.get('status'), 24)}] {_text(item.get('text'), 300)}"
            for item in _normalise_plan(state.get("plan"))
        ]),
        ("【决策摘要】", _unique_text(state.get("decisions"), limit=18)),
        ("【持久化工具与验证状态（标识/状态原样提取）】", [
            f"{_text(item.get('label'), 240)} [{_text(item.get('status'), 32)}]"
            for item in _normalise_evidence(state.get("evidence"))
        ]),
        ("【提及的产物】", _unique_text(state.get("artifacts"), limit=30)),
        ("【未完成、失败或待核验】", _unique_text(state.get("blockers"), limit=20)),
        ("【下一步】", _unique_text(state.get("next_actions"), limit=8)),
    )
    for heading, rows in sections:
        if rows:
            parts.append(heading)
            parts.extend(f"- {row}" for row in rows)
    parts.extend([
        "【证据边界】",
        "- 模型自述不是完成证据；续接时以持久化事件、文件状态和确定性验证为准。",
        "- 不包含模型私有思维链，只保留计划、决策摘要、动作、证据与验收状态。",
    ])
    rendered = "\n".join(parts).strip()
    if len(rendered) <= limit:
        return rendered
    head = limit // 2
    tail = limit - head - 40
    return rendered[:head] + "\n…[检查点中段已折叠]…\n" + rendered[-tail:]


__all__ = [
    "SCHEMA", "TRACE_SCHEMA", "initialise_handoff", "advance_handoff",
    "render_handoff",
]
