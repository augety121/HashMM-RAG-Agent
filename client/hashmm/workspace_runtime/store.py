"""Durable, owner-scoped Cloud workspace execution receipts."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from hashmm.api import database as db


def ensure_tables() -> None:
    with db._conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_executions (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL, provider TEXT NOT NULL, state TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(owner_id, workspace_id, idempotency_key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspace_exec_owner_updated ON workspace_executions(owner_id,workspace_id,updated_at DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_execution_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL,
                owner_id TEXT NOT NULL, event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workspace_exec_events ON workspace_execution_events(owner_id,execution_id,seq)")


def create_or_get(owner_id: str, workspace_id: str, run_id: str, provider: str,
                  idempotency_key: str, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    ensure_tables()
    with db._conn() as conn:
        existing = conn.execute(
            "SELECT id FROM workspace_executions WHERE owner_id=? AND workspace_id=? AND idempotency_key=?",
            (owner_id, workspace_id, idempotency_key),
        ).fetchone()
    if existing:
        return get(str(existing["id"]), owner_id, workspace_id) or {}, False
    execution_id = "wex_" + uuid.uuid4().hex
    now = time.time()
    try:
        with db._conn() as conn:
            conn.execute(
                "INSERT INTO workspace_executions(id,owner_id,workspace_id,run_id,provider,state,idempotency_key,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (execution_id, owner_id, workspace_id, run_id, provider, "queued", idempotency_key,
                 json.dumps(request, ensure_ascii=False, separators=(",", ":")), now, now),
            )
    except Exception:
        with db._conn() as conn:
            row = conn.execute(
                "SELECT id FROM workspace_executions WHERE owner_id=? AND workspace_id=? AND idempotency_key=?",
                (owner_id, workspace_id, idempotency_key),
            ).fetchone()
        if row:
            return get(str(row["id"]), owner_id, workspace_id) or {}, False
        raise
    append_event(execution_id, owner_id, "state", {"state": "queued"})
    return get(execution_id, owner_id, workspace_id) or {}, True


def update(execution_id: str, owner_id: str, workspace_id: str, state: str, *,
           result: dict[str, Any] | None = None, error_code: str = "") -> None:
    ensure_tables()
    with db._conn() as conn:
        cursor = conn.execute(
            "UPDATE workspace_executions SET state=?,result_json=?,error_code=?,updated_at=? WHERE id=? AND owner_id=? AND workspace_id=?",
            (state, json.dumps(result or {}, ensure_ascii=False, separators=(",", ":")), error_code[:80],
             time.time(), execution_id, owner_id, workspace_id),
        )
        if cursor.rowcount != 1:
            raise KeyError("workspace execution not found")
    append_event(execution_id, owner_id, "state", {"state": state, "error_code": error_code[:80]})


def append_event(execution_id: str, owner_id: str, event_type: str, payload: dict[str, Any]) -> None:
    ensure_tables()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO workspace_execution_events(execution_id,owner_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (execution_id, owner_id, event_type[:40], json.dumps(payload, ensure_ascii=False, separators=(",", ":")), time.time()),
        )


def get(execution_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
    ensure_tables()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM workspace_executions WHERE id=? AND owner_id=? AND workspace_id=?",
            (execution_id, owner_id, workspace_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("request_json", "result_json"):
        try:
            item[key[:-5]] = json.loads(item.pop(key) or "{}")
        except (TypeError, ValueError):
            item[key[:-5]] = {}
    item.pop("idempotency_key", None)
    return item


def events(execution_id: str, owner_id: str, after: int = 0) -> list[dict[str, Any]]:
    ensure_tables()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT seq,event_type,payload_json,created_at FROM workspace_execution_events WHERE execution_id=? AND owner_id=? AND seq>? ORDER BY seq LIMIT 500",
            (execution_id, owner_id, max(0, int(after))),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except (TypeError, ValueError):
            item["payload"] = {}
        result.append(item)
    return result
