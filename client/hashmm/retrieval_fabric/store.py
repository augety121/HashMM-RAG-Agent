from __future__ import annotations

import json
import time
from typing import Any

from hashmm.api import database as db


def ensure_tables() -> None:
    with db._conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_runs (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, query TEXT NOT NULL,
                mode TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_runs_owner_updated ON search_runs(owner_id,updated_at DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_run_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                owner_id TEXT NOT NULL, event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_events_owner_run ON search_run_events(owner_id,run_id,seq)")


def create_run(run_id: str, owner_id: str, request: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(); now = time.time()
    with db._conn() as conn:
        conn.execute("INSERT INTO search_runs(id,owner_id,query,mode,state,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (run_id, owner_id, request["query"], request["mode"], "queued",
                      json.dumps(request, ensure_ascii=False, separators=(",", ":")), now, now))
    append_event(run_id, owner_id, "state", {"state": "queued"})
    return get_run(run_id, owner_id) or {}


def update_run(run_id: str, owner_id: str, state: str, *, result: dict | None = None, error_code: str = "") -> None:
    now = time.time(); ensure_tables()
    with db._conn() as conn:
        cursor = conn.execute("UPDATE search_runs SET state=?,result_json=?,error_code=?,updated_at=? WHERE id=? AND owner_id=?",
            (state, json.dumps(result or {}, ensure_ascii=False, separators=(",", ":")), error_code[:80], now, run_id, owner_id))
        if cursor.rowcount != 1:
            raise KeyError("search run not found")
    append_event(run_id, owner_id, "state", {"state": state, "error_code": error_code})


def append_event(run_id: str, owner_id: str, event_type: str, payload: dict[str, Any]) -> None:
    ensure_tables()
    with db._conn() as conn:
        conn.execute("INSERT INTO search_run_events(run_id,owner_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
                     (run_id, owner_id, event_type[:40], json.dumps(payload, ensure_ascii=False, separators=(",", ":")), time.time()))


def get_run(run_id: str, owner_id: str) -> dict[str, Any] | None:
    ensure_tables()
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM search_runs WHERE id=? AND owner_id=?", (run_id, owner_id)).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("request_json", "result_json"):
        try: item[key[:-5]] = json.loads(item.pop(key) or "{}")
        except (TypeError, ValueError): item[key[:-5]] = {}
    return item


def list_events(run_id: str, owner_id: str, after: int = 0) -> list[dict[str, Any]]:
    ensure_tables()
    with db._conn() as conn:
        rows = conn.execute("SELECT seq,event_type,payload_json,created_at FROM search_run_events WHERE run_id=? AND owner_id=? AND seq>? ORDER BY seq LIMIT 500",
                            (run_id, owner_id, max(0, int(after)))).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try: item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except (TypeError, ValueError): item["payload"] = {}
        result.append(item)
    return result
