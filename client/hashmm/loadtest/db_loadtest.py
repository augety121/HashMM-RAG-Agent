"""v17 Phase 85 — database load-test harness (轴 F, Postgres scaling).

A small, dependency-light, **DB-agnostic** load generator: you give it a
zero-arg ``task_fn`` (one unit of work — e.g. "run this query") and it drives it
at a target concurrency, then reports throughput + latency percentiles + error
rate. Thread-based, so it works with blocking DB drivers (psycopg2).

- Harness core (``run_load_test`` + ``percentile``) is pure and fully testable
  with a stub or a real sqlite query — no Postgres needed to verify the mechanics.
- ``build_pg_task(dsn, sql)`` / ``build_sqlite_task(path, sql)`` are convenience
  factories; the PG one imports psycopg lazily so this module loads without it.

Real Postgres run is a one-liner on your box (see the changelog); the numbers
(p95/p99/QPS under N concurrency) only mean something against your real DB +
hardware, which is why the *target* is injectable and the *harness* is verified.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Callable

from hashmm.utils import get_logger

logger = get_logger(__name__)


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in [0,100]). Empty → 0.0."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    s = sorted(values)
    p = min(100.0, max(0.0, p))
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


@dataclass
class LoadTestResult:
    total: int
    ok: int
    errors: int
    duration_s: float
    qps: float
    latency_ms_mean: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    latency_ms_max: float
    error_rate: float
    concurrency: int

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def run_load_test(task_fn: Callable[[], object], *, n_requests: int = 100,
                  concurrency: int = 10, warmup: int = 0) -> LoadTestResult:
    """Drive ``task_fn`` ``n_requests`` times at ``concurrency`` parallel workers.

    A task that raises counts as an error (its latency is not recorded). Returns
    a LoadTestResult. Never raises for individual task failures."""
    n_requests = max(0, int(n_requests))
    concurrency = max(1, int(concurrency))

    def _timed() -> tuple[bool, float]:
        t0 = time.perf_counter()
        try:
            task_fn()
            return True, (time.perf_counter() - t0) * 1000.0
        except Exception as e:
            logger.debug(f"load-test task failed: {e}")
            return False, (time.perf_counter() - t0) * 1000.0

    # Warmup (not measured).
    for _ in range(max(0, warmup)):
        try:
            task_fn()
        except Exception:
            pass

    if n_requests == 0:
        return LoadTestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, concurrency)

    latencies: list[float] = []
    ok = 0
    errors = 0
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(_timed) for _ in range(n_requests)]
        for f in as_completed(futures):
            success, ms = f.result()
            if success:
                ok += 1
                latencies.append(ms)
            else:
                errors += 1
    duration = max(1e-9, time.perf_counter() - t_start)

    mean = sum(latencies) / len(latencies) if latencies else 0.0
    return LoadTestResult(
        total=n_requests, ok=ok, errors=errors,
        duration_s=duration, qps=ok / duration,
        latency_ms_mean=mean,
        latency_ms_p50=percentile(latencies, 50),
        latency_ms_p95=percentile(latencies, 95),
        latency_ms_p99=percentile(latencies, 99),
        latency_ms_max=max(latencies) if latencies else 0.0,
        error_rate=errors / n_requests,
        concurrency=concurrency,
    )


# ── target factories ────────────────────────────────────────────────────────
def build_sqlite_task(db_path: str, sql: str = "SELECT 1", params: tuple = ()) -> Callable[[], object]:
    """A task that opens a sqlite connection, runs sql, closes. (Useful for
    verifying the harness against a real DB driver without Postgres.)"""
    import sqlite3

    def _task():
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    return _task


def build_pg_task(dsn: str, sql: str = "SELECT 1", params: tuple = ()) -> Callable[[], object]:
    """A task that runs ``sql`` against Postgres via a fresh connection.

    psycopg (v3) or psycopg2 is imported lazily, so this module imports fine
    without a Postgres driver installed. For realistic numbers, point ``dsn`` at
    a pooled endpoint (PgBouncer) rather than opening a raw connection per call."""
    def _connect():
        try:
            import psycopg  # v3
            return psycopg.connect(dsn)
        except Exception:
            import psycopg2  # v2 fallback
            return psycopg2.connect(dsn)

    def _task():
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            try:
                cur.fetchall()
            except Exception:
                pass  # non-SELECT
            conn.commit()
        finally:
            conn.close()
    return _task
