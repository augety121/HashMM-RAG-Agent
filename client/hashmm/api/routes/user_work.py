"""Owner-facing workspace API shared by desktop and App.

This module is intentionally a projection over existing authoritative systems:
projects, conversations, WorkRuntime, scheduled tasks and execution devices.
It does not create a second task engine or let a client claim work succeeded.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from hashmm.api import database as db
from hashmm.api.auth import require_auth, require_conv_access
from hashmm.agent import work_runtime
from hashmm import scheduler


router = APIRouter(prefix="/api/user-work", tags=["user-work"])
_USER_ACTIONS = {"corpus_digest", "kg_health", "daily_brief", "ai_news_radar"}
_SCHEDULE_KINDS = {"interval", "daily"}
_RESULT_DESTINATIONS = {"conversation", "work_ledger"}


def _routine_public(row: dict) -> dict:
    try:
        params = json.loads(row.get("params") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        params = {}
    owner = str(row.get("tenant_id") or "")
    latest_run = (
        work_runtime.get_latest_run_by_source_prefix(
            f"schedule:{str(row.get('id') or '')}:", owner,
        )
        if owner else None
    )
    latest_snapshot = (
        dict(latest_run.get("snapshot") or {})
        if isinstance(latest_run, dict) else {}
    )
    handoff = (
        dict(latest_snapshot.get("long_horizon_handoff") or {})
        if isinstance(latest_snapshot.get("long_horizon_handoff"), dict) else {}
    )
    available_actions = (
        list((latest_run.get("control") or {}).get("available_actions") or [])
        if isinstance(latest_run, dict)
        and isinstance(latest_run.get("control"), dict) else []
    )
    latest_status = str((latest_run or {}).get("status") or "")
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or "")[:120],
        "action": str(row.get("action") or ""),
        "schedule_kind": str(row.get("schedule_kind") or "interval"),
        "interval_seconds": int(row.get("interval_seconds") or 3600),
        "daily_at": str(row.get("daily_at") or ""),
        "enabled": bool(row.get("enabled")),
        "last_run": float(row.get("last_run") or 0),
        "next_run": float(row.get("next_run") or 0),
        "last_status": str(row.get("last_status") or ""),
        "last_result": str(row.get("last_result") or "")[:500],
        "run_count": int(row.get("run_count") or 0),
        "conversation_id": str(params.get("conv_id") or ""),
        "timezone": str(params.get("timezone") or "UTC"),
        "result_destination": str(
            params.get("result_destination")
            or ("conversation" if params.get("conv_id") else "work_ledger")
        ),
        "permission_mode": "read_only",
        "created_at": float(row.get("created_at") or 0),
        "last_work_run_id": str((latest_run or {}).get("id") or ""),
        "last_work_status": latest_status,
        "resumable": (
            latest_status in {
                "queued", "running", "waiting_input", "waiting_approval", "blocked",
            }
            or any(action in {"resume", "retry"} for action in available_actions)
        ),
        "next_actions": [
            str(item)[:240] for item in list(handoff.get("next_actions") or [])[:6]
        ],
        "verification_status": str(
            (handoff.get("verification") or {}).get("status") or ""
        ) if isinstance(handoff.get("verification"), dict) else "",
    }


def _available_actions() -> list[dict]:
    installed = set(scheduler.list_actions())
    labels = {
        "corpus_digest": ("资料摘要", "汇总当前知识资料的可用规模"),
        "kg_health": ("知识关系检查", "检查知识关系网络是否可用"),
        "daily_brief": ("每日工作简报", "汇总最近对话、项目和工作状态"),
        "ai_news_radar": (
            "AI 热点候选雷达",
            "用当前账号的联网搜索收集最新候选线索，核验与发布仍回到原对话完成",
        ),
    }
    return [
        {"id": action, "name": labels[action][0], "description": labels[action][1]}
        for action in sorted(_USER_ACTIONS) if action in installed
    ]


def _schedule_input(body: dict, current: dict | None = None) -> tuple[str, int, str]:
    current = current or {}
    schedule_kind = str(
        body.get("schedule_kind", current.get("schedule_kind") or "daily")
    ).strip()
    if schedule_kind not in _SCHEDULE_KINDS:
        raise HTTPException(422, "未知的执行频率")
    try:
        interval = int(
            body.get("interval_seconds", current.get("interval_seconds") or 3600)
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "执行间隔必须是整数秒") from exc
    interval = max(300, min(interval, 31 * 86400))
    daily_at = str(
        body.get("daily_at", current.get("daily_at") or "09:00")
    ).strip()
    if schedule_kind == "daily":
        try:
            hour, minute = (int(part) for part in daily_at.split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
            daily_at = f"{hour:02d}:{minute:02d}"
        except (TypeError, ValueError):
            raise HTTPException(422, "每日时间必须是 HH:MM")
    return schedule_kind, interval, daily_at


def _preference_input(body: dict, params: dict | None = None) -> tuple[str, str]:
    params = params or {}
    timezone_name = str(
        body.get("timezone", params.get("timezone") or "UTC")
    ).strip()
    if not scheduler.valid_timezone_name(timezone_name):
        raise HTTPException(422, "时区必须是有效的 IANA 时区名称")
    destination = str(
        body.get(
            "result_destination",
            params.get("result_destination") or "work_ledger",
        )
    ).strip()
    if destination not in _RESULT_DESTINATIONS:
        raise HTTPException(422, "未知的结果去向")
    return timezone_name, destination


def _bind_conversation(
    request: Request,
    owner: str,
    conv_id: str,
    params: dict,
) -> None:
    params.pop("conv_id", None)
    params.pop("conv_owner_uid", None)
    if not conv_id:
        return
    conv = require_conv_access(request, conv_id)
    if str(conv.get("user_id") or "") != owner:
        raise HTTPException(404, "对话不存在")
    params.update({
        "conv_id": conv_id,
        "conv_owner_uid": str(conv.get("user_id") or ""),
    })


def _apply_result_destination(params: dict, destination: str) -> None:
    """Keep delivery bindings consistent with the user-visible destination."""
    params["result_destination"] = destination
    if destination != "conversation":
        params.pop("conv_id", None)
        params.pop("conv_owner_uid", None)


@router.get("/overview")
async def user_work_overview(request: Request, project_id: str = ""):
    """One bounded, owner-scoped snapshot for desktop/App cold start."""
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    selected_project = str(project_id or "").strip()
    if selected_project and not db.get_project_for_user(selected_project, owner):
        raise HTTPException(404, "项目不存在")
    feed = work_runtime.list_runs(
        owner,
        after_cursor=0,
        project_id=selected_project,
        include_terminal=True,
        limit=250,
    )
    routines = [_routine_public(item) for item in scheduler.list_tasks(owner)]
    try:
        from hashmm.agent import dispatch
        devices = dispatch.runners_status(
            owner=owner, created_by=str(user.get("sub") or "")
        )
        online_devices = sum(bool(item.get("online")) for item in devices)
    except Exception:
        online_devices = 0
    projects = db.list_projects(owner)
    payload = {
        "schema": "hashmm.user-workspace.v1",
        "generated_at": time.time(),
        "selected_project_id": selected_project,
        "projects": projects,
        "work": feed,
        "routines": {
            "schema": "hashmm.user-routines.v1",
            "available": scheduler.scheduler_enabled(),
            "actions": _available_actions(),
            "items": routines,
        },
        "continuity": {
            "online_devices": online_devices,
            "cross_device": online_devices > 0,
            "supported": True,
            "state": "ready" if online_devices > 0 else "waiting_device",
            "authoritative_source": "server",
        },
        "trust": {
            "schema": "hashmm.user-trust.v1",
            "account": "connected",
            "role": str(user.get("role") or "user"),
            "owner_isolation": True,
            "approval_policy": "runtime_scoped",
            "completion_requires_evidence": True,
        },
    }
    project_revision = sum(int(item.get("revision") or 0) for item in projects)
    routine_revision = sum(
        int(item.get("run_count") or 0) + int(bool(item.get("enabled")))
        for item in routines
    )
    etag = (
        f'"user-work-{owner}-{selected_project}-'
        f'{feed.get("high_water_cursor", 0)}-{project_revision}-'
        f'{routine_revision}-{online_devices}"'
    )
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={
                "Cache-Control": "private, no-cache",
                "Vary": "Authorization",
                "ETag": etag,
            },
        )
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers={
            "Cache-Control": "private, no-cache",
            "Vary": "Authorization",
            "ETag": etag,
        },
    )


@router.get("/routines")
async def list_user_routines(request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    return {
        "schema": "hashmm.user-routines.v1",
        "available": scheduler.scheduler_enabled(),
        "actions": _available_actions(),
        "items": [_routine_public(item) for item in scheduler.list_tasks(owner)],
    }


@router.post("/routines")
async def create_user_routine(request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    body = await request.json()
    action = str(body.get("action") or "").strip()
    if action not in _USER_ACTIONS or action not in scheduler.list_actions():
        raise HTTPException(422, "这个例行任务暂不可用")
    schedule_kind, interval, daily_at = _schedule_input(body)
    timezone_name, destination = _preference_input(body)
    params = {
        "owner_uid": owner,
        "timezone": timezone_name,
        "result_destination": destination,
    }
    conv_id = str(body.get("conversation_id") or "").strip()
    if destination == "conversation":
        _bind_conversation(request, owner, conv_id, params)
    _apply_result_destination(params, destination)
    if destination == "conversation" and not conv_id:
        raise HTTPException(422, "选择回到对话时必须先绑定一个对话")
    task = scheduler.create_task(
        action=action,
        name=str(body.get("name") or "").strip()[:120],
        params=params,
        schedule_kind=schedule_kind,
        interval_seconds=interval,
        daily_at=daily_at,
        tenant_id=owner,
        created_by=str(user.get("sub") or ""),
        allow_unattended=False,
    )
    db.audit(owner, str(user.get("sub") or ""), "create_user_routine", action)
    return {"ok": True, "task": _routine_public(task)}


@router.patch("/routines/{task_id}")
async def update_user_routine(task_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    current = scheduler.get_task_for_tenant(task_id, owner)
    if not current:
        raise HTTPException(404, "例行任务不存在")
    body = await request.json()
    try:
        params = json.loads(current.get("params") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        params = {}
    params["owner_uid"] = owner
    schedule_kind, interval, daily_at = _schedule_input(body, current)
    timezone_name, destination = _preference_input(body, params)
    params["timezone"] = timezone_name
    _apply_result_destination(params, destination)
    if destination == "conversation" and "conversation_id" in body:
        conv_id = str(body.get("conversation_id") or "").strip()
        _bind_conversation(request, owner, conv_id, params)
        _apply_result_destination(params, destination)
    if destination == "conversation" and not str(params.get("conv_id") or ""):
        raise HTTPException(422, "选择回到对话时必须先绑定一个对话")
    updated = scheduler.update_task_for_tenant(
        task_id,
        owner,
        name=str(body.get("name", current.get("name") or "")).strip()[:120],
        schedule_kind=schedule_kind,
        interval_seconds=interval,
        daily_at=daily_at,
        params=params,
    )
    if not updated:
        raise HTTPException(404, "例行任务不存在")
    db.audit(owner, str(user.get("sub") or ""), "update_user_routine", task_id)
    return {"ok": True, "task": _routine_public(updated)}


@router.post("/routines/{task_id}/run")
async def run_user_routine(task_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    result = await asyncio.to_thread(
        scheduler.run_task_now_for_tenant, task_id, owner
    )
    if result.get("error") == "task not found":
        raise HTTPException(404, "例行任务不存在")
    return result


@router.post("/routines/{task_id}/toggle")
async def toggle_user_routine(task_id: str, request: Request):
    user = require_auth(request)
    body = await request.json()
    ok = scheduler.set_enabled_for_tenant(
        task_id, str(user.get("uid") or ""), bool(body.get("enabled", True))
    )
    if not ok:
        raise HTTPException(404, "例行任务不存在")
    return {"ok": True}


@router.delete("/routines/{task_id}")
async def delete_user_routine(task_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    ok = scheduler.delete_task_for_tenant(task_id, owner)
    if not ok:
        raise HTTPException(404, "例行任务不存在")
    db.audit(owner, str(user.get("sub") or ""), "delete_user_routine", task_id)
    return {"ok": True}


@router.get("/search")
async def search_user_work(request: Request, q: str = "", limit: int = 30):
    """Search the caller's projects, conversations, messages and work ledger."""
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    query = str(q or "").strip()
    if len(query) < 2:
        return {"schema": "hashmm.user-search.v1", "query": query, "items": []}
    limit = max(1, min(int(limit or 30), 60))
    pattern = f"%{query}%"
    items: list[dict] = []
    with db._conn() as conn:
        for row in conn.execute(
            """SELECT id,name,goal,deliverable,updated_at FROM projects
               WHERE user_id=? AND archived=0
               AND (name LIKE ? OR goal LIKE ? OR deliverable LIKE ?)
               ORDER BY updated_at DESC LIMIT ?""",
            (owner, pattern, pattern, pattern, limit),
        ).fetchall():
            item = dict(row)
            items.append({
                "kind": "project", "id": item["id"], "title": item["name"],
                "snippet": item.get("goal") or item.get("deliverable") or "",
                "updated_at": float(item.get("updated_at") or 0),
            })
        remaining = max(0, limit - len(items))
        if remaining:
            for row in conn.execute(
                """SELECT c.id,c.title,c.updated_at,
                          COALESCE((
                            SELECT m.content FROM messages m
                            WHERE m.conv_id=c.id AND m.content LIKE ?
                            ORDER BY m.created_at DESC LIMIT 1
                          ),'') AS snippet
                   FROM conversations c
                   WHERE c.user_id=? AND (
                     c.title LIKE ? OR EXISTS (
                       SELECT 1 FROM messages m
                       WHERE m.conv_id=c.id AND m.content LIKE ?
                     ))
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (pattern, owner, pattern, pattern, remaining),
            ).fetchall():
                item = dict(row)
                items.append({
                    "kind": "conversation", "id": item["id"],
                    "title": item.get("title") or "对话",
                    "snippet": str(item.get("snippet") or "")[:320],
                    "updated_at": float(item.get("updated_at") or 0),
                })
        remaining = max(0, limit - len(items))
        if remaining:
            for row in conn.execute(
                """SELECT id,title,status,snapshot_json,updated_at
                   FROM work_runs
                   WHERE user_id=? AND (title LIKE ? OR snapshot_json LIKE ?)
                   ORDER BY updated_at DESC LIMIT ?""",
                (owner, pattern, pattern, remaining),
            ).fetchall():
                item = dict(row)
                items.append({
                    "kind": "work", "id": item["id"],
                    "title": item.get("title") or "工作",
                    "snippet": f"状态：{item.get('status') or 'unknown'}",
                    "updated_at": float(item.get("updated_at") or 0),
                })
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return {
        "schema": "hashmm.user-search.v1",
        "query": query,
        "items": items[:limit],
    }
