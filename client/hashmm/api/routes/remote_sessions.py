"""HTTP control plane for owner-bound remote sessions."""
from __future__ import annotations

import time
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from hashmm.api.auth import require_auth
from hashmm.api.remote_sessions import REMOTE_SCOPES, normalize_scopes, remote_session_registry
from hashmm.api.remote_devices import remote_device_registry
from hashmm.api.remote_transport import secure_remote_required, turn_configuration_status
from hashmm.api.remote_work import RemoteWorkNotFound, bind_remote_work


router = APIRouter(prefix="/api/remote", tags=["remote-sessions"])


class SessionRequest(BaseModel):
    host_device_id: str = Field(min_length=1, max_length=128)
    viewer_device_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["view", "control"], max_length=16)
    work_run_id: str = Field(default="", max_length=128)
    # Deprecated V420 request field. Conflicting values are rejected instead
    # of guessing which work the user intended to control.
    work_id: str = Field(default="", max_length=128)
    conversation_id: str = Field(default="", max_length=160)
    client_request_id: str = Field(default="", max_length=120)


class SessionDecision(BaseModel):
    host_device_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["view", "control"], max_length=16)
    reason: str = Field(default="", max_length=120)


class SessionRevoke(BaseModel):
    device_id: str = Field(default="", max_length=128)
    reason: str = Field(default="revoked", max_length=120)


class SessionHandoff(BaseModel):
    actor_device_id: str = Field(min_length=1, max_length=128)
    new_viewer_device_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list, max_length=16)


class SessionComplete(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="remote_session_completed", max_length=120)


class TicketRequest(BaseModel):
    role: str
    device_id: str = Field(min_length=1, max_length=128)


def _owned(request: Request, session_id: str):
    user = require_auth(request)
    session = remote_session_registry.get(user["uid"], session_id)
    if not session:
        # Missing and foreign sessions deliberately share the same result.
        raise HTTPException(status_code=404, detail="远程会话不存在")
    return user, session


@router.get("/config")
async def remote_config(request: Request):
    require_auth(request)
    turn = turn_configuration_status()
    return {
        "schema": "hashmm.remote.config.v2",
        "scopes": sorted(REMOTE_SCOPES),
        "default_scopes": ["view", "control"],
        "permission_mode": "ask",
        "ticket_ttl_seconds": 120,
        "turn_configured": turn["configured"],
        "turn_credential_mode": "temporary" if turn["dynamic_credentials"] else "static" if turn["static_credentials"] else "none",
        "secure_transport_required": secure_remote_required(),
        "audit_persistent": True,
        "handoff_supported": True,
        "completion_receipts": True,
        "public_url": os.environ.get("HASHMM_PUBLIC_URL", "").strip(),
        "protocol": "secure-remote-workspace/3.0",
    }


@router.get("/readiness")
async def remote_readiness(request: Request):
    user = require_auth(request)
    uid = user["uid"]
    turn = turn_configuration_status()
    network = remote_session_registry.latest_acceptance(uid, "network")
    soak = remote_session_registry.latest_acceptance(uid, "soak")
    now = time.time()
    network_criteria = ((network or {}).get("evidence") or {}).get("criteria", {})
    soak_criteria = ((soak or {}).get("evidence") or {}).get("criteria", {})
    network_fresh = bool(network and network.get("status") == "passed"
                         and network_criteria.get("same_session_relay_observed_on_both_ends") is True
                         and network_criteria.get("symmetric_nat_observed") is True
                         and now - float(network.get("created_at") or 0) <= 30 * 86400)
    soak_fresh = bool(soak and soak.get("status") == "passed"
                      and soak_criteria.get("remote_relay_coverage_reached") is True
                      and float(soak.get("duration_seconds") or 0) >= 86400
                      and now - float(soak.get("created_at") or 0) <= 14 * 86400)
    criteria = {
        "secure_transport_enforced": secure_remote_required(),
        "public_https_url": os.environ.get("HASHMM_PUBLIC_URL", "").strip().lower().startswith("https://"),
        "supabase_identity": _supabase_identity_ready(),
        "temporary_turn_credentials": bool(turn["dynamic_credentials"]),
        "public_multi_device_acceptance": network_fresh,
        "twenty_four_hour_soak": soak_fresh,
        "persistent_audit": True,
        "owner_scoped_device_registry": True,
    }
    fabric = remote_device_registry.diagnostics(uid)
    return {
        "schema": "hashmm.remote.readiness.v3",
        "production_ready": all(criteria.values()),
        "criteria": criteria,
        "fabric": fabric,
        "network_acceptance": _acceptance_projection(network),
        "soak_acceptance": _acceptance_projection(soak),
    }


def _supabase_identity_ready() -> bool:
    try:
        from hashmm.api import supabase_auth
        return bool(supabase_auth.enabled() and supabase_auth.publishable_key())
    except Exception:
        return False


def _acceptance_projection(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "run_id": record.get("run_id"), "status": record.get("status"),
        "created_at": record.get("created_at"), "duration_seconds": record.get("duration_seconds"),
        "device_count": record.get("device_count"),
    }


@router.get("/sessions")
async def list_remote_sessions(request: Request):
    user = require_auth(request)
    return {"schema": "hashmm.remote.sessions.v3", "sessions": remote_session_registry.list_for_owner(user["uid"])}


@router.get("/audit")
async def owner_remote_audit(request: Request, since: float = Query(default=0, ge=0),
                             limit: int = Query(default=500, ge=1, le=2000)):
    """Bounded owner-only stream used by operations and real soak acceptance."""
    user = require_auth(request)
    return {
        "schema": "hashmm.remote.owner-audit.v1",
        "events": remote_session_registry.audit_snapshot(user["uid"], since_at=since, limit=limit),
    }


@router.post("/sessions")
async def request_remote_session(body: SessionRequest, request: Request):
    user = require_auth(request)
    scopes = normalize_scopes(body.scopes)
    if not scopes:
        raise HTTPException(status_code=400, detail="empty_scopes")
    if body.host_device_id == body.viewer_device_id:
        raise HTTPException(status_code=400, detail="same_device")
    request_id = body.client_request_id.strip()
    if request_id and (len(request_id) < 8 or not all(
        char.isalnum() or char in "_.:-" for char in request_id
    )):
        raise HTTPException(status_code=400, detail="invalid_client_request_id")
    if body.work_run_id and body.work_id and body.work_run_id != body.work_id:
        raise HTTPException(status_code=400, detail="work_run_id_conflict")
    requested_work = body.work_run_id or body.work_id
    try:
        work_run_id = bind_remote_work(
            user["uid"],
            requested_work,
            host_device_id=body.host_device_id,
            viewer_device_id=body.viewer_device_id,
            conversation_id=body.conversation_id,
            source_id=f"remote-request:{request_id}" if request_id else "",
        )
    except RemoteWorkNotFound as exc:
        # A missing work and another owner's work are deliberately
        # indistinguishable.
        raise HTTPException(status_code=404, detail="work_run_not_found") from exc
    try:
        session = remote_session_registry.request(user["uid"], body.host_device_id, body.viewer_device_id,
                                                  scopes, work_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": session.public()}


@router.post("/sessions/{session_id}/approve")
async def approve_remote_session(session_id: str, body: SessionDecision, request: Request):
    user, _ = _owned(request, session_id)
    session = remote_session_registry.approve(user["uid"], session_id, body.host_device_id, body.scopes)
    if not session:
        raise HTTPException(status_code=404, detail="远程会话不存在")
    return {"session": session.public()}


@router.post("/sessions/{session_id}/deny")
async def deny_remote_session(session_id: str, body: SessionDecision, request: Request):
    user, _ = _owned(request, session_id)
    session = remote_session_registry.deny(user["uid"], session_id, body.host_device_id, body.reason or "denied")
    if not session:
        raise HTTPException(status_code=404, detail="远程会话不存在")
    return {"session": session.public()}


@router.post("/sessions/{session_id}/revoke")
async def revoke_remote_session(session_id: str, body: SessionRevoke, request: Request):
    user, _ = _owned(request, session_id)
    session = remote_session_registry.revoke(user["uid"], session_id, body.device_id, body.reason)
    if not session:
        raise HTTPException(status_code=404, detail="远程会话不存在")
    return {"session": session.public()}


@router.post("/sessions/{session_id}/handoff")
async def handoff_remote_session(session_id: str, body: SessionHandoff, request: Request):
    user, current = _owned(request, session_id)
    requested_scopes = normalize_scopes(body.scopes) if body.scopes else current.granted_scopes
    if not requested_scopes:
        raise HTTPException(status_code=400, detail="empty_scopes")
    try:
        successor = remote_session_registry.handoff(
            user["uid"], session_id, body.actor_device_id,
            body.new_viewer_device_id, requested_scopes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not successor:
        raise HTTPException(status_code=404, detail="remote_session_not_found")
    return {
        "schema": "hashmm.remote.handoff.v1",
        "predecessor_session_id": session_id,
        "session": successor.public(),
    }


@router.post("/sessions/{session_id}/complete")
async def complete_remote_session(session_id: str, body: SessionComplete, request: Request):
    user, _ = _owned(request, session_id)
    try:
        session = remote_session_registry.complete(
            user["uid"], session_id, body.device_id, body.summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not session:
        raise HTTPException(status_code=404, detail="remote_session_not_found")
    return {
        "schema": "hashmm.remote.completion.v1",
        "session": session.public(),
    }


@router.post("/sessions/{session_id}/ticket")
async def issue_remote_ticket(session_id: str, body: TicketRequest, request: Request):
    user, _ = _owned(request, session_id)
    token = remote_session_registry.issue_ticket(user["uid"], session_id, body.role, body.device_id)
    if not token:
        raise HTTPException(status_code=404, detail="远程会话不存在")
    return {"ticket": token, "token_type": "Remote", "expires_in": 120}


@router.get("/sessions/{session_id}/audit")
async def remote_session_audit(session_id: str, request: Request):
    user, _ = _owned(request, session_id)
    events = remote_session_registry.audit_for_session(user["uid"], session_id)
    return {"schema": "hashmm.remote.audit.v1", "events": events}
