"""Proactive services — persistent scheduled tasks (Marvis-style).

Until now the assistant was purely reactive: it answered when asked. Proactive
services let it run work on a schedule — e.g. "every morning summarise documents
added to the knowledge base in the last 24h", "every hour check KG health". This
is what separates an assistant from an ops layer (Marvis's headline feature).

Design (deterministic + safe, matching the rest of the system):
  - **Persistent.** Tasks live in the `scheduled_tasks` table, so they survive
    restarts. Each has an action, params, a schedule (interval or daily-at), an
    owning tenant, and run bookkeeping (last_run/next_run/status).
  - **Pluggable actions.** An action is a registered handler `fn(params) -> str`.
    Built-ins are read-only/safe (corpus digest, KG health). New actions register
    via `register_action`.
  - **Opt-in loop.** The background tick loop only runs when
    HASHMM_SCHEDULER=1, so default deployments are unaffected. The loop is a
    single asyncio task that wakes periodically, runs due tasks, reschedules.
  - **Safe execution.** Actions run through the same observability + (if they use
    tools) the hook boundary. Failures are caught per-task; one bad task never
    stops the loop.

This module owns scheduling/persistence; actions own the work. Deterministic code
owns time and storage (never the model).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.scheduler")


def scheduler_enabled() -> bool:
    return os.environ.get("HASHMM_SCHEDULER", "0") == "1"


# ── Action registry ─────────────────────────────────────────────────
_ACTIONS: dict[str, Callable[[dict], str]] = {}
# Whether each action has side effects (writes/sends/deletes/external). A
# side-effecting action must NOT run unattended on a schedule unless the task is
# explicitly created with allow_unattended=True — the cc/Marvis safety line:
# don't let a timer do irreversible work with no human in the loop.
_ACTION_SIDE_EFFECT: dict[str, bool] = {}


def register_action(name: str, fn: Callable[[dict], str], side_effect: bool = False) -> None:
    _ACTIONS[name] = fn
    _ACTION_SIDE_EFFECT[name] = bool(side_effect)


def action_has_side_effect(name: str) -> bool:
    return _ACTION_SIDE_EFFECT.get(name, False)


def list_actions() -> list[str]:
    return sorted(_ACTIONS.keys())


# ── Schedule math ───────────────────────────────────────────────────

def _compute_next_run(kind: str, interval_seconds: int, daily_at: str, now: float | None = None) -> float:
    """Next run timestamp. interval → now+interval; daily → next HH:MM."""
    now = now if now is not None else time.time()
    if kind == "daily" and daily_at:
        try:
            hh, mm = (int(x) for x in daily_at.split(":"))
            lt = time.localtime(now)
            target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0,
                                  lt.tm_wday, lt.tm_yday, lt.tm_isdst))
            if target <= now:
                target += 86400  # tomorrow
            return target
        except Exception as _e:
            log_suppressed(logger, _e)
    return now + max(60, int(interval_seconds or 3600))


# ── CRUD (persistent) ───────────────────────────────────────────────

def create_task(*, action: str, name: str = "", params: dict | None = None,
                schedule_kind: str = "interval", interval_seconds: int = 3600,
                daily_at: str = "", tenant_id: str = "default",
                created_by: str = "", allow_unattended: bool = False, db=None) -> dict:
    """Create a scheduled task. Returns the row. Validates the action exists.

    Safety: a side-effecting action (writes/sends/deletes) can only be scheduled
    unattended when ``allow_unattended=True`` is passed explicitly — otherwise we
    refuse, so a timer never silently does irreversible work (cc/Marvis line)."""
    if db is None:
        from hashmm.api import database as db
    if action not in _ACTIONS:
        raise ValueError(f"unknown action '{action}' (known: {list_actions()})")
    if action_has_side_effect(action) and not allow_unattended:
        raise ValueError(
            f"action '{action}' 有副作用，定时无人值守运行需显式 allow_unattended=True "
            f"（不可逆操作不应无人确认地定时执行）")
    tid = uuid.uuid4().hex[:12]
    now = time.time()
    nxt = _compute_next_run(schedule_kind, interval_seconds, daily_at, now)
    try:
        with db._conn() as c:
            c.execute("""INSERT INTO scheduled_tasks
                (id, name, action, params, schedule_kind, interval_seconds, daily_at,
                 tenant_id, created_by, enabled, next_run, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
                (tid, name or action, action, json.dumps(params or {}), schedule_kind,
                 int(interval_seconds), daily_at, tenant_id, created_by, nxt, now))
    except Exception as _e:
        log_suppressed(logger, _e)
    return get_task(tid, db=db)


def get_task(task_id: str, db=None) -> dict:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            r = c.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else {}
    except Exception as _e:
        log_suppressed(logger, _e)
        return {}


def list_tasks(tenant_id: str | None = None, db=None) -> list[dict]:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            if tenant_id:
                rows = c.execute("SELECT * FROM scheduled_tasks WHERE tenant_id=? ORDER BY created_at DESC",
                                 (tenant_id,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    except Exception as _e:
        log_suppressed(logger, _e)
        return []


def set_enabled(task_id: str, enabled: bool, db=None) -> bool:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            c.execute("UPDATE scheduled_tasks SET enabled=? WHERE id=?", (1 if enabled else 0, task_id))
        return True
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


def delete_task(task_id: str, db=None) -> bool:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            c.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        return True
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


# ── Execution ───────────────────────────────────────────────────────

def run_task_now(task_id: str, db=None) -> dict:
    """Execute a task's action immediately (manual trigger / scheduler tick).
    Records status/result and reschedules next_run. Never raises."""
    if db is None:
        from hashmm.api import database as db
    t = get_task(task_id, db=db)
    if not t:
        return {"ok": False, "error": "task not found"}
    action = _ACTIONS.get(t["action"])
    if not action:
        return {"ok": False, "error": f"unknown action {t['action']}"}
    now = time.time()
    status, result = "ok", ""
    try:
        params = json.loads(t.get("params") or "{}")
    except Exception:
        params = {}
    try:
        result = str(action(params))[:2000]
    except Exception as e:
        status = "error"
        result = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning(f"scheduled task {task_id} ({t['action']}) failed: {result}")
    nxt = _compute_next_run(t["schedule_kind"], t["interval_seconds"], t.get("daily_at", ""), now)
    try:
        with db._conn() as c:
            c.execute("""UPDATE scheduled_tasks SET last_run=?, next_run=?, last_status=?,
                         last_result=?, run_count=run_count+1 WHERE id=?""",
                      (now, nxt, status, result, task_id))
    except Exception as _e:
        log_suppressed(logger, _e)
    # observability
    try:
        from hashmm import observability as obs
        if hasattr(obs, "record_scheduled_run"):
            obs.record_scheduled_run(t["action"], status)
    except Exception as _e:
        log_suppressed(logger, _e)
    return {"ok": status == "ok", "status": status, "result": result, "next_run": nxt}


def due_tasks(now: float | None = None, db=None) -> list[dict]:
    """Tasks that are enabled and due (next_run <= now)."""
    if db is None:
        from hashmm.api import database as db
    now = now if now is not None else time.time()
    try:
        with db._conn() as c:
            rows = c.execute("SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run<=? AND next_run>0",
                             (now,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as _e:
        log_suppressed(logger, _e)
        return []


# ── Background loop ──────────────────────────────────────────────────
_loop_task: asyncio.Task | None = None


async def _scheduler_loop(tick_seconds: int = 60):
    logger.info("[Scheduler] background loop started")
    while True:
        try:
            for t in due_tasks():
                logger.info(f"[Scheduler] running due task {t['id']} ({t['action']})")
                await asyncio.to_thread(run_task_now, t["id"])
        except Exception as _e:
            log_suppressed(logger, _e)
        await asyncio.sleep(tick_seconds)


def start_scheduler() -> bool:
    """Start the background loop if enabled and not already running. Call from
    server startup. No-op when HASHMM_SCHEDULER != 1."""
    global _loop_task
    if not scheduler_enabled():
        return False
    if _loop_task and not _loop_task.done():
        return True
    try:
        _loop_task = asyncio.create_task(_scheduler_loop())
        return True
    except RuntimeError:
        # no running loop yet (called too early) — caller can retry in startup
        return False


# ── Built-in actions (read-only, safe) ──────────────────────────────

def _action_corpus_digest(params: dict) -> str:
    """Summarise documents/vectors currently in the corpus (a daily 'what's in
    the KB' digest). Read-only."""
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        n_vec = getattr(ServiceRegistry, "n_vectors", 0) or 0
        n_doc = getattr(ServiceRegistry, "n_docs", 0) or 0
        return f"语料库现状：{n_vec} 向量 / {n_doc} 文档"
    except Exception as e:
        return f"digest unavailable: {type(e).__name__}"


def _action_kg_health(params: dict) -> str:
    """Report KG size/health. Read-only."""
    try:
        from hashmm.kg.kg_retriever import get_kg_retriever
        kr = get_kg_retriever()
        if not getattr(kr, "is_available", False):
            return "KG 未加载"
        return "KG 健康：已加载"
    except Exception as e:
        return f"kg health unavailable: {type(e).__name__}"


def install_default_actions() -> None:
    register_action("corpus_digest", _action_corpus_digest)
    register_action("kg_health", _action_kg_health)
    # V103.31: 自发现 —— 注册成可调度的主动 action（从定时任务起步让 Agent 自找活）。
    try:
        from hashmm.agent.discovery import action_discovery
        register_action("discovery", action_discovery)
    except Exception:
        pass


install_default_actions()
