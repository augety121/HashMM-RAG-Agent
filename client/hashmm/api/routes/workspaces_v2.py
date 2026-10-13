"""V519+ user-facing Workspace command/query API.

This is a compatibility-preserving facade over WorkRuntime, not a second
executor.  Every object lookup includes the authenticated owner and returns
the same not-found response for absent and unauthorized objects.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from hashmm.agent import work_control, work_runtime
from hashmm.api.auth import require_auth, require_conv_access
from hashmm.work.workspace_kernel import (
    build_workspace_snapshot,
    create_workspace_run,
    get_workspace_run,
    resolve_workspace,
)


router = APIRouter(prefix="/api/v2/workspaces", tags=["workspaces-v2"])
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_WORKSPACE_ID = re.compile(r"^(personal|[A-Za-z0-9_.:-]{3,120})$")
_START_KINDS = {
    "chat", "loop", "team", "browser", "computer", "remote",
    "artifact", "workflow",
}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="工作空间或任务不存在")


def _owner(request: Request) -> dict:
    user = require_auth(request)
    if not str(user.get("uid") or ""):
        raise HTTPException(status_code=401, detail="需要登录")
    return user


def _validate_workspace(value: str) -> str:
    workspace_id = str(value or "").strip()
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise _not_found()
    return workspace_id


@router.get("/{workspace_id}/snapshot", summary="统一工作空间增量快照")
async def workspace_snapshot(
    workspace_id: str, request: Request, after_cursor: int = 0, limit: int = 250,
):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    payload = build_workspace_snapshot(
        user=user,
        workspace_id=workspace_id,
        after_cursor=max(0, int(after_cursor or 0)),
        limit=max(1, min(int(limit or 250), 250)),
    )
    if payload is None:
        raise _not_found()
    etag = f'"workspace-v2-{payload["etag"]}"'
    headers = {
        "Cache-Control": "private, no-cache",
        "Vary": "Authorization",
        "ETag": etag,
        "X-Work-Cursor": str(payload["sync"]["next_cursor"]),
        "X-Work-High-Water": str(payload["sync"]["high_water_cursor"]),
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers=headers,
    )


@router.get("/{workspace_id}/stream", summary="统一工作空间事件流")
async def workspace_stream(
    workspace_id: str, request: Request, after_cursor: int = 0,
):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    if not resolve_workspace(str(user.get("uid") or ""), workspace_id):
        raise _not_found()
    cursor = max(0, int(after_cursor or 0))

    async def events():
        nonlocal cursor
        heartbeat = time.monotonic()
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            payload = build_workspace_snapshot(
                user=user,
                workspace_id=workspace_id,
                after_cursor=cursor,
                limit=250,
            )
            if payload is None:
                return
            high = int(payload["sync"]["high_water_cursor"])
            if payload["runs"] or high > cursor:
                cursor = int(payload["sync"]["next_cursor"])
                data = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":"),
                )
                yield f"id: {cursor}\nevent: workspace\ndata: {data}\n\n"
                heartbeat = time.monotonic()
            elif time.monotonic() - heartbeat >= 15:
                yield f": heartbeat {int(time.time())}\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-cache, no-transform",
            "Vary": "Authorization",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{workspace_id}/sync", summary="拉取账号隔离的工作协议增量")
async def workspace_sync(
    workspace_id: str,
    request: Request,
    after_cursor: int = 0,
    limit: int = 250,
):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    if not resolve_workspace(str(user.get("uid") or ""), workspace_id):
        raise _not_found()
    payload = work_runtime.list_sync_changes(
        str(user.get("uid") or ""),
        after_cursor=max(0, int(after_cursor or 0)),
        limit=max(1, min(int(limit or 250), 500)),
    )
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers={
            "Cache-Control": "private, no-cache",
            "Vary": "Authorization",
            "X-Work-Cursor": str(payload["next_cursor"]),
        },
    )


@router.post("/{workspace_id}/runs", summary="以结果契约创建一项待执行工作")
async def create_run(workspace_id: str, request: Request):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise HTTPException(status_code=400, detail="idempotency_key 格式无效")
    conversation_id = str(body.get("conversation_id") or "").strip()
    if conversation_id:
        conversation = require_conv_access(request, conversation_id)
        if str(conversation.get("user_id") or "") != str(user.get("uid") or ""):
            raise _not_found()
    kind = str(body.get("kind") or "workflow").strip().lower()
    if kind not in _START_KINDS:
        raise HTTPException(status_code=422, detail="当前任务类型不受支持")
    result = create_workspace_run(
        user=user,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        goal=str(body.get("goal") or ""),
        deliverable=str(body.get("deliverable") or ""),
        success_criteria=(
            body.get("success_criteria")
            if isinstance(body.get("success_criteria"), list) else []
        ),
        permission_mode=str(body.get("permission_mode") or "ask"),
        conversation_id=conversation_id,
        kind=kind,
        work_method=(
            body.get("work_method")
            if isinstance(body.get("work_method"), dict) else {}
        ),
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise _not_found()
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail="任务目标或工作范围无效",
        )
    return JSONResponse(
        status_code=201,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "Location": (
                f"/api/v2/workspaces/{workspace_id}/runs/"
                f"{result['run']['id']}"
            ),
        },
    )


@router.get("/{workspace_id}/runs/{run_id}", summary="读取统一任务详情")
async def run_detail(workspace_id: str, run_id: str, request: Request):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    if not _OBJECT_ID.fullmatch(str(run_id or "")):
        raise _not_found()
    run = get_workspace_run(
        str(user.get("uid") or ""), workspace_id, run_id,
    )
    if run is None:
        raise _not_found()
    return JSONResponse(
        content=json.loads(json.dumps(run, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-cache", "Vary": "Authorization"},
    )


@router.post(
    "/{workspace_id}/runs/{run_id}/commands",
    summary="控制、验收或退回一项工作",
)
async def run_command(workspace_id: str, run_id: str, request: Request):
    user = _owner(request)
    workspace_id = _validate_workspace(workspace_id)
    if not _OBJECT_ID.fullmatch(str(run_id or "")):
        raise _not_found()
    projected = get_workspace_run(
        str(user.get("uid") or ""), workspace_id, run_id,
    )
    if projected is None:
        raise _not_found()
    try:
        body = await request.json()
    except Exception:
        body = {}
    command_id = str(body.get("command_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    try:
        revision = int(body.get("expected_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    if not _IDEMPOTENCY.fullmatch(command_id) or revision < 1:
        raise HTTPException(status_code=400, detail="命令标识或修订号无效")
    if action in {"accept_delivery", "request_changes"}:
        result = work_runtime.apply_decision(
            run_id,
            user_id=str(user.get("uid") or ""),
            decision_id=command_id,
            action=action,
            expected_revision=revision,
            note=str(body.get("note") or ""),
        )
    elif action in {"pause", "resume", "cancel", "retry"}:
        result = await work_control.execute_command(
            run_id,
            user=user,
            command_id=command_id,
            action=action,
            expected_revision=revision,
        )
    else:
        raise HTTPException(status_code=422, detail="当前操作不受支持")
    error = str(result.get("error") or "")
    if error == "not_found":
        raise _not_found()
    status = 200 if result.get("ok") else (
        409 if error in {
            "revision_conflict", "unsupported", "decision_id_conflict",
            "verification_blocked", "executor_state_changed",
        } else 422
    )
    current = get_workspace_run(
        str(user.get("uid") or ""), workspace_id, run_id,
    )
    return JSONResponse(
        status_code=status,
        content=json.loads(json.dumps({
            "schema": "hashmm.workspace-command.v2",
            "ok": bool(result.get("ok")),
            "duplicate": bool(result.get("duplicate")),
            "error": error,
            "run": current,
        }, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )
