"""Multi-tenancy — minimal skeleton (opt-in, behaviour-preserving).

Design (per the chosen "scaffold first" approach, sized for ~500 tenants):

  - Every user belongs to exactly one tenant via ``users.tenant_id``. The default
    tenant id is ``"default"``, and ALL existing users are assigned to it by the
    migration, so a single-tenant deployment behaves EXACTLY as before — tenancy
    is invisible until you actually create tenants and assign users.

  - A ``tenants`` table holds metadata + quotas (max_users, storage, monthly
    token budget). At 500 tenants this is a tiny table; lookups are by primary
    key, so no scaling concern.

  - This phase is *logical* isolation at the data layer: tenant resolution +
    quota checks + a helper to scope queries by the caller's tenant. Index/KG
    namespace partitioning is intentionally deferred (scaffold first) — the
    tenant_id is threaded through now so that later partitioning is a localized
    change, not a schema rewrite.

Nothing here changes behaviour unless tenants are created and
``HASHMM_MULTI_TENANT=1`` is set; resolution always falls back to "default".
"""
from __future__ import annotations

import os
import time

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.tenancy")

DEFAULT_TENANT = "default"


def multi_tenant_enabled() -> bool:
    """Master switch. Off → every user resolves to the default tenant and quota
    checks are no-ops, i.e. current single-tenant behaviour."""
    return os.environ.get("HASHMM_MULTI_TENANT", "0") == "1"


# Default quotas for the default tenant / unset fields. Sized so a fresh
# single-tenant install is effectively unlimited.
_DEFAULT_QUOTA = {
    "max_users": 100000,
    "max_storage_mb": 1_000_000,
    "monthly_token_quota": 10_000_000_000,
}


def _ensure_schema(db) -> None:
    """Create the tenants table + users.tenant_id column if missing. Idempotent;
    safe to call repeatedly (used by both migration and lazy paths)."""
    try:
        with db._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    id          TEXT PRIMARY KEY,
                    name        TEXT DEFAULT '',
                    max_users   INTEGER DEFAULT 100000,
                    max_storage_mb INTEGER DEFAULT 1000000,
                    monthly_token_quota INTEGER DEFAULT 10000000000,
                    tokens_used_this_month INTEGER DEFAULT 0,
                    created_at  REAL DEFAULT (strftime('%s','now')),
                    active      INTEGER DEFAULT 1
                )
            """)
            # users.tenant_id (ALTER is safe — wrapped, runs once via migration).
            try:
                c.execute("ALTER TABLE users ADD COLUMN tenant_id TEXT DEFAULT 'default'")
            except Exception:
                pass  # nosem: observability-fallback  (column already exists)
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
            # Seed the default tenant so FK-style lookups always resolve.
            c.execute(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_TENANT, "Default Tenant", time.time()),
            )
    except Exception as _e:
        log_suppressed(logger, _e)


def resolve_tenant(db, user_id: str | None) -> str:
    """Return the tenant id for a user. Always returns a value (default on any
    miss), so callers never have to special-case absence."""
    if not user_id or not multi_tenant_enabled():
        return DEFAULT_TENANT
    try:
        with db._conn() as c:
            row = c.execute("SELECT tenant_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["tenant_id"]:
            return row["tenant_id"]
    except Exception as _e:
        log_suppressed(logger, _e)
    return DEFAULT_TENANT


def get_tenant(db, tenant_id: str) -> dict:
    """Tenant metadata + quotas; default-filled when the row is missing."""
    try:
        with db._conn() as c:
            row = c.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        if row:
            return dict(row)
    except Exception as _e:
        log_suppressed(logger, _e)
    return {"id": tenant_id, **_DEFAULT_QUOTA, "tokens_used_this_month": 0, "active": 1}


def create_tenant(db, tenant_id: str, name: str = "", **quotas) -> dict:
    """Create (or no-op if exists) a tenant. quotas overrides defaults."""
    _ensure_schema(db)
    q = {**_DEFAULT_QUOTA, **{k: v for k, v in quotas.items() if k in _DEFAULT_QUOTA}}
    try:
        with db._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO tenants (id, name, max_users, max_storage_mb, "
                "monthly_token_quota, created_at) VALUES (?,?,?,?,?,?)",
                (tenant_id, name, q["max_users"], q["max_storage_mb"],
                 q["monthly_token_quota"], time.time()),
            )
    except Exception as _e:
        log_suppressed(logger, _e)
    return get_tenant(db, tenant_id)


def assign_user(db, user_id: str, tenant_id: str) -> bool:
    """Move a user into a tenant. Returns success."""
    _ensure_schema(db)
    try:
        with db._conn() as c:
            c.execute("UPDATE users SET tenant_id = ? WHERE id = ?", (tenant_id, user_id))
        return True
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


def check_quota(db, tenant_id: str, *, add_tokens: int = 0) -> dict:
    """Check (and optionally account) token usage against the tenant's monthly
    quota. Returns {allowed, used, quota, remaining}. No-op-allow when tenancy is
    disabled, so single-tenant deployments are never gated."""
    if not multi_tenant_enabled():
        return {"allowed": True, "used": 0, "quota": _DEFAULT_QUOTA["monthly_token_quota"],
                "remaining": _DEFAULT_QUOTA["monthly_token_quota"]}
    t = get_tenant(db, tenant_id)
    used = int(t.get("tokens_used_this_month", 0))
    quota = int(t.get("monthly_token_quota", _DEFAULT_QUOTA["monthly_token_quota"]))
    allowed = (used + add_tokens) <= quota
    if add_tokens and allowed:
        try:
            with db._conn() as c:
                c.execute("UPDATE tenants SET tokens_used_this_month = tokens_used_this_month + ? "
                          "WHERE id = ?", (add_tokens, tenant_id))
        except Exception as _e:
            log_suppressed(logger, _e)
    return {"allowed": allowed, "used": used, "quota": quota,
            "remaining": max(0, quota - used)}


def tenant_overview(db) -> dict:
    """Admin summary: tenant count + per-tenant user count & token usage.
    Sized for ~500 tenants (single small query)."""
    try:
        with db._conn() as c:
            tenants = [dict(r) for r in c.execute("SELECT * FROM tenants").fetchall()]
            counts = {r["tenant_id"]: r["n"] for r in c.execute(
                "SELECT tenant_id, COUNT(*) AS n FROM users GROUP BY tenant_id").fetchall()}
    except Exception as _e:
        log_suppressed(logger, _e)
        return {"enabled": multi_tenant_enabled(), "n_tenants": 0, "tenants": []}
    for t in tenants:
        t["user_count"] = counts.get(t["id"], 0)
    return {"enabled": multi_tenant_enabled(), "n_tenants": len(tenants), "tenants": tenants}
