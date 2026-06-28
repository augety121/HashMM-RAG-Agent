"""v17 Phase 81 — bounded background job queue (轴 F, scaling foundation).

``api/jobs.py`` tracks job status, but ``spawn`` fires **unbounded** asyncio
tasks — N concurrent ingests all hit BGE-M3 / the GPU at once, which is how you
get OOM / thrashing under load. This adds a **worker pool** on top of the same
status store:

- **Bounded concurrency + backpressure**: at most ``HASHMM_JOB_CONCURRENCY`` jobs
  run at once; the rest wait their turn (a semaphore), so a burst of submissions
  doesn't overwhelm the box.
- **Retry with exponential backoff** for transient failures (``HASHMM_JOB_MAX_RETRIES``).
- Reuses ``jobs.create_job/_progress/get_job`` so the existing ``/api/jobs`` UI
  keeps working — same status model (pending|running|done|error + progress).

Opt-in: this does **not** replace ``jobs.spawn`` (zero behavior change). Callers
that want bounded execution call ``get_job_queue().submit(...)`` instead. Async,
never crashes the loop (a worker exception becomes job status=error).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable

from hashmm.api import jobs
from hashmm.utils import get_logger

logger = get_logger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except Exception:
        return default


class JobQueue:
    def __init__(self, concurrency: int | None = None, max_retries: int | None = None,
                 base_backoff: float = 0.5, max_backoff: float = 30.0):
        self.concurrency = max(1, concurrency if concurrency is not None
                               else _int_env("HASHMM_JOB_CONCURRENCY", 2))
        self.max_retries = max_retries if max_retries is not None else _int_env("HASHMM_JOB_MAX_RETRIES", 0)
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._sem = asyncio.Semaphore(self.concurrency)
        self._tasks: set = set()
        self._active = 0
        self._peak = 0
        self._submitted = 0
        self._done = 0
        self._errors = 0
        self._retries = 0

    def _backoff(self, attempt: int) -> float:
        return min(self.max_backoff, self.base_backoff * (2 ** attempt))

    def submit(self, kind: str, worker: Callable[..., Awaitable[Any]], *,
               total: int = 0, retries: int | None = None) -> str:
        """Create a tracked job and schedule it under the concurrency cap.
        Returns the job_id immediately. ``worker(progress_cb)`` is the same
        convention as jobs.run_job."""
        job_id = jobs.create_job(kind, total)
        self._submitted += 1
        task = asyncio.create_task(self._run(job_id, worker, retries))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id

    async def _run(self, job_id: str, worker: Callable[..., Awaitable[Any]], retries: int | None):
        retries = self.max_retries if retries is None else max(0, retries)
        async with self._sem:                       # backpressure: ≤ concurrency at once
            self._active += 1
            self._peak = max(self._peak, self._active)
            try:
                attempts = retries + 1
                last_err: Exception | None = None
                for i in range(attempts):
                    try:
                        jobs.update_job(job_id, status="running")
                        result = await worker(jobs._progress(job_id))
                        jobs.update_job(job_id, status="done", result=result, finished_at=time.time())
                        self._done += 1
                        return
                    except Exception as e:
                        last_err = e
                        if i < attempts - 1:
                            self._retries += 1
                            jobs.update_job(job_id, message=f"重试中 {i + 1}/{retries}…")
                            try:
                                await asyncio.sleep(self._backoff(i))
                            except Exception:
                                pass
                            continue
                # all attempts exhausted
                logger.warning(f"job {job_id} failed after {attempts} attempt(s): {last_err}")
                jobs.update_job(job_id, status="error", error=str(last_err)[:300],
                                finished_at=time.time())
                self._errors += 1
            finally:
                self._active -= 1

    async def join(self):
        """Wait for all currently-submitted jobs to finish (tests / shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def stats(self) -> dict:
        return {
            "concurrency": self.concurrency,
            "max_retries": self.max_retries,
            "active": self._active,
            "peak_active": self._peak,
            "submitted": self._submitted,
            "done": self._done,
            "errors": self._errors,
            "retries": self._retries,
            "outstanding": len(self._tasks),
        }


# ── process-wide default queue (lazy; bound to the running loop) ──
_QUEUE: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = JobQueue()
    return _QUEUE


def reset_job_queue():
    """Tests: drop the singleton so a fresh queue binds to the current loop."""
    global _QUEUE
    _QUEUE = None


def current_stats() -> dict | None:
    """Stats of the existing queue, or None if no queue has been created yet.
    Does NOT create a queue (safe to call from a sync Prometheus scrape)."""
    return _QUEUE.stats() if _QUEUE is not None else None
