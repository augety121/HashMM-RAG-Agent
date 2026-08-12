"""Durable, owner-scoped provider routing for HashMM V2600.

This module is the southbound provider plane.  It deliberately does not share
credentials with the northbound ``/v1`` API-key gateway.  Connections can
represent an official API, an authorised Sub2API deployment, a generic
compatible gateway, or an explicitly local runtime.

Only metadata needed for routing and accounting is persisted.  Prompts,
responses and upstream Authorization headers are never written here.
"""
from __future__ import annotations

import ipaddress
import hashlib
import json
import socket
import time
import uuid
from urllib.parse import urlparse

from hashmm.api import database as db
from hashmm.secrets_crypto import decrypt_secret, encrypt_secret


SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_connections (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
 kind TEXT NOT NULL, base_url TEXT NOT NULL, wire_api TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'unverified',
 config_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
 updated_at REAL NOT NULL, UNIQUE(owner_id,name)
);
CREATE INDEX IF NOT EXISTS idx_provider_connections_owner
 ON provider_connections(owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS provider_credentials (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, connection_id TEXT NOT NULL,
 label TEXT NOT NULL DEFAULT 'primary', secret_enc TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(owner_id,connection_id,label)
);
CREATE TABLE IF NOT EXISTS provider_channels (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, connection_id TEXT NOT NULL,
 model_alias TEXT NOT NULL, upstream_model TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 100, weight INTEGER NOT NULL DEFAULT 100,
 max_concurrency INTEGER NOT NULL DEFAULT 4, enabled INTEGER NOT NULL DEFAULT 1,
 capabilities_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
 updated_at REAL NOT NULL, UNIQUE(owner_id,connection_id,model_alias,upstream_model)
);
CREATE INDEX IF NOT EXISTS idx_provider_channels_route
 ON provider_channels(owner_id,model_alias,enabled,priority);
CREATE TABLE IF NOT EXISTS routing_policies (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT '',
 model_alias TEXT NOT NULL, strategy TEXT NOT NULL DEFAULT 'sticky_health',
 retry_before_first_token INTEGER NOT NULL DEFAULT 1,
 max_attempts INTEGER NOT NULL DEFAULT 2, config_json TEXT NOT NULL DEFAULT '{}',
 updated_at REAL NOT NULL, UNIQUE(owner_id,project_id,model_alias)
);
CREATE TABLE IF NOT EXISTS sticky_bindings (
 owner_id TEXT NOT NULL, sticky_key TEXT NOT NULL, model_alias TEXT NOT NULL,
 channel_id TEXT NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(owner_id,sticky_key,model_alias)
);
CREATE TABLE IF NOT EXISTS provider_health_samples (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 ok INTEGER NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0,
 error_code TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_health_channel
 ON provider_health_samples(owner_id,channel_id,created_at DESC);
CREATE TABLE IF NOT EXISTS provider_circuit_states (
 owner_id TEXT NOT NULL, channel_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'closed',
 failure_count INTEGER NOT NULL DEFAULT 0, open_until REAL NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL, PRIMARY KEY(owner_id,channel_id)
);
CREATE TABLE IF NOT EXISTS provider_leases (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 request_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
 expires_at REAL NOT NULL, created_at REAL NOT NULL, released_at REAL NOT NULL DEFAULT 0,
 UNIQUE(owner_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_provider_leases_active
 ON provider_leases(owner_id,channel_id,state,expires_at);
CREATE TABLE IF NOT EXISTS provider_request_attempts (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_id TEXT NOT NULL,
 channel_id TEXT NOT NULL, attempt INTEGER NOT NULL, outcome TEXT NOT NULL,
 error_code TEXT NOT NULL DEFAULT '', first_token_seen INTEGER NOT NULL DEFAULT 0,
 latency_ms INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_usage_events (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_id TEXT NOT NULL,
 channel_id TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0, cost_micros INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, UNIQUE(owner_id,request_id,channel_id)
);
"""

ALLOWED_KINDS = {"official", "sub2api", "openai_compatible", "anthropic_compatible", "local"}
ALLOWED_WIRES = {"chat_completions", "responses", "anthropic_messages"}
ALLOWED_STRATEGIES = {"sticky_health", "priority_weighted", "lowest_latency"}


def _init() -> None:
    with db._conn() as conn:  # same pool/transaction boundary as the main DB
        conn.executescript(SCHEMA)


def validate_endpoint(base_url: str, kind: str, *, resolve_dns: bool = False) -> str:
    """Fail closed for server-side provider endpoints.

    Local runtimes are an explicit connection kind.  Every other kind requires
    HTTPS and rejects loopback, link-local, multicast and private destinations.
    DNS resolution is optional at configuration time and mandatory for probes.
    """
    value = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Base URL 必须是没有内嵌凭据的 http(s) 地址")
    if kind not in ALLOWED_KINDS:
        raise ValueError("不支持的连接类型")
    if kind != "local" and parsed.scheme != "https":
        raise ValueError("公网 Provider 必须使用 HTTPS；本地端点请选择 local")

    hosts: set[str] = {parsed.hostname}
    if resolve_dns:
        try:
            hosts.update(item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443))
        except OSError as exc:
            raise ValueError("Provider 域名无法解析") from exc
    for host in hosts:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        unsafe = (address.is_private or address.is_loopback or address.is_link_local
                  or address.is_multicast or address.is_unspecified or address.is_reserved)
        if unsafe and kind != "local":
            raise ValueError("公网 Provider 不能指向内网、回环或保留地址")
    return value


def _json(value: object) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _load(value: object) -> dict:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _channel(row: dict, *, include_secret: bool = False) -> dict:
    item = {
        "id": row["id"], "connection_id": row["connection_id"],
        "connection_name": row.get("connection_name", ""),
        "kind": row.get("kind", ""), "base_url": row.get("base_url", ""),
        "wire_api": row.get("wire_api", "chat_completions"),
        "model_alias": row["model_alias"], "upstream_model": row["upstream_model"],
        "enabled": bool(int(row.get("enabled", 1) or 0)),
        "priority": int(row.get("priority") or 100),
        "weight": max(1, int(row.get("weight") or 1)),
        "max_concurrency": max(1, int(row.get("max_concurrency") or 1)),
        "active_leases": max(0, int(row.get("active_leases") or 0)),
        "capabilities": _load(row.get("capabilities_json")),
        "connection_status": row.get("connection_status", "unverified"),
        "circuit_state": row.get("circuit_state", "closed"),
        "open_until": float(row.get("open_until") or 0),
    }
    if include_secret:
        item["api_key"] = str(row.get("api_key") or "")
    return item


def add_channel(owner_id: str, connection_id: str, payload: dict) -> dict:
    """Create another immutable routing target under an owned connection."""
    _init()
    alias = str(payload.get("model_alias") or "default").strip()[:100]
    upstream = str(payload.get("upstream_model") or "").strip()[:160]
    if not alias or not upstream:
        raise ValueError("model_alias 和 upstream_model 不能为空")
    now, channel_id = time.time(), uuid.uuid4().hex
    with db._conn() as conn:
        owned = conn.execute(
            "SELECT 1 FROM provider_connections WHERE id=? AND owner_id=?",
            (connection_id, owner_id),
        ).fetchone()
        if not owned:
            raise LookupError("Provider 连接不存在")
        conn.execute(
            "INSERT INTO provider_channels(id,owner_id,connection_id,model_alias,upstream_model,"
            "priority,weight,max_concurrency,capabilities_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (channel_id, owner_id, connection_id, alias, upstream,
             max(0, min(10_000, int(payload.get("priority") or 100))),
             max(1, min(10_000, int(payload.get("weight") or 100))),
             max(1, min(128, int(payload.get("max_concurrency") or 4))),
             _json(payload.get("capabilities")), now, now),
        )
    return next(
        item for item in (get_connection(owner_id, connection_id) or {}).get("channels", [])
        if item.get("id") == channel_id
    )


def set_routing_policy(owner_id: str, model_alias: str, payload: dict) -> dict:
    _init()
    alias = str(model_alias or "").strip()[:100]
    project_id = str(payload.get("project_id") or "").strip()[:120]
    strategy = str(payload.get("strategy") or "sticky_health").strip()
    if not alias or strategy not in ALLOWED_STRATEGIES:
        raise ValueError("路由别名或策略无效")
    now, policy_id = time.time(), uuid.uuid4().hex
    retry = 1 if payload.get("retry_before_first_token", True) else 0
    attempts = max(1, min(5, int(payload.get("max_attempts") or 2)))
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO routing_policies(id,owner_id,project_id,model_alias,strategy,"
            "retry_before_first_token,max_attempts,config_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(owner_id,project_id,model_alias) DO UPDATE SET "
            "strategy=excluded.strategy,retry_before_first_token=excluded.retry_before_first_token,"
            "max_attempts=excluded.max_attempts,config_json=excluded.config_json,updated_at=excluded.updated_at",
            (policy_id, owner_id, project_id, alias, strategy, retry, attempts,
             _json(payload.get("config")), now),
        )
        row = conn.execute(
            "SELECT * FROM routing_policies WHERE owner_id=? AND project_id=? AND model_alias=?",
            (owner_id, project_id, alias),
        ).fetchone()
    result = dict(row)
    result["retry_before_first_token"] = bool(result["retry_before_first_token"])
    result["config"] = _load(result.pop("config_json", "{}"))
    return result


def _policy(conn, owner_id: str, alias: str, project_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM routing_policies WHERE owner_id=? AND model_alias=? "
        "AND project_id IN (?, '') ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END LIMIT 1",
        (owner_id, alias, project_id, project_id),
    ).fetchone()
    if not row:
        return {"strategy": "sticky_health", "retry_before_first_token": 1,
                "max_attempts": 2, "config": {}}
    result = dict(row)
    result["config"] = _load(result.pop("config_json", "{}"))
    return result


def select_route(
    owner_id: str,
    model_alias: str,
    *,
    request_id: str,
    sticky_key: str = "",
    project_id: str = "",
    connection_id: str = "",
    lease_ttl: int = 180,
    acquire_lease: bool = True,
) -> dict:
    """Choose one healthy capacity-bounded channel before the first token.

    Selection is deterministic for a request/sticky key, so retries converge.
    The returned credential is an internal-only value and is never projected by
    the public preview endpoint.
    """
    _init()
    owner = str(owner_id or "").strip()[:160]
    alias = str(model_alias or "default").strip()[:100]
    req = str(request_id or "").strip()[:160]
    sticky = str(sticky_key or "").strip()[:160]
    project = str(project_id or "").strip()[:120]
    if not owner or not alias or not req:
        raise ValueError("owner_id、model_alias 和 request_id 不能为空")
    now = time.time()
    ttl = max(30, min(int(lease_ttl or 180), 900))
    with db._conn() as conn:
        conn.execute("UPDATE provider_leases SET state='expired',released_at=? WHERE state='active' AND expires_at<=?", (now, now))
        conn.execute("DELETE FROM sticky_bindings WHERE expires_at<=?", (now,))
        duplicate = conn.execute(
            "SELECT channel_id,id,expires_at FROM provider_leases "
            "WHERE owner_id=? AND request_id=? AND state='active' AND expires_at>?",
            (owner, req, now),
        ).fetchone()
        params: list[object] = [now, owner, alias]
        connection_clause = ""
        if connection_id:
            connection_clause = " AND ch.connection_id=?"
            params.append(connection_id)
        rows = conn.execute(
            "SELECT ch.*,pc.name AS connection_name,pc.kind,pc.base_url,pc.wire_api,"
            "pc.status AS connection_status,COALESCE(cs.state,'closed') AS circuit_state,"
            "COALESCE(cs.open_until,0) AS open_until,"
            "(SELECT COUNT(*) FROM provider_leases pl WHERE pl.owner_id=ch.owner_id "
            "AND pl.channel_id=ch.id AND pl.state='active' AND pl.expires_at>?) AS active_leases "
            "FROM provider_channels ch JOIN provider_connections pc ON pc.id=ch.connection_id "
            "LEFT JOIN provider_circuit_states cs ON cs.owner_id=ch.owner_id AND cs.channel_id=ch.id "
            "WHERE ch.owner_id=? AND ch.model_alias=? AND ch.enabled=1 AND pc.enabled=1"
            + connection_clause,
            tuple(params),
        ).fetchall()
        candidates = [dict(row) for row in rows]
        candidates = [row for row in candidates if not (
            row.get("circuit_state") == "open" and float(row.get("open_until") or 0) > now
        ) and (
            int(row.get("active_leases") or 0) < max(1, int(row.get("max_concurrency") or 1))
            or bool(duplicate and row["id"] == duplicate["channel_id"])
        )]
        if duplicate:
            selected = next((row for row in candidates if row["id"] == duplicate["channel_id"]), None)
            if selected:
                lease_id = str(duplicate["id"])
            else:
                raise RuntimeError("已存在的 Provider 租约所指通道当前不可用")
        else:
            if not candidates:
                raise RuntimeError("没有健康且具备并发容量的 Provider 通道")
            policy = _policy(conn, owner, alias, project)
            healthy = [row for row in candidates if row.get("connection_status") == "healthy"]
            pool = healthy or candidates
            minimum_priority = min(int(row.get("priority") or 100) for row in pool)
            pool = [row for row in pool if int(row.get("priority") or 100) == minimum_priority]
            selected = None
            if sticky and policy.get("strategy") == "sticky_health":
                bound = conn.execute(
                    "SELECT channel_id FROM sticky_bindings WHERE owner_id=? AND sticky_key=? "
                    "AND model_alias=? AND expires_at>?",
                    (owner, sticky, alias, now),
                ).fetchone()
                if bound:
                    selected = next((row for row in pool if row["id"] == bound["channel_id"]), None)
            if selected is None:
                ordered = sorted(pool, key=lambda row: str(row["id"]))
                total = sum(max(1, int(row.get("weight") or 1)) for row in ordered)
                seed = hashlib.sha256(f"{owner}\0{alias}\0{sticky or req}".encode("utf-8")).digest()
                point = int.from_bytes(seed[:8], "big") % total
                selected = ordered[-1]
                for row in ordered:
                    point -= max(1, int(row.get("weight") or 1))
                    if point < 0:
                        selected = row
                        break
            lease_id = uuid.uuid4().hex
            if acquire_lease:
                conn.execute(
                    "INSERT INTO provider_leases(id,owner_id,channel_id,request_id,state,expires_at,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (lease_id, owner, selected["id"], req, "active", now + ttl, now),
                )
            else:
                lease_id = ""
            if sticky:
                conn.execute(
                    "INSERT INTO sticky_bindings(owner_id,sticky_key,model_alias,channel_id,expires_at,updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,sticky_key,model_alias) DO UPDATE SET "
                    "channel_id=excluded.channel_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                    (owner, sticky, alias, selected["id"], now + 86400, now),
                )
        cred = conn.execute(
            "SELECT secret_enc FROM provider_credentials WHERE owner_id=? AND connection_id=? "
            "AND enabled=1 ORDER BY updated_at DESC LIMIT 1",
            (owner, selected["connection_id"]),
        ).fetchone()
        selected["api_key"] = decrypt_secret(str(cred["secret_enc"])) if cred else ""
        policy = _policy(conn, owner, alias, project)
    return {
        "contract": "hashmm.provider-route.v1", "request_id": req,
        "lease_id": lease_id, "expires_at": now + ttl if lease_id else 0,
        "channel": _channel(selected, include_secret=True),
        "policy": policy, "retry_boundary": "before_first_token",
    }


def release_lease(owner_id: str, lease_id: str, *, state: str = "released") -> bool:
    _init()
    safe_state = state if state in {"released", "completed", "failed", "expired"} else "released"
    with db._conn() as conn:
        result = conn.execute(
            "UPDATE provider_leases SET state=?,released_at=? WHERE id=? AND owner_id=? AND state='active'",
            (safe_state, time.time(), lease_id, owner_id),
        )
        return bool(result.rowcount)


def record_attempt(owner_id: str, request_id: str, channel_id: str, *, attempt: int,
                   outcome: str, error_code: str = "", first_token_seen: bool = False,
                   latency_ms: int = 0) -> None:
    """Persist one attempt and update the circuit without allowing mid-stream failover."""
    _init()
    now = time.time()
    ok = outcome == "success"
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO provider_request_attempts(id,owner_id,request_id,channel_id,attempt,outcome,"
            "error_code,first_token_seen,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, owner_id, request_id, channel_id, max(1, int(attempt)),
             str(outcome)[:40], str(error_code)[:80], int(first_token_seen),
             max(0, int(latency_ms)), now),
        )
        prior = conn.execute(
            "SELECT failure_count FROM provider_circuit_states WHERE owner_id=? AND channel_id=?",
            (owner_id, channel_id),
        ).fetchone()
        failures = 0 if ok else int((prior or {"failure_count": 0})["failure_count"] or 0) + 1
        state = "closed" if ok or failures < 3 else "open"
        open_until = 0 if state == "closed" else now + min(300, 30 * (2 ** min(failures - 3, 3)))
        conn.execute(
            "INSERT INTO provider_circuit_states(owner_id,channel_id,state,failure_count,open_until,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,channel_id) DO UPDATE SET "
            "state=excluded.state,failure_count=excluded.failure_count,open_until=excluded.open_until,updated_at=excluded.updated_at",
            (owner_id, channel_id, state, failures, open_until, now),
        )


def create_connection(owner_id: str, payload: dict) -> dict:
    _init()
    kind = str(payload.get("kind") or "openai_compatible").strip().lower()
    wire = str(payload.get("wire_api") or "chat_completions").strip().lower()
    if wire not in ALLOWED_WIRES:
        raise ValueError("不支持的协议")
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("连接名称不能为空")
    endpoint = validate_endpoint(str(payload.get("base_url") or ""), kind)
    now, connection_id = time.time(), uuid.uuid4().hex
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO provider_connections(id,owner_id,name,kind,base_url,wire_api,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (connection_id, owner_id, name, kind, endpoint, wire, _json(payload.get("config")), now, now),
        )
        secret = str(payload.get("api_key") or "").strip()
        if secret:
            conn.execute(
                "INSERT INTO provider_credentials(id,owner_id,connection_id,label,secret_enc,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, owner_id, connection_id, "primary", encrypt_secret(secret), now, now),
            )
        alias = str(payload.get("model_alias") or payload.get("upstream_model") or "default").strip()[:100]
        upstream = str(payload.get("upstream_model") or payload.get("model_name") or "").strip()[:160]
        if upstream:
            conn.execute(
                "INSERT INTO provider_channels(id,owner_id,connection_id,model_alias,upstream_model,priority,weight,max_concurrency,capabilities_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, owner_id, connection_id, alias, upstream,
                 int(payload.get("priority") or 100), int(payload.get("weight") or 100),
                 max(1, min(128, int(payload.get("max_concurrency") or 4))),
                 _json(payload.get("capabilities")), now, now),
            )
    return get_connection(owner_id, connection_id) or {}


def _project_connection(row: dict, *, credential_count: int = 0, channels: list[dict] | None = None) -> dict:
    config = json.loads(row.get("config_json") or "{}")
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "base_url": row["base_url"], "wire_api": row["wire_api"],
        "enabled": bool(row["enabled"]), "status": row["status"],
        "has_credential": credential_count > 0, "credential_count": credential_count,
        "config": config, "channels": channels or [], "updated_at": row["updated_at"],
    }


def get_connection(owner_id: str, connection_id: str, *, include_secret: bool = False) -> dict | None:
    _init()
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM provider_connections WHERE id=? AND owner_id=?", (connection_id, owner_id)).fetchone()
        if not row:
            return None
        creds = conn.execute("SELECT * FROM provider_credentials WHERE connection_id=? AND owner_id=? AND enabled=1", (connection_id, owner_id)).fetchall()
        channels = [_channel(dict(item)) for item in conn.execute("SELECT * FROM provider_channels WHERE connection_id=? AND owner_id=? ORDER BY priority,id", (connection_id, owner_id)).fetchall()]
    projected = _project_connection(dict(row), credential_count=len(creds), channels=channels)
    if include_secret:
        projected["api_key"] = decrypt_secret(str(creds[0]["secret_enc"])) if creds else ""
    return projected


def list_connections(owner_id: str) -> list[dict]:
    _init()
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM provider_connections WHERE owner_id=? ORDER BY updated_at DESC", (owner_id,)).fetchall()
        result = []
        for row in rows:
            count = int(conn.execute("SELECT COUNT(*) FROM provider_credentials WHERE owner_id=? AND connection_id=? AND enabled=1", (owner_id, row["id"])).fetchone()[0])
            channels = [_channel(dict(item)) for item in conn.execute("SELECT * FROM provider_channels WHERE owner_id=? AND connection_id=? ORDER BY priority,id", (owner_id, row["id"])).fetchall()]
            result.append(_project_connection(dict(row), credential_count=count, channels=channels))
        return result


def delete_connection(owner_id: str, connection_id: str) -> bool:
    _init()
    with db._conn() as conn:
        exists = conn.execute("SELECT 1 FROM provider_connections WHERE id=? AND owner_id=?", (connection_id, owner_id)).fetchone()
        if not exists:
            return False
        channel_ids = [str(row[0]) for row in conn.execute(
            "SELECT id FROM provider_channels WHERE connection_id=? AND owner_id=?",
            (connection_id, owner_id),
        ).fetchall()]
        for channel_id in channel_ids:
            for table in (
                "provider_health_samples", "provider_circuit_states", "provider_leases",
                "provider_request_attempts", "provider_usage_events",
            ):
                conn.execute(f"DELETE FROM {table} WHERE channel_id=? AND owner_id=?", (channel_id, owner_id))
            conn.execute("DELETE FROM sticky_bindings WHERE channel_id=? AND owner_id=?", (channel_id, owner_id))
        for table in ("provider_credentials", "provider_channels"):
            conn.execute(f"DELETE FROM {table} WHERE connection_id=? AND owner_id=?", (connection_id, owner_id))
        conn.execute("DELETE FROM provider_connections WHERE id=? AND owner_id=?", (connection_id, owner_id))
    return True


def record_probe(owner_id: str, connection_id: str, *, ok: bool, latency_ms: int, error_code: str = "") -> None:
    connection = get_connection(owner_id, connection_id)
    if not connection:
        return
    now = time.time()
    with db._conn() as conn:
        conn.execute("UPDATE provider_connections SET status=?,updated_at=? WHERE id=? AND owner_id=?", ("healthy" if ok else "degraded", now, connection_id, owner_id))
        for channel in connection["channels"]:
            conn.execute("INSERT INTO provider_health_samples(id,owner_id,channel_id,ok,latency_ms,error_code,created_at) VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, owner_id, channel["id"], int(ok), int(latency_ms), str(error_code)[:80], now))
            state = "closed" if ok else "open"
            failures = 0 if ok else 1
            open_until = 0 if ok else now + 30
            conn.execute("INSERT INTO provider_circuit_states(owner_id,channel_id,state,failure_count,open_until,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,channel_id) DO UPDATE SET state=excluded.state,failure_count=CASE WHEN excluded.state='closed' THEN 0 ELSE provider_circuit_states.failure_count+1 END,open_until=excluded.open_until,updated_at=excluded.updated_at", (owner_id, channel["id"], state, failures, open_until, now))


def connection_as_model(owner_id: str, connection_id: str) -> dict | None:
    item = get_connection(owner_id, connection_id, include_secret=True)
    if not item or not item.get("enabled") or not item.get("channels"):
        return None
    channel = next((c for c in item["channels"] if int(c.get("enabled") or 0)), None)
    if not channel:
        return None
    provider = "anthropic" if item["wire_api"] == "anthropic_messages" else "custom"
    return {
        "name": item["name"], "provider": provider, "base_url": item["base_url"],
        "api_key": item.get("api_key") or "", "model_name": channel["upstream_model"],
        "wire_api": item["wire_api"], "config": {
            "wire_api": item["wire_api"], "provider_connection_id": connection_id,
            "provider_owner_id": owner_id, "model_alias": channel["model_alias"],
        },
        "temperature": 0.1, "max_tokens": 16384,
    }


def overview(owner_id: str) -> dict:
    items = list_connections(owner_id)
    channels = sum(len(item["channels"]) for item in items)
    return {
        "contract": "hashmm.provider-fabric.v2", "connections": items,
        "summary": {"connections": len(items), "channels": channels,
                    "healthy": sum(item["status"] == "healthy" for item in items),
                    "sub2api": sum(item["kind"] == "sub2api" for item in items)},
        "security": {"credentials_encrypted": True, "upstream_auth_isolated": True,
                     "retry_boundary": "before_first_token", "prompt_logging": False},
    }
