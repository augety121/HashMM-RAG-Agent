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
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time
import uuid
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

def valid_timezone_name(value: str) -> bool:
    """Whether ``value`` is a real IANA timezone understood by this runtime."""
    try:
        ZoneInfo(str(value or "").strip())
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _compute_next_run(
    kind: str,
    interval_seconds: int,
    daily_at: str,
    now: float | None = None,
    timezone_name: str = "",
) -> float:
    """Return the next occurrence in UTC seconds.

    Daily schedules are interpreted in the user's IANA timezone and converted
    back to UTC.  ``timedelta(days=1)`` is intentionally applied to a local
    calendar date, so daylight-saving changes do not silently shift the wall
    clock time.  Legacy tasks without a timezone retain server-local behavior.
    """
    now = now if now is not None else time.time()
    if kind == "daily" and daily_at:
        try:
            hh, mm = (int(x) for x in daily_at.split(":"))
            if not 0 <= hh <= 23 or not 0 <= mm <= 59:
                raise ValueError("invalid daily time")
            zone_name = str(timezone_name or "").strip()
            if zone_name:
                zone = ZoneInfo(zone_name)
                current = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(zone)
                target = current.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if target.timestamp() <= now:
                    target = (target + timedelta(days=1)).replace(
                        hour=hh, minute=mm, second=0, microsecond=0,
                    )
                return target.astimezone(timezone.utc).timestamp()
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
    task_params = dict(params or {})
    nxt = _compute_next_run(
        schedule_kind,
        interval_seconds,
        daily_at,
        now,
        str(task_params.get("timezone") or ""),
    )
    try:
        with db._conn() as c:
            c.execute("""INSERT INTO scheduled_tasks
                (id, name, action, params, schedule_kind, interval_seconds, daily_at,
                 tenant_id, created_by, enabled, next_run, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
                (tid, name or action, action, json.dumps(task_params), schedule_kind,
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


def get_task_for_tenant(task_id: str, tenant_id: str, db=None) -> dict:
    """Read one routine only inside the authenticated owner's namespace."""
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            row = c.execute(
                "SELECT * FROM scheduled_tasks WHERE id=? AND tenant_id=?",
                (str(task_id or ""), str(tenant_id or "")),
            ).fetchone()
        return dict(row) if row else {}
    except Exception as _e:
        log_suppressed(logger, _e)
        return {}


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


def set_enabled_for_tenant(
    task_id: str, tenant_id: str, enabled: bool, db=None
) -> bool:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            changed = c.execute(
                "UPDATE scheduled_tasks SET enabled=? WHERE id=? AND tenant_id=?",
                (1 if enabled else 0, str(task_id or ""), str(tenant_id or "")),
            )
        return changed.rowcount == 1
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


def update_task_for_tenant(
    task_id: str,
    tenant_id: str,
    *,
    name: str,
    schedule_kind: str,
    interval_seconds: int,
    daily_at: str,
    params: dict,
    db=None,
) -> dict:
    """Update one owner's routine without widening its action or ownership.

    The action is deliberately immutable in the ordinary user API.  Replacing
    an action is semantically a new automation and should produce a new audit
    trail instead of rewriting historical runs.
    """
    if db is None:
        from hashmm.api import database as db
    current = get_task_for_tenant(task_id, tenant_id, db=db)
    if not current:
        return {}
    now = time.time()
    next_run = _compute_next_run(
        schedule_kind,
        interval_seconds,
        daily_at,
        now,
        str((params or {}).get("timezone") or ""),
    )
    try:
        with db._conn() as c:
            changed = c.execute(
                """UPDATE scheduled_tasks
                   SET name=?, params=?, schedule_kind=?, interval_seconds=?,
                       daily_at=?, next_run=?
                   WHERE id=? AND tenant_id=?""",
                (
                    str(name or current.get("name") or current.get("action") or "")[:120],
                    json.dumps(params or {}),
                    schedule_kind,
                    int(interval_seconds),
                    daily_at,
                    next_run,
                    str(task_id or ""),
                    str(tenant_id or ""),
                ),
            )
        return (
            get_task_for_tenant(task_id, tenant_id, db=db)
            if changed.rowcount == 1 else {}
        )
    except Exception as _e:
        log_suppressed(logger, _e)
        return {}


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


def delete_task_for_tenant(task_id: str, tenant_id: str, db=None) -> bool:
    if db is None:
        from hashmm.api import database as db
    try:
        with db._conn() as c:
            changed = c.execute(
                "DELETE FROM scheduled_tasks WHERE id=? AND tenant_id=?",
                (str(task_id or ""), str(tenant_id or "")),
            )
        return changed.rowcount == 1
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


# ── Execution ───────────────────────────────────────────────────────

def _begin_work_run(task: dict, params: dict, *, trigger: str, occurrence_key: str, db):
    """Bind one schedule occurrence to the same durable Work runtime as Chat."""
    try:
        from hashmm.agent import work_runtime
        # Tests and third-party adapters may inject a minimal DB facade.  The
        # bundled runtime shares the authoritative database module and is the
        # only place where the Work ledger can be updated atomically.
        if getattr(work_runtime, "db", None) is not db:
            return None
        owner = str(
            task.get("tenant_id") or params.get("owner_uid")
            or params.get("conv_owner_uid") or ""
        ).strip()
        if not owner:
            return None
        occurrence = str(occurrence_key or "").strip() or uuid.uuid4().hex
        source_id = f"schedule:{task.get('id')}:{occurrence}"[:180]
        name = str(task.get("name") or task.get("action") or "自动任务")[:120]
        goal = f"按既定安排执行「{name}」，保留运行证据，并把结果送到约定位置。"
        from hashmm.agent.task_method import build_task_contract
        contract = build_task_contract(
            user_goal=goal,
            task_type="scheduled_long_task",
            execution_mode="scheduled_long_task",
            evidence_expected=True,
            requires_plan=True,
            plan_items=[
                {
                    "id": "schedule-scope",
                    "text": "确认账号、对话、动作和无人值守权限边界",
                    "status": "completed",
                },
                {
                    "id": "schedule-execute",
                    "text": "执行已配置的自动任务动作",
                    "status": "pending",
                },
                {
                    "id": "schedule-verify",
                    "text": "记录确定性结果或错误证据",
                    "status": "pending",
                },
                {
                    "id": "schedule-deliver",
                    "text": "把结果写入运行记录和约定对话",
                    "status": "pending",
                },
            ],
        )
        run = work_runtime.create_run(
            user_id=owner,
            kind="workflow",
            source_id=source_id,
            conv_id=str(params.get("conv_id") or ""),
            title=name,
            status="queued",
            snapshot={
                "goal": goal,
                "task_type": "scheduled_long_task",
                "run_manifest": {
                    "execution_mode": "scheduled_long_task",
                    "task_contract": contract,
                },
                "automation": {
                    "task_id": str(task.get("id") or ""),
                    "action": str(task.get("action") or ""),
                    "trigger": "scheduled" if trigger == "scheduled" else "manual",
                    "occurrence": occurrence[:80],
                    "result_destination": (
                        "conversation" if params.get("conv_id") else "work_ledger"
                    ),
                },
            },
        )
        return run
    except Exception as exc:
        logger.warning(
            "scheduled task %s could not enter Work runtime: %s",
            task.get("id"), type(exc).__name__,
        )
        return None


def _append_work_event(run: dict | None, *, event_type: str, status: str,
                       summary: str, payload: dict, idempotency_key: str) -> dict | None:
    if not run:
        return None
    try:
        from hashmm.agent import work_runtime
        result = work_runtime.append_event_once(
            str(run.get("id") or ""),
            user_id=str(run.get("user_id") or ""),
            event_type=event_type,
            status=status,
            summary=summary,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return result.get("run") if result.get("state") in {"applied", "duplicate"} else None
    except Exception as exc:
        logger.warning(
            "scheduled work event %s was not persisted: %s",
            event_type, type(exc).__name__,
        )
        return None


def run_task_now(task_id: str, db=None, *, trigger: str = "manual",
                 occurrence_key: str = "") -> dict:
    """Execute a task's action immediately (manual trigger / scheduler tick).
    Every occurrence is also an owner-bound WorkRun, so it can be inspected and
    resumed by Chat, desktop and App. Records status/result and reschedules.
    Never raises."""
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
    if trigger == "scheduled" and not occurrence_key:
        occurrence_key = str(int(float(t.get("next_run") or now)))
    occurrence = str(occurrence_key or uuid.uuid4().hex)[:80]
    work_run = _begin_work_run(
        t, params, trigger=trigger, occurrence_key=occurrence, db=db,
    )
    # A scheduler retry for an already completed due slot is convergent: do not
    # repeat even a currently read-only action just because two ticks raced.
    if (
        trigger == "scheduled"
        and work_run
        and work_run.get("status") in {"completed", "observed", "cancelled"}
    ):
        return {
            "ok": work_run.get("status") == "completed",
            "status": str(work_run.get("status") or "completed"),
            "result": str(t.get("last_result") or "")[:2000],
            "next_run": float(t.get("next_run") or 0),
            "work_run_id": str(work_run.get("id") or ""),
            "duplicate": True,
        }
    started = _append_work_event(
        work_run,
        event_type="automation_started",
        status="running",
        summary=f"开始执行「{str(t.get('name') or t.get('action') or '自动任务')[:120]}」",
        payload={
            "source": "scheduler",
            "trigger": "scheduled" if trigger == "scheduled" else "manual",
            "step_id": "schedule-execute",
        },
        idempotency_key=f"{occurrence}:started",
    )
    if started:
        work_run = started
    try:
        result = str(action(params))[:2000]
    except Exception as e:
        status = "error"
        result = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning(f"scheduled task {task_id} ({t['action']}) failed: {result}")
    action_ok = status == "ok"
    nxt = _compute_next_run(
        t["schedule_kind"],
        t["interval_seconds"],
        t.get("daily_at", ""),
        now,
        str(params.get("timezone") or ""),
    )
    try:
        with db._conn() as c:
            c.execute("""UPDATE scheduled_tasks SET last_run=?, next_run=?, last_status=?,
                         last_result=?, run_count=run_count+1 WHERE id=?""",
                      (now, nxt, status, result, task_id))
    except Exception as _e:
        log_suppressed(logger, _e)
    # 主动服务不是孤立日志：如果创建任务时绑定了会话，把有界结果投递回原 Chat。
    # conv_owner_uid 是创建 API 在完成 owner check 后写入的快照；执行时必须再次匹配，
    # 防止会话被删除后同 ID 复用造成跨用户投递。
    conv_id = str(params.get("conv_id") or "").strip()
    expected_owner = str(params.get("conv_owner_uid") or "").strip()
    result_destination = str(
        params.get("result_destination") or (
            "conversation" if conv_id else "work_ledger"
        )
    ).strip()
    deliver_to_conversation = result_destination == "conversation" and bool(conv_id)
    delivery_ok = not deliver_to_conversation
    delivery_summary = "结果已写入任务运行记录"
    if deliver_to_conversation and expected_owner:
        try:
            conv = db.get_conversation(conv_id)
            if conv and str(conv.get("user_id") or "") == expected_owner:
                task_name = str(t.get("name") or t.get("action") or "定时任务")[:120]
                status_text = "完成" if status == "ok" else "失败"
                db.create_message(
                    conv_id,
                    "assistant",
                    (
                        f"自动任务「{task_name}」{status_text}\n\n{result}"
                        + (
                            f"\n\n运行记录：{work_run.get('id')}"
                            if work_run and work_run.get("id") else ""
                        )
                    ),
                    status="complete",
                )
                delivery_ok = True
                delivery_summary = "结果已写入运行记录并送回原对话"
            elif conv:
                logger.warning("scheduled task %s skipped Chat delivery: owner changed", task_id)
                delivery_summary = "对话归属已变化，结果仅保留在运行记录"
            else:
                delivery_summary = "原对话已不存在，结果仅保留在运行记录"
        except Exception as _e:
            log_suppressed(logger, _e)
            delivery_summary = "结果已保留在运行记录，但送回原对话失败"
    elif deliver_to_conversation:
        delivery_summary = "缺少已验证的对话所有者，结果仅保留在运行记录"

    overall_ok = action_ok and delivery_ok
    if action_ok and not delivery_ok:
        status = "error"
        result = (result + f"\n\n投递未完成：{delivery_summary}")[:2000]
        try:
            with db._conn() as c:
                c.execute(
                    "UPDATE scheduled_tasks SET last_status=?, last_result=? WHERE id=?",
                    (status, result, task_id),
                )
        except Exception as _e:
            log_suppressed(logger, _e)

    result_run = _append_work_event(
        work_run,
        event_type="automation_result",
        status="running" if action_ok else "failed",
        summary=(
            "自动任务动作已返回确定性结果"
            if action_ok else "自动任务失败，已保留错误类型供续接"
        ),
        payload={
            "source": "scheduler_action",
            "verification_status": "passed" if action_ok else "failed",
            "result_status": "ok" if action_ok else "error",
            "step_id": "schedule-execute",
        },
        idempotency_key=f"{occurrence}:result",
    )
    if result_run:
        work_run = result_run
    if action_ok:
        verified_run = _append_work_event(
            work_run,
            event_type="verification",
            status="running",
            summary="任务动作已返回且结果已持久化",
            payload={
                "source": "scheduler",
                "verification_status": "passed",
                "step_id": "schedule-verify",
            },
            idempotency_key=f"{occurrence}:verified",
        )
        if verified_run:
            work_run = verified_run
        delivered_run = _append_work_event(
            work_run,
            event_type="step_done",
            status="completed" if delivery_ok else "failed",
            summary=delivery_summary,
            payload={
                "source": "scheduler_delivery",
                "verification_status": "passed" if delivery_ok else "failed",
                "step_id": "schedule-deliver",
                "destination": (
                    "conversation" if deliver_to_conversation else "work_ledger"
                ),
            },
            idempotency_key=f"{occurrence}:delivered",
        )
        if delivered_run:
            work_run = delivered_run
    # observability
    try:
        from hashmm import observability as obs
        if hasattr(obs, "record_scheduled_run"):
            obs.record_scheduled_run(t["action"], status)
    except Exception as _e:
        log_suppressed(logger, _e)
    available_actions = (
        list((work_run.get("control") or {}).get("available_actions") or [])
        if isinstance(work_run, dict)
        and isinstance(work_run.get("control"), dict) else []
    )
    work_status = str((work_run or {}).get("status") or "")
    return {
        "ok": overall_ok,
        "status": status,
        "result": result,
        "next_run": nxt,
        "work_run_id": str((work_run or {}).get("id") or ""),
        "resumable": (
            work_status in {
                "queued", "running", "waiting_input", "waiting_approval", "blocked",
            }
            or any(action in {"resume", "retry"} for action in available_actions)
        ),
    }


def run_task_now_for_tenant(task_id: str, tenant_id: str, db=None) -> dict:
    """Run a routine only after an owner-scoped lookup.

    Returning the same not-found shape for missing and foreign rows avoids
    turning task ids into a cross-account enumeration channel.
    """
    task = get_task_for_tenant(task_id, tenant_id, db=db)
    if not task:
        return {"ok": False, "error": "task not found"}
    return run_task_now(task_id, db=db)


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
                await asyncio.to_thread(
                    run_task_now,
                    t["id"],
                    trigger="scheduled",
                    occurrence_key=str(int(float(t.get("next_run") or time.time()))),
                )
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


def _action_daily_brief(params: dict) -> str:
    """Build a deterministic owner-scoped brief from persisted state."""
    owner = str(params.get("owner_uid") or "").strip()
    if not owner:
        return "没有可用的账号范围，未生成简报"
    try:
        from hashmm.api import database as db
        conversations = db.list_conversations(owner, limit=5)
        projects = db.list_projects(owner)[:5]
        with db._conn() as conn:
            rows = conn.execute(
                """SELECT title,status,updated_at FROM work_runs
                   WHERE user_id=? ORDER BY updated_at DESC LIMIT 5""",
                (owner,),
            ).fetchall()
        active = [
            dict(row) for row in rows
            if str(row["status"]) not in {"completed", "observed", "cancelled"}
        ]
        parts = [
            f"最近对话 {len(conversations)} 条",
            f"进行中的项目 {sum(str(p.get('status')) == 'active' for p in projects)} 个",
            f"需要继续关注的工作 {len(active)} 项",
        ]
        if active:
            parts.append(
                "最近工作：" + "、".join(str(item.get("title") or "未命名")[:40]
                                      for item in active[:3])
            )
        return "\n".join(parts)
    except Exception as exc:
        return f"简报暂不可用：{type(exc).__name__}"


def _action_ai_news_radar(params: dict) -> str:
    """Collect owner-scoped AI-news candidates without claiming publication.

    The result is deliberately a research inbox rather than a finished article:
    search snippets are untrusted leads and still need opening and primary-source
    verification through the ``AI 公众号简报工作室`` skill.
    """
    owner = str(params.get("owner_uid") or "").strip()
    if not owner:
        return "没有可用的账号范围，未执行 AI 热点雷达"
    now = datetime.now(timezone.utc)
    date_label = now.strftime("%Y-%m-%d")
    queries = (
        f"{date_label} site:openai.com OR site:anthropic.com AI announcement",
        f"{date_label} site:ai.googleblog.com OR site:deepmind.google AI release",
        f"{date_label} 人工智能 模型 发布 官方 公告",
    )
    sections: list[str] = []
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    def add_candidates(raw: object, query: str) -> None:
        """Normalise search leads without promoting snippets to facts."""
        rows: list[dict] = []
        if isinstance(raw, dict):
            value = raw.get("items") or raw.get("results") or raw.get("data")
            if isinstance(value, list):
                rows = [item for item in value if isinstance(item, dict)]
        elif isinstance(raw, list):
            rows = [item for item in raw if isinstance(item, dict)]
        text = str(raw or "")
        urls = re.findall(r"https?://[^\s)<>\]\"']+", text)
        if not rows and urls:
            rows = [{"url": url} for url in urls]
        for row in rows[:8]:
            url = str(row.get("url") or row.get("link") or "").strip().rstrip(".,;")
            title = " ".join(str(row.get("title") or row.get("name") or "").split())[:180]
            if not title and url:
                title = url.split("//", 1)[-1].split("/", 1)[0]
            key_url = url.casefold()
            key_title = title.casefold()
            if (not url and not title) or (key_url and key_url in seen_urls) or (key_title and key_title in seen_titles):
                continue
            if key_url:
                seen_urls.add(key_url)
            if key_title:
                seen_titles.add(key_title)
            host = url.split("//", 1)[1].split("/", 1)[0].lower() if "://" in url else ""
            official = any(host == domain or host.endswith("." + domain) for domain in (
                "openai.com", "anthropic.com", "deepmind.google", "ai.googleblog.com",
                "github.com", "huggingface.co", "arxiv.org",
            ))
            candidates.append({
                "title": title or "未命名候选",
                "source_url": url or "（搜索结果未提供原始 URL）",
                "publisher": host or "搜索服务",
                "status": "待核验",
                "confidence": "中" if official else "低",
                "source_quality": "一手来源候选" if official else "二手/未知来源候选",
                "query": query[:160],
            })
    try:
        from hashmm.api.tool_registry import execute_tool
        for query in queries:
            result = execute_tool(
                "web_search",
                {"query": query, "num_results": 5},
                {
                    "user_id": owner,
                    "uid": owner,
                    "source": "scheduled_ai_news_radar",
                    "read_only": True,
                },
            )
            sections.append(f"### 检索：{query}\n{str(result)[:6000]}")
    except Exception as exc:
        return f"AI 热点雷达暂不可用：{type(exc).__name__}"
    for section in sections:
        add_candidates(section, "scheduled search")
    structured = [
        "## 去重后的候选（均未核验）",
        *[
            f"- {item['title']}｜{item['status']}｜置信度 {item['confidence']}｜{item['source_quality']}\n"
            f"  来源：{item['source_url']}（{item['publisher']}）\n"
            f"  检索式：{item['query']}；事件日期、正文事实和影响仍需打开原文核对。"
            for item in candidates[:24]
        ],
    ]
    if not candidates:
        structured.append("- 本轮没有提取到带原始 URL 的候选；请检查搜索服务配置后再运行。")
    return "\n".join([
        f"# AI 热点候选雷达（{date_label} UTC）",
        "",
        "以下内容是搜索服务返回的候选线索，不是已核验新闻，也不会自动发布。",
        "请在原对话中使用“AI 公众号简报工作室”打开原文、核对发布时间并补齐来源后再成稿。",
        "",
        *structured,
        "",
        *sections,
    ])


def install_default_actions() -> None:
    register_action("corpus_digest", _action_corpus_digest)
    register_action("kg_health", _action_kg_health)
    register_action("daily_brief", _action_daily_brief)
    register_action("ai_news_radar", _action_ai_news_radar)
    # V103.31: 自发现 —— 注册成可调度的主动 action（从定时任务起步让 Agent 自找活）。
    try:
        from hashmm.agent.discovery import action_discovery
        register_action("discovery", action_discovery)
    except Exception:
        pass


install_default_actions()
