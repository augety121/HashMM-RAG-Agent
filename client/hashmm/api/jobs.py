"""Durable bounded background-job facade.

Job metadata survives restarts and every owned job is projected into the same
WorkRuntime used by Chat.  Python callables are intentionally not serialised;
queued/running work found after a restart becomes ``interrupted`` instead of
being replayed with uncertain side effects.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any, Awaitable, Callable

from hashmm.api import database as db
from hashmm.utils import get_logger


logger = get_logger("hashmm.jobs")
_SCHEMA_LOCK = threading.RLock()
_RECONCILED_PATHS: set[str] = set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _ensure_table() -> None:
    path_key = str(db.DB_PATH)
    interrupted: list[tuple[str, str, str]] = []
    with _SCHEMA_LOCK:
        with db._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS background_jobs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'system',
                    project_id TEXT NOT NULL DEFAULT '',
                    work_run_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    total INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_background_jobs_owner_time
                    ON background_jobs(owner_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_background_jobs_status_time
                    ON background_jobs(status, started_at DESC);
                """
            )
            if path_key not in _RECONCILED_PATHS:
                now = time.time()
                stale = conn.execute(
                    "SELECT id,owner_id,work_run_id FROM background_jobs "
                    "WHERE status IN ('pending','running','retrying')"
                ).fetchall()
                interrupted = [
                    (str(row["id"]), str(row["owner_id"]), str(row["work_run_id"] or ""))
                    for row in stale
                ]
                conn.execute(
                    "UPDATE background_jobs SET status='interrupted',"
                    "message='Service restarted before the callable completed',"
                    "error='restart_interrupted',updated_at=?,finished_at=? "
                    "WHERE status IN ('pending','running','retrying')",
                    (now, now),
                )
                _RECONCILED_PATHS.add(path_key)
        # WorkRuntime uses the same database pool, so project interrupted rows
        # only after releasing the schema transaction.
        for job_id, owner_id, run_id in interrupted:
            if not run_id:
                continue
            try:
                from hashmm.agent import work_runtime
                work_runtime.append_event_once(
                    run_id, user_id=owner_id, event_type="background_job.interrupted",
                    status="interrupted", summary="Service restarted before the callable completed",
                    payload={"job_id": job_id, "restart_replay_supported": False},
                    idempotency_key=f"background-job:{job_id}:restart-interrupted",
                )
            except Exception as exc:
                logger.warning("background job restart projection failed: %s", type(exc).__name__)


def _public(row: Any) -> dict[str, Any]:
    status = str(row["status"])
    canonical = {"pending": "queued", "done": "delivered", "error": "failed"}.get(status, status)
    return {
        "id": str(row["id"]), "owner_id": str(row["owner_id"]),
        "project_id": str(row["project_id"] or ""), "work_run_id": str(row["work_run_id"] or ""),
        "kind": str(row["kind"]), "status": status, "task_state": canonical,
        "total": int(row["total"] or 0), "done": int(row["done"] or 0),
        "message": str(row["message"] or ""), "result": _load(row["result_json"], {}),
        "error": str(row["error"] or ""), "started_at": float(row["started_at"]),
        "updated_at": float(row["updated_at"]),
        "finished_at": float(row["finished_at"] or 0) or None,
        "recoverable": False if status == "interrupted" else status in {"pending", "running", "retrying"},
    }


def create_job(kind: str, total: int = 0, *, owner_id: str = "system",
               project_id: str = "", work_run_id: str = "") -> str:
    _ensure_table()
    job_id = "job_" + uuid.uuid4().hex
    owner = str(owner_id or "system")[:160]
    work_run_id = str(work_run_id or "")[:120]
    if not work_run_id:
        try:
            from hashmm.agent import work_runtime
            run = work_runtime.create_run(
                user_id=owner, kind="workflow", source_id=f"background-job:{job_id}",
                title=str(kind or "background job")[:240], status="queued",
                project_id=str(project_id or "")[:120],
                snapshot={"background_job_id": job_id, "job_kind": str(kind or "")[:120],
                          "restart_replay_supported": False},
            )
            work_run_id = str(run.get("id") or "")
        except Exception as exc:
            logger.warning("background job WorkRuntime projection failed: %s", type(exc).__name__)
    now = time.time()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO background_jobs"
            "(id,owner_id,project_id,work_run_id,kind,status,total,done,message,result_json,error,"
            "started_at,updated_at,finished_at) VALUES(?,?,?,?,?,'pending',0,0,'','{}','',?,?,0)",
            (job_id, owner, str(project_id or "")[:120], work_run_id, str(kind or "")[:120], now, now),
        )
        if total:
            conn.execute("UPDATE background_jobs SET total=? WHERE id=?", (max(0, int(total)), job_id))
    return job_id


def get_job(job_id: str, *, owner_id: str = "") -> dict[str, Any] | None:
    _ensure_table()
    with db._conn() as conn:
        if owner_id:
            row = conn.execute("SELECT * FROM background_jobs WHERE id=? AND owner_id=?",
                               (job_id, owner_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone()
    return _public(row) if row is not None else None


def list_jobs(limit: int = 30, *, owner_id: str = "") -> list[dict[str, Any]]:
    _ensure_table()
    with db._conn() as conn:
        if owner_id:
            rows = conn.execute(
                "SELECT * FROM background_jobs WHERE owner_id=? ORDER BY started_at DESC LIMIT ?",
                (owner_id, max(1, min(int(limit), 200))),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM background_jobs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
    return [_public(row) for row in rows]


def status_counts() -> dict[str, int]:
    _ensure_table()
    counts = {"pending": 0, "running": 0, "retrying": 0, "done": 0,
              "error": 0, "interrupted": 0}
    with db._conn() as conn:
        rows = conn.execute("SELECT status,COUNT(*) AS n FROM background_jobs GROUP BY status").fetchall()
    for row in rows:
        counts[str(row["status"])] = int(row["n"])
    return counts


def update_job(job_id: str, **fields: Any) -> None:
    _ensure_table()
    allowed = {"status", "total", "done", "message", "result", "error", "finished_at"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    if "status" in values and str(values["status"]) not in {
        "pending", "running", "retrying", "done", "error", "interrupted",
    }:
        raise ValueError("invalid background job status")
    if "result" in values:
        values["result_json"] = _json(values.pop("result"))
    values["updated_at"] = time.time()
    clause = ",".join(f"{key}=?" for key in values)
    with db._conn() as conn:
        conn.execute(f"UPDATE background_jobs SET {clause} WHERE id=?", (*values.values(), job_id))
        row = conn.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or not row["work_run_id"]:
        return
    if "status" in values:
        canonical = {"pending": "queued", "done": "delivered", "error": "failed"}.get(
            str(values["status"]), str(values["status"]),
        )
        try:
            from hashmm.agent import work_runtime
            work_runtime.append_event_once(
                str(row["work_run_id"]), user_id=str(row["owner_id"]),
                event_type=f"background_job.{values['status']}", status=canonical,
                summary=str(row["message"] or row["error"] or f"Background job {values['status']}")[:500],
                payload={"job_id": job_id, "done": int(row["done"]), "total": int(row["total"])},
                idempotency_key=f"background-job:{job_id}:{values['status']}:{int(row['done'])}",
            )
        except Exception as exc:
            logger.warning("background job state projection failed: %s", type(exc).__name__)


def _progress(job_id: str):
    def callback(done: int | None = None, total: int | None = None, message: str | None = None):
        values: dict[str, Any] = {}
        if done is not None:
            values["done"] = max(0, int(done))
        if total is not None:
            values["total"] = max(0, int(total))
        if message is not None:
            values["message"] = str(message)[:500]
        update_job(job_id, **values)
    return callback


async def run_job(job_id: str, worker: Callable[..., Awaitable[Any]]) -> None:
    if get_job(job_id) is None:
        return
    update_job(job_id, status="running")
    try:
        result = await worker(_progress(job_id))
        current = get_job(job_id) or {}
        update_job(job_id, status="done", result=result,
                   done=int(current.get("done") or current.get("total") or 0),
                   finished_at=time.time())
    except Exception as exc:
        logger.warning("job %s failed: %s", job_id, exc, exc_info=True)
        update_job(job_id, status="error", error=str(exc)[:300], finished_at=time.time())


def spawn(kind: str, worker: Callable[..., Awaitable[Any]], total: int = 0, *,
          owner_id: str = "system", project_id: str = "", work_run_id: str = "") -> str:
    """Submit through the bounded queue; never fire an unbounded task."""
    from hashmm.api.job_queue import get_job_queue
    return get_job_queue().submit(
        kind, worker, total=total, owner_id=owner_id, project_id=project_id,
        work_run_id=work_run_id,
    )


__all__ = ["create_job", "get_job", "list_jobs", "run_job", "spawn", "status_counts", "update_job"]
