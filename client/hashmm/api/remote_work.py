"""Bind remote-control sessions to the owner-scoped WorkRuntime ledger.

Remote signalling is ephemeral.  User-visible progress is not: a request,
approval, revocation or interruption must be projected into the same durable
work run used by Chat, App and desktop.  This module is intentionally small so
the HTTP and WebSocket admission paths cannot grow different authorization
rules.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Any, TYPE_CHECKING

from hashmm.agent import work_runtime
from hashmm.utils import get_logger

if TYPE_CHECKING:
    from hashmm.api.remote_sessions import RemoteSession


logger = get_logger("hashmm.remote_work")

_WORK_ID = re.compile(r"^[A-Za-z0-9_.:-]{3,128}$")
_PROJECTED_EVENTS = {
    "requested", "approved", "denied", "revoked", "expired", "interrupted",
    "transport_observed", "superseded", "completed",
}
_REMOTE_TERMINAL_STATUS = {
    "denied": "cancelled",
    "revoked": "interrupted",
    "expired": "interrupted",
    "interrupted": "interrupted",
    "completed": "delivered",
}


class RemoteWorkNotFound(LookupError):
    """Requested work is absent or belongs to another owner."""


def _digest(value: str) -> str:
    """Return a stable non-reversible device reference for durable metadata."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def bind_remote_work(
    user_id: str,
    requested_work_run_id: str = "",
    *,
    host_device_id: str = "",
    viewer_device_id: str = "",
    conversation_id: str = "",
    source_id: str = "",
) -> str:
    """Resolve an owned work run or create a dedicated remote work run.

    Missing and foreign identifiers deliberately use the same exception.  A
    caller must never fall back to an unowned string after this function
    rejects it.
    """
    owner = str(user_id or "").strip()[:160]
    requested = str(requested_work_run_id or "").strip()[:128]
    if not owner:
        raise RemoteWorkNotFound("work_run_not_found")
    if requested:
        if not _WORK_ID.fullmatch(requested):
            raise RemoteWorkNotFound("work_run_not_found")
        run = work_runtime.get_run(requested, owner, after_seq=0, limit=1)
        if run is None:
            raise RemoteWorkNotFound("work_run_not_found")
        return str(run["id"])

    source = str(source_id or "").strip()[:180] or f"remote:{uuid.uuid4().hex}"
    run = work_runtime.create_run(
        user_id=owner,
        kind="remote",
        source_id=source,
        conv_id=str(conversation_id or "")[:160],
        title="远程协作",
        status="waiting_approval",
        snapshot={
            "remote_execution": {
                "state": "preparing",
                "host_device_ref": _digest(host_device_id) if host_device_id else "",
                "viewer_device_ref": _digest(viewer_device_id) if viewer_device_id else "",
            }
        },
    )
    return str(run["id"])


def project_remote_transition(
    session: "RemoteSession", event: str, detail: dict[str, Any] | None = None
) -> None:
    """Idempotently project a bounded remote lifecycle fact into WorkRuntime."""
    event_name = str(event or "").strip().lower()
    run_id = str(session.work_run_id or "").strip()
    if not run_id or event_name not in _PROJECTED_EVENTS:
        return
    owner = str(session.uid or "").strip()
    run = work_runtime.get_run(run_id, owner, after_seq=0, limit=1)
    if run is None:
        # Legacy rows are intentionally not attached merely because their old
        # work_id happens to contain text.
        logger.warning("Remote session %s references unavailable owned work", session.id)
        return

    safe_detail = detail if isinstance(detail, dict) else {}
    scopes = (
        list(session.granted_scopes)
        if session.granted_scopes
        else list(session.requested_scopes)
    )
    payload: dict[str, Any] = {
        "session_id": session.id,
        "state": session.state,
        "generation": session.generation,
        "scopes": scopes[:16],
    }
    if session.predecessor_session_id:
        payload["predecessor_session_id"] = session.predecessor_session_id
    reason = str(safe_detail.get("reason") or session.reason or "")[:120]
    if reason:
        payload["reason"] = reason
    if event_name == "transport_observed":
        role = str(safe_detail.get("role") or "")
        if role in {"host", "viewer"}:
            payload["role"] = role
        candidate = str(safe_detail.get("candidate_type") or "unknown")
        protocol = str(safe_detail.get("protocol") or "unknown")
        payload["candidate_type"] = (
            candidate if candidate in {"host", "srflx", "prflx", "relay", "unknown"}
            else "unknown"
        )
        payload["protocol"] = (
            protocol if protocol in {"udp", "tcp", "tls", "unknown"}
            else "unknown"
        )
        for key in ("rtt_ms", "packet_loss_pct"):
            value = safe_detail.get(key)
            if isinstance(value, (int, float)):
                payload[key] = float(value)
    if event_name == "completed":
        roles = [
            role for role in list(safe_detail.get("transport_roles") or [])[:2]
            if role in {"host", "viewer"}
        ]
        verification = str(safe_detail.get("verification") or "not_observed")
        payload["transport_roles"] = roles
        try:
            sample_count = int(safe_detail.get("transport_samples") or 0)
        except (TypeError, ValueError):
            sample_count = 0
        payload["transport_samples"] = max(0, min(sample_count, 100_000))
        payload["verification"] = (
            verification if verification in {"verified", "partial", "not_observed"}
            else "not_observed"
        )
    if event_name == "superseded":
        successor = str(safe_detail.get("successor_session_id") or "")[:128]
        if successor:
            payload["successor_session_id"] = successor

    status = ""
    if str(run.get("kind") or "") == "remote":
        if event_name == "requested":
            status = "waiting_approval"
        elif event_name == "approved":
            status = "running"
        elif event_name == "superseded":
            status = "waiting_approval"
        else:
            status = _REMOTE_TERMINAL_STATUS.get(event_name, "")

    summaries = {
        "requested": "已请求使用远程电脑，等待设备确认",
        "approved": "远程设备已授权，工作可以继续",
        "denied": "远程设备拒绝了本次授权",
        "revoked": "远程授权已撤销",
        "expired": "远程授权已过期",
        "interrupted": "远程连接已中断",
        "transport_observed": "远程连接质量已更新",
        "superseded": "远程工作正在接力到另一台设备",
        "completed": "远程协作已正常结束，等待检查结果",
    }
    snapshot_updates: dict[str, Any]
    if event_name == "transport_observed":
        previous = dict((run.get("snapshot") or {}).get("remote_transport") or {})
        role = str(payload.get("role") or "unknown")
        previous[role] = payload
        snapshot_updates = {"remote_transport": previous}
    elif event_name == "superseded":
        snapshot_updates = {"remote_handoff": payload}
    elif event_name == "completed":
        snapshot_updates = {
            "remote_completion": {
                "schema": "hashmm.remote-completion.v1",
                **payload,
                "completed_at": time.time(),
            },
            "remote_session": payload,
        }
    else:
        snapshot_updates = {"remote_session": payload}
    bucket = (
        f":{str(payload.get('role') or 'unknown')}:{int(time.time() // 60)}"
        if event_name == "transport_observed" else ""
    )
    result = work_runtime.append_event_once(
        run_id,
        user_id=owner,
        event_type=f"remote.{event_name}",
        summary=summaries[event_name],
        status=status,
        payload=payload,
        snapshot_updates=snapshot_updates,
        idempotency_key=(
            f"remote:{session.id}:{session.generation}:{event_name}{bucket}"
        ),
    )
    result_state = str(result.get("state") or "")
    current_status = str(
        result.get("current_status")
        or (result.get("run") or {}).get("status")
        or run.get("status")
        or ""
    )
    if result_state == "invalid_transition" and current_status in {
        "delivered", "cancelled", "interrupted", "failed", "completed",
    }:
        # A late socket close after a durable terminal receipt is an idempotent
        # observation, not a second Work transition.
        return
    if result_state not in {"applied", "duplicate"}:
        logger.warning(
            "Failed to project remote session %s to work %s: %s",
            session.id,
            run_id,
            result.get("state"),
        )
