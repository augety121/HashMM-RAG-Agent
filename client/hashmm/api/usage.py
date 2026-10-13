"""Usage & cost tracking (V15 Phase 10).

Records token usage per request and computes cost, so an enterprise can answer:
  - How much are we spending on the LLM?
  - Which users / models consume the most?
  - What's the daily trend?

Cost is computed from a configurable per-model price table (per 1M tokens),
matching how DeepSeek/OpenAI bill. Prices are editable via env or the table.

Recording is best-effort and never blocks the response.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.usage")

# Default prices in CNY per 1M tokens (input, output). Override via
# HASHMM_MODEL_PRICES='{"deepseek-v4-pro":{"in":2.0,"out":8.0}}'
_DEFAULT_PRICES = {
    "deepseek-v4-pro": {"in": 2.0, "out": 8.0},
    "deepseek-chat": {"in": 1.0, "out": 2.0},
    "default": {"in": 2.0, "out": 8.0},
}


def _prices() -> dict:
    raw = os.environ.get("HASHMM_MODEL_PRICES", "")
    if raw:
        try:
            return {**_DEFAULT_PRICES, **json.loads(raw)}
        except Exception as _e:
            log_suppressed(logger, _e)
    return _DEFAULT_PRICES


def _price_for(model: str) -> dict:
    p = _prices()
    return p.get(model, p.get("default", {"in": 2.0, "out": 8.0}))


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost in CNY for a single call."""
    pr = _price_for(model)
    cost = (tokens_in / 1_000_000) * pr["in"] + (tokens_out / 1_000_000) * pr["out"]
    return round(cost, 6)


def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id          TEXT PRIMARY KEY,
                user_id     TEXT,
                username    TEXT DEFAULT '',
                model       TEXT DEFAULT '',
                tokens_in   BIGINT DEFAULT 0,
                tokens_out  BIGINT DEFAULT 0,
                cost        DOUBLE PRECISION DEFAULT 0,
                task_type   TEXT DEFAULT '',
                ts          DOUBLE PRECISION DEFAULT (strftime('%s','now'))
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts DESC)")


def record(user_id: str, username: str, model: str,
           tokens_in: int, tokens_out: int, task_type: str = "") -> None:
    """Record one usage event (best-effort, never raises)."""
    try:
        _ensure_table()
        cost = compute_cost(model, tokens_in, tokens_out)
        from hashmm.api import database as db
        with db._conn() as c:
            c.execute(
                """INSERT INTO usage_log
                   (id,user_id,username,model,tokens_in,tokens_out,cost,task_type)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex[:12], user_id, username, model,
                 int(tokens_in), int(tokens_out), cost, task_type),
            )
    except Exception as e:
        logger.debug(f"usage record skipped: {e}")


def summary(days: int = 30) -> dict:
    """Aggregate usage over the last N days: totals + by-user + by-model + daily."""
    _ensure_table()
    since = time.time() - days * 86400
    from hashmm.api import database as db
    with db._conn() as c:
        total = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0) ti, "
            "COALESCE(SUM(tokens_out),0) to_, COALESCE(SUM(cost),0) cost "
            "FROM usage_log WHERE ts >= ?", (since,)).fetchone()
        by_user = c.execute(
            "SELECT username, COUNT(*) n, COALESCE(SUM(cost),0) cost, "
            "COALESCE(SUM(tokens_in+tokens_out),0) tok FROM usage_log "
            "WHERE ts >= ? GROUP BY username ORDER BY cost DESC LIMIT 20", (since,)).fetchall()
        by_model = c.execute(
            "SELECT model, COUNT(*) n, COALESCE(SUM(cost),0) cost, "
            "COALESCE(SUM(tokens_in+tokens_out),0) tok FROM usage_log "
            "WHERE ts >= ? GROUP BY model ORDER BY cost DESC", (since,)).fetchall()

    t = dict(total)
    return {
        "days": days,
        "total_requests": t["n"],
        "total_tokens_in": t["ti"],
        "total_tokens_out": t["to_"],
        "total_cost": round(t["cost"], 4),
        "currency": "CNY",
        "by_user": [dict(r) for r in by_user],
        "by_model": [dict(r) for r in by_model],
        "prices": _prices(),
    }


def user_quota_used(user_id: str, days: int = 30) -> dict:
    """How much a single user has consumed (for quota enforcement)."""
    _ensure_table()
    since = time.time() - days * 86400
    from hashmm.api import database as db
    with db._conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(tokens_in+tokens_out),0) tok, "
            "COALESCE(SUM(tokens_in),0) ti, COALESCE(SUM(tokens_out),0) tout, "
            "COALESCE(SUM(cost),0) cost FROM usage_log WHERE user_id=? AND ts>=?",
            (user_id, since)).fetchone()
        models = c.execute(
            "SELECT model, COUNT(*) requests, COALESCE(SUM(tokens_in+tokens_out),0) tokens, "
            "COALESCE(SUM(cost),0) cost FROM usage_log WHERE user_id=? AND ts>=? "
            "GROUP BY model ORDER BY tokens DESC LIMIT 12", (user_id, since)).fetchall()
    d = dict(r)
    return {
        "days": days,
        "requests": d["n"],
        "tokens": d["tok"],
        "tokens_in": d["ti"],
        "tokens_out": d["tout"],
        "cost": round(d["cost"], 4),
        "currency": "CNY",
        "by_model": [{**dict(row), "cost": round(dict(row)["cost"], 4)} for row in models],
    }


def overview_for(user: dict, days: int = 30) -> dict:
    """Return one stable, user-facing usage contract for every client.

    Administrators receive the team aggregate; everyone else receives only
    their own rows.  Both branches expose the same totals/model keys so mobile
    does not have to guess which endpoint shape it received.
    """
    safe_days = max(1, min(int(days or 30), 365))
    is_admin = str(user.get("role") or "").lower() == "admin"
    if is_admin:
        raw = summary(safe_days)
        models = [
            {
                "model": str(row.get("model") or "未标注模型"),
                "requests": int(row.get("n") or row.get("requests") or 0),
                "tokens": int(row.get("tok") or row.get("tokens") or 0),
                "cost": round(float(row.get("cost") or 0), 4),
            }
            for row in (raw.get("by_model") or [])
        ]
        users = [
            {
                "username": str(row.get("username") or "未标注成员"),
                "requests": int(row.get("n") or row.get("requests") or 0),
                "tokens": int(row.get("tok") or row.get("tokens") or 0),
                "cost": round(float(row.get("cost") or 0), 4),
            }
            for row in (raw.get("by_user") or [])
        ]
        tokens_in = int(raw.get("total_tokens_in") or 0)
        tokens_out = int(raw.get("total_tokens_out") or 0)
        return {
            "contract": "hashmm.usage-overview.v1",
            "scope": "team",
            "days": safe_days,
            "requests": int(raw.get("total_requests") or 0),
            "tokens": tokens_in + tokens_out,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": round(float(raw.get("total_cost") or 0), 4),
            "currency": str(raw.get("currency") or "CNY"),
            "by_model": models,
            "by_user": users,
        }

    raw = user_quota_used(str(user.get("uid") or ""), safe_days)
    return {
        "contract": "hashmm.usage-overview.v1",
        "scope": "personal",
        "days": safe_days,
        "requests": int(raw.get("requests") or 0),
        "tokens": int(raw.get("tokens") or 0),
        "tokens_in": int(raw.get("tokens_in") or 0),
        "tokens_out": int(raw.get("tokens_out") or 0),
        "cost": round(float(raw.get("cost") or 0), 4),
        "currency": str(raw.get("currency") or "CNY"),
        "by_model": raw.get("by_model") or [],
        "by_user": [],
    }
