"""Owner-bound remote control sessions and short-lived capability tickets.

This module deliberately contains no FastAPI or socket code.  The WebSocket
hub, HTTP relay and API routes all use the same registry, so permission state
has one source of truth.  A process restart invalidates every ticket and
session (fail closed); durable audit storage can consume ``audit_snapshot``.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Callable, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from hashmm.api.remote_persistence import RemotePersistence


REMOTE_TICKET_AUDIENCE = "hashmm.remote.v1"
REMOTE_SCOPES = frozenset({
    "view", "control", "clipboard", "file_read", "file_write", "audio", "power",
})
DEFAULT_REMOTE_SCOPES = ("view", "control")

REMOTE_MILESTONES = frozenset({
    "connect_requested", "permission_pending", "permission_approved",
    "viewer_joined_delivered", "capture_starting", "capture_started",
    "offer_created", "offer_forwarded", "offer_received",
    "answer_created", "answer_forwarded", "answer_received",
    "candidates_gathered", "candidate_pair_selected", "dtls_connected",
    "track_received", "first_frame_rendered", "fallback_started",
    "fallback_first_frame", "reconnecting", "migrating", "terminal_error",
})

REMOTE_ERROR_CODES = frozenset({
    "TURN_NOT_CONFIGURED", "TURN_DNS_FAILED", "TURN_AUTH_FAILED",
    "TURN_NO_RELAY_CANDIDATE", "TURN_UDP_BLOCKED", "TURN_TLS_FAILED",
    "OFFER_NOT_DELIVERED", "ANSWER_NOT_DELIVERED", "ICE_ALL_PAIRS_FAILED",
    "DTLS_HANDSHAKE_FAILED", "CAPTURE_DENIED", "CAPTURE_EMPTY",
    "ENCODER_INIT_FAILED", "CODEC_MISMATCH", "TRACK_NOT_RECEIVED",
    "FIRST_FRAME_TIMEOUT", "RELAY_TICKET_MISSING", "RELAY_HTTP_401",
    "RELAY_HTTP_403", "RELAY_HTTP_404", "RELAY_REQUEST_TIMEOUT",
    "RELAY_HOST_NOT_PUSHING", "DECODER_STALLED", "CONTROL_SOCKET_LOST",
    "CONTROL_SOCKET_TIMEOUT",
    "REMOTE_INTERNAL_ERROR",
})


def normalize_scopes(scopes: Iterable[str] | None) -> tuple[str, ...]:
    """Return a stable, allow-listed scope tuple; unknown values never widen access."""
    raw = scopes if scopes is not None else DEFAULT_REMOTE_SCOPES
    return tuple(sorted({str(scope).strip() for scope in raw if str(scope).strip() in REMOTE_SCOPES}))


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _ticket_key() -> bytes:
    # Mirror auth.SECRET without importing FastAPI into this pure logic module.
    # The derived sub-key cannot mint login JWTs.
    secret = os.environ.get("HASHMM_JWT_SECRET", "hashmm-jwt-secret-change-me")
    return hmac.new(secret.encode("utf-8"), b"hashmm.remote.ticket.v1", hashlib.sha256).digest()


@dataclass
class RemoteSession:
    id: str
    uid: str
    host_device_id: str
    viewer_device_id: str
    requested_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...] = ()
    state: str = "pending"
    generation: int = 0
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    work_run_id: str = ""
    predecessor_session_id: str = ""
    reason: str = ""
    last_seq: dict[str, int] = field(default_factory=dict, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host_device_id": self.host_device_id,
            "viewer_device_id": self.viewer_device_id,
            "requested_scopes": list(self.requested_scopes),
            "granted_scopes": list(self.granted_scopes),
            "state": self.state,
            "generation": self.generation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "work_run_id": self.work_run_id,
            # One-release compatibility projection for V420 clients.  New
            # clients must use work_run_id; it is no longer an arbitrary ID.
            "work_id": self.work_run_id,
            "predecessor_session_id": self.predecessor_session_id,
            "reason": self.reason,
        }


class RemoteSessionRegistry:
    """Thread-safe, owner-scoped session state with replay protection."""

    def __init__(self, *, active_ttl: int | None = None, pending_ttl: int = 120,
                 store: "RemotePersistence | None" = None,
                 transition_sink: Callable[[RemoteSession, str, dict[str, Any]], None] | None = None):
        configured = int(os.environ.get("HASHMM_REMOTE_SESSION_TTL", "1800"))
        self.active_ttl = max(60, min(active_ttl or configured, 8 * 3600))
        self.pending_ttl = max(30, min(pending_ttl, 600))
        self._sessions: dict[str, RemoteSession] = {}
        self._revoked_jti: dict[str, float] = {}
        self._audit: deque[dict[str, Any]] = deque(maxlen=2000)
        self._lock = threading.RLock()
        self._store = store
        self._transition_sink = transition_sink
        self._initialized = store is None

    def initialize(self) -> None:
        """Hydrate durable history and invalidate every pre-restart live grant."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            assert self._store is not None
            rows = self._store.initialize()
            restored: dict[str, RemoteSession] = {}
            for row in rows:
                try:
                    requested = json.loads(str(row.get("requested_scopes_json") or "[]"))
                    granted = json.loads(str(row.get("granted_scopes_json") or "[]"))
                    session = RemoteSession(
                        id=str(row["id"]), uid=str(row["user_id"]),
                        host_device_id=str(row["host_device_id"]), viewer_device_id=str(row["viewer_device_id"]),
                        requested_scopes=normalize_scopes(requested), granted_scopes=normalize_scopes(granted),
                        state=str(row.get("state") or "interrupted"), generation=int(row.get("generation") or 0),
                        created_at=float(row.get("created_at") or 0), expires_at=float(row.get("expires_at") or 0),
                        work_run_id=str(row.get("work_run_id") or ""),
                        predecessor_session_id=str(row.get("predecessor_session_id") or ""),
                        reason=str(row.get("reason") or ""),
                    )
                    restored[session.id] = session
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            self._sessions = restored
            self._initialized = True
            # Persistence reconciles live grants to interrupted before these
            # rows are returned.  Re-projecting is safe because WorkRuntime
            # admission is idempotent and repairs a crash between ledgers.
            for session in restored.values():
                if session.reason == "server_restarted":
                    self._project_transition(session, "interrupted", {"reason": session.reason})

    def _project_transition(self, session: RemoteSession, event: str,
                            detail: dict[str, Any]) -> None:
        if self._transition_sink is None:
            return
        try:
            self._transition_sink(session, event, detail)
        except Exception as exc:
            # The permission ledger was already durably written.  Never widen
            # access because a user-facing projection failed.
            from hashmm.utils import get_logger
            get_logger("hashmm.remote_sessions").warning(
                "Remote work projection failed for %s: %s", session.id, exc
            )

    def _event(self, event: str, session: RemoteSession, **extra: Any) -> None:
        persisted = None
        if self._store is not None:
            persisted = self._store.persist_transition(vars(session), event, extra)
        self._audit.append({
            "event": event,
            "session_id": session.id,
            "uid_hash": hashlib.sha256(session.uid.encode()).hexdigest()[:12],
            "at": persisted["at"] if persisted else time.time(),
            **extra,
        })
        self._project_transition(session, event, extra)

    def _expire_locked(self, now: float) -> None:
        for session in self._sessions.values():
            if session.state in ("pending", "active") and session.expires_at <= now:
                session.state = "expired"
                session.generation += 1
                self._event("expired", session)
        self._revoked_jti = {jti: exp for jti, exp in self._revoked_jti.items() if exp > now}

    def request(self, uid: str, host_device_id: str, viewer_device_id: str,
                scopes: Iterable[str] | None = None, work_run_id: str = "",
                predecessor_session_id: str = "") -> RemoteSession:
        self.initialize()
        requested = normalize_scopes(scopes)
        if not uid or not host_device_id or not viewer_device_id:
            raise ValueError("missing_identity")
        if not requested:
            raise ValueError("empty_scopes")
        now = time.time()
        session = RemoteSession(
            id="rs_" + secrets.token_urlsafe(18), uid=str(uid),
            host_device_id=str(host_device_id)[:128], viewer_device_id=str(viewer_device_id)[:128],
            requested_scopes=requested, expires_at=now + self.pending_ttl,
            work_run_id=str(work_run_id or "")[:128],
            predecessor_session_id=str(predecessor_session_id or "")[:128],
        )
        with self._lock:
            self._expire_locked(now)
            self._event("requested", session, scopes=list(requested))
            self._sessions[session.id] = session
        return session

    def get(self, uid: str, session_id: str) -> RemoteSession | None:
        self.initialize()
        with self._lock:
            self._expire_locked(time.time())
            session = self._sessions.get(str(session_id))
            return session if session and hmac.compare_digest(session.uid, str(uid)) else None

    def list_for_owner(self, uid: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._lock:
            self._expire_locked(time.time())
            return [s.public() for s in sorted(self._sessions.values(), key=lambda item: item.created_at, reverse=True)
                    if hmac.compare_digest(s.uid, str(uid))]

    def approve(self, uid: str, session_id: str, host_device_id: str,
                scopes: Iterable[str] | None = None) -> RemoteSession | None:
        self.initialize()
        now = time.time()
        with self._lock:
            session = self.get(uid, session_id)
            if not session or session.state != "pending" or not hmac.compare_digest(session.host_device_id, str(host_device_id)):
                return None
            granted = normalize_scopes(scopes if scopes is not None else session.requested_scopes)
            granted = tuple(scope for scope in granted if scope in session.requested_scopes)
            if "view" not in granted:
                return None
            session.granted_scopes = granted
            session.state = "active"
            session.generation += 1
            session.expires_at = now + self.active_ttl
            session.last_seq.clear()
            self._event("approved", session, scopes=list(granted))
            return session

    def deny(self, uid: str, session_id: str, host_device_id: str, reason: str = "denied") -> RemoteSession | None:
        self.initialize()
        with self._lock:
            session = self.get(uid, session_id)
            if not session or session.state != "pending" or not hmac.compare_digest(session.host_device_id, str(host_device_id)):
                return None
            session.state = "denied"
            session.reason = str(reason or "denied")[:120]
            session.generation += 1
            self._event("denied", session, reason=session.reason)
            return session

    def revoke(self, uid: str, session_id: str, device_id: str = "", reason: str = "revoked") -> RemoteSession | None:
        self.initialize()
        with self._lock:
            session = self.get(uid, session_id)
            if not session or session.state not in ("pending", "active"):
                return None
            if device_id and device_id not in (session.host_device_id, session.viewer_device_id):
                return None
            session.state = "revoked"
            session.reason = str(reason or "revoked")[:120]
            session.generation += 1
            session.last_seq.clear()
            self._event("revoked", session, reason=session.reason)
            return session

    def handoff(self, uid: str, session_id: str, actor_device_id: str,
                new_viewer_device_id: str,
                scopes: Iterable[str] | None = None) -> RemoteSession | None:
        """Replace an active viewer with a new least-privilege pending session.

        A handoff never reuses the old generation or ticket.  Repeating the
        same request after the predecessor was superseded returns the existing
        successor instead of creating overlapping grants.
        """
        self.initialize()
        owner = str(uid)
        predecessor_id = str(session_id)
        actor = str(actor_device_id)
        next_viewer = str(new_viewer_device_id)[:128]
        if not next_viewer:
            return None
        with self._lock:
            current = self.get(owner, predecessor_id)
            if not current or actor not in {
                current.host_device_id, current.viewer_device_id,
            }:
                return None
            if current.state == "superseded":
                for candidate in self._sessions.values():
                    if (hmac.compare_digest(candidate.uid, owner)
                            and candidate.predecessor_session_id == predecessor_id
                            and candidate.viewer_device_id == next_viewer
                            and candidate.state in {"pending", "active"}):
                        return candidate
                return None
            if current.state != "active" or next_viewer in {
                current.host_device_id, current.viewer_device_id,
            }:
                return None
            inherited = normalize_scopes(scopes if scopes is not None else current.granted_scopes)
            if any(scope not in current.granted_scopes for scope in inherited):
                raise ValueError("scope_escalation")
            if "view" not in inherited:
                raise ValueError("view_scope_required")
            successor = RemoteSession(
                id="rs_" + secrets.token_urlsafe(18),
                uid=current.uid,
                host_device_id=current.host_device_id,
                viewer_device_id=next_viewer,
                requested_scopes=inherited,
                expires_at=time.time() + self.pending_ttl,
                work_run_id=current.work_run_id,
                predecessor_session_id=current.id,
            )
            # Persist the successor before invalidating the predecessor.  A
            # failure can leave the old session active, but never an
            # unapproved new grant.
            self._event("requested", successor, scopes=list(inherited))
            self._sessions[successor.id] = successor
            current.state = "superseded"
            current.reason = "device_handoff"
            current.generation += 1
            current.expires_at = time.time()
            current.last_seq.clear()
            self._event(
                "superseded", current, reason=current.reason,
                successor_session_id=successor.id,
            )
            return successor

    def complete(self, uid: str, session_id: str, device_id: str,
                 summary: str = "") -> RemoteSession | None:
        """Close a live remote session normally and emit a bounded receipt."""
        self.initialize()
        with self._lock:
            session = self.get(uid, session_id)
            if (not session or session.state != "active"
                    or str(device_id) not in {
                        session.host_device_id, session.viewer_device_id,
                    }):
                return None
            observed = self.audit_for_session(uid, session_id)
            roles = sorted({
                str(((item.get("detail") or {}).get("role") or item.get("role") or ""))
                for item in observed
                if item.get("event") == "transport_observed"
            } - {""})
            sample_count = sum(
                1 for item in observed if item.get("event") == "transport_observed"
            )
            verification = "verified" if {"host", "viewer"}.issubset(roles) else (
                "partial" if roles else "not_observed"
            )
            session.state = "completed"
            session.reason = str(summary or "remote_session_completed")[:120]
            session.generation += 1
            session.expires_at = time.time()
            session.last_seq.clear()
            self._event(
                "completed", session, reason=session.reason,
                transport_roles=roles, transport_samples=sample_count,
                verification=verification,
            )
            return session

    def issue_ticket(self, uid: str, session_id: str, role: str, device_id: str,
                     ttl: int = 120) -> str | None:
        self.initialize()
        role = str(role)
        if role not in ("host", "viewer"):
            return None
        now = int(time.time())
        with self._lock:
            session = self.get(uid, session_id)
            expected = session.host_device_id if role == "host" and session else session.viewer_device_id if session else ""
            if not session or session.state != "active" or not hmac.compare_digest(expected, str(device_id)):
                return None
            exp = min(int(session.expires_at), now + max(30, min(int(ttl), 300)))
            payload = {
                "aud": REMOTE_TICKET_AUDIENCE, "uid": session.uid, "sid": session.id,
                "role": role, "device": str(device_id), "scopes": list(session.granted_scopes),
                "generation": session.generation, "jti": secrets.token_urlsafe(12),
                "iat": now, "exp": exp,
            }
            encoded = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            signature = _b64e(hmac.new(_ticket_key(), encoded.encode("ascii"), hashlib.sha256).digest())
            self._event("ticket_issued", session, role=role)
            return encoded + "." + signature

    def verify_ticket(self, token: str, *, required_scope: str = "", role: str = "",
                      session_id: str = "", device_id: str = "") -> dict[str, Any] | None:
        self.initialize()
        try:
            encoded, supplied = str(token).split(".", 1)
            expected = _b64e(hmac.new(_ticket_key(), encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                return None
            payload = json.loads(_b64d(encoded))
            now = time.time()
            if payload.get("aud") != REMOTE_TICKET_AUDIENCE or float(payload.get("exp", 0)) <= now:
                return None
            if role and payload.get("role") != role:
                return None
            if session_id and not hmac.compare_digest(str(payload.get("sid", "")), str(session_id)):
                return None
            if device_id and not hmac.compare_digest(str(payload.get("device", "")), str(device_id)):
                return None
            if required_scope and required_scope not in payload.get("scopes", []):
                return None
            with self._lock:
                self._expire_locked(now)
                if payload.get("jti") in self._revoked_jti:
                    return None
                session = self._sessions.get(str(payload.get("sid", "")))
                if (not session or session.state != "active"
                        or not hmac.compare_digest(session.uid, str(payload.get("uid", "")))
                        or session.generation != int(payload.get("generation", -1))):
                    return None
                expected_device = session.host_device_id if payload.get("role") == "host" else session.viewer_device_id
                if not hmac.compare_digest(expected_device, str(payload.get("device", ""))):
                    return None
            return payload
        except Exception:
            return None

    def validate_envelope(self, uid: str, session_id: str, role: str, device_id: str,
                          seq: Any, timestamp_ms: Any, required_scope: str = "control") -> bool:
        """Reject stale, future, duplicate and reordered control messages."""
        self.initialize()
        try:
            seq_i, ts_i = int(seq), int(timestamp_ms)
        except (TypeError, ValueError):
            return False
        now_ms = int(time.time() * 1000)
        if seq_i < 1 or ts_i < now_ms - 30_000 or ts_i > now_ms + 10_000:
            return False
        with self._lock:
            session = self.get(uid, session_id)
            if not session or session.state != "active" or required_scope not in session.granted_scopes:
                return False
            expected = session.host_device_id if role == "host" else session.viewer_device_id
            if not hmac.compare_digest(expected, str(device_id)):
                return False
            key = role + ":" + str(device_id)
            if seq_i <= session.last_seq.get(key, 0):
                return False
            session.last_seq[key] = seq_i
            return True

    def audit_snapshot(self, uid: str, *, since_at: float = 0.0, limit: int = 500) -> list[dict[str, Any]]:
        self.initialize()
        if self._store is not None:
            return self._store.list_audit(str(uid), since_at=since_at, limit=limit)
        uid_hash = hashlib.sha256(str(uid).encode()).hexdigest()[:12]
        with self._lock:
            rows = [dict(item) for item in self._audit
                    if item.get("uid_hash") == uid_hash and float(item.get("at") or 0) >= since_at]
            return rows[-max(1, min(int(limit), 2000)):]

    def audit_for_session(self, uid: str, session_id: str) -> list[dict[str, Any]]:
        self.initialize()
        session = self.get(uid, session_id)
        if not session:
            return []
        if self._store is not None:
            return self._store.list_audit(str(uid), session_id=str(session_id))
        return [item for item in self.audit_snapshot(uid) if item.get("session_id") == session_id]

    def record_transport(self, uid: str, session_id: str, role: str, device_id: str,
                         report: dict[str, Any]) -> bool:
        """Persist a bounded browser-observed selected-transport summary."""
        self.initialize()
        if role not in ("host", "viewer") or not isinstance(report, dict):
            return False
        with self._lock:
            session = self.get(uid, session_id)
            expected = session.host_device_id if session and role == "host" else session.viewer_device_id if session else ""
            if not session or session.state != "active" or not hmac.compare_digest(expected, str(device_id)):
                return False
            candidate = str(report.get("candidateType") or "unknown").lower()
            protocol = str(report.get("protocol") or "unknown").lower()
            if candidate not in {"host", "srflx", "prflx", "relay", "unknown"}:
                candidate = "unknown"
            if protocol not in {"udp", "tcp", "tls", "unknown"}:
                protocol = "unknown"
            def number(name: str, low: float, high: float):
                try: return max(low, min(float(report.get(name)), high))
                except (TypeError, ValueError): return None
            self._event("transport_observed", session, role=role, candidate_type=candidate,
                        protocol=protocol, rtt_ms=number("rttMs", 0, 120_000),
                        packet_loss_pct=number("packetLossPct", 0, 100),
                        bytes_sent=number("bytesSent", 0, 1e15), bytes_received=number("bytesReceived", 0, 1e15))
            return True

    def record_milestone(self, uid: str, session_id: str, role: str, device_id: str,
                         milestone: str, detail: dict[str, Any] | None = None) -> bool:
        """Persist one bounded media-plane fact without SDP, addresses or credentials."""
        self.initialize()
        milestone = str(milestone or "").strip().lower()
        if role not in ("host", "viewer") or milestone not in REMOTE_MILESTONES:
            return False
        with self._lock:
            session = self.get(uid, session_id)
            expected = session.host_device_id if session and role == "host" else session.viewer_device_id if session else ""
            pending_event = milestone in {"connect_requested", "permission_pending"}
            state_allowed = bool(session and (session.state == "active" or (pending_event and session.state == "pending")))
            if not state_allowed or not hmac.compare_digest(expected, str(device_id)):
                return False
            raw = detail if isinstance(detail, dict) else {}
            safe: dict[str, Any] = {"role": role, "milestone": milestone}
            stage = str(raw.get("stage") or milestone)[:64]
            safe["stage"] = stage
            code = str(raw.get("errorCode") or raw.get("error_code") or "").upper()[:80]
            if code:
                safe["error_code"] = code if code in REMOTE_ERROR_CODES else "REMOTE_INTERNAL_ERROR"
            for key in ("candidateType", "protocol", "path", "codec"):
                value = str(raw.get(key) or "").lower()[:32]
                if value:
                    safe[{"candidateType": "candidate_type"}.get(key, key)] = value
            for key in ("host", "srflx", "prflx", "relay"):
                try:
                    value = int((raw.get("candidateCounts") or {}).get(key, 0))
                except (AttributeError, TypeError, ValueError):
                    value = 0
                if value:
                    safe[f"candidate_{key}"] = max(0, min(value, 256))
            for source, target, high in (
                ("elapsedMs", "elapsed_ms", 120_000), ("rttMs", "rtt_ms", 120_000),
                ("width", "width", 16_384), ("height", "height", 16_384),
                ("fps", "fps", 240),
            ):
                try:
                    value = float(raw.get(source))
                except (TypeError, ValueError):
                    continue
                safe[target] = max(0, min(value, high))
            self._event("remote_milestone", session, **safe)
            return True

    def record_acceptance(self, uid: str, kind: str, status: str, started_at: float,
                          finished_at: float, device_count: int, evidence: dict[str, Any]) -> str:
        self.initialize()
        if self._store is None:
            raise RuntimeError("persistence_required")
        return self._store.record_acceptance(uid, kind, status, started_at, finished_at, device_count, evidence)

    def latest_acceptance(self, uid: str, kind: str) -> dict[str, Any] | None:
        self.initialize()
        return self._store.latest_acceptance(uid, kind) if self._store is not None else None


from hashmm.api.remote_persistence import remote_persistence
from hashmm.api.remote_work import project_remote_transition

remote_session_registry = RemoteSessionRegistry(
    store=remote_persistence,
    transition_sink=project_remote_transition,
)
