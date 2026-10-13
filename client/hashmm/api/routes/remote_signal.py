"""hashmm/api/routes/remote_signal.py — 账号级远程信令 WebSocket 端点（薄适配层）。

端点：WS /api/remote/ws
首条消息必须是 {type:"auth", token, role:"host"|"viewer", name, platform}；token 用账号登录
令牌（与其它 API 同一套 JWT）。认证通过后加入该账号房间，由 SignalHub 在同账号 viewer⇄host
之间中继信令与输入。真实屏幕视频走 WebRTC P2P 直连，不经此服务器。

ICE 服务器可经环境变量 HASHMM_ICE_SERVERS（JSON 数组）配置，默认免费公共 STUN。
"""
from __future__ import annotations

import json
import hashlib
import asyncio
import secrets
import time
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from hashmm.api.auth import verify_any_token, get_current_user, require_auth
from hashmm.api.remote_devices import remote_device_registry
from hashmm.api.remote_hub import SignalHub, Conn
from hashmm.api.remote_sessions import remote_session_registry
from hashmm.api.remote_transport import (
    REMOTE_PROTOCOL, LEGACY_REMOTE_PROTOCOL, REMOTE_TIME_BUDGETS,
    issue_ice_servers, load_ice_servers, remote_bootstrap_config,
    request_is_secure, request_security_context, secure_remote_required,
    turn_configuration_status,
)
from hashmm.api.remote_work import bind_remote_work
from hashmm.agent import dispatch as dispatch_queue
from hashmm.release import MINIMUM_COMPATIBLE
from hashmm.utils import get_logger

router = APIRouter(prefix="/api/remote", tags=["remote"])
logger = get_logger("hashmm.remote_signal")


hub = SignalHub(ice_servers=load_ice_servers(), session_registry=remote_session_registry,
                ice_provider=issue_ice_servers, work_binder=bind_remote_work,
                device_registry=remote_device_registry)


def _safe(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _attempt(value: str) -> str:
    """Return a bounded non-secret correlation id suitable for support logs."""
    text = str(value or "unknown")[:64]
    return text if all(ch.isalnum() or ch in "_-" for ch in text) else "invalid"


def _log_pre_auth_exit(socket_id: str, stage: str, attempt_id: str,
                       started_at: float, close_code: int) -> None:
    logger.warning(
        "remote_pre_auth_rejected socket=%s stage=%s code=%s attempt=%s duration_ms=%d",
        socket_id, stage, close_code, _attempt(attempt_id),
        int((time.monotonic() - started_at) * 1000),
    )


def _socket_error_code(exc: Exception) -> str:
    """Map socket failures to stable, non-secret support codes."""
    name = type(exc).__name__.lower()
    message = str(exc or "").lower()
    if "disconnect" in name or "disconnect" in message or "closed" in message:
        return "CONTROL_SOCKET_LOST"
    if "timeout" in name or "timeout" in message:
        return "CONTROL_SOCKET_TIMEOUT"
    return "REMOTE_INTERNAL_ERROR"


@router.get("/v4/bootstrap")
@router.get("/v3/bootstrap")
async def remote_bootstrap(request: Request):
    """Return the only production control-plane endpoints clients may use."""
    require_auth(request)
    try:
        protocol = REMOTE_PROTOCOL if "/v4/" in request.url.path else LEGACY_REMOTE_PROTOCOL
        result = remote_bootstrap_config(protocol=protocol)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    now = int(time.time())
    minimum_desktop = str(MINIMUM_COMPATIBLE.get("desktop") or "").strip()
    return {**result, "issued_at": now, "expires_at": now + 3600,
            "min_client_version": str(os.environ.get(
                "HASHMM_REMOTE_MIN_CLIENT_VERSION", minimum_desktop
            ))[:40]}


@router.get("/v4/preflight")
async def remote_preflight(request: Request, device_id: str = ""):
    """Secret-free deployment readiness; client reachability still requires ICE evidence."""
    user = require_auth(request)
    turn = turn_configuration_status()
    return {
        "schema": "hashmm.remote-preflight.v4",
        "protocol": REMOTE_PROTOCOL,
        "owner_fingerprint": _safe(str(user["uid"]).removeprefix("sb_")),
        "device_fingerprint": _safe(str(device_id or "")),
        "control_secure": request_is_secure(request) or not secure_remote_required(),
        "turn": turn,
        "paths": {
            "direct": "candidate_required",
            "turn_udp": "candidate_required" if turn.get("configured") else "not_configured",
            "turn_tcp_tls": "candidate_required" if turn.get("configured") else "not_configured",
            "compat_preview": "diagnostic_only",
        },
        "time_budgets": dict(REMOTE_TIME_BUDGETS),
        "note": "configured is not connectivity proof; selected relay candidates are required",
    }


@router.post("/v4/socket-ticket")
@router.post("/v3/socket-ticket")
@router.post("/v2/socket-ticket")
async def remote_socket_ticket(request: Request):
    """Issue a single-use, 30-second WebSocket admission ticket."""
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    role = str(body.get("role") or "")
    device_id = str(body.get("device_id") or body.get("deviceId") or "")
    protocol = (REMOTE_PROTOCOL if "/v4/" in request.url.path else
                LEGACY_REMOTE_PROTOCOL if "/v3/" in request.url.path else "hashmm.remote.v2")
    try:
        bootstrap = remote_bootstrap_config(protocol=protocol) if protocol in {REMOTE_PROTOCOL, LEGACY_REMOTE_PROTOCOL} else {}
        result = remote_device_registry.issue_socket_ticket(
            str(user["uid"]), device_id, role,
            trace_id=str(body.get("trace_id") or body.get("traceId") or ""),
            protocol=protocol,
            endpoint_host=str(request.headers.get("host") or ""),
        )
        logger.info(
            "remote_ticket_issued owner=%s device=%s role=%s attempt=%s",
            _safe(str(user["uid"])), _safe(device_id), role,
            _attempt(str(result.get("attempt_id") or "")),
        )
        return {**result, **({"control_wss": bootstrap["control_wss"],
                              "config_revision": bootstrap["config_revision"]} if bootstrap else {})}
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v4/devices")
@router.get("/v3/devices")
@router.get("/v2/devices")
async def remote_devices(request: Request, include_offline: bool = True):
    user = require_auth(request)
    items = remote_device_registry.list_devices(
        str(user["uid"]), include_offline=bool(include_offline)
    )
    return {
        "protocol": (REMOTE_PROTOCOL if "/v4/" in request.url.path else
                     LEGACY_REMOTE_PROTOCOL if "/v3/" in request.url.path else "hashmm.remote.v2"),
        "owner_fingerprint": _safe(str(user["uid"]).removeprefix("sb_")),
        "lease_ttl_seconds": remote_device_registry.lease_ttl,
        "devices": items,
    }


@router.get("/v4/diagnostics/self")
@router.get("/v3/diagnostics/self")
@router.get("/v2/diagnostics/self")
async def remote_diagnostics(request: Request, device_id: str = ""):
    user = require_auth(request)
    return remote_device_registry.diagnostics(str(user["uid"]), str(device_id or ""))


@router.post("/v2/devices/register")
async def remote_register_device(request: Request):
    """HTTP registration is used by diagnostics and non-WebSocket agents.

    Interactive remote hosts normally register atomically through the socket
    hub so a listed remote-ready device always has a routable live connection.
    """
    user = require_auth(request)
    try:
        body = await request.json()
        return remote_device_registry.register(
            str(user["uid"]), str(body.get("device_id") or ""),
            connection_id=str(body.get("connection_id") or "http-diagnostic"),
            role=str(body.get("role") or "viewer"),
            name=str(body.get("name") or ""), platform=str(body.get("platform") or ""),
            app_version=str(body.get("app_version") or ""),
            capabilities=body.get("capabilities") if isinstance(body.get("capabilities"), list) else [],
        )
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v2/devices/heartbeat")
async def remote_device_heartbeat(request: Request):
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = remote_device_registry.heartbeat(
        str(user["uid"]), str(body.get("device_id") or ""),
        str(body.get("lease_id") or ""), int(body.get("generation") or 0),
        remote_ready=body.get("remote_ready") if isinstance(body.get("remote_ready"), bool) else None,
        busy=body.get("busy") if isinstance(body.get("busy"), bool) else None,
    )
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="stale_or_missing_lease")
    return {"ok": True, "lease_expires_in": remote_device_registry.lease_ttl}


@router.get("/status")
async def remote_status(request: Request):
    """当前账号在线的被控端（桌面客户端）状态，供 App 显示「客户端在线」并启用远程控制。

    同账号校验：只统计与当前登录用户同一 uid 的 host，跨账号不可见。
    """
    user = get_current_user(request)
    if not user or not user.get("uid"):
        return {"online": False, "count": 0, "hosts": []}
    owner = str(user["uid"])
    hosts = hub.hosts(owner)
    runners = dispatch_queue.runners_status(
        owner=str(user.get("uid") or ""),
        created_by=str(user.get("sub") or ""),
    )
    runner_by_device = {
        str(item.get("device_id") or item.get("id") or ""): item
        for item in runners
        if item.get("device_id") or item.get("id")
    }
    host_by_device = {str(item.device_id): item for item in hosts}
    registry_devices = {
        str(item.get("device_id") or ""): item
        for item in remote_device_registry.list_devices(owner, include_offline=True)
    }
    device_ids = sorted(set(runner_by_device) | set(host_by_device) | set(registry_devices))
    devices = []
    for device_id in device_ids:
        runner = runner_by_device.get(device_id) or {}
        host = host_by_device.get(device_id)
        registered = registry_devices.get(device_id) or {}
        capabilities = []
        if runner and runner.get("online"):
            capabilities.append("dispatch")
        if host is not None:
            capabilities.extend(["remote_view", "remote_control"])
        devices.append({
            "device_id": device_id,
            "connection_id": host.id if host is not None else "",
            "name": (host.name if host is not None else str(registered.get("name") or runner.get("name") or "电脑")),
            "platform": (host.platform if host is not None else str(registered.get("platform") or "desktop")),
            "app_version": str(registered.get("app_version") or runner.get("version") or ""),
            "online": bool((runner and runner.get("online")) or registered.get("online") or host is not None),
            # The live hub is the routing truth for this compatibility
            # endpoint.  Rich lease state is available from /v2/devices.
            "remote_ready": host is not None,
            "capabilities": capabilities,
            "last_seen": registered.get("last_seen") or runner.get("last_seen"),
        })
    return {
        "online": len(hosts) > 0,
        "count": len(hosts),
        "desktop_count": sum(1 for item in devices if item["online"]),
        "hosts": [{"id": h.id, "device_id": h.device_id, "name": h.name, "platform": h.platform} for h in hosts],
        "devices": devices,
    }


async def _dispatch(actions):
    for c, msg in actions:
        try:
            if c.ws is not None:
                await c.ws.send_text(json.dumps(msg))
        except Exception:
            pass


@router.websocket("/v4/ws")
@router.websocket("/v3/ws")
@router.websocket("/ws")
async def remote_ws(ws: WebSocket):
    socket_id = "rs_" + secrets.token_hex(6)
    started_at = time.monotonic()
    stage = "accepting"
    await ws.accept()
    stage = "accepted"
    conn = None
    attempt_fingerprint = _attempt(str(ws.query_params.get("attempt_id") or ""))
    trace_id = str(ws.query_params.get("trace_id") or "")[:96]
    security_context = request_security_context(ws)
    try:
        if secure_remote_required() and not request_is_secure(ws):
            stage = "secure_transport_rejected"
            forwarded = str(security_context.get("forwarded_proto") or "")
            if forwarded in ("http", "ws"):
                error_code = "PUBLIC_ENDPOINT_INSECURE"
            elif security_context.get("trusted_proxy") and not forwarded:
                error_code = "EDGE_HEADER_MISSING"
            else:
                error_code = "PROXY_SECURITY_UNVERIFIED"
            remote_device_registry.transition_attempt(
                attempt_fingerprint, stage, error_code=error_code, close_code=4403,
                security=security_context, detail="secure transport rejected before authentication", terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "secure_transport_required",
                                           "errorCode": error_code, "traceId": trace_id}))
            await ws.close(code=4403)
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4403)
            return
        stage = "waiting_first_frame"
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
        stage = "first_frame_received"
        if len(first) > 16_384:
            stage = "auth_frame_too_large"
            await ws.close(code=1009)
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 1009)
            return
        try:
            msg = json.loads(first)
        except Exception:
            stage = "bad_auth_json"
            await ws.send_text(json.dumps({"type": "authFail", "reason": "bad_auth"}))
            await ws.close()
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4400)
            return
        if msg.get("type") != "auth":
            stage = "auth_message_missing"
            await ws.send_text(json.dumps({"type": "authFail", "reason": "need_auth"}))
            await ws.close()
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4400)
            return
        ticket = str(msg.get("ticket") or "")
        stage = "consuming_ticket" if ticket else "verifying_token"
        ticket_data = remote_device_registry.consume_socket_ticket_detailed(ticket) if ticket else None
        ticket_attempt = _attempt(str((ticket_data or {}).get("attempt_id") or "legacy"))
        if ticket_data and any(part in ws.url.path for part in ("/v3/", "/v4/")) and attempt_fingerprint not in ("", "unknown", ticket_attempt):
            remote_device_registry.transition_attempt(
                ticket_attempt, "attempt_mismatch", error_code="ATTEMPT_MISMATCH",
                close_code=4403, security=security_context, terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "attempt_mismatch",
                                           "errorCode": "ATTEMPT_MISMATCH"}))
            await ws.close(code=4403)
            return
        expected_protocol = (REMOTE_PROTOCOL if "/v4/" in ws.url.path else
                             LEGACY_REMOTE_PROTOCOL if "/v3/" in ws.url.path else "")
        if ticket_data and expected_protocol and str(ticket_data.get("protocol") or "") != expected_protocol:
            remote_device_registry.transition_attempt(
                ticket_attempt, "protocol_mismatch", error_code="PROTOCOL_MISMATCH",
                close_code=4406, security=security_context, terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "protocol_mismatch",
                                           "errorCode": "PROTOCOL_MISMATCH"}))
            await ws.close(code=4406)
            return
        attempt_fingerprint = ticket_attempt
        remote_device_registry.transition_attempt(attempt_fingerprint, "authenticating", security=security_context)
        data = ({"uid": ticket_data["uid"]} if ticket_data else verify_any_token(msg.get("token", "")))
        if not data or not data.get("uid"):
            stage = "credential_rejected"
            error_code = "TICKET_INVALID_OR_REPLAYED" if ticket else "TOKEN_INVALID"
            remote_device_registry.transition_attempt(
                attempt_fingerprint, stage, error_code=error_code, close_code=4401,
                security=security_context, terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "invalid_token",
                                           "errorCode": error_code}))
            await ws.close()
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4401)
            return

        stage = "validating_identity"
        role = str((ticket_data or {}).get("role") or msg.get("role") or "")
        if role not in ("host", "viewer"):
            stage = "role_rejected"
            remote_device_registry.transition_attempt(
                attempt_fingerprint, stage, error_code="ROLE_MISMATCH", close_code=4403,
                security=security_context, terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "invalid_role"}))
            await ws.close()
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4403)
            return
        device_id = str((ticket_data or {}).get("device_id") or msg.get("deviceId") or "").strip()
        if not device_id or len(device_id) > 128:
            stage = "device_rejected"
            remote_device_registry.transition_attempt(
                attempt_fingerprint, stage, error_code="DEVICE_ID_INVALID", close_code=4403,
                security=security_context, terminal=True,
            )
            await ws.send_text(json.dumps({"type": "authFail", "reason": "invalid_device"}))
            await ws.close()
            _log_pre_auth_exit(socket_id, stage, attempt_fingerprint, started_at, 4403)
            return
        conn = Conn(uid=data["uid"], role=role,
                    name=str(msg.get("name", ""))[:80], platform=str(msg.get("platform", ""))[:40],
                    device_id=device_id, app_version=str(msg.get("appVersion", ""))[:40],
                    protocol=str((ticket_data or {}).get("protocol") or msg.get("protocol") or "hashmm.remote.v2"))
        conn.ws = ws
        stage = "registering"
        await _dispatch(hub.add(conn))
        stage = "registered"
        remote_device_registry.transition_attempt(attempt_fingerprint, "registered", security=security_context)
        logger.info("remote_registered owner=%s device=%s role=%s conn=%s protocol=%s attempt=%s",
                    _safe(conn.uid), _safe(conn.device_id), conn.role, conn.id,
                    conn.protocol if ticket_data else "v1-token",
                    _attempt(str((ticket_data or {}).get("attempt_id") or "legacy")))

        while True:
            txt = await ws.receive_text()
            if len(txt) > 1_048_576:
                await ws.close(code=1009)
                return
            try:
                m = json.loads(txt)
            except Exception:
                continue
            if not isinstance(m, dict):
                continue
            message_type = str(m.get("type") or "")
            if message_type in {"connect", "permissionDecision"}:
                logger.info(
                    "remote_control_event owner=%s device=%s role=%s type=%s session=%s decision=%s",
                    _safe(conn.uid), _safe(conn.device_id), conn.role, message_type,
                    _safe(str(m.get("sessionId") or "")),
                    str(m.get("decision") or "-")[:16],
                )
            await _dispatch(hub.on_message(conn, m))
    except WebSocketDisconnect as exc:
        if conn is not None:
            logger.info("remote_disconnected owner=%s device=%s role=%s code=%s",
                        _safe(conn.uid), _safe(conn.device_id), conn.role, getattr(exc, "code", ""))
        else:
            logger.warning(
                "remote_pre_auth_disconnected socket=%s stage=%s code=%s attempt=%s duration_ms=%d",
                socket_id, stage, getattr(exc, "code", ""), _attempt(attempt_fingerprint),
                int((time.monotonic() - started_at) * 1000),
            )
        remote_device_registry.transition_attempt(
            attempt_fingerprint, "closed", close_code=int(getattr(exc, "code", 0) or 0),
            security=security_context, terminal=True,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "remote_pre_auth_timeout socket=%s stage=%s attempt=%s duration_ms=%d",
            socket_id, stage, _attempt(attempt_fingerprint),
            int((time.monotonic() - started_at) * 1000),
        )
        try:
            await ws.close(code=4408)
        except Exception:
            pass
        remote_device_registry.transition_attempt(
            attempt_fingerprint, "auth_timeout", error_code="AUTH_TIMEOUT", close_code=4408,
            security=security_context, terminal=True,
        )
    except Exception as exc:
        error_code = _socket_error_code(exc)
        logger.warning("remote_socket_failed owner=%s device=%s role=%s error=%s error_code=%s stage=%s socket=%s attempt=%s duration_ms=%d",
                       _safe(conn.uid) if conn else "unknown",
                       _safe(conn.device_id) if conn else "unknown",
                       conn.role if conn else "unknown", type(exc).__name__, error_code, stage, socket_id,
                       _attempt(attempt_fingerprint), int((time.monotonic() - started_at) * 1000))
        if conn is not None and conn.role == "viewer" and conn.session_id:
            remote_session_registry.record_milestone(
                conn.uid, conn.session_id, conn.role, conn.device_id,
                "terminal_error", {"stage": "control_socket", "errorCode": error_code},
            )
        remote_device_registry.transition_attempt(
            attempt_fingerprint, "socket_failed", error_code=error_code,
            security=security_context, detail=type(exc).__name__, terminal=True,
        )
    finally:
        if conn is not None:
            await _dispatch(hub.remove(conn))
