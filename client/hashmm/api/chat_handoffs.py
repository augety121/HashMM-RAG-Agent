"""Verified, owner-scoped continuity between durable HashMM conversations.

This module persists public task state only.  It deliberately excludes message
``thinking``, hidden reasoning, raw tool arguments, cookies and credentials.
Runtime receipts are references, not proof manufactured from model prose.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from hashmm.api import database as db


SCHEMA = "hashmm.chat-continuation.v2"
_SECRET = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|token|password|passwd|secret|authorization|cookie)"
    r"\s*[:=]\s*[^\s,;]+)"
)


def _text(value: Any, limit: int = 1000) -> str:
    clean = _SECRET.sub("[REDACTED]", str(value or "").replace("\x00", "").strip())
    return clean[: max(0, limit)]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_text_list(values: Any, limit: int = 24) -> list[str]:
    result: list[str] = []
    for value in list(values or [])[:limit]:
        if isinstance(value, Mapping):
            value = value.get("text") or value.get("label") or value.get("summary") or ""
        item = _text(value, 360)
        if item and item not in result:
            result.append(item)
    return result


def _safe_plan(values: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, raw in enumerate(list(values or [])[:32]):
        item = _mapping(raw)
        text = _text(item.get("text") or item.get("label") or raw, 360)
        if not text:
            continue
        status = str(item.get("status") or "pending").lower()
        if status not in {"pending", "running", "completed", "skipped", "blocked", "failed"}:
            status = "pending"
        result.append({
            "id": _text(item.get("id") or f"step-{index + 1}", 96),
            "text": text,
            "status": status,
        })
    return result


def _latest_owned_run(owner_id: str, conv_id: str) -> dict[str, Any] | None:
    try:
        from hashmm.agent import work_runtime
        page = work_runtime.list_runs(
            owner_id, conv_id=conv_id, include_terminal=True, limit=250,
        )
        rows = list(page.get("items") or [])
        return max(rows, key=lambda row: float(row.get("updated_at") or 0)) if rows else None
    except Exception:
        return None


def _artifact_refs(conv_id: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in db.list_conversation_files(conv_id)[:32]:
        path = Path(str(row.get("path") or ""))
        digest = ""
        exists = path.is_file()
        if exists:
            try:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                exists = False
        refs.append({
            "id": _text(row.get("id"), 120),
            "filename": _text(row.get("filename"), 240),
            "size": int(row.get("size") or 0),
            "sha256": digest,
            "exists_at_seal": exists,
        })
    return refs


def _pending_approval_refs(owner_id: str, conv_id: str) -> list[dict[str, Any]]:
    now = time.time()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id,work_run_id,step_id,call_id,tool_name,risk,status,expires_at "
            "FROM tool_approval_requests WHERE user_id=? AND conv_id=? "
            "AND status='pending' AND expires_at>? ORDER BY created_at DESC LIMIT 24",
            (owner_id, conv_id, now),
        ).fetchall()
    return [{
        "id": str(row["id"]),
        "work_run_id": str(row["work_run_id"] or ""),
        "step_id": str(row["step_id"] or ""),
        "call_id": str(row["call_id"] or ""),
        "tool_name": _text(row["tool_name"], 100),
        "risk": _text(row["risk"], 40),
        "status": "pending",
        "expires_at": float(row["expires_at"] or 0),
        "permission_carried": False,
    } for row in rows]


def build_payload(owner_id: str, source_conversation_id: str) -> dict[str, Any]:
    conversation = db.get_conversation(source_conversation_id)
    if not conversation or str(conversation.get("user_id") or "") != str(owner_id):
        raise LookupError("source_conversation_not_found")
    messages = db.get_latest_messages(source_conversation_id, limit=500)
    user_messages = [
        _text(message.get("content"), 4000)
        for message in messages
        if message.get("role") == "user" and _text(message.get("content"), 1)
    ]
    run = _latest_owned_run(owner_id, source_conversation_id)
    snapshot = _mapping((run or {}).get("snapshot"))
    horizon = _mapping(
        snapshot.get("long_horizon_handoff")
        or _mapping(snapshot.get("run_manifest")).get("long_horizon_handoff")
    )
    plan = _safe_plan(horizon.get("plan") or snapshot.get("plan"))
    completed = [item["text"] for item in plan if item["status"] == "completed"]
    pending = [item["text"] for item in plan if item["status"] in {"pending", "running", "blocked", "failed"}]
    objective = _text(
        horizon.get("goal") or snapshot.get("goal") or (user_messages[0] if user_messages else ""),
        1200,
    )
    checkpoint = None
    if run:
        try:
            from hashmm.agent import work_runtime
            checkpoint = work_runtime.latest_checkpoint(str(run.get("id") or ""), owner_id)
        except Exception:
            checkpoint = None
    execution_target = _mapping((run or {}).get("execution_target"))
    workspace = {
        "root": _text(execution_target.get("workspace_root") or execution_target.get("root"), 500),
        "git_head": _text(execution_target.get("git_head"), 80),
        "branch": _text(execution_target.get("branch"), 160),
        "worktree_id": _text(execution_target.get("worktree_id"), 160),
        "verification_state": "must_revalidate",
    }
    verification = _mapping(horizon.get("verification"))
    verification.update({
        "model_prose_is_evidence": False,
        "checkpoint_id": str((checkpoint or {}).get("id") or ""),
        "checkpoint_generation": int((checkpoint or {}).get("generation") or 0),
        "run_id": str((run or {}).get("id") or ""),
        "run_status": str((run or {}).get("status") or ""),
    })
    return {
        "schema": SCHEMA,
        "objective": objective,
        "latest_user_request": user_messages[-1] if user_messages else "",
        "constraints": _safe_text_list(horizon.get("constraints")),
        "plan": plan,
        "decisions": _safe_text_list(horizon.get("decisions")),
        "completed_steps": completed,
        "pending_steps": pending,
        "blockers": _safe_text_list(horizon.get("blockers")),
        "evidence_refs": [
            {
                "label": _text(item.get("label"), 300),
                "status": _text(item.get("status"), 40),
                "source": _text(item.get("source"), 80),
            }
            for item in list(horizon.get("evidence") or [])[:40]
            if isinstance(item, Mapping)
        ],
        "artifact_refs": _artifact_refs(source_conversation_id),
        "workspace": workspace,
        "pending_approvals": _pending_approval_refs(owner_id, source_conversation_id),
        "verification": verification,
        "next_action": pending[0] if pending else _text((horizon.get("next_actions") or [""])[0], 500),
        "policy": {
            "private_reasoning_persisted": False,
            "raw_tool_arguments_persisted": False,
            "permissions_carried": False,
            "facts_require_receipts": True,
        },
    }


def create(
    owner_id: str,
    source_conversation_id: str,
    *,
    idempotency_key: str,
    target_title: str = "",
    expires_in_seconds: int = 7 * 86400,
) -> tuple[dict, bool]:
    source = db.get_conversation(source_conversation_id)
    if not source or str(source.get("user_id") or "") != str(owner_id):
        raise LookupError("source_conversation_not_found")
    payload = build_payload(owner_id, source_conversation_id)
    title = _text(target_title, 100) or f"Continue: {_text(source.get('title') or 'Chat', 80)}"
    ttl = max(300, min(int(expires_in_seconds or 7 * 86400), 30 * 86400))
    target_id = str(uuid.uuid4())
    handoff, created = db.create_conversation_handoff(
        owner_id=owner_id,
        source_conversation_id=source_conversation_id,
        target_conversation_id=target_id,
        target_title=title,
        payload=payload,
        idempotency_key=idempotency_key,
        expires_at=time.time() + ttl,
    )
    if created:
        try:
            from hashmm.api import supabase_sync
            target = db.get_conversation(str(handoff.get("target_conversation_id") or ""))
            if target and supabase_sync.enabled():
                supabase_sync.push_conversation(target)
        except Exception:
            pass
    return handoff, created


def verify_current_state(handoff: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(handoff.get("payload"))
    reasons: list[str] = []
    source = db.get_conversation(str(handoff.get("source_conversation_id") or ""))
    if source is None:
        reasons.append("source_conversation_missing")
    elif int(source.get("revision") or 1) != int(handoff.get("source_revision") or 1):
        reasons.append("source_revision_changed")
    existing = {str(row.get("filename") or ""): row for row in db.list_conversation_files(
        str(handoff.get("source_conversation_id") or "")
    )}
    for artifact in list(payload.get("artifact_refs") or []):
        if not isinstance(artifact, Mapping):
            continue
        filename = str(artifact.get("filename") or "")
        row = existing.get(filename)
        if row is None or not Path(str(row.get("path") or "")).is_file():
            reasons.append(f"artifact_missing:{filename}")
            continue
        sealed_hash = str(artifact.get("sha256") or "")
        if sealed_hash:
            try:
                with Path(str(row.get("path"))).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sealed_hash:
                        reasons.append(f"artifact_changed:{filename}")
            except OSError:
                reasons.append(f"artifact_unreadable:{filename}")
    workspace = _mapping(payload.get("workspace"))
    verification = _mapping(payload.get("verification"))
    run_id = str(verification.get("run_id") or "")
    if run_id:
        try:
            from hashmm.agent import work_runtime
            current_run = work_runtime.get_run(run_id, str(handoff.get("owner_id") or ""), limit=1)
            if current_run is None:
                reasons.append("work_run_missing")
            elif str(verification.get("run_status") or "") and str(current_run.get("status") or "") != str(verification.get("run_status") or ""):
                reasons.append("work_run_status_changed")
            checkpoint_id = str(verification.get("checkpoint_id") or "")
            if checkpoint_id and not any(str(item.get("id") or "") == checkpoint_id for item in list(current_run.get("checkpoints") or [])):
                reasons.append("checkpoint_missing")
        except Exception:
            reasons.append("work_run_verification_unavailable")
    if workspace.get("root") or workspace.get("git_head"):
        # Server-side Chat cannot prove a desktop worktree state.  The receiver
        # must ask the desktop bridge to verify it before reusing conclusions.
        reasons.append("workspace_requires_receiver_verification")
    return {
        "stale": bool(reasons),
        "reasons": reasons,
        "checked_at": time.time(),
    }


def incoming(owner_id: str, target_conversation_id: str) -> dict | None:
    handoff = db.get_incoming_conversation_handoff(target_conversation_id, owner_id)
    if not handoff:
        return None
    handoff["state_verification"] = verify_current_state(handoff)
    return handoff


def render_for_context(owner_id: str, target_conversation_id: str) -> str:
    handoff = incoming(owner_id, target_conversation_id)
    if not handoff or handoff.get("status") not in {"sealed", "claimed", "acknowledged"}:
        return ""
    payload = _mapping(handoff.get("payload"))
    state = _mapping(handoff.get("state_verification"))
    lines = [
        "[Verified Project Continuity / cross-Chat handoff]",
        f"- source_conversation_id: {handoff.get('source_conversation_id')}",
        f"- handoff_status: {handoff.get('status')}",
        f"- stale: {str(bool(state.get('stale'))).lower()}",
    ]
    if state.get("reasons"):
        lines.append("- revalidation_required: " + ", ".join(_safe_text_list(state.get("reasons"))))
    for key, label in (
        ("objective", "objective"),
        ("latest_user_request", "latest_user_request"),
        ("next_action", "next_action"),
    ):
        value = _text(payload.get(key), 1800)
        if value:
            lines.append(f"- {label}: {value}")
    for key in ("constraints", "completed_steps", "pending_steps", "blockers"):
        values = _safe_text_list(payload.get(key))
        if values:
            lines.append(f"- {key}:")
            lines.extend(f"  - {value}" for value in values)
    lines.extend([
        "- This handoff contains public task state only; it is not private reasoning.",
        "- Prior model claims are not execution evidence. Re-check artifacts, WorkRun receipts and Git state.",
        "- Prior approvals are references only and do not grant permission in this Chat.",
    ])
    return "\n".join(lines)[:12000]
