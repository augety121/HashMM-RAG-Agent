"""Durable public platform protocol for HashMM.

This module is deliberately a thin control plane over ``work_runtime``.  It
stores API resources, replayable response events and request idempotency, but
does not introduce a second task executor.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any, Iterable

from hashmm.api import database as db


SCHEMA_VERSION = "2026-08-08.v2"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SECRET_RE = re.compile(r"(^|_)(api[_-]?key|secret|password|token|authorization|cookie|credential)(_|$)", re.I)
_SCHEMA_LOCK = threading.RLock()


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:100_000]
    if isinstance(value, dict):
        result = {}
        for raw_key, item in list(value.items())[:200]:
            key = str(raw_key)[:120]
            result[key] = "[redacted]" if _SECRET_RE.search(key) else _safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth=depth + 1) for item in list(value)[:500]]
    return str(value)[:1000]


def _load(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def ensure_schema() -> None:
    """Create additive protocol tables for old and fresh databases."""
    with _SCHEMA_LOCK:
        with db._conn() as conn:
            conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_idempotency (
                owner_id TEXT NOT NULL,
                route TEXT NOT NULL,
                idem_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                status_code INTEGER NOT NULL DEFAULT 0,
                response_json TEXT NOT NULL DEFAULT '{}',
                resource_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(owner_id, route, idem_key)
            );
            CREATE INDEX IF NOT EXISTS idx_api_idempotency_updated
                ON api_idempotency(updated_at);

            CREATE TABLE IF NOT EXISTS api_threads (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_threads_owner_updated
                ON api_threads(owner_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_api_threads_owner_project
                ON api_threads(owner_id, project_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS api_turns (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                sequence INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(owner_id, thread_id, sequence),
                FOREIGN KEY(thread_id) REFERENCES api_threads(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_api_turns_owner_thread
                ON api_turns(owner_id, thread_id, sequence);

            CREATE TABLE IF NOT EXISTS api_items (
                id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                sequence INTEGER NOT NULL,
                content_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE(owner_id, turn_id, sequence),
                FOREIGN KEY(turn_id) REFERENCES api_turns(id) ON DELETE CASCADE,
                FOREIGN KEY(thread_id) REFERENCES api_threads(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_api_items_owner_thread
                ON api_items(owner_id, thread_id, created_at);

            CREATE TABLE IF NOT EXISTS api_responses (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'created',
                request_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '[]',
                usage_json TEXT NOT NULL DEFAULT '{}',
                error_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_responses_owner_updated
                ON api_responses(owner_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_api_responses_owner_thread
                ON api_responses(owner_id, thread_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS api_response_events (
                id TEXT PRIMARY KEY,
                response_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE(owner_id, response_id, sequence),
                FOREIGN KEY(response_id) REFERENCES api_responses(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_api_response_events_owner_response
                ON api_response_events(owner_id, response_id, sequence);

            CREATE TABLE IF NOT EXISTS usage_events (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                step_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                service TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'token',
                currency TEXT NOT NULL DEFAULT 'CNY',
                price_version TEXT NOT NULL DEFAULT '',
                estimated_cost REAL NOT NULL DEFAULT 0,
                provider_reported_cost REAL NOT NULL DEFAULT 0,
                reconciled_cost REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'estimated',
                state TEXT NOT NULL DEFAULT 'settled',
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(owner_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_usage_events_owner_time
                ON usage_events(owner_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS usage_reservations (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                state TEXT NOT NULL DEFAULT 'reserved',
                idempotency_key TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(owner_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_usage_reservations_owner_state
                ON usage_reservations(owner_id, state, expires_at);
            """
            )
            # V820 additive usage ownership.  Keep historical rows nullable;
            # legacy keys and pre-V820 events remain valid evidence.
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()}
            if "access_key_id" not in columns:
                conn.execute("ALTER TABLE usage_events ADD COLUMN access_key_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_events_access_key_time "
                "ON usage_events(access_key_id, created_at DESC)"
            )


def begin_idempotent(owner_id: str, route: str, key: str, request: Any,
                     *, reclaim_after: float = 120.0) -> dict[str, Any]:
    """Atomically claim a write or return its durable replay.

    A key cannot be reused with a different request fingerprint.  A recent
    pending claim is never executed concurrently.  Old pending claims can be
    reclaimed because effect identifiers are deterministically derived from
    the key by callers.
    """
    ensure_schema()
    owner = str(owner_id or "").strip()
    route = str(route or "").strip()[:160]
    key = str(key or "").strip()
    if not owner:
        raise ProtocolError("authentication_required", "authentication required", 401)
    if not key:
        raise ProtocolError("idempotency_key_required", "Idempotency-Key is required", 400)
    if not _ID_RE.fullmatch(key):
        raise ProtocolError("invalid_idempotency_key", "invalid Idempotency-Key", 400)
    request_fp = fingerprint(request)
    now = time.time()
    with db._conn() as conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO api_idempotency "
            "(owner_id,route,idem_key,request_fingerprint,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (owner, route, key, request_fp, "pending", now, now),
        ).rowcount
        row = conn.execute(
            "SELECT * FROM api_idempotency WHERE owner_id=? AND route=? AND idem_key=?",
            (owner, route, key),
        ).fetchone()
        if row is None:
            raise ProtocolError("idempotency_unavailable", "idempotency ledger unavailable", 503)
        if str(row["request_fingerprint"]) != request_fp:
            raise ProtocolError(
                "idempotency_conflict",
                "Idempotency-Key was already used with a different request",
                409,
            )
        if inserted:
            return {"state": "claimed", "fingerprint": request_fp}
        if str(row["state"]) == "complete":
            return {
                "state": "replay",
                "status_code": int(row["status_code"] or 200),
                "response": _load(row["response_json"], {}),
                "resource_id": str(row["resource_id"] or ""),
            }
        if now - float(row["updated_at"] or now) < max(1.0, reclaim_after):
            raise ProtocolError("request_in_progress", "request with this key is still in progress", 409)
        changed = conn.execute(
            "UPDATE api_idempotency SET updated_at=? WHERE owner_id=? AND route=? "
            "AND idem_key=? AND state='pending' AND updated_at=?",
            (now, owner, route, key, float(row["updated_at"])),
        ).rowcount
        if not changed:
            raise ProtocolError("request_in_progress", "request with this key is still in progress", 409)
        return {"state": "reclaimed", "fingerprint": request_fp}


def finish_idempotent(owner_id: str, route: str, key: str, *, status_code: int,
                      response: Any, resource_id: str = "") -> None:
    ensure_schema()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE api_idempotency SET state='complete',status_code=?,response_json=?,"
            "resource_id=?,updated_at=? WHERE owner_id=? AND route=? AND idem_key=? "
            "AND state='pending'",
            (int(status_code), _json(response), str(resource_id or "")[:160], time.time(),
             owner_id, route, key),
        ).rowcount
        if not changed:
            row = conn.execute(
                "SELECT state FROM api_idempotency WHERE owner_id=? AND route=? AND idem_key=?",
                (owner_id, route, key),
            ).fetchone()
            if row is None or str(row["state"]) != "complete":
                raise ProtocolError("idempotency_commit_unavailable", "could not commit idempotent response", 503)


def _thread(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "object": "thread", "project_id": str(row["project_id"] or ""),
        "title": str(row["title"] or ""), "metadata": _load(row["metadata_json"], {}),
        "status": str(row["status"]), "revision": int(row["revision"] or 1),
        "created_at": float(row["created_at"]), "updated_at": float(row["updated_at"]),
    }


def _owned_project(owner_id: str, project_id: str) -> bool:
    if not project_id:
        return True
    try:
        db._ensure_project_table()
        with db._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND user_id=?", (project_id, owner_id)
            ).fetchone() is not None
    except Exception:
        return False


def create_thread(owner_id: str, *, title: str = "", project_id: str = "",
                  metadata: dict[str, Any] | None = None, thread_id: str = "") -> dict[str, Any]:
    ensure_schema()
    if project_id and not _owned_project(owner_id, project_id):
        raise ProtocolError("not_found", "project not found", 404)
    now = time.time()
    tid = thread_id if _ID_RE.fullmatch(thread_id or "") else _id("thread")
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO api_threads(id,owner_id,project_id,title,metadata_json,status,revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'active',1,?,?)",
            (tid, owner_id, project_id[:120], str(title or "")[:240], _json(_safe(metadata or {})), now, now),
        )
        return _thread(conn.execute("SELECT * FROM api_threads WHERE id=?", (tid,)).fetchone())


def get_thread(owner_id: str, thread_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_threads WHERE id=? AND owner_id=?", (thread_id, owner_id)
        ).fetchone()
        return _thread(row) if row is not None else None


def list_threads(owner_id: str, *, project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    with db._conn() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM api_threads WHERE owner_id=? AND project_id=? "
                "ORDER BY updated_at DESC LIMIT ?", (owner_id, project_id, max(1, min(limit, 100)))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM api_threads WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                (owner_id, max(1, min(limit, 100))),
            ).fetchall()
    return [_thread(row) for row in rows]


def update_thread(owner_id: str, thread_id: str, *, title: str | None = None,
                  metadata: dict[str, Any] | None = None, status: str | None = None,
                  expected_revision: int | None = None) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_threads WHERE id=? AND owner_id=?", (thread_id, owner_id)
        ).fetchone()
        if row is None:
            return None
        if expected_revision is not None and int(row["revision"]) != int(expected_revision):
            raise ProtocolError("revision_conflict", "thread revision conflict", 409)
        next_status = str(status or row["status"])
        if next_status not in {"active", "archived", "deleted"}:
            raise ProtocolError("invalid_status", "invalid thread status", 400)
        conn.execute(
            "UPDATE api_threads SET title=?,metadata_json=?,status=?,revision=revision+1,updated_at=? "
            "WHERE id=? AND owner_id=?",
            (str(title if title is not None else row["title"])[:240],
             _json(_safe(metadata if metadata is not None else _load(row["metadata_json"], {}))),
             next_status, time.time(), thread_id, owner_id),
        )
        return _thread(conn.execute("SELECT * FROM api_threads WHERE id=?", (thread_id,)).fetchone())


def fork_thread(owner_id: str, thread_id: str, *, title: str = "") -> dict[str, Any] | None:
    """Fork an owner thread and copy only its public turn/item records."""
    source = get_thread(owner_id, thread_id)
    if source is None:
        return None
    forked = create_thread(
        owner_id, title=title or f"{source['title']} (fork)", project_id=source["project_id"],
        metadata={**source["metadata"], "forked_from": thread_id},
    )
    now = time.time()
    with db._conn() as conn:
        turns = conn.execute(
            "SELECT * FROM api_turns WHERE owner_id=? AND thread_id=? ORDER BY sequence",
            (owner_id, thread_id),
        ).fetchall()
        for turn in turns:
            new_turn = _id("turn")
            conn.execute(
                "INSERT INTO api_turns VALUES(?,?,?,?,?,?,?,?)",
                (new_turn, forked["id"], owner_id, str(turn["status"]), int(turn["sequence"]),
                 str(turn["metadata_json"]), float(turn["created_at"]), now, ),
            )
            # The explicit column form avoids schema-order coupling below.
            items = conn.execute(
                "SELECT * FROM api_items WHERE owner_id=? AND turn_id=? ORDER BY sequence",
                (owner_id, str(turn["id"])),
            ).fetchall()
            for item in items:
                conn.execute(
                    "INSERT INTO api_items(id,turn_id,thread_id,owner_id,item_type,role,sequence,content_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (_id("item"), new_turn, forked["id"], owner_id, str(item["item_type"]),
                     str(item["role"]), int(item["sequence"]), str(item["content_json"]),
                     float(item["created_at"])),
                )
    return get_thread(owner_id, forked["id"])


def create_turn_with_input(owner_id: str, thread_id: str, input_items: Iterable[dict[str, Any]]) -> str:
    ensure_schema()
    if get_thread(owner_id, thread_id) is None:
        raise ProtocolError("not_found", "thread not found", 404)
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM api_turns WHERE owner_id=? AND thread_id=?",
            (owner_id, thread_id),
        ).fetchone()
        turn_id = _id("turn")
        conn.execute(
            "INSERT INTO api_turns(id,thread_id,owner_id,status,sequence,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'{}',?,?)",
            (turn_id, thread_id, owner_id, "created", int(row["n"] or 1), now, now),
        )
        for sequence, item in enumerate(input_items, 1):
            conn.execute(
                "INSERT INTO api_items(id,turn_id,thread_id,owner_id,item_type,role,sequence,content_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (_id("item"), turn_id, thread_id, owner_id, str(item.get("type") or "message")[:40],
                 str(item.get("role") or "user")[:20], sequence, _json(item), now),
            )
        conn.execute("UPDATE api_threads SET updated_at=? WHERE id=? AND owner_id=?", (now, thread_id, owner_id))
    return turn_id


def _response(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "object": "response", "status": str(row["status"]),
        "thread_id": str(row["thread_id"] or ""), "turn_id": str(row["turn_id"] or ""),
        "run_id": str(row["run_id"] or ""), "model": str(row["model"] or ""),
        "output": _load(row["output_json"], []), "usage": _load(row["usage_json"], {}),
        "error": _load(row["error_json"], {}) or None,
        "created_at": float(row["created_at"]), "updated_at": float(row["updated_at"]),
    }


def create_response(owner_id: str, *, request: dict[str, Any], thread_id: str,
                    turn_id: str, run_id: str, model: str,
                    response_id: str = "") -> dict[str, Any]:
    ensure_schema()
    rid = response_id if _ID_RE.fullmatch(response_id or "") else _id("resp")
    now = time.time()
    with db._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO api_responses"
            "(id,owner_id,thread_id,turn_id,run_id,model,status,request_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'created',?,?,?)",
            (rid, owner_id, thread_id, turn_id, run_id, str(model or "")[:120], _json(_safe(request)), now, now),
        )
        row = conn.execute("SELECT * FROM api_responses WHERE id=? AND owner_id=?", (rid, owner_id)).fetchone()
        return _response(row)


def get_response(owner_id: str, response_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_responses WHERE id=? AND owner_id=?", (response_id, owner_id)
        ).fetchone()
        return _response(row) if row is not None else None


def update_response(owner_id: str, response_id: str, *, status: str,
                    output: list[dict[str, Any]] | None = None,
                    usage: dict[str, Any] | None = None,
                    error: dict[str, Any] | None = None) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE api_responses SET status=?,output_json=?,usage_json=?,error_json=?,updated_at=? "
            "WHERE id=? AND owner_id=?",
            (status, _json(output or []), _json(usage or {}), _json(error or {}), time.time(),
             response_id, owner_id),
        ).rowcount
        if not changed:
            return None
        return _response(conn.execute("SELECT * FROM api_responses WHERE id=?", (response_id,)).fetchone())


def complete_response(owner_id: str, response_id: str, *,
                      output: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any] | None:
    """Atomically publish terminal response state and its terminal event.

    A reader must never observe ``status=completed`` before the replayable
    ``response.completed`` event exists.  Keeping both writes in one SQLite
    transaction also removes a background-worker race seen by fast pollers.
    """
    ensure_schema()
    now = time.time()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE api_responses SET status='completed',output_json=?,usage_json=?,error_json='{}',updated_at=? "
            "WHERE id=? AND owner_id=? AND status NOT IN ('completed','failed','cancelled')",
            (_json(output or []), _json(usage or {}), now, response_id, owner_id),
        ).rowcount
        row = conn.execute(
            "SELECT * FROM api_responses WHERE id=? AND owner_id=?", (response_id, owner_id)
        ).fetchone()
        if row is None:
            return None
        response = _response(row)
        if changed:
            sequence_row = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM api_response_events "
                "WHERE owner_id=? AND response_id=?", (owner_id, response_id),
            ).fetchone()
            sequence = int(sequence_row["n"] or 1)
            event_id = f"event_{response_id}_{sequence}"
            envelope = {
                "event_id": event_id, "sequence": sequence, "response_id": response_id,
                "timestamp": now, "schema_version": SCHEMA_VERSION,
                "run_id": response.get("run_id", ""), "turn_id": response.get("turn_id", ""),
                "item_id": "", "status": "completed", "result": response,
            }
            conn.execute(
                "INSERT INTO api_response_events(id,response_id,owner_id,sequence,event_type,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (event_id, response_id, owner_id, sequence, "response.completed", _json(envelope), now),
            )
        return response


def delete_response(owner_id: str, response_id: str) -> bool:
    ensure_schema()
    with db._conn() as conn:
        return bool(conn.execute(
            "DELETE FROM api_responses WHERE id=? AND owner_id=?", (response_id, owner_id)
        ).rowcount)


def append_response_event(owner_id: str, response_id: str, event_type: str,
                          payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM api_response_events "
            "WHERE owner_id=? AND response_id=?", (owner_id, response_id)
        ).fetchone()
        sequence = int(row["n"] or 1)
        event_id = f"event_{response_id}_{sequence}"
        envelope = {
            "event_id": event_id, "sequence": sequence, "response_id": response_id,
            "timestamp": now, "schema_version": SCHEMA_VERSION, **payload,
        }
        conn.execute(
            "INSERT INTO api_response_events(id,response_id,owner_id,sequence,event_type,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (event_id, response_id, owner_id, sequence, event_type, _json(envelope), now),
        )
    return {"id": event_id, "event": event_type, "data": envelope}


def list_response_events(owner_id: str, response_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
    ensure_schema()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM api_response_events WHERE owner_id=? AND response_id=? AND sequence>? "
            "ORDER BY sequence LIMIT 1000", (owner_id, response_id, max(0, int(after_sequence)))
        ).fetchall()
    return [{"id": str(row["id"]), "event": str(row["event_type"]),
             "data": _load(row["payload_json"], {})} for row in rows]


def reserve_budget(owner_id: str, *, run_id: str, amount: float, currency: str,
                   idempotency_key: str, limit: float | None = None,
                   ttl_seconds: int = 900) -> dict[str, Any]:
    """Reserve estimated spend before a provider call; fail closed on limit."""
    ensure_schema()
    amount = max(0.0, float(amount or 0))
    now = time.time()
    with db._conn() as conn:
        existing = conn.execute(
            "SELECT * FROM usage_reservations WHERE owner_id=? AND idempotency_key=?",
            (owner_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        conn.execute(
            "UPDATE usage_reservations SET state='expired',updated_at=? "
            "WHERE owner_id=? AND state='reserved' AND expires_at<=?", (now, owner_id, now)
        )
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM usage_reservations "
            "WHERE owner_id=? AND state='reserved' AND expires_at>?", (owner_id, now)
        ).fetchone()
        reserved = float(row["total"] or 0)
        if limit is not None and reserved + amount > max(0.0, float(limit)):
            raise ProtocolError("budget_exceeded", "budget reservation exceeds configured limit", 402)
        rid = _id("reserve")
        conn.execute(
            "INSERT INTO usage_reservations VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rid, owner_id, run_id, amount, currency[:8], "reserved", idempotency_key,
             now + max(30, int(ttl_seconds)), now, now),
        )
        return dict(conn.execute("SELECT * FROM usage_reservations WHERE id=?", (rid,)).fetchone())


def release_budget(owner_id: str, *, reservation_id: str = "",
                   idempotency_key: str = "") -> bool:
    """Release an unspent reservation on every terminal failure path."""
    ensure_schema()
    if not reservation_id and not idempotency_key:
        return False
    where = "id=?" if reservation_id else "idempotency_key=?"
    value = reservation_id or idempotency_key
    with db._conn() as conn:
        return bool(conn.execute(
            f"UPDATE usage_reservations SET state='released',updated_at=? "
            f"WHERE owner_id=? AND {where} AND state='reserved'",
            (time.time(), owner_id, value),
        ).rowcount)


def settle_usage(owner_id: str, *, reservation_id: str, run_id: str, step_id: str,
                 provider: str, service: str, model: str, quantity: float, unit: str,
                 estimated_cost: float, currency: str, price_version: str,
                 idempotency_key: str, provider_reported_cost: float = 0,
                 metadata: dict[str, Any] | None = None,
                 access_key_id: str = "") -> dict[str, Any]:
    ensure_schema()
    now = time.time()
    with db._conn() as conn:
        existing = conn.execute(
            "SELECT * FROM usage_events WHERE owner_id=? AND idempotency_key=?",
            (owner_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        event_id = _id("usage")
        source = "provider_reported" if provider_reported_cost else "estimated"
        conn.execute(
            "INSERT INTO usage_events("
            "id,owner_id,run_id,step_id,provider,service,model,quantity,unit,currency,"
            "price_version,estimated_cost,provider_reported_cost,reconciled_cost,source,state,"
            "idempotency_key,metadata_json,created_at,updated_at,access_key_id"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, owner_id, run_id, step_id, provider[:80], service[:80], model[:120],
             max(0.0, float(quantity or 0)), unit[:40], currency[:8], price_version[:80],
             max(0.0, float(estimated_cost or 0)), max(0.0, float(provider_reported_cost or 0)),
             0.0, source, "settled", idempotency_key, _json(metadata or {}), now, now,
             str(access_key_id or "")),
        )
        if reservation_id:
            conn.execute(
                "UPDATE usage_reservations SET state='settled',updated_at=? "
                "WHERE id=? AND owner_id=? AND state='reserved'", (now, reservation_id, owner_id)
            )
        return dict(conn.execute("SELECT * FROM usage_events WHERE id=?", (event_id,)).fetchone())


def usage_summary(owner_id: str, *, days: int = 30) -> dict[str, Any]:
    ensure_schema()
    since = time.time() - max(1, min(int(days), 366)) * 86400
    with db._conn() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS events,COALESCE(SUM(quantity),0) AS quantity,"
            "COALESCE(SUM(estimated_cost),0) AS estimated,"
            "COALESCE(SUM(provider_reported_cost),0) AS reported,"
            "COALESCE(SUM(reconciled_cost),0) AS reconciled "
            "FROM usage_events WHERE owner_id=? AND created_at>=?", (owner_id, since)
        ).fetchone()
        rows = conn.execute(
            "SELECT provider,service,model,unit,COUNT(*) AS events,COALESCE(SUM(quantity),0) AS quantity,"
            "COALESCE(SUM(estimated_cost),0) AS estimated_cost FROM usage_events "
            "WHERE owner_id=? AND created_at>=? GROUP BY provider,service,model,unit "
            "ORDER BY estimated_cost DESC", (owner_id, since)
        ).fetchall()
    return {
        "object": "usage_summary", "days": max(1, min(int(days), 366)),
        "events": int(totals["events"] or 0), "quantity": float(totals["quantity"] or 0),
        "estimated_cost": float(totals["estimated"] or 0),
        "provider_reported_cost": float(totals["reported"] or 0),
        "reconciled_cost": float(totals["reconciled"] or 0),
        "breakdown": [dict(row) for row in rows],
    }


__all__ = [
    "ProtocolError", "SCHEMA_VERSION", "append_response_event", "begin_idempotent", "complete_response",
    "create_response", "create_thread", "create_turn_with_input", "delete_response",
    "ensure_schema", "fingerprint", "finish_idempotent", "fork_thread", "get_response",
    "get_thread", "list_response_events", "list_threads", "reserve_budget", "settle_usage",
    "update_response", "update_thread", "usage_summary",
]
