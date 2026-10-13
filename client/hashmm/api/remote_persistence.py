"""Durable remote-session, audit and acceptance persistence.

The live socket registry remains responsible for routing, while this store is
the recovery and accountability boundary.  No account token, TURN credential,
SDP, ICE candidate, clipboard body, file body or input payload is persisted.

Audit rows form an HMAC-linked chain per owner/session.  This detects accidental
or out-of-band row modification while the deployment secret is stable; it is
integrity evidence, not third-party notarisation or non-repudiation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Callable


ConnectFactory = Callable[[], Any]


def _default_connect():
    from hashmm.api.database import _conn
    return _conn()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _audit_key() -> bytes:
    secret = os.environ.get("HASHMM_SECRET", "hashmm-secret-change-me")
    return hmac.new(secret.encode("utf-8"), b"hashmm.remote.audit.v1", hashlib.sha256).digest()


def _bounded_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-content fields that are useful during incident review."""
    allowed = {
        "scopes", "role", "reason", "transport", "candidate_type", "protocol",
        "rtt_ms", "packet_loss_pct", "bytes_sent", "bytes_received", "device_role",
        "transport_roles", "transport_samples", "verification",
        "successor_session_id",
    }
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in allowed:
            continue
        if key in {"scopes", "transport_roles"} and isinstance(value, (list, tuple)):
            allowed_items = {"host", "viewer"} if key == "transport_roles" else None
            clean[key] = [
                str(item)[:32] for item in value[:16]
                if allowed_items is None or str(item) in allowed_items
            ]
        elif isinstance(value, bool) or value is None:
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value if abs(float(value)) < 1e15 else None
        else:
            clean[key] = str(value)[:160]
    return clean


class RemotePersistence:
    def __init__(self, connect: ConnectFactory | None = None, *, clock: Callable[[], float] = time.time):
        self._connect = connect or _default_connect
        self._clock = clock
        self._lock = threading.RLock()
        self._ready = False

    def initialize(self) -> list[dict[str, Any]]:
        """Create/repair tables and fail closed any session left in flight."""
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                now = self._clock()
                in_flight = conn.execute(
                    "SELECT * FROM remote_sessions WHERE state IN ('pending','active')"
                ).fetchall()
                for row in in_flight:
                    record = dict(row)
                    record["state"] = "interrupted"
                    record["generation"] = int(record.get("generation") or 0) + 1
                    record["reason"] = "server_restarted"
                    record["expires_at"] = now
                    self._upsert_session(conn, record, now)
                    self._append_event(conn, record["user_id"], record["id"],
                                       "interrupted", {"reason": "server_restarted"}, now)
                rows = conn.execute(
                    "SELECT * FROM remote_sessions ORDER BY updated_at DESC LIMIT 5000"
                ).fetchall()
            self._ready = True
            return [dict(row) for row in rows]

    def persist_transition(self, record: dict[str, Any], event: str,
                           detail: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if not self._ready:
                self.initialize()
            now = self._clock()
            with self._connect() as conn:
                self._upsert_session(conn, record, now)
                public_event = self._append_event(
                    conn, str(record["uid"]), str(record["id"]), str(event), detail or {}, now
                )
                self._prune(conn, now)
            return public_event

    def list_audit(self, user_id: str, *, session_id: str = "", limit: int = 500,
                   since_at: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            if not self._ready:
                self.initialize()
            cap = max(1, min(int(limit), 2000))
            with self._connect() as conn:
                if session_id:
                    rows = conn.execute(
                        "SELECT * FROM remote_audit_events WHERE user_id=? AND session_id=? "
                        "AND at>=? ORDER BY at,event_id LIMIT ?",
                        (str(user_id), str(session_id), max(0.0, float(since_at)), cap)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM remote_audit_events WHERE user_id=? AND at>=? "
                        "ORDER BY at DESC,event_id DESC LIMIT ?",
                        (str(user_id), max(0.0, float(since_at)), cap)
                    ).fetchall()
            records = [dict(row) for row in rows]
            if session_id:
                return self._verify_chain(records)
            return [self._public_event(row, integrity=None) for row in records]

    def record_acceptance(self, user_id: str, kind: str, status: str,
                          started_at: float, finished_at: float,
                          device_count: int, evidence: dict[str, Any]) -> str:
        kind = str(kind)
        status = str(status)
        if kind not in {"network", "soak"} or status not in {"passed", "failed", "incomplete"}:
            raise ValueError("invalid_acceptance")
        run_id = "ra_" + secrets.token_urlsafe(18)
        bounded = _bounded_acceptance_evidence(kind, evidence)
        status = _validated_acceptance_status(
            kind, status, bounded, float(started_at), float(finished_at), max(0, int(device_count))
        )
        now = self._clock()
        with self._lock:
            if not self._ready:
                self.initialize()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO remote_acceptance_runs "
                    "(run_id,user_id,kind,status,started_at,finished_at,duration_seconds,device_count,evidence_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, str(user_id), kind, status, float(started_at), float(finished_at),
                     max(0.0, float(finished_at) - float(started_at)), max(0, int(device_count)),
                     _json(bounded), now),
                )
        return run_id

    def latest_acceptance(self, user_id: str, kind: str) -> dict[str, Any] | None:
        with self._lock:
            if not self._ready:
                self.initialize()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM remote_acceptance_runs WHERE user_id=? AND kind=? "
                    "ORDER BY created_at DESC LIMIT 1", (str(user_id), str(kind))
                ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["evidence"] = json.loads(result.pop("evidence_json"))
        except Exception:
            result["evidence"] = {}
            result.pop("evidence_json", None)
        return result

    @staticmethod
    def _ensure_schema(conn: Any) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_sessions (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, host_device_id TEXT NOT NULL,
            viewer_device_id TEXT NOT NULL, requested_scopes_json TEXT NOT NULL DEFAULT '[]',
            granted_scopes_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'pending',
            generation INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            expires_at REAL NOT NULL DEFAULT 0, work_id TEXT NOT NULL DEFAULT '',
            work_run_id TEXT NOT NULL DEFAULT '',
            predecessor_session_id TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)""")
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(remote_sessions)").fetchall()
        }
        if "work_run_id" not in columns:
            conn.execute(
                "ALTER TABLE remote_sessions ADD COLUMN work_run_id TEXT NOT NULL DEFAULT ''"
            )
        if "predecessor_session_id" not in columns:
            conn.execute(
                "ALTER TABLE remote_sessions ADD COLUMN predecessor_session_id "
                "TEXT NOT NULL DEFAULT ''"
            )
        # Only backfill legacy identifiers that are proven to reference a Work
        # owned by the same account.  Unknown strings stay detached.
        has_work_runtime = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_runs'"
        ).fetchone()
        if has_work_runtime:
            conn.execute(
                "UPDATE remote_sessions SET work_run_id=work_id "
                "WHERE work_run_id='' AND work_id<>'' AND EXISTS ("
                "SELECT 1 FROM work_runs WHERE work_runs.id=remote_sessions.work_id "
                "AND work_runs.user_id=remote_sessions.user_id)"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_updated ON remote_sessions(user_id,updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_work ON remote_sessions(user_id,work_run_id,updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_predecessor ON remote_sessions(user_id,predecessor_session_id,updated_at DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_audit_events (
            event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
            event TEXT NOT NULL, at REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
            previous_hash TEXT NOT NULL DEFAULT '', event_hash TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_audit_owner_time ON remote_audit_events(user_id,at DESC)")
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_acceptance_runs (
            run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL NOT NULL,
            duration_seconds REAL NOT NULL DEFAULT 0, device_count INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_acceptance_owner_kind ON remote_acceptance_runs(user_id,kind,created_at DESC)")

    @staticmethod
    def _upsert_session(conn: Any, record: dict[str, Any], now: float) -> None:
        requested = record.get("requested_scopes", record.get("requested_scopes_json", []))
        granted = record.get("granted_scopes", record.get("granted_scopes_json", []))
        if isinstance(requested, str):
            try: requested = json.loads(requested)
            except Exception: requested = []
        if isinstance(granted, str):
            try: granted = json.loads(granted)
            except Exception: granted = []
        values = (
            str(record["id"]), str(record.get("uid", record.get("user_id", ""))),
            str(record.get("host_device_id", "")), str(record.get("viewer_device_id", "")),
            _json(list(requested)), _json(list(granted)), str(record.get("state", "pending")),
            int(record.get("generation") or 0), float(record.get("created_at") or now),
            float(record.get("expires_at") or 0),
            str(record.get("work_run_id") or "")[:128],
            str(record.get("work_run_id") or "")[:128],
            str(record.get("predecessor_session_id") or "")[:128],
            str(record.get("reason") or "")[:120], now,
        )
        conn.execute(
            "INSERT INTO remote_sessions "
            "(id,user_id,host_device_id,viewer_device_id,requested_scopes_json,granted_scopes_json,state,generation,created_at,expires_at,work_id,work_run_id,predecessor_session_id,reason,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "user_id=excluded.user_id,host_device_id=excluded.host_device_id,viewer_device_id=excluded.viewer_device_id,"
            "requested_scopes_json=excluded.requested_scopes_json,granted_scopes_json=excluded.granted_scopes_json,"
            "state=excluded.state,generation=excluded.generation,expires_at=excluded.expires_at,"
            "work_id=excluded.work_id,work_run_id=excluded.work_run_id,"
            "predecessor_session_id=excluded.predecessor_session_id,"
            "reason=excluded.reason,updated_at=excluded.updated_at",
            values,
        )

    @staticmethod
    def _append_event(conn: Any, user_id: str, session_id: str, event: str,
                      detail: dict[str, Any], at: float) -> dict[str, Any]:
        previous = conn.execute(
            "SELECT event_hash,at FROM remote_audit_events WHERE user_id=? AND session_id=? "
            "ORDER BY at DESC,event_id DESC LIMIT 1", (user_id, session_id)
        ).fetchone()
        previous_hash = str(previous["event_hash"] if previous else "")
        # A custom/test clock and very fast transitions may return the same
        # timestamp.  Give every link a deterministic order so chain replay
        # never depends on a random event-id tie break.
        if previous:
            at = max(float(at), float(previous["at"]) + 0.000001)
        event_id = "rae_" + secrets.token_urlsafe(18)
        clean = _bounded_detail(detail)
        canonical = _json({"event_id": event_id, "user_id": user_id, "session_id": session_id,
                           "event": event, "at": at, "detail": clean, "previous_hash": previous_hash})
        event_hash = hmac.new(_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        conn.execute(
            "INSERT INTO remote_audit_events "
            "(event_id,user_id,session_id,event,at,detail_json,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, user_id, session_id, event[:64], at, _json(clean), previous_hash, event_hash),
        )
        return {"event_id": event_id, "event": event[:64], "session_id": session_id,
                "at": at, "detail": clean, "integrity": True}

    @staticmethod
    def _public_event(row: dict[str, Any], integrity: bool | None) -> dict[str, Any]:
        try: detail = json.loads(str(row.get("detail_json") or "{}"))
        except Exception: detail = {}
        result = {"event_id": row.get("event_id"), "event": row.get("event"),
                  "session_id": row.get("session_id"), "at": row.get("at"), "detail": detail}
        if integrity is not None:
            result["integrity"] = integrity
        return result

    def _verify_chain(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Retention may intentionally remove an old prefix.  The first
        # retained row therefore anchors verification at its recorded parent;
        # every retained link and body is still authenticated.
        previous = str(rows[0].get("previous_hash") or "") if rows else ""
        result: list[dict[str, Any]] = []
        for row in rows:
            try: detail = json.loads(str(row.get("detail_json") or "{}"))
            except Exception: detail = {}
            canonical = _json({"event_id": row.get("event_id"), "user_id": row.get("user_id"),
                               "session_id": row.get("session_id"), "event": row.get("event"),
                               "at": row.get("at"), "detail": detail, "previous_hash": row.get("previous_hash") or ""})
            expected = hmac.new(_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(str(row.get("previous_hash") or ""), previous) and hmac.compare_digest(
                str(row.get("event_hash") or ""), expected)
            result.append(self._public_event(row, integrity=valid))
            previous = str(row.get("event_hash") or "")
        return result

    @staticmethod
    def _prune(conn: Any, now: float) -> None:
        try: days = max(1, min(int(os.environ.get("HASHMM_REMOTE_AUDIT_RETENTION_DAYS", "90")), 3650))
        except ValueError: days = 90
        conn.execute("DELETE FROM remote_audit_events WHERE at<?", (now - days * 86400,))


def _bounded_acceptance_evidence(kind: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    keys = {
        "network": {"schema", "devices", "nat_observations", "relay_sessions", "criteria", "failures"},
        "soak": {"schema", "samples", "successes", "failures", "max_consecutive_failures", "availability_pct",
                 "reconnects", "relay_samples", "relay_coverage_pct", "criteria"},
    }[kind]
    clean = {key: evidence[key] for key in keys if key in evidence}
    encoded = _json(clean)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        return {"truncated": True, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
    return clean


def _validated_acceptance_status(kind: str, requested: str, evidence: dict[str, Any],
                                 started_at: float, finished_at: float, device_count: int) -> str:
    """A caller cannot turn an incomplete or legacy payload into a pass flag."""
    if requested != "passed":
        return requested
    criteria = evidence.get("criteria") if isinstance(evidence.get("criteria"), dict) else {}
    if kind == "network":
        required = {
            "at_least_two_devices", "host_and_viewer_measured", "secure_backend_reachable",
            "symmetric_nat_observed", "turn_udp_reachable", "turn_tls_reachable",
            "same_session_relay_observed_on_both_ends",
        }
        valid = (evidence.get("schema") == "hashmm.remote.network-acceptance.v1"
                 and device_count >= 2 and required.issubset(criteria)
                 and all(criteria.get(name) is True for name in required))
    else:
        required = {
            "duration_reached", "sample_density_reached", "availability_reached",
            "remote_relay_coverage_reached", "failure_streak_bounded",
        }
        valid = (evidence.get("schema") == "hashmm.remote.soak-acceptance.v1"
                 and finished_at - started_at >= 86_400 and required.issubset(criteria)
                 and all(criteria.get(name) is True for name in required))
    return "passed" if valid else "incomplete"


remote_persistence = RemotePersistence()
