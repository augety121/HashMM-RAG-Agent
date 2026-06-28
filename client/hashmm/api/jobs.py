"""Background job manager (V15 Phase 9).

Enterprise data ops (bulk import, full re-index) can take minutes. Running them
synchronously blocks the request and gives the user no feedback. This module
runs them as background jobs with progress the UI can poll.

Design: in-process async tasks + an in-memory job registry. Jobs survive for the
process lifetime; for a single-container deployment that's exactly right (no
external queue needed — that would be over-engineering at this scale).

A job:
  {id, kind, status: pending|running|done|error, total, done, message,
   started_at, finished_at, result, error}
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable

from hashmm.utils import get_logger

logger = get_logger("hashmm.jobs")

_JOBS: dict[str, dict] = {}
_MAX_JOBS = 100  # keep the most recent N jobs


def _prune():
    if len(_JOBS) <= _MAX_JOBS:
        return
    # drop oldest finished jobs
    finished = sorted(
        [j for j in _JOBS.values() if j["status"] in ("done", "error")],
        key=lambda j: j.get("finished_at") or 0,
    )
    for j in finished[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(j["id"], None)


def create_job(kind: str, total: int = 0) -> str:
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {
        "id": job_id, "kind": kind, "status": "pending",
        "total": total, "done": 0, "message": "",
        "started_at": time.time(), "finished_at": None,
        "result": None, "error": "",
    }
    _prune()
    return job_id


def get_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def status_counts() -> dict:
    """Count tracked jobs by status (for capacity/backlog monitoring)."""
    counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
    for j in _JOBS.values():
        s = j.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
    return counts


def list_jobs(limit: int = 30) -> list[dict]:
    jobs = sorted(_JOBS.values(), key=lambda j: j.get("started_at") or 0, reverse=True)
    return jobs[:limit]


def update_job(job_id: str, **fields):
    j = _JOBS.get(job_id)
    if j:
        j.update(fields)


def _progress(job_id: str):
    """Return a callback the worker can call to report progress."""
    def cb(done: int = None, total: int = None, message: str = None):
        j = _JOBS.get(job_id)
        if not j:
            return
        if done is not None:
            j["done"] = done
        if total is not None:
            j["total"] = total
        if message is not None:
            j["message"] = message
    return cb


async def run_job(job_id: str, worker: Callable[..., Awaitable[Any]]):
    """Run `worker(progress_cb)` as a tracked background job."""
    j = _JOBS.get(job_id)
    if not j:
        return
    j["status"] = "running"
    try:
        result = await worker(_progress(job_id))
        j["status"] = "done"
        j["result"] = result
        if j["total"] and not j["done"]:
            j["done"] = j["total"]
    except Exception as e:
        logger.warning(f"job {job_id} ({j['kind']}) failed: {e}", exc_info=True)
        j["status"] = "error"
        j["error"] = str(e)[:300]
    finally:
        j["finished_at"] = time.time()


def spawn(kind: str, worker: Callable[..., Awaitable[Any]], total: int = 0) -> str:
    """Create + schedule a background job. Returns job_id immediately."""
    job_id = create_job(kind, total)
    asyncio.create_task(run_job(job_id, worker))
    return job_id
