"""Authenticated API for Agent Retrieval Fabric 1.0."""
from __future__ import annotations

import anyio
import asyncio
import time
from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.retrieval_fabric import SearchRequest, get_retrieval_fabric
from hashmm.retrieval_fabric import store

router = APIRouter(prefix="/api/v1/search", tags=["retrieval-fabric"])


def _identity(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    owner = str(user.get("uid") or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="登录状态无效")
    return user, owner


@router.get("/providers")
async def provider_health(request: Request):
    _, owner = _identity(request)
    items = get_retrieval_fabric().provider_status(owner)
    # Bing Search APIs were retired; publish the state so clients do not keep
    # presenting an unusable credential field.
    items.append({"provider": "bing", "label": "Bing Search API", "kind": "retired",
                  "configured": False, "retired": True, "retired_at": "2025-08-11"})
    return {"protocol": "agent-retrieval-fabric/1.0", "providers": items}


@router.post("")
async def create_search_run(request: Request):
    user, owner = _identity(request)
    try:
        body = await request.json()
        spec = SearchRequest(
            query=body.get("query", ""), mode=body.get("mode", "verified"),
            max_results=body.get("max_results", 8), providers=body.get("providers") or [],
            freshness_days=body.get("freshness_days"), locale=body.get("locale", "zh-CN"),
            project_id=body.get("project_id"),
        ).validate()
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background = bool(body.get("background", False))
    fabric = get_retrieval_fabric()
    if background:
        result = fabric.create(owner, spec)
        asyncio.create_task(_execute_background(str(result["id"]), owner, spec))
    else:
        result = await anyio.to_thread.run_sync(lambda: fabric.run(owner, spec))
    db.audit(owner, str(user.get("sub") or owner), "search_run_create", str(result.get("id") or ""))
    return result


async def _execute_background(run_id: str, owner: str, spec: SearchRequest) -> None:
    try:
        await anyio.to_thread.run_sync(
            lambda: get_retrieval_fabric().execute(run_id, owner, spec)
        )
    except Exception:
        current = store.get_run(run_id, owner)
        if current and current.get("state") != "cancelled":
            store.update_run(run_id, owner, "failed", result=current.get("result") or {},
                             error_code="execution_failed")


@router.get("/{run_id}")
async def read_search_run(run_id: str, request: Request):
    _, owner = _identity(request)
    item = store.get_run(run_id, owner)
    if not item:
        # Missing and unauthorized are intentionally indistinguishable.
        raise HTTPException(status_code=404, detail="搜索任务不存在")
    return item


@router.get("/{run_id}/events")
async def read_search_events(run_id: str, request: Request, after: int = 0):
    _, owner = _identity(request)
    if not store.get_run(run_id, owner):
        raise HTTPException(status_code=404, detail="搜索任务不存在")
    return {"run_id": run_id, "events": store.list_events(run_id, owner, after)}


@router.post("/{run_id}/cancel")
async def cancel_search_run(run_id: str, request: Request):
    user, owner = _identity(request)
    item = store.get_run(run_id, owner)
    if not item:
        raise HTTPException(status_code=404, detail="搜索任务不存在")
    if item.get("state") not in {"queued", "searching", "verifying", "comparing"}:
        raise HTTPException(status_code=409, detail="搜索任务已进入终态")
    store.update_run(run_id, owner, "cancelled", result=item.get("result") or {})
    db.audit(owner, str(user.get("sub") or owner), "search_run_cancel", run_id)
    return store.get_run(run_id, owner)


@router.post("/{run_id}/resume")
async def resume_search_run(run_id: str, request: Request):
    user, owner = _identity(request)
    item = store.get_run(run_id, owner)
    if not item:
        raise HTTPException(status_code=404, detail="搜索任务不存在")
    stale_active = item.get("state") in {"queued", "searching", "verifying", "comparing"} and (
        float(item.get("updated_at") or 0) < time.time() - 60
    )
    if item.get("state") not in {"failed", "cancelled", "partial"} and not stale_active:
        raise HTTPException(status_code=409, detail="仅失败、取消或部分完成的任务可恢复")
    body = item.get("request") or {}
    try:
        spec = SearchRequest(**{key: body.get(key) for key in (
            "query", "mode", "max_results", "providers", "freshness_days", "locale", "project_id"
        )}).validate()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="原始搜索请求不可恢复") from exc
    result = await anyio.to_thread.run_sync(lambda: get_retrieval_fabric().run(owner, spec))
    new_id = str(result.get("id") or "")
    store.append_event(new_id, owner, "resumed_from", {"run_id": run_id})
    db.audit(owner, str(user.get("sub") or owner), "search_run_resume", f"{run_id}->{new_id}")
    return result
