"""Durable, owner-scoped access control for HashMM's public API.

The raw API secret is returned exactly once by :func:`create_key`.  Only a
SHA-256 digest of a 256-bit random value is persisted.  This module contains
no provider credentials and deliberately has no dependency on Redis so the
single-node desktop/server product keeps its existing dependency set.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from hashmm.api import database as db


VALID_SCOPES = frozenset({
    "*", "models:read", "responses:read", "responses:write",
    "threads:read", "threads:write", "runs:read", "runs:write", "usage:read",
})
DEFAULT_SCOPES = ("models:read", "responses:read", "responses:write")


class AccessError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400,
                 *, retry_after: int = 0, limit: int | None = None,
                 remaining: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = max(0, int(retry_after or 0))
        self.limit = limit
        self.remaining = remaining


@dataclass(frozen=True)
class AccessContext:
    kind: str
    owner_id: str
    key_id: str = ""
    key_prefix: str = ""
    scopes: tuple[str, ...] = ()
    allowed_models: tuple[str, ...] = ()
    allowed_projects: tuple[str, ...] = ()
    quota_limit: float | None = None
    quota_used: float = 0.0
    rpm_limit: int | None = None
    concurrent_limit: int | None = None

    @property
    def managed(self) -> bool:
        return self.kind == "managed"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_access_keys (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    secret_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    scopes_json TEXT NOT NULL DEFAULT '[]',
    allowed_models_json TEXT NOT NULL DEFAULT '[]',
    allowed_projects_json TEXT NOT NULL DEFAULT '[]',
    ip_allowlist_json TEXT NOT NULL DEFAULT '[]',
    ip_denylist_json TEXT NOT NULL DEFAULT '[]',
    quota_limit REAL,
    quota_used REAL NOT NULL DEFAULT 0,
    rpm_limit INTEGER,
    concurrent_limit INTEGER,
    expires_at REAL,
    last_used_at REAL NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    revoked_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_access_keys_owner
    ON api_access_keys(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_access_keys_prefix
    ON api_access_keys(key_prefix);

CREATE TABLE IF NOT EXISTS api_key_rate_windows (
    key_id TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(key_id, window_start),
    FOREIGN KEY(key_id) REFERENCES api_access_keys(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS api_key_leases (
    id TEXT PRIMARY KEY,
    key_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    released_at REAL NOT NULL DEFAULT 0,
    UNIQUE(key_id, request_id),
    FOREIGN KEY(key_id) REFERENCES api_access_keys(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_key_leases_active
    ON api_key_leases(key_id, state, expires_at);

CREATE TABLE IF NOT EXISTS api_key_quota_reservations (
    id TEXT PRIMARY KEY,
    key_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    amount REAL NOT NULL,
    settled_amount REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'reserved',
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(key_id, idempotency_key),
    FOREIGN KEY(key_id) REFERENCES api_access_keys(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_key_quota_active
    ON api_key_quota_reservations(key_id, state, expires_at);
"""


def _loads(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def ensure_schema() -> None:
    """Create only additive V820 tables/columns."""
    with db._conn() as conn:
        conn.executescript(_SCHEMA)
        # ``usage_events`` belongs to platform_protocol and may not exist yet.
        # Creating its schema first also makes this migration safe on V810 DBs.
    from hashmm.api import platform_protocol
    platform_protocol.ensure_schema()
    with db._conn() as conn:
        try:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()}
            if "access_key_id" not in columns:
                conn.execute("ALTER TABLE usage_events ADD COLUMN access_key_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_events_access_key_time "
                "ON usage_events(access_key_id, created_at DESC)"
            )
        except Exception:
            # PostgreSQL adapters do not expose PRAGMA.  Keep an equivalent
            # additive migration without weakening failure behavior elsewhere.
            try:
                conn.execute("ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS access_key_id TEXT")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_usage_events_access_key_time "
                    "ON usage_events(access_key_id, created_at DESC)"
                )
            except Exception:
                raise


def _string_list(value: Any, *, field: str, max_items: int = 100,
                 max_length: int = 160) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise AccessError("invalid_request", f"{field} must be an array", 400)
    result: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if not item:
            continue
        if len(item) > max_length:
            raise AccessError("invalid_request", f"{field} contains an overlong value", 400)
        if item not in result:
            result.append(item)
        if len(result) > max_items:
            raise AccessError("invalid_request", f"{field} has too many values", 400)
    return tuple(result)


def _scopes(value: Any) -> tuple[str, ...]:
    result = _string_list(DEFAULT_SCOPES if value is None else value, field="scopes", max_items=30)
    invalid = sorted(set(result) - VALID_SCOPES)
    if invalid:
        raise AccessError("invalid_scope", f"unsupported scope: {invalid[0]}", 400)
    if not result:
        raise AccessError("invalid_scope", "at least one scope is required", 400)
    return result


def _networks(value: Any, *, field: str) -> tuple[str, ...]:
    result = _string_list(value, field=field, max_items=100, max_length=80)
    canonical: list[str] = []
    for raw in result:
        try:
            item = str(ipaddress.ip_network(raw, strict=False))
        except ValueError as exc:
            raise AccessError("invalid_ip_rule", f"invalid {field} entry", 400) from exc
        if item not in canonical:
            canonical.append(item)
    return tuple(canonical)


def _optional_limit(value: Any, *, field: str, integer: bool = False,
                    allow_zero: bool = False) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise AccessError("invalid_request", f"{field} must be numeric", 400) from exc
    if number < 0 or (number == 0 and not allow_zero):
        raise AccessError("invalid_request", f"{field} must be positive", 400)
    return number


def _public_key(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "object": "api_key", "name": str(row["name"]),
        "prefix": str(row["key_prefix"]), "status": str(row["status"]),
        "scopes": _loads(row["scopes_json"], []),
        "allowed_models": _loads(row["allowed_models_json"], []),
        "allowed_projects": _loads(row["allowed_projects_json"], []),
        "ip_allowlist": _loads(row["ip_allowlist_json"], []),
        "ip_denylist": _loads(row["ip_denylist_json"], []),
        "quota_limit": row["quota_limit"], "quota_used": float(row["quota_used"] or 0),
        "rpm_limit": row["rpm_limit"], "concurrent_limit": row["concurrent_limit"],
        "expires_at": row["expires_at"], "last_used_at": float(row["last_used_at"] or 0),
        "revision": int(row["revision"] or 1), "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]), "revoked_at": float(row["revoked_at"] or 0),
    }


def _row_context(row: Any) -> AccessContext:
    return AccessContext(
        kind="managed", owner_id=str(row["owner_id"]), key_id=str(row["id"]),
        key_prefix=str(row["key_prefix"]), scopes=tuple(_loads(row["scopes_json"], [])),
        allowed_models=tuple(_loads(row["allowed_models_json"], [])),
        allowed_projects=tuple(_loads(row["allowed_projects_json"], [])),
        quota_limit=float(row["quota_limit"]) if row["quota_limit"] is not None else None,
        quota_used=float(row["quota_used"] or 0),
        rpm_limit=int(row["rpm_limit"]) if row["rpm_limit"] is not None else None,
        concurrent_limit=int(row["concurrent_limit"]) if row["concurrent_limit"] is not None else None,
    )


def create_key(owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    owner = str(owner_id or "").strip()
    name = str(payload.get("name") or "").strip()
    if not owner:
        raise AccessError("invalid_owner", "owner is required", 400)
    if not 1 <= len(name) <= 80:
        raise AccessError("invalid_request", "name must contain 1 to 80 characters", 400)
    scopes = _scopes(payload.get("scopes"))
    models = _string_list(payload.get("allowed_models"), field="allowed_models")
    projects = _string_list(payload.get("allowed_projects"), field="allowed_projects")
    allow = _networks(payload.get("ip_allowlist"), field="ip_allowlist")
    deny = _networks(payload.get("ip_denylist"), field="ip_denylist")
    quota = _optional_limit(payload.get("quota_limit"), field="quota_limit", allow_zero=True)
    rpm = _optional_limit(payload.get("rpm_limit"), field="rpm_limit", integer=True)
    concurrent = _optional_limit(payload.get("concurrent_limit"), field="concurrent_limit", integer=True)
    expires = _optional_limit(payload.get("expires_at"), field="expires_at", allow_zero=False)
    if expires is not None and float(expires) <= time.time():
        raise AccessError("invalid_request", "expires_at must be in the future", 400)

    secret = "hmm_v2_" + secrets.token_urlsafe(32)
    now = time.time()
    key_id = "key_" + uuid.uuid4().hex
    prefix = secret[:15]
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO api_access_keys("
            "id,owner_id,name,key_prefix,secret_hash,status,scopes_json,allowed_models_json,"
            "allowed_projects_json,ip_allowlist_json,ip_denylist_json,quota_limit,quota_used,"
            "rpm_limit,concurrent_limit,expires_at,last_used_at,revision,created_at,updated_at,revoked_at"
            ") VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,0,?,?,?,0,1,?,?,0)",
            (key_id, owner, name, prefix, _hash_secret(secret), _dumps(scopes), _dumps(models),
             _dumps(projects), _dumps(allow), _dumps(deny), quota, rpm, concurrent, expires, now, now),
        )
        row = conn.execute(
            "SELECT * FROM api_access_keys WHERE id=? AND owner_id=?", (key_id, owner)
        ).fetchone()
    result = _public_key(row)
    result["secret"] = secret
    return result


def list_keys(owner_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM api_access_keys WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)
        ).fetchall()
    return [_public_key(row) for row in rows]


def get_key(owner_id: str, key_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_access_keys WHERE id=? AND owner_id=?", (key_id, owner_id)
        ).fetchone()
    return _public_key(row) if row is not None else None


def update_key(owner_id: str, key_id: str, payload: dict[str, Any],
               *, expected_revision: int) -> dict[str, Any] | None:
    ensure_schema()
    if expected_revision < 1:
        raise AccessError("revision_required", "revision is required", 400)
    assignments: list[str] = []
    values: list[Any] = []
    validators = {
        "scopes": lambda v: _dumps(_scopes(v)),
        "allowed_models": lambda v: _dumps(_string_list(v, field="allowed_models")),
        "allowed_projects": lambda v: _dumps(_string_list(v, field="allowed_projects")),
        "ip_allowlist": lambda v: _dumps(_networks(v, field="ip_allowlist")),
        "ip_denylist": lambda v: _dumps(_networks(v, field="ip_denylist")),
        "quota_limit": lambda v: _optional_limit(v, field="quota_limit", allow_zero=True),
        "rpm_limit": lambda v: _optional_limit(v, field="rpm_limit", integer=True),
        "concurrent_limit": lambda v: _optional_limit(v, field="concurrent_limit", integer=True),
        "expires_at": lambda v: _optional_limit(v, field="expires_at", allow_zero=False),
    }
    columns = {
        "scopes": "scopes_json", "allowed_models": "allowed_models_json",
        "allowed_projects": "allowed_projects_json", "ip_allowlist": "ip_allowlist_json",
        "ip_denylist": "ip_denylist_json", "quota_limit": "quota_limit",
        "rpm_limit": "rpm_limit", "concurrent_limit": "concurrent_limit",
        "expires_at": "expires_at",
    }
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not 1 <= len(name) <= 80:
            raise AccessError("invalid_request", "name must contain 1 to 80 characters", 400)
        assignments.append("name=?")
        values.append(name)
    if "status" in payload:
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"active", "disabled"}:
            raise AccessError("invalid_request", "status must be active or disabled", 400)
        assignments.append("status=?")
        values.append(status)
    for field, validator in validators.items():
        if field in payload:
            value = validator(payload[field])
            if field == "expires_at" and value is not None and float(value) <= time.time():
                raise AccessError("invalid_request", "expires_at must be in the future", 400)
            assignments.append(f"{columns[field]}=?")
            values.append(value)
    if not assignments:
        raise AccessError("invalid_request", "no editable fields supplied", 400)
    now = time.time()
    assignments.extend(["revision=revision+1", "updated_at=?"])
    values.extend([now, key_id, owner_id, expected_revision])
    with db._conn() as conn:
        result = conn.execute(
            f"UPDATE api_access_keys SET {','.join(assignments)} "
            "WHERE id=? AND owner_id=? AND revision=? AND status!='revoked'", tuple(values),
        )
        if not result.rowcount:
            exists = conn.execute(
                "SELECT revision FROM api_access_keys WHERE id=? AND owner_id=?", (key_id, owner_id)
            ).fetchone()
            if exists is not None and int(exists["revision"] or 0) != expected_revision:
                raise AccessError("revision_conflict", "API key changed; refresh and retry", 409)
            return None
        row = conn.execute("SELECT * FROM api_access_keys WHERE id=?", (key_id,)).fetchone()
    return _public_key(row)


def revoke_key(owner_id: str, key_id: str) -> dict[str, Any] | None:
    ensure_schema()
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_access_keys WHERE id=? AND owner_id=?", (key_id, owner_id)
        ).fetchone()
        if row is None:
            return None
        if str(row["status"]) != "revoked":
            conn.execute(
                "UPDATE api_access_keys SET status='revoked',revoked_at=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND owner_id=?", (now, now, key_id, owner_id),
            )
            conn.execute(
                "UPDATE api_key_leases SET state='released',released_at=? "
                "WHERE key_id=? AND state='active'", (now, key_id),
            )
        row = conn.execute("SELECT * FROM api_access_keys WHERE id=?", (key_id,)).fetchone()
    return _public_key(row)


def _ip_allowed(row: Any, source_ip: str) -> bool:
    allow = tuple(_loads(row["ip_allowlist_json"], []))
    deny = tuple(_loads(row["ip_denylist_json"], []))
    if not allow and not deny:
        return True
    try:
        address = ipaddress.ip_address(str(source_ip or "").split("%", 1)[0])
    except ValueError:
        return False
    if any(address in ipaddress.ip_network(item, strict=False) for item in deny):
        return False
    return not allow or any(address in ipaddress.ip_network(item, strict=False) for item in allow)


def authenticate(secret: str, *, source_ip: str = "") -> AccessContext | None:
    ensure_schema()
    supplied = str(secret or "")
    if not supplied:
        return None
    digest = _hash_secret(supplied)
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_access_keys WHERE secret_hash=?", (digest,)
        ).fetchone()
        if row is None:
            return None
        if str(row["status"]) != "active" or (
            row["expires_at"] is not None and float(row["expires_at"]) <= now
        ):
            raise AccessError("invalid_api_key", "invalid API key", 401)
        if not _ip_allowed(row, source_ip):
            raise AccessError("resource_not_allowed", "request source is not allowed", 403)
        conn.execute("UPDATE api_access_keys SET last_used_at=? WHERE id=?", (now, row["id"]))
        return _row_context(row)


def require_scope(context: AccessContext, scope: str) -> None:
    if not context.managed or not scope:
        return
    if "*" not in context.scopes and scope not in context.scopes:
        raise AccessError("insufficient_scope", "API key lacks the required scope", 403)


def require_resources(context: AccessContext, *, model: str = "", project_id: str = "") -> None:
    if not context.managed:
        return
    if model and context.allowed_models and model not in context.allowed_models:
        raise AccessError("resource_not_allowed", "model is not allowed for this API key", 403)
    if project_id and context.allowed_projects and project_id not in context.allowed_projects:
        raise AccessError("resource_not_allowed", "project is not allowed for this API key", 403)


def consume_rate_limit(context: AccessContext, *, now: float | None = None) -> dict[str, int] | None:
    if not context.managed or context.rpm_limit is None:
        return None
    ensure_schema()
    current = float(now if now is not None else time.time())
    window = int(current // 60) * 60
    limit = max(1, int(context.rpm_limit))
    with db._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO api_key_rate_windows(key_id,window_start,request_count,updated_at) "
            "VALUES(?,?,0,?)", (context.key_id, window, current),
        )
        changed = conn.execute(
            "UPDATE api_key_rate_windows SET request_count=request_count+1,updated_at=? "
            "WHERE key_id=? AND window_start=? AND request_count<?",
            (current, context.key_id, window, limit),
        ).rowcount
        row = conn.execute(
            "SELECT request_count FROM api_key_rate_windows WHERE key_id=? AND window_start=?",
            (context.key_id, window),
        ).fetchone()
        conn.execute(
            "DELETE FROM api_key_rate_windows WHERE key_id=? AND window_start<?",
            (context.key_id, window - 3600),
        )
    count = int(row["request_count"] or 0)
    remaining = max(0, limit - count)
    if not changed:
        raise AccessError(
            "rate_limit_exceeded", "API key request rate exceeded", 429,
            retry_after=max(1, int(window + 60 - current)), limit=limit, remaining=0,
        )
    return {"limit": limit, "remaining": remaining, "reset": window + 60}


def acquire_lease(context: AccessContext, *, request_id: str,
                  ttl_seconds: int = 900) -> dict[str, Any] | None:
    if not context.managed or context.concurrent_limit is None:
        return None
    ensure_schema()
    now = time.time()
    limit = max(1, int(context.concurrent_limit))
    request_key = str(request_id or "")[:200]
    if not request_key:
        raise AccessError("invalid_request", "request id is required for concurrency control", 400)
    with db._conn() as conn:
        # Establish the write lock before observing the active count.
        conn.execute("UPDATE api_access_keys SET updated_at=updated_at WHERE id=?", (context.key_id,))
        conn.execute(
            "UPDATE api_key_leases SET state='expired' WHERE key_id=? AND state='active' AND expires_at<=?",
            (context.key_id, now),
        )
        existing = conn.execute(
            "SELECT * FROM api_key_leases WHERE key_id=? AND request_id=?",
            (context.key_id, request_key),
        ).fetchone()
        if existing is not None and str(existing["state"]) == "active" and float(existing["expires_at"]) > now:
            return dict(existing)
        active = int(conn.execute(
            "SELECT COUNT(*) FROM api_key_leases WHERE key_id=? AND state='active' AND expires_at>?",
            (context.key_id, now),
        ).fetchone()[0])
        if active >= limit:
            raise AccessError(
                "concurrency_limit_exceeded", "API key concurrency limit exceeded", 429,
                retry_after=1, limit=limit, remaining=0,
            )
        lease_id = "lease_" + uuid.uuid4().hex
        expires = now + max(30, int(ttl_seconds))
        if existing is None:
            conn.execute(
                "INSERT INTO api_key_leases(id,key_id,owner_id,request_id,state,expires_at,created_at,released_at) "
                "VALUES(?,?,?,?,'active',?,?,0)",
                (lease_id, context.key_id, context.owner_id, request_key, expires, now),
            )
        else:
            lease_id = str(existing["id"])
            conn.execute(
                "UPDATE api_key_leases SET state='active',expires_at=?,created_at=?,released_at=0 WHERE id=?",
                (expires, now, lease_id),
            )
        return dict(conn.execute("SELECT * FROM api_key_leases WHERE id=?", (lease_id,)).fetchone())


def release_lease(context: AccessContext, lease_id: str) -> bool:
    if not context.managed or not lease_id:
        return False
    ensure_schema()
    with db._conn() as conn:
        return bool(conn.execute(
            "UPDATE api_key_leases SET state='released',released_at=? "
            "WHERE id=? AND key_id=? AND owner_id=? AND state='active'",
            (time.time(), lease_id, context.key_id, context.owner_id),
        ).rowcount)


def reserve_quota(context: AccessContext, *, amount: float, idempotency_key: str,
                  ttl_seconds: int = 900) -> dict[str, Any] | None:
    if not context.managed or context.quota_limit is None:
        return None
    ensure_schema()
    requested = max(0.0, float(amount or 0))
    now = time.time()
    idem = str(idempotency_key or "")[:240]
    if not idem:
        raise AccessError("invalid_request", "idempotency key is required for quota reservation", 400)
    with db._conn() as conn:
        conn.execute("UPDATE api_access_keys SET updated_at=updated_at WHERE id=?", (context.key_id,))
        existing = conn.execute(
            "SELECT * FROM api_key_quota_reservations WHERE key_id=? AND idempotency_key=?",
            (context.key_id, idem),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        conn.execute(
            "UPDATE api_key_quota_reservations SET state='expired',updated_at=? "
            "WHERE key_id=? AND state='reserved' AND expires_at<=?", (now, context.key_id, now),
        )
        key = conn.execute(
            "SELECT quota_limit,quota_used,status FROM api_access_keys WHERE id=?", (context.key_id,)
        ).fetchone()
        if key is None or str(key["status"]) != "active":
            raise AccessError("invalid_api_key", "invalid API key", 401)
        reserved = float(conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM api_key_quota_reservations "
            "WHERE key_id=? AND state='reserved' AND expires_at>?", (context.key_id, now),
        ).fetchone()[0] or 0)
        used = float(key["quota_used"] or 0)
        limit = float(key["quota_limit"] or 0)
        if used + reserved + requested > limit + 1e-12:
            raise AccessError("quota_exceeded", "API key quota exceeded", 429)
        reservation_id = "keyreserve_" + uuid.uuid4().hex
        conn.execute(
            "INSERT INTO api_key_quota_reservations("
            "id,key_id,owner_id,idempotency_key,amount,settled_amount,state,expires_at,created_at,updated_at"
            ") VALUES(?,?,?,?,?,0,'reserved',?,?,?)",
            (reservation_id, context.key_id, context.owner_id, idem, requested,
             now + max(30, int(ttl_seconds)), now, now),
        )
        return dict(conn.execute(
            "SELECT * FROM api_key_quota_reservations WHERE id=?", (reservation_id,)
        ).fetchone())


def settle_quota(context: AccessContext, reservation_id: str, *, actual_amount: float) -> bool:
    if not context.managed or not reservation_id:
        return False
    ensure_schema()
    actual = max(0.0, float(actual_amount or 0))
    now = time.time()
    with db._conn() as conn:
        conn.execute("UPDATE api_access_keys SET updated_at=updated_at WHERE id=?", (context.key_id,))
        row = conn.execute(
            "SELECT * FROM api_key_quota_reservations WHERE id=? AND key_id=? AND owner_id=?",
            (reservation_id, context.key_id, context.owner_id),
        ).fetchone()
        if row is None:
            return False
        if str(row["state"]) == "settled":
            return True
        if str(row["state"]) != "reserved":
            return False
        conn.execute(
            "UPDATE api_access_keys SET quota_used=quota_used+?,updated_at=? WHERE id=?",
            (actual, now, context.key_id),
        )
        conn.execute(
            "UPDATE api_key_quota_reservations SET state='settled',settled_amount=?,updated_at=? WHERE id=?",
            (actual, now, reservation_id),
        )
        return True


def release_quota(context: AccessContext, reservation_id: str) -> bool:
    if not context.managed or not reservation_id:
        return False
    ensure_schema()
    with db._conn() as conn:
        return bool(conn.execute(
            "UPDATE api_key_quota_reservations SET state='released',updated_at=? "
            "WHERE id=? AND key_id=? AND owner_id=? AND state='reserved'",
            (time.time(), reservation_id, context.key_id, context.owner_id),
        ).rowcount)


def usage_for_key(owner_id: str, key_id: str, *, days: int = 30) -> dict[str, Any] | None:
    ensure_schema()
    safe_days = max(1, min(int(days or 30), 366))
    since = time.time() - safe_days * 86400
    with db._conn() as conn:
        key = conn.execute(
            "SELECT * FROM api_access_keys WHERE id=? AND owner_id=?", (key_id, owner_id)
        ).fetchone()
        if key is None:
            return None
        totals = conn.execute(
            "SELECT COUNT(*) AS events,COALESCE(SUM(quantity),0) AS quantity,"
            "COALESCE(SUM(estimated_cost),0) AS estimated_cost,"
            "COALESCE(SUM(provider_reported_cost),0) AS provider_reported_cost "
            "FROM usage_events WHERE owner_id=? AND access_key_id=? AND created_at>=?",
            (owner_id, key_id, since),
        ).fetchone()
        groups = conn.execute(
            "SELECT model,service,unit,COUNT(*) AS events,COALESCE(SUM(quantity),0) AS quantity,"
            "COALESCE(SUM(estimated_cost),0) AS estimated_cost "
            "FROM usage_events WHERE owner_id=? AND access_key_id=? AND created_at>=? "
            "GROUP BY model,service,unit ORDER BY estimated_cost DESC",
            (owner_id, key_id, since),
        ).fetchall()
    return {
        "object": "api_key_usage", "key": _public_key(key), "days": safe_days,
        "totals": dict(totals), "data": [dict(row) for row in groups],
    }


def access_key_breakdown(owner_id: str, *, days: int = 30) -> list[dict[str, Any]]:
    ensure_schema()
    since = time.time() - max(1, min(int(days or 30), 366)) * 86400
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT e.access_key_id,k.key_prefix,k.name,COUNT(*) AS events,"
            "COALESCE(SUM(e.quantity),0) AS quantity,COALESCE(SUM(e.estimated_cost),0) AS estimated_cost "
            "FROM usage_events e LEFT JOIN api_access_keys k ON k.id=e.access_key_id "
            "WHERE e.owner_id=? AND e.created_at>=? GROUP BY e.access_key_id,k.key_prefix,k.name "
            "ORDER BY estimated_cost DESC", (owner_id, since),
        ).fetchall()
    return [dict(row) for row in rows]

