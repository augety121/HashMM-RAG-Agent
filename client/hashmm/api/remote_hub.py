"""hashmm/api/remote_hub.py — 账号级远程信令中继（纯逻辑，不依赖 FastAPI，便于单测）。

同一账号（uid）的设备连进同一个「房间」。被控端注册为 host，控制端为 viewer。
本中继只在同账号的 viewer ⇄ host 之间转发 WebRTC 信令(offer/answer/ICE)与输入事件——
真正的屏幕视频走 viewer↔host 的 WebRTC P2P 直连，不经过本服务器（省带宽、低延迟）。
跨网络之所以无需用户自己的服务器：用你**已有的公网账号后端**当这个「碰头点」即可。

设计要点：on_message / add / remove 都**返回待发送动作列表** [(conn, message), ...]，
不直接做 IO，因此与 asyncio/FastAPI 解耦、可用假连接对象单测（见 tests/test_remote_hub.py）。
"""
from __future__ import annotations

import time
import uuid
import re
from typing import Any, Callable, Dict, List, Tuple, Optional

from hashmm.api.remote_sessions import RemoteSessionRegistry, normalize_scopes


DEFAULT_ICE = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
]
_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")


class Conn:
    """一个已认证连接。adapter 会把真实 WebSocket 挂到 .ws 上；hub 只认 .send 抽象。"""

    def __init__(self, uid: str, role: str, name: str = "", platform: str = "", device_id: str = "",
                 app_version: str = "", protocol: str = "hashmm.remote.v2"):
        self.id = uuid.uuid4().hex[:12]
        self.uid = str(uid)
        self.role = "host" if role == "host" else "viewer"
        self.name = name or ("被控端" if self.role == "host" else "查看端")
        self.platform = platform or ""
        self.device_id = (device_id or self.id)[:128]
        self.app_version = str(app_version or "")[:40]
        self.protocol = str(protocol or "hashmm.remote.v2")[:40]
        self.peer: Optional[str] = None      # viewer：所连 host 的 conn id
        self.viewers: set = set()            # host：已连入的 viewer conn id 集合
        self.session_id: Optional[str] = None
        self.session_ids: Dict[str, str] = {}  # host: viewer conn id -> session id
        self.ts = time.time()
        self.ws: Any = None                  # adapter 填充（真实 WebSocket）
        self.lease_id: str = ""
        self.generation: int = 0


Action = Tuple[Conn, Dict[str, Any]]


class SignalHub:
    def __init__(self, ice_servers: Optional[List[dict]] = None,
                 session_registry: Optional[RemoteSessionRegistry] = None,
                 ice_provider: Optional[Callable[[str, str], List[dict]]] = None,
                 work_binder: Optional[Callable[..., str]] = None,
                 device_registry: Any = None):
        self.accounts: Dict[str, Dict[str, Conn]] = {}    # uid -> {conn_id -> Conn}
        self.ice_servers = ice_servers or list(DEFAULT_ICE)
        # ``None`` keeps the pure hub backwards compatible for local/LAN unit
        # users.  The production route always injects the shared registry.
        self.sessions = session_registry
        self.ice_provider = ice_provider
        self.work_binder = work_binder
        self.device_registry = device_registry
        self.pending: Dict[str, Tuple[str, str, str]] = {}  # session -> (host conn, viewer conn, transport mode)

    # ── 注册 / 注销 ──────────────────────────────────────────
    def _room(self, uid: str) -> Dict[str, Conn]:
        return self.accounts.get(str(uid), {})

    def add(self, conn: Conn) -> List[Action]:
        room = self.accounts.setdefault(conn.uid, {})
        actions: List[Action] = []
        # Stable device identity fences an older socket for the same role and
        # device.  The stale connection may still physically exist for a short
        # time, but it is no longer routable and cannot remove the new lease.
        for existing in list(room.values()):
            if existing.role == conn.role and existing.device_id == conn.device_id:
                room.pop(existing.id, None)
                actions.append((existing, {"type": "deviceReplaced", "reason": "new_generation"}))
        room[conn.id] = conn
        ice_servers = self.ice_servers
        if self.ice_provider is not None:
            try:
                provided = self.ice_provider(conn.uid, conn.device_id)
                if provided:
                    ice_servers = provided
            except Exception:
                # Static validated ICE remains a safe availability fallback;
                # a production deployment can set HASHMM_REMOTE_REQUIRE_TURN=1
                # to make missing dynamic TURN fail at startup instead.
                pass
        registration = {}
        if self.device_registry is not None:
            registration = self.device_registry.register(
                conn.uid, conn.device_id, connection_id=conn.id, role=conn.role,
                name=conn.name, platform=conn.platform, app_version=conn.app_version,
                capabilities=(
                    ["view", "control", "clipboard", "file_write", "audio", "power"]
                    if conn.role == "host" else []
                ),
                routable=True,
            )
            conn.lease_id = str(registration.get("lease_id") or "")
            conn.generation = int(registration.get("generation") or 0)
        is_v4 = conn.protocol == "hashmm.remote.v4"
        turn_available = any(
            str(url).startswith(("turn:", "turns:"))
            for item in ice_servers for url in (
                [item.get("urls")] if isinstance(item.get("urls"), str) else item.get("urls") or []
            )
        )
        actions.append((conn, {"type": "authOk", "iceServers": ice_servers, "id": conn.id,
                        "deviceId": conn.device_id, "stableDeviceId": conn.device_id,
                         "protocol": conn.protocol if self.device_registry is not None else "hashmm.remote.v1",
                        "transportPolicy": "direct-turn-migrate-compat-preview" if is_v4 else "ice-direct-turn-fallback",
                        "turnAvailable": turn_available,
                        "legacyRelayAfterMs": 8000 if is_v4 else 12000,
                        "timeBudgets": ({"offerAnswerMs": 2000, "pathSelectionMs": 5000,
                                         "firstFrameMs": 8000, "migrationMs": 6000,
                                         "compatFirstFrameMs": 8000} if is_v4 else {}),
                        "ownerFingerprint": registration.get("owner_fingerprint", ""),
                        "deviceFingerprint": registration.get("device_fingerprint", ""),
                        "leaseExpiresIn": registration.get("lease_expires_in", 0),
                        "generation": conn.generation,
                        "permissionMode": ("same-account-auto" if self.sessions and is_v4
                                           else "ask" if self.sessions else "legacy")}))
        if conn.role == "host":
            for viewer in room.values():
                if viewer.role == "viewer" and viewer.id != conn.id:
                    actions.append((viewer, {"type": "deviceChanged", "deviceId": conn.device_id,
                                             "online": True, "remoteReady": True}))
        return actions

    def remove(self, conn: Conn) -> List[Action]:
        out: List[Action] = []
        room = self._room(conn.uid)
        room.pop(conn.id, None)
        if self.device_registry is not None and conn.lease_id:
            self.device_registry.disconnect(
                conn.uid, conn.device_id, conn.lease_id, conn.generation
            )
        if conn.role == "viewer" and conn.peer:
            host = room.get(conn.peer)
            if host:
                host.viewers.discard(conn.id)
                sid = host.session_ids.pop(conn.id, None)
                if self.sessions and sid:
                    self.sessions.revoke(conn.uid, sid, conn.device_id, "viewer_disconnected")
                out.append((host, {"type": "viewerLeft", "vid": conn.id}))
        elif conn.role == "host":
            for vid in list(conn.viewers):
                v = room.get(vid)
                if v:
                    sid = conn.session_ids.pop(vid, None)
                    if self.sessions and sid:
                        self.sessions.revoke(conn.uid, sid, conn.device_id, "host_disconnected")
                    v.peer = None
                    out.append((v, {"type": "pairFail", "reason": "offline"}))
        for sid, pair in list(self.pending.items()):
            if conn.id in pair:
                self.pending.pop(sid, None)
                if self.sessions:
                    self.sessions.revoke(conn.uid, sid, conn.device_id, "device_disconnected")
        if not room:
            self.accounts.pop(conn.uid, None)
        elif conn.role == "host":
            for viewer in room.values():
                if viewer.role == "viewer":
                    out.append((viewer, {"type": "deviceChanged", "deviceId": conn.device_id,
                                         "online": False, "remoteReady": False}))
        return out

    def hosts(self, uid: str) -> List[Conn]:
        return [c for c in self._room(uid).values() if c.role == "host"]

    def _active_session(self, conn: Conn, peer_id: str = ""):
        if not self.sessions:
            return None
        sid = conn.session_id if conn.role == "viewer" else conn.session_ids.get(peer_id)
        return self.sessions.get(conn.uid, sid or "") if sid else None

    @staticmethod
    def _scope_for_message(message_type: str) -> str:
        if message_type in {"input", "control", "selectMonitor", "setQuality", "setPrivacy"}:
            return "control"
        if message_type == "clip":
            return "clipboard"
        if message_type in {"fileOffer", "fileAccept", "fileReject", "fileProgress", "fileDone", "fileCancel",
                            "fileChunk", "fileResumeQuery", "fileResumeState"}:
            return "file_write"
        return "view"

    @staticmethod
    def _scope_for_control(msg: Dict[str, Any]) -> str:
        return "audio" if str(msg.get("action") or "") == "audio" else "control"

    @staticmethod
    def _scope_for_input(msg: Dict[str, Any]) -> str:
        action = str(msg.get("action") or (msg.get("data") or {}).get("action") or "")
        if action in {"system", "device"}:
            return "power"
        if action == "clipboard_set":
            return "clipboard"
        return "control"

    # ── 消息处理（同账号内 viewer ⇄ host 中继）──────────────
    def _activate_pending_session(
        self, conn: Conn, viewer: Conn, sid: str, transport_mode: str,
        scopes: Any = None, *, approval_mode: str = "host-confirmed",
    ) -> List[Action]:
        """Approve, ticket and bind one owner-scoped remote session."""
        session = self.sessions.approve(conn.uid, sid, conn.device_id, scopes)
        if not session:
            return [(viewer, {"type": "pairFail", "reason": "permission_invalid", "sessionId": sid})]
        host_ticket = self.sessions.issue_ticket(conn.uid, sid, "host", conn.device_id)
        viewer_ticket = self.sessions.issue_ticket(conn.uid, sid, "viewer", viewer.device_id)
        if not host_ticket or not viewer_ticket:
            self.sessions.revoke(conn.uid, sid, conn.device_id, "ticket_issue_failed")
            self.pending.pop(sid, None)
            return [
                (conn, {"type": "permissionFailed", "reason": "ticket_issue_failed", "sessionId": sid}),
                (viewer, {"type": "pairFail", "reason": "ticket_issue_failed", "sessionId": sid}),
            ]
        self.pending.pop(sid, None)
        viewer.peer = conn.id
        viewer.session_id = sid
        conn.viewers.add(viewer.id)
        conn.session_ids[viewer.id] = sid
        v4_pair = conn.protocol == "hashmm.remote.v4" and viewer.protocol == "hashmm.remote.v4"
        transport_policy = "direct-turn-migrate-compat-preview" if v4_pair else "ice-direct-turn-fallback"
        fallback_after_ms = 8000 if v4_pair else 12000
        time_budgets = ({"offerAnswerMs": 2000, "pathSelectionMs": 5000,
                         "firstFrameMs": 8000, "migrationMs": 6000,
                         "compatFirstFrameMs": 8000} if v4_pair else None)
        self.sessions.record_milestone(
            conn.uid, sid, "host", conn.device_id,
            "permission_approved", {"stage": "permission_approved", "approvalMode": approval_mode},
        )
        return [
            (conn, {"type": "viewerJoined", "vid": viewer.id, "sessionId": sid,
                    "scopes": list(session.granted_scopes), "ticket": host_ticket,
                    "ticketGeneration": session.generation, "approvalMode": approval_mode,
                    "transportMode": transport_mode, "relayOnly": transport_mode == "turn-only",
                    "transportPolicy": transport_policy, "legacyRelayAfterMs": fallback_after_ms,
                    **({"timeBudgets": time_budgets} if time_budgets else {})}),
            (viewer, {"type": "ready", "sessionId": sid,
                      "scopes": list(session.granted_scopes), "ticket": viewer_ticket,
                      "ticketGeneration": session.generation, "approvalMode": approval_mode,
                      "transportMode": transport_mode,
                      "transportPolicy": transport_policy, "legacyRelayAfterMs": fallback_after_ms,
                      **({"timeBudgets": time_budgets} if time_budgets else {}),
                      "expiresAt": session.expires_at}),
        ]

    def on_message(self, conn: Conn, msg: Dict[str, Any]) -> List[Action]:
        # A replaced socket is fenced even before the transport is physically
        # closed, so it cannot route input or overwrite the current lease.
        if self._room(conn.uid).get(conn.id) is not conn:
            return [(conn, {"type": "authFail", "reason": "stale_generation"})]
        t = msg.get("type")
        if t == "heartbeat":
            if self.device_registry is None:
                return [(conn, {"type": "heartbeatAck", "leaseExpiresIn": 0})]
            ok = self.device_registry.heartbeat(
                conn.uid, conn.device_id, conn.lease_id, conn.generation,
                remote_ready=conn.role == "host", busy=bool(conn.viewers) if conn.role == "host" else False,
            )
            return [(conn, {"type": "heartbeatAck" if ok else "leaseRejected",
                            "leaseExpiresIn": self.device_registry.lease_ttl,
                            "generation": conn.generation})]
        if t == "listDevices":
            if self.device_registry is not None:
                lst = self.device_registry.list_devices(
                    conn.uid, remote_only=True, include_offline=False
                )
            else:
                lst = [{"id": h.id, "name": h.name, "platform": h.platform}
                       for h in self.hosts(conn.uid) if h.id != conn.id]
            return [(conn, {"type": "devices", "list": lst})]

        if t == "connect":                                    # viewer 选择某台 host
            if conn.role != "viewer":
                return [(conn, {"type": "pairFail", "reason": "invalid_role"})]
            target = str(msg.get("target") or "")
            host = self._room(conn.uid).get(target)
            if (not host or host.role != "host") and target:
                host = next((item for item in self.hosts(conn.uid)
                             if item.device_id == target), None)
            if not host or host.role != "host":
                return [(conn, {"type": "pairFail", "reason": "offline"})]
            transport_mode = "turn-only" if str(msg.get("transportMode") or "").lower() == "turn-only" else "auto"
            if self.sessions:
                # A desktop can expose one interactive control boundary at a
                # time. This prevents overlapping grants, input races and a
                # second pending prompt from silently superseding the first.
                if host.viewers or any(pair[0] == host.id for pair in self.pending.values()):
                    return [(conn, {"type": "pairFail", "reason": "busy"})]
                requested_scopes = normalize_scopes(msg.get("scopes"))
                if not requested_scopes:
                    return [(conn, {"type": "pairFail", "reason": "empty_scopes"})]
                if host.device_id == conn.device_id:
                    return [(conn, {"type": "pairFail", "reason": "same_device"})]
                client_request_id = str(msg.get("clientRequestId") or "").strip()
                if client_request_id and not _CLIENT_REQUEST_ID.fullmatch(client_request_id):
                    return [(conn, {"type": "pairFail", "reason": "invalid_client_request_id"})]
                try:
                    requested_work = str(
                        msg.get("workRunId") or msg.get("workId") or ""
                    )
                    work_run_id = requested_work
                    if self.work_binder is not None:
                        work_run_id = self.work_binder(
                            conn.uid,
                            requested_work,
                            host_device_id=host.device_id,
                            viewer_device_id=conn.device_id,
                            conversation_id=str(msg.get("conversationId") or "")[:160],
                            source_id=(
                                f"remote-request:{client_request_id}"
                                if client_request_id else ""
                            ),
                        )
                    session = self.sessions.request(
                        conn.uid, host.device_id, conn.device_id,
                        requested_scopes, work_run_id,
                    )
                    self.sessions.record_milestone(
                        conn.uid, session.id, "viewer", conn.device_id,
                        "connect_requested", {"stage": "permission_pending"},
                    )
                except (ValueError, LookupError) as exc:
                    return [(conn, {"type": "pairFail", "reason": str(exc)})]
                self.pending[session.id] = (host.id, conn.id, transport_mode)
                # Both v4 sockets were admitted with independent single-use
                # tickets minted for this exact Supabase owner. Same-account
                # device access therefore follows the user's account policy
                # and does not require a second desktop click.
                if host.protocol == "hashmm.remote.v4" and conn.protocol == "hashmm.remote.v4":
                    return self._activate_pending_session(
                        host, conn, session.id, transport_mode,
                        list(session.requested_scopes), approval_mode="same-account-auto",
                    )
                return [
                    (host, {"type": "permissionRequest", "sessionId": session.id,
                            "viewer": {"id": conn.id, "deviceId": conn.device_id,
                                       "name": conn.name, "platform": conn.platform},
                            "scopes": list(session.requested_scopes),
                            "workRunId": session.work_run_id,
                            "workId": session.work_run_id,
                            "transportMode": transport_mode,
                            "expiresAt": session.expires_at}),
                    (conn, {"type": "permissionPending", "sessionId": session.id,
                            "target": {"id": host.id, "deviceId": host.device_id, "name": host.name},
                            "expiresAt": session.expires_at}),
                ]
            conn.peer = host.id
            host.viewers.add(conn.id)
            return [
                (host, {"type": "viewerJoined", "vid": conn.id,
                        "transportMode": transport_mode, "relayOnly": transport_mode == "turn-only",
                        "transportPolicy": "ice-direct-turn-fallback", "legacyRelayAfterMs": 12000}),
                (conn, {"type": "ready", "transportMode": transport_mode,
                        "transportPolicy": "ice-direct-turn-fallback",
                        "legacyRelayAfterMs": 12000}),
            ]

        if t == "permissionDecision" and self.sessions:
            if conn.role != "host":
                return []
            sid = str(msg.get("sessionId") or "")
            pair = self.pending.get(sid)
            if not pair or pair[0] != conn.id:
                return []
            viewer = self._room(conn.uid).get(pair[1])
            if not viewer or viewer.role != "viewer":
                self.pending.pop(sid, None)
                return []
            if msg.get("decision") != "approve":
                self.sessions.deny(conn.uid, sid, conn.device_id, str(msg.get("reason") or "denied"))
                self.pending.pop(sid, None)
                return [(viewer, {"type": "pairFail", "reason": "denied", "sessionId": sid})]
            transport_mode = pair[2] if len(pair) > 2 else "auto"
            return self._activate_pending_session(
                conn, viewer, sid, transport_mode, msg.get("scopes"),
                approval_mode="host-confirmed",
            )

        if t == "refreshTicket" and self.sessions:
            if conn.role == "viewer":
                session = self._active_session(conn)
                if not session or session.state != "active":
                    return []
                ticket = self.sessions.issue_ticket(conn.uid, session.id, "viewer", conn.device_id)
                return [(conn, {"type": "ticket", "sessionId": session.id, "ticket": ticket,
                                "ticketGeneration": session.generation,
                                "expiresAt": session.expires_at})] if ticket else []
            viewer_id = str(msg.get("vid") or "")
            session = self._active_session(conn, viewer_id)
            if not session or session.state != "active":
                return []
            ticket = self.sessions.issue_ticket(conn.uid, session.id, "host", conn.device_id)
            return [(conn, {"type": "ticket", "sessionId": session.id, "vid": viewer_id,
                            "ticket": ticket, "ticketGeneration": session.generation,
                            "expiresAt": session.expires_at})] if ticket else []

        if t == "transportReport" and self.sessions:
            sid = str(msg.get("sessionId") or "")
            if not sid and conn.role == "viewer":
                sid = str(conn.session_id or "")
            if not sid and conn.role == "host":
                sid = str(conn.session_ids.get(str(msg.get("vid") or "")) or "")
            ok = self.sessions.record_transport(
                conn.uid, sid, conn.role, conn.device_id,
                msg.get("report") if isinstance(msg.get("report"), dict) else {},
            )
            return [(conn, {"type": "transportRecorded", "sessionId": sid})] if ok else []

        if t == "milestone" and self.sessions:
            sid = str(msg.get("sessionId") or conn.session_id or "")
            if conn.role == "host" and not sid:
                sid = str(conn.session_ids.get(str(msg.get("vid") or "")) or "")
            ok = self.sessions.record_milestone(
                conn.uid, sid, conn.role, conn.device_id,
                str(msg.get("milestone") or ""),
                msg.get("detail") if isinstance(msg.get("detail"), dict) else {},
            )
            return [(conn, {"type": "milestoneRecorded", "sessionId": sid,
                            "milestone": str(msg.get("milestone") or "")})] if ok else []

        if t == "compatReady" and conn.role == "viewer" and conn.peer:
            host = self._room(conn.uid).get(conn.peer)
            session = self._active_session(conn)
            if host and session and session.state == "active" and "view" in session.granted_scopes:
                return [(host, {"type": "compatReady", "vid": conn.id,
                                "sessionId": session.id})]
            return []

        if t == "compatStarted" and conn.role == "host":
            viewer_id = str(msg.get("vid") or "")
            viewer = self._room(conn.uid).get(viewer_id)
            session = self._active_session(conn, viewer_id)
            if viewer and session and session.state == "active" and "view" in session.granted_scopes:
                return [(viewer, {"type": "compatStarted", "sessionId": session.id})]
            return []

        if t == "complete" and self.sessions:
            viewer: Optional[Conn] = None
            host: Optional[Conn] = None
            if conn.role == "viewer":
                viewer = conn
                host = self._room(conn.uid).get(conn.peer) if conn.peer else None
                session = self._active_session(conn)
            else:
                viewer_id = str(msg.get("vid") or "")
                viewer = self._room(conn.uid).get(viewer_id)
                host = conn
                session = self._active_session(conn, viewer_id)
            if not session or session.state != "active":
                return [(conn, {"type": "completeRejected", "reason": "inactive_session"})]
            completed = self.sessions.complete(
                conn.uid, session.id, conn.device_id,
                str(msg.get("summary") or "remote_session_completed")[:120],
            )
            if not completed:
                return [(conn, {"type": "completeRejected", "reason": "not_participant"})]
            if viewer:
                viewer.peer = None
                viewer.session_id = None
            if host and viewer:
                host.viewers.discard(viewer.id)
                host.session_ids.pop(viewer.id, None)
            receipt = {
                "type": "remoteCompleted", "sessionId": completed.id,
                "state": completed.state, "generation": completed.generation,
            }
            targets = [item for item in (host, viewer) if item is not None]
            return [(target, dict(receipt)) for target in dict.fromkeys(targets)]

        if t == "rtcSignal":
            if conn.role == "host":                           # host → 指定 viewer
                v = self._room(conn.uid).get(msg.get("vid"))
                if v and v.peer == conn.id:
                    session = self._active_session(conn, v.id)
                    if self.sessions and (not session or session.state != "active" or "view" not in session.granted_scopes):
                        return []
                    kind = str(msg.get("kind") or "")
                    if self.sessions and kind in {"offer", "answer"}:
                        self.sessions.record_milestone(
                            conn.uid, session.id, "host", conn.device_id,
                            f"{kind}_forwarded", {"stage": "signaling"},
                        )
                    return [(v, {"type": "rtcSignal", "kind": kind, "data": msg.get("data")})]
                return []
            # viewer → 它所连的 host
            host = self._room(conn.uid).get(conn.peer) if conn.peer else None
            if host:
                session = self._active_session(conn)
                if self.sessions and (not session or session.state != "active" or "view" not in session.granted_scopes):
                    return []
                kind = str(msg.get("kind") or "")
                if self.sessions and kind in {"offer", "answer"}:
                    self.sessions.record_milestone(
                        conn.uid, session.id, "viewer", conn.device_id,
                        f"{kind}_forwarded", {"stage": "signaling"},
                    )
                return [(host, {"type": "rtcSignal", "vid": conn.id, "kind": kind, "data": msg.get("data")})]
            return []

        if t in ("input", "rtcOn", "rtcOff"):                 # viewer → host（host 渲染进程转给主进程注入）
            if conn.role == "viewer" and conn.peer:
                host = self._room(conn.uid).get(conn.peer)
                if host:
                    session = self._active_session(conn)
                    required = self._scope_for_input(msg) if t == "input" else "view"
                    if self.sessions:
                        if not session or session.state != "active" or required not in session.granted_scopes:
                            return []
                        if t == "input" and not self.sessions.validate_envelope(
                                conn.uid, session.id, "viewer", conn.device_id,
                                msg.get("seq"), msg.get("timestamp"), required):
                            return [(conn, {"type": "controlRejected", "reason": "stale_or_replayed"})]
                    fwd = dict(msg)
                    fwd["vid"] = conn.id
                    if session:
                        fwd["sessionId"] = session.id
                    return [(host, fwd)]
            return []

        # V103.51: 对标 UU 远程的扩展能力。这些都是**小控制消息**，在已配对的
        # viewer ⇄ host 之间转发；真正的文件字节走 P2P WebRTC DataChannel（不经本服务器，
        # 故文件字节通常不占后端带宽；接收端仍执行 256MB 单文件上限与分块校验）。
        #   fileOffer/fileAccept/fileReject/fileProgress/fileDone — 文件传输握手与进度
        #   selectMonitor/monitorList                            — 多屏选择（多屏协作/被控端选屏）
        #   setQuality                                           — 画质（fps / jpeg 质量 / 真彩）
        #   setPrivacy                                           — 隐私防护（被控端黑屏 / 锁输入）
        # 双向转发：viewer→host 与 host→viewer 都允许（如 host 回 monitorList / fileProgress）。
        _VIEWER_TO_HOST = {"fileOffer", "fileAccept", "fileReject", "fileProgress", "fileChunk",
                           "fileDone", "fileCancel", "fileResumeQuery", "fileResumeState",
                           "selectMonitor", "setQuality", "setPrivacy", "clip", "control"}
        _HOST_TO_VIEWER = {"fileOffer", "fileAccept", "fileReject", "fileProgress", "fileResumeState",
                           "fileDone", "fileCancel", "monitorList", "privacyState", "clip"}
        if t in _VIEWER_TO_HOST or t in _HOST_TO_VIEWER:
            if conn.role == "viewer" and conn.peer and t in _VIEWER_TO_HOST:
                host = self._room(conn.uid).get(conn.peer)
                if host:
                    session = self._active_session(conn)
                    required = self._scope_for_control(msg) if t == "control" else self._scope_for_message(str(t))
                    if self.sessions and (not session or session.state != "active" or required not in session.granted_scopes):
                        return []
                    if self.sessions and t == "control" and not self.sessions.validate_envelope(
                            conn.uid, session.id, "viewer", conn.device_id,
                            msg.get("seq"), msg.get("timestamp"), required):
                        return [(conn, {"type": "controlRejected", "reason": "stale_or_replayed"})]
                    fwd = dict(msg)
                    fwd["vid"] = conn.id
                    if session:
                        fwd["sessionId"] = session.id
                    return [(host, fwd)]
                return []
            if conn.role == "host" and t in _HOST_TO_VIEWER:
                # host 指定某个 viewer（msg["vid"]）；不指定则广播给它所有 viewer。
                vid = msg.get("vid")
                if vid:
                    v = self._room(conn.uid).get(vid)
                    if v and v.peer == conn.id:
                        session = self._active_session(conn, v.id)
                        required = self._scope_for_message(str(t))
                        if self.sessions and (not session or session.state != "active" or required not in session.granted_scopes):
                            return []
                        return [(v, {k: val for k, val in msg.items() if k != "vid"} | {"type": t})]
                    return []
                out: List[Action] = []
                for vid2 in list(conn.viewers):
                    v = self._room(conn.uid).get(vid2)
                    if v:
                        session = self._active_session(conn, v.id)
                        required = self._scope_for_message(str(t))
                        if self.sessions and (not session or session.state != "active" or required not in session.granted_scopes):
                            continue
                        out.append((v, {k: val for k, val in msg.items() if k != "vid"} | {"type": t}))
                return out
            return []

        return []
