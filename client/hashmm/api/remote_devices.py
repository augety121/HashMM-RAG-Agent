"""Durable, owner-scoped device directory for Remote Fabric v2.

The WebSocket hub remains the live routing boundary.  This registry gives all
clients one stable device identity and a short presence lease that survives UI
navigation and can be inspected without exposing tokens, IP addresses, SDP or
ICE candidates.

SQLite is a safe single-origin fallback and is shared by multiple processes on
the same host.  Cross-host/multi-origin signaling still requires the optional
shared broker declared by ``HASHMM_REMOTE_SHARED_BROKER``; readiness reports
that boundary instead of pretending the fallback is horizontally scalable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable


ConnectFactory = Callable[[], Any]
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_ROLES = {"host", "viewer"}
_TRACE_ID = re.compile(r"^rt_[A-Za-z0-9_-]{12,80}$")
_ATTEMPT_ID = re.compile(r"^ra_[A-Za-z0-9_-]{8,80}$")
_CAPABILITIES = {
    "view", "control", "clipboard", "clipboard_read", "clipboard_write",
    "file", "file_read", "file_write", "audio", "power", "dispatch",
}


def _default_connect():
    from hashmm.api.database import _conn
    return _conn()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def owner_fingerprint(owner: str) -> str:
    value = str(owner or "").removeprefix("sb_")
    return _digest(value)[:12] if value else ""


def device_fingerprint(device_id: str) -> str:
    return _digest(str(device_id or ""))[:12] if device_id else ""


class RemoteDeviceRegistry:
    def __init__(self, connect: ConnectFactory | None = None, *,
                 clock: Callable[[], float] = time.time, lease_ttl: int | None = None):
        self._connect = connect or _default_connect
        self._clock = clock
        self._lease_ttl = max(20, min(int(lease_ttl or os.environ.get(
            "HASHMM_REMOTE_PRESENCE_TTL", "35")), 120))
        self._lock = threading.RLock()
        self._ready = False

    @property
    def lease_ttl(self) -> int:
        return self._lease_ttl

    def initialize(self) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                # Do not clear leases here: another worker may own a valid
                # connection.  Dead-process leases disappear naturally after
                # the short TTL, while generation fencing prevents stale
                # connections from overwriting a newer registration.
                conn.execute("DELETE FROM remote_socket_tickets WHERE expires_at<? OR used_at>0",
                             (self._clock() - 300,))
                conn.execute("DELETE FROM remote_connection_attempts WHERE updated_at<?",
                             (self._clock() - 7 * 86400,))
            self._ready = True

    def register(self, owner: str, device_id: str, *, connection_id: str,
                 role: str, name: str = "", platform: str = "",
                 app_version: str = "", capabilities: list[str] | tuple[str, ...] = (),
                 routable: bool = False) -> dict[str, Any]:
        owner = str(owner or "").strip()[:160]
        device_id = str(device_id or "").strip()
        role = str(role or "").strip()
        if not owner or not _DEVICE_ID.fullmatch(device_id) or role not in _ROLES:
            raise ValueError("invalid_device_registration")
        caps = sorted({str(item) for item in capabilities if str(item) in _CAPABILITIES})
        if role == "host" and not caps:
            caps = ["view", "control"]
        lease_id = "rl_" + secrets.token_urlsafe(18)
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT generation,created_at,trust_state,revoked_at FROM remote_devices "
                    "WHERE user_id=? AND device_id=?", (owner, device_id)
                ).fetchone()
                generation = int(row["generation"] if row else 0) + 1
                created_at = float(row["created_at"] if row else now)
                trust_state = str(row["trust_state"] if row else "account")
                revoked_at = float(row["revoked_at"] if row else 0)
                if revoked_at > 0:
                    raise ValueError("device_revoked")
                conn.execute(
                    "INSERT INTO remote_devices "
                    "(user_id,device_id,role,name,platform,app_version,capabilities_json,"
                    "trust_state,created_at,last_registered_at,last_seen,lease_id,generation,"
                    "lease_expires_at,connection_id,remote_ready,agent_ready,busy,revoked_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(user_id,device_id) DO UPDATE SET role=excluded.role,name=excluded.name,"
                    "platform=excluded.platform,app_version=excluded.app_version,"
                    "capabilities_json=excluded.capabilities_json,last_registered_at=excluded.last_registered_at,"
                    "last_seen=excluded.last_seen,lease_id=excluded.lease_id,generation=excluded.generation,"
                    "lease_expires_at=excluded.lease_expires_at,connection_id=excluded.connection_id,"
                    "remote_ready=excluded.remote_ready,busy=0",
                    (owner, device_id, role, str(name or ("电脑" if role == "host" else "移动设备"))[:80],
                     str(platform or "")[:40], str(app_version or "")[:40], _json(caps), trust_state,
                     created_at, now, now, lease_id, generation, now + self._lease_ttl,
                     str(connection_id or "")[:128], 1 if role == "host" and routable else 0, 0, 0, 0),
                )
        return {"device_id": device_id, "lease_id": lease_id, "generation": generation,
                "lease_expires_in": self._lease_ttl, "owner_fingerprint": owner_fingerprint(owner),
                "device_fingerprint": device_fingerprint(device_id)}

    def heartbeat(self, owner: str, device_id: str, lease_id: str, generation: int,
                  *, remote_ready: bool | None = None, busy: bool | None = None) -> bool:
        now = self._clock()
        fields = ["last_seen=?", "lease_expires_at=?"]
        values: list[Any] = [now, now + self._lease_ttl]
        if remote_ready is not None:
            fields.append("remote_ready=?")
            values.append(1 if remote_ready else 0)
        if busy is not None:
            fields.append("busy=?")
            values.append(1 if busy else 0)
        values.extend([str(owner), str(device_id), str(lease_id), int(generation)])
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                cur = conn.execute(
                    f"UPDATE remote_devices SET {','.join(fields)} WHERE user_id=? AND device_id=? "
                    "AND lease_id=? AND generation=? AND revoked_at=0", values
                )
                return cur.rowcount == 1

    def disconnect(self, owner: str, device_id: str, lease_id: str, generation: int) -> bool:
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE remote_devices SET lease_expires_at=0,connection_id='',remote_ready=0,busy=0 "
                    "WHERE user_id=? AND device_id=? AND lease_id=? AND generation=?",
                    (str(owner), str(device_id), str(lease_id), int(generation)),
                )
                return cur.rowcount == 1

    def list_devices(self, owner: str, *, remote_only: bool = False,
                     include_offline: bool = True) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM remote_devices WHERE user_id=? AND revoked_at=0 "
                    "ORDER BY last_seen DESC,device_id", (str(owner),)
                ).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            online = float(row.get("lease_expires_at") or 0) > now
            remote_ready = online and bool(row.get("remote_ready")) and row.get("role") == "host"
            if remote_only and not remote_ready:
                continue
            if not include_offline and not online:
                continue
            try:
                caps = json.loads(str(row.get("capabilities_json") or "[]"))
            except Exception:
                caps = []
            result.append({
                "device_id": str(row.get("device_id") or ""),
                "id": str(row.get("device_id") or ""),
                "name": str(row.get("name") or "电脑"),
                "platform": str(row.get("platform") or ""),
                "role": str(row.get("role") or ""),
                "app_version": str(row.get("app_version") or ""),
                "online": online,
                "remote_ready": remote_ready,
                "agent_ready": online and bool(row.get("agent_ready")),
                "busy": online and bool(row.get("busy")),
                "last_seen": float(row.get("last_seen") or 0),
                "capabilities": caps if isinstance(caps, list) else [],
                "generation": int(row.get("generation") or 0),
                "device_fingerprint": device_fingerprint(str(row.get("device_id") or "")),
            })
        return result

    def get_connection_id(self, owner: str, device_id: str) -> str:
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT connection_id,lease_expires_at,remote_ready FROM remote_devices "
                    "WHERE user_id=? AND device_id=? AND revoked_at=0",
                    (str(owner), str(device_id)),
                ).fetchone()
        if not row or float(row["lease_expires_at"] or 0) <= now or not bool(row["remote_ready"]):
            return ""
        return str(row["connection_id"] or "")

    def issue_socket_ticket(self, owner: str, device_id: str, role: str, *, ttl: int = 30,
                            trace_id: str = "", protocol: str = "hashmm.remote.v2",
                            endpoint_host: str = "") -> dict[str, Any]:
        if not str(owner or "") or not _DEVICE_ID.fullmatch(str(device_id or "")) or role not in _ROLES:
            raise ValueError("invalid_socket_ticket_request")
        token = "rst_" + secrets.token_urlsafe(32)
        attempt_id = "ra_" + secrets.token_urlsafe(12)
        trace_id = str(trace_id or "").strip()
        if not _TRACE_ID.fullmatch(trace_id):
            trace_id = "rt_" + secrets.token_urlsafe(16)
        protocol = str(protocol or "hashmm.remote.v2")[:40]
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                # Consumed tickets are no longer credentials, but their
                # non-secret attempt metadata is valuable for cross-device
                # diagnostics. Keep one hour of evidence instead of deleting
                # the viewer attempt whenever the host requests a new ticket.
                conn.execute(
                    "DELETE FROM remote_socket_tickets "
                    "WHERE expires_at<? OR (used_at>0 AND used_at<?)",
                    (now - 3600, now - 3600),
                )
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM remote_socket_tickets "
                    "WHERE user_id=? AND used_at=0 AND expires_at>?",
                    (str(owner), now),
                ).fetchone()
                if int(active["n"] if active else 0) >= 64:
                    raise ValueError("too_many_active_socket_tickets")
                conn.execute(
                    "INSERT INTO remote_socket_tickets "
                    "(ticket_hash,user_id,device_id,role,attempt_id,trace_id,protocol,created_at,expires_at,used_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (_digest(token), str(owner), str(device_id), role, attempt_id, trace_id, protocol,
                     now, now + max(10, min(int(ttl), 60))),
                )
                conn.execute(
                    "INSERT INTO remote_connection_attempts "
                    "(attempt_id,trace_id,user_id,device_id,role,protocol,endpoint_host,stage,error_code,"
                    "close_code,security_json,detail,created_at,updated_at,registered_at,closed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)",
                    (attempt_id, trace_id, str(owner), str(device_id), role, protocol,
                     str(endpoint_host or "")[:160], "ticket_issued", "", 0, "{}", "", now, now),
                )
        return {"ticket": token, "expires_in": max(10, min(int(ttl), 60)),
                "protocol": protocol, "attempt_id": attempt_id, "trace_id": trace_id}

    def _consume_socket_ticket(self, token: str, *, include_attempt: bool) -> dict[str, str] | None:
        digest = _digest(str(token or ""))
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM remote_socket_tickets WHERE ticket_hash=? AND used_at=0 AND expires_at>?",
                    (digest, now),
                ).fetchone()
                if not row:
                    return None
                cur = conn.execute(
                    "UPDATE remote_socket_tickets SET used_at=? WHERE ticket_hash=? AND used_at=0",
                    (now, digest),
                )
                if cur.rowcount != 1:
                    return None
        result = {"uid": str(row["user_id"]), "device_id": str(row["device_id"]),
                  "role": str(row["role"])}
        if include_attempt:
            result["attempt_id"] = str(row["attempt_id"] or "")
            result["protocol"] = str(row["protocol"] or "hashmm.remote.v2")
            result["trace_id"] = str(row["trace_id"] or "")
            self.transition_attempt(result["attempt_id"], "ticket_consumed")
        return result

    def consume_socket_ticket(self, token: str) -> dict[str, str] | None:
        """Consume a ticket using the stable v2 return shape."""
        return self._consume_socket_ticket(token, include_attempt=False)

    def consume_socket_ticket_detailed(self, token: str) -> dict[str, str] | None:
        """Consume a ticket and include its non-secret diagnostic correlation id."""
        return self._consume_socket_ticket(token, include_attempt=True)

    def transition_attempt(self, attempt_id: str, stage: str, *, error_code: str = "",
                           close_code: int = 0, security: dict[str, Any] | None = None,
                           detail: str = "", terminal: bool = False) -> bool:
        """Persist a bounded, non-secret connection transition for support diagnostics."""
        attempt_id = str(attempt_id or "").strip()
        if not _ATTEMPT_ID.fullmatch(attempt_id):
            return False
        now = self._clock()
        safe_security = {
            key: value for key, value in dict(security or {}).items()
            if key in {"secure", "source", "asgi_scheme", "forwarded_proto", "trusted_proxy",
                       "peer_class", "cloudflare", "cf_ray"}
        }
        if safe_security.get("cf_ray"):
            safe_security["cf_ray"] = _digest(str(safe_security["cf_ray"]))[:12]
        registered_at = now if stage == "registered" else 0
        closed_at = now if terminal else 0
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE remote_connection_attempts SET stage=?,error_code=?,close_code=?,"
                    "security_json=?,detail=?,updated_at=?,"
                    "registered_at=CASE WHEN ?>0 THEN ? ELSE registered_at END,"
                    "closed_at=CASE WHEN ?>0 THEN ? ELSE closed_at END WHERE attempt_id=?",
                    (str(stage or "unknown")[:64], str(error_code or "")[:80], int(close_code or 0),
                     _json(safe_security), str(detail or "")[:240], now,
                     registered_at, registered_at, closed_at, closed_at, attempt_id),
                )
                return cur.rowcount == 1

    def diagnostics(self, owner: str, device_id: str = "") -> dict[str, Any]:
        devices = self.list_devices(owner, include_offline=True)
        selected = next((item for item in devices if item["device_id"] == device_id), None)
        broker = str(os.environ.get("HASHMM_REMOTE_SHARED_BROKER", "") or "").strip().lower()
        now = self._clock()
        with self._lock:
            self._ensure_ready()
            with self._connect() as conn:
                ticket_rows = conn.execute(
                    "SELECT role,created_at,expires_at,used_at,attempt_id,trace_id,protocol "
                    "FROM remote_socket_tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
                    (str(owner),),
                ).fetchall()
                attempt_rows = conn.execute(
                    "SELECT attempt_id,trace_id,device_id,role,protocol,stage,error_code,close_code,"
                    "security_json,detail,created_at,updated_at,registered_at,closed_at "
                    "FROM remote_connection_attempts WHERE user_id=? ORDER BY updated_at DESC LIMIT 20",
                    (str(owner),),
                ).fetchall()
        tickets = [dict(row) for row in ticket_rows]
        attempts = [dict(row) for row in attempt_rows]
        last_host = next((row for row in tickets if row.get("role") == "host"), None)
        last_viewer = next((row for row in tickets if row.get("role") == "viewer"), None)
        host_registered = any(item["remote_ready"] for item in devices)
        viewer_registered = any(item["online"] and item.get("role") == "viewer" for item in devices)
        host_error = next((row for row in attempts if row.get("role") == "host" and row.get("error_code")), None)
        latest_failure = next((row for row in attempts if row.get("error_code")), None)
        host_detail = self._attempt_error_detail(str((host_error or {}).get("error_code") or "")) if host_error else "电脑主机尚未完成 WSS 注册"
        steps = [
            {"id": "identity", "state": "ok", "detail": "账号身份已通过后端验证"},
            {"id": "host_ticket", "state": "ok" if last_host else "missing",
             "detail": "电脑已申请主机信令票据" if last_host else "尚未收到电脑主机票据请求"},
            {"id": "host_socket", "state": "ok" if host_registered else ("blocked" if host_error else "waiting"),
             "detail": "电脑主机已注册并可路由" if host_registered else host_detail},
            {"id": "viewer_socket", "state": "ok" if viewer_registered else ("waiting" if last_viewer else "missing"),
             "detail": "移动端查看器已完成 WSS 注册" if viewer_registered else
                       "移动端已申请查看票据，正在等待 WSS 注册" if last_viewer else
                       "尚未收到移动端查看器注册"},
        ]
        return {
            "schema": "hashmm.remote-diagnostics.v2",
            "protocol": "hashmm.remote.v4",
            "owner_fingerprint": owner_fingerprint(owner),
            "device_fingerprint": device_fingerprint(device_id),
            "device": selected,
            "online_devices": sum(1 for item in devices if item["online"]),
            "remote_ready_devices": sum(1 for item in devices if item["remote_ready"]),
            "presence_backend": "sqlite_lease",
            "shared_broker": broker or "unconfigured",
            "shared_broker_status": "adapter_not_deployed" if broker else "unconfigured",
            "multi_instance_ready": False,
            "lease_ttl_seconds": self._lease_ttl,
            "host_registered": host_registered,
            "viewer_registered": viewer_registered,
            "latest_failure": self._serialize_attempt(latest_failure),
            "steps": steps,
            "last_attempts": [
                {"role": str(row.get("role") or ""), "attempt_id": str(row.get("attempt_id") or ""),
                 "trace_id": str(row.get("trace_id") or ""), "protocol": str(row.get("protocol") or ""),
                 "created_at": float(row.get("created_at") or 0),
                 "consumed": float(row.get("used_at") or 0) > 0,
                 "expired": float(row.get("expires_at") or 0) <= now}
                for row in tickets[:6]
            ],
            "connection_attempts": [self._serialize_attempt(row) for row in attempts[:8]],
        }

    @staticmethod
    def _attempt_error_detail(code: str) -> str:
        return {
            "PUBLIC_ENDPOINT_INSECURE": "安全入口验证失败：桌面端未通过公网 WSS 建立主机通道",
            "PROXY_SECURITY_UNVERIFIED": "反向代理安全信息无法验证，请检查 Cloudflare Tunnel",
            "EDGE_HEADER_MISSING": "公网入口缺少 HTTPS 转发信息，请检查 Tunnel 路由",
            "TICKET_EXPIRED": "主机票据已过期，正在重新申请",
            "TICKET_REPLAYED": "主机票据已被使用，已拒绝重复连接",
            "TICKET_INVALID_OR_REPLAYED": "一次性信令票据无效、过期或已被使用",
            "TOKEN_INVALID": "账号会话已失效，请重新登录",
            "ATTEMPT_MISMATCH": "连接尝试与一次性票据不匹配",
            "ROLE_MISMATCH": "连接角色与票据不匹配",
            "DEVICE_ID_INVALID": "设备身份格式无效",
            "AUTH_TIMEOUT": "安全通道建立后未在时限内完成认证",
            "SOCKET_FAILED": "远程控制通道异常中断",
            "OWNER_MISMATCH": "设备与当前账号不匹配",
            "PROTOCOL_MISMATCH": "远程协议版本不匹配，请更新桌面端或 App",
            "CONTROL_SOCKET_LOST": "远程控制通道异常断开",
            "CONTROL_SOCKET_TIMEOUT": "远程控制通道等待超时",
            "REMOTE_INTERNAL_ERROR": "远程控制通道发生内部错误",
        }.get(str(code or ""), "电脑主机注册失败，请复制诊断编号交给管理员")

    @staticmethod
    def _serialize_attempt(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        try:
            security = json.loads(str(row.get("security_json") or "{}"))
        except Exception:
            security = {}
        code = str(row.get("error_code") or "")
        return {
            "attempt_id": str(row.get("attempt_id") or ""), "trace_id": str(row.get("trace_id") or ""),
            "device_fingerprint": device_fingerprint(str(row.get("device_id") or "")),
            "role": str(row.get("role") or ""), "protocol": str(row.get("protocol") or ""),
            "stage": str(row.get("stage") or ""), "error_code": code,
            "close_code": int(row.get("close_code") or 0),
            "detail": RemoteDeviceRegistry._attempt_error_detail(code) if code else str(row.get("detail") or ""),
            "security": security if isinstance(security, dict) else {},
            "created_at": float(row.get("created_at") or 0), "updated_at": float(row.get("updated_at") or 0),
            "registered_at": float(row.get("registered_at") or 0), "closed_at": float(row.get("closed_at") or 0),
        }

    def _ensure_ready(self) -> None:
        if not self._ready:
            self.initialize()

    @staticmethod
    def _ensure_schema(conn: Any) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_devices (
            user_id TEXT NOT NULL, device_id TEXT NOT NULL, role TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '',
            app_version TEXT NOT NULL DEFAULT '', capabilities_json TEXT NOT NULL DEFAULT '[]',
            trust_state TEXT NOT NULL DEFAULT 'account', created_at REAL NOT NULL,
            last_registered_at REAL NOT NULL, last_seen REAL NOT NULL,
            lease_id TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0,
            lease_expires_at REAL NOT NULL DEFAULT 0, connection_id TEXT NOT NULL DEFAULT '',
            remote_ready INTEGER NOT NULL DEFAULT 0, agent_ready INTEGER NOT NULL DEFAULT 0,
            busy INTEGER NOT NULL DEFAULT 0, revoked_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id,device_id))""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_devices_owner_seen "
            "ON remote_devices(user_id,last_seen DESC)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_socket_tickets (
            ticket_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
            role TEXT NOT NULL, attempt_id TEXT NOT NULL DEFAULT '', trace_id TEXT NOT NULL DEFAULT '',
            protocol TEXT NOT NULL DEFAULT 'hashmm.remote.v2',
            created_at REAL NOT NULL, expires_at REAL NOT NULL,
            used_at REAL NOT NULL DEFAULT 0)""")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(remote_socket_tickets)").fetchall()}
        if "attempt_id" not in columns:
            conn.execute("ALTER TABLE remote_socket_tickets ADD COLUMN attempt_id TEXT NOT NULL DEFAULT ''")
        if "trace_id" not in columns:
            conn.execute("ALTER TABLE remote_socket_tickets ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''")
        if "protocol" not in columns:
            conn.execute("ALTER TABLE remote_socket_tickets ADD COLUMN protocol TEXT NOT NULL DEFAULT 'hashmm.remote.v2'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_socket_tickets_expiry "
            "ON remote_socket_tickets(expires_at)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS remote_connection_attempts (
            attempt_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, user_id TEXT NOT NULL,
            device_id TEXT NOT NULL, role TEXT NOT NULL, protocol TEXT NOT NULL,
            endpoint_host TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL,
            error_code TEXT NOT NULL DEFAULT '', close_code INTEGER NOT NULL DEFAULT 0,
            security_json TEXT NOT NULL DEFAULT '{}', detail TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            registered_at REAL NOT NULL DEFAULT 0, closed_at REAL NOT NULL DEFAULT 0)""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_attempts_owner_updated "
            "ON remote_connection_attempts(user_id,updated_at DESC)"
        )


remote_device_registry = RemoteDeviceRegistry()
