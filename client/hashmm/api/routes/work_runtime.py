"""Authenticated incremental work feed shared by desktop and App."""
from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from hashmm.api.auth import require_auth
from hashmm.agent import work_runtime
from hashmm.agent import work_control
from hashmm.agent import work_automation
from hashmm.release import PROTOCOLS, public_release_info

router = APIRouter(prefix="/api/work-runs", tags=["work-runtime"])
_COMMAND_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_DECISION_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9_.:-]{3,160}$")
_LEASE_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_ANNOTATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORKFLOW_ID = re.compile(r"^wf_[0-9a-f]{32}$")
_PROACTIVE_ID = re.compile(r"^pwi_[0-9a-f]{32}$")


def _int(value: str | None, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


@router.get("/protocol", summary="跨端 Work Event 协议能力")
async def work_protocol(request: Request):
    """Return a secret-free, authenticated contract before clients sync state."""
    require_auth(request)
    return {
        "schema": "hashmm.work-protocol.v1",
        "authoritative": "server",
        "client_events_accepted": False,
        "cursor": "monotonic_owner_scoped",
        "delivery": ["sse", "cursor_polling"],
        "idempotency": "required_for_commands",
        "concurrency": "optimistic_revision",
        "event_envelope": PROTOCOLS["event"],
        "work_schema": PROTOCOLS["work"],
        "sync_schema": PROTOCOLS["sync"],
        "release": public_release_info(),
    }


@router.get("", summary="账号级增量工作运行列表")
async def work_runs_feed(request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    after = _int(request.query_params.get("after_cursor"))
    limit = min(_int(request.query_params.get("limit"), 100), 250)
    conv_id = str(request.query_params.get("conversation_id") or "")[:160]
    project_id = str(request.query_params.get("project_id") or "")[:120]
    active_only = str(request.query_params.get("active_only") or "").lower() in {"1", "true", "yes"}
    payload = work_runtime.list_runs(
        owner,
        after_cursor=after,
        conv_id=conv_id,
        project_id=project_id,
        include_terminal=not active_only,
        limit=limit,
    )
    etag = f'"work-feed-{owner}-{payload["high_water_cursor"]}"'
    headers = {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Vary": "Authorization",
        "X-Work-Cursor": str(payload["next_cursor"]),
        "X-Work-High-Water": str(payload["high_water_cursor"]),
    }
    if request.headers.get("If-None-Match", "") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers=headers,
    )


@router.get("/stream", summary="账号级工作事件流")
async def work_runs_stream(request: Request):
    """Push owner-scoped Work deltas; ETag/cursor polling remains the fallback."""
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    cursor = _int(request.query_params.get("after_cursor"))
    project_id = str(request.query_params.get("project_id") or "")[:120]

    async def event_stream():
        nonlocal cursor
        last_heartbeat = time.monotonic()
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            payload = work_runtime.list_runs(
                owner,
                after_cursor=cursor,
                project_id=project_id,
                include_terminal=True,
                limit=250,
            )
            if payload["items"] or payload["high_water_cursor"] > cursor:
                cursor = int(payload["next_cursor"])
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: work\ndata: {data}\n\n"
                last_heartbeat = time.monotonic()
            elif time.monotonic() - last_heartbeat >= 15:
                yield f": heartbeat {int(time.time())}\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-cache, no-transform",
            "Vary": "Authorization",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/devices", summary="当前账号可接力的在线执行设备")
async def work_execution_devices(request: Request):
    user = require_auth(request)
    from hashmm.agent import dispatch

    rows = dispatch.runners_status(
        owner=str(user.get("uid") or ""),
        created_by=str(user.get("sub") or ""),
    )
    devices = [{
        "device_id": str(item.get("device_id") or item.get("id") or ""),
        "name": str(item.get("name") or "电脑"),
        "runner": str(item.get("runner") or "desktop"),
        "version": str(item.get("version") or ""),
        "online": bool(item.get("online")),
        "last_seen": float(item.get("last_seen") or 0),
    } for item in rows if item.get("device_id") or item.get("id")]
    return JSONResponse(
        content={
            "schema": "hashmm.execution-devices.v1",
            "items": devices,
            "online_count": sum(bool(item["online"]) for item in devices),
            "server_time": time.time(),
        },
        headers={"Cache-Control": "private, no-cache", "Vary": "Authorization"},
    )


@router.get("/workflows", summary="有执行证据的可复用工作流")
async def work_workflows(request: Request):
    user = require_auth(request)
    return {
        "schema": "hashmm.workflow-feed.v1",
        "items": work_automation.list_workflows(str(user.get("uid") or "")),
    }


@router.post("/workflows", summary="从一次真实执行创建工作流候选")
async def create_work_workflow(request: Request):
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if not _LEASE_ID.fullmatch(idempotency_key):
        raise HTTPException(status_code=400, detail="idempotency_key 格式无效")
    result = work_automation.create_workflow_candidate(
        user_id=str(user.get("uid") or ""),
        source_run_id=str(body.get("source_run_id") or ""),
        name=str(body.get("name") or ""),
        idempotency_key=idempotency_key,
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作运行不存在")
    return JSONResponse(
        status_code=200 if result.get("ok") else 409,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/workflows/{workflow_id}/verify", summary="用独立执行回放验证工作流")
async def verify_work_workflow(workflow_id: str, request: Request):
    user = require_auth(request)
    if not _WORKFLOW_ID.fullmatch(workflow_id):
        raise HTTPException(status_code=404, detail="工作流不存在")
    try:
        body = await request.json()
        expected_revision = int(body.get("expected_revision") or 0)
    except Exception:
        raise HTTPException(status_code=400, detail="工作流验证参数无效")
    result = work_automation.verify_workflow_replay(
        user_id=str(user.get("uid") or ""),
        workflow_id=workflow_id,
        replay_run_id=str(body.get("replay_run_id") or ""),
        expected_revision=expected_revision,
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作流不存在")
    return JSONResponse(
        status_code=200 if result.get("ok") else 409,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/workflows/{workflow_id}/publish", summary="确认后发布已回放验证的工作流")
async def publish_work_workflow(workflow_id: str, request: Request):
    user = require_auth(request)
    if not _WORKFLOW_ID.fullmatch(workflow_id):
        raise HTTPException(status_code=404, detail="工作流不存在")
    try:
        body = await request.json()
        expected_revision = int(body.get("expected_revision") or 0)
    except Exception:
        raise HTTPException(status_code=400, detail="工作流发布参数无效")
    result = work_automation.publish_workflow(
        user_id=str(user.get("uid") or ""),
        workflow_id=workflow_id,
        expected_revision=expected_revision,
        approved=body.get("approved") is True,
        parameters=body.get("parameters") if isinstance(body.get("parameters"), dict) else {},
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作流不存在")
    return JSONResponse(
        status_code=200 if result.get("ok") else 409,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.get("/proactive", summary="需要用户确认的主动工作建议")
async def proactive_work_feed(request: Request):
    user = require_auth(request)
    return {
        "schema": "hashmm.proactive-work-feed.v1",
        "items": work_automation.refresh_proactive_items(
            str(user.get("uid") or "")
        ),
    }


@router.post("/proactive/{item_id}/decision", summary="批准或忽略主动工作建议")
async def proactive_work_decision(item_id: str, request: Request):
    user = require_auth(request)
    if not _PROACTIVE_ID.fullmatch(item_id):
        raise HTTPException(status_code=404, detail="主动建议不存在")
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = work_automation.decide_proactive_item(
        user_id=str(user.get("uid") or ""),
        item_id=item_id,
        decision=str(body.get("decision") or ""),
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="主动建议不存在")
    return JSONResponse(
        status_code=200 if result.get("ok") else 409,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/proactive/{item_id}/execute", summary="执行已批准的主动工作建议")
async def proactive_work_execute(item_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    if not _PROACTIVE_ID.fullmatch(item_id):
        raise HTTPException(status_code=404, detail="主动建议不存在")
    item = work_automation.get_proactive_item(owner, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="主动建议不存在")
    if item.get("status") != "approved":
        raise HTTPException(status_code=409, detail="主动建议尚未批准")
    try:
        body = await request.json()
    except Exception:
        body = {}
    run = work_runtime.get_run(str(item.get("run_id") or ""), owner, limit=1)
    if run is None:
        raise HTTPException(status_code=404, detail="工作运行不存在")

    action = str(item.get("action") or "")
    outcome: dict = {"ok": False, "error": "unsupported"}
    if action == "retry":
        outcome = await work_control.execute_command(
            str(run.get("id") or ""),
            user=user,
            command_id=f"proactive:{item_id}",
            action="retry",
            expected_revision=int(run.get("revision") or 0),
        )
    elif action == "select_device":
        requested_id = str(body.get("device_id") or "").strip()
        from hashmm.agent import dispatch

        selected = next(
            (
                row for row in dispatch.runners_status(
                    owner=owner, created_by=str(user.get("sub") or ""),
                )
                if bool(row.get("online"))
                and str(row.get("device_id") or row.get("id") or "") == requested_id
            ),
            None,
        )
        if selected is None:
            outcome = {"ok": False, "error": "device_unavailable"}
        else:
            outcome = work_runtime.request_execution_device(
                str(run.get("id") or ""),
                user_id=owner,
                device_id=requested_id,
                device_label=str(selected.get("name") or "在线电脑"),
                expected_revision=int(run.get("revision") or 0),
            )
    final_status = (
        "executed" if outcome.get("ok")
        else "needs_user" if outcome.get("error") in {
            "device_unavailable", "unsupported",
        }
        else "failed"
    )
    finalized = work_automation.finalize_proactive_item(
        user_id=owner,
        item_id=item_id,
        status=final_status,
        result={
            "ok": bool(outcome.get("ok")),
            "error": str(outcome.get("error") or ""),
        },
    )
    return JSONResponse(
        status_code=200 if outcome.get("ok") else 409,
        content=json.loads(json.dumps({
            "ok": bool(outcome.get("ok")),
            "outcome": outcome,
            "item": finalized.get("item"),
        }, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.get("/{run_id}", summary="单项工作的增量事件账本")
async def work_run_detail(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    after_seq = _int(request.query_params.get("after_seq"))
    limit = min(_int(request.query_params.get("limit"), 100), 250)
    payload = work_runtime.get_run(run_id, owner, after_seq=after_seq, limit=limit)
    # Missing and another owner's run are intentionally indistinguishable.
    if payload is None:
        raise HTTPException(status_code=404, detail="工作运行不存在")
    etag = f'"work-run-{payload["id"]}-{payload["revision"]}"'
    headers = {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Vary": "Authorization",
        "X-Work-Event-Cursor": str(payload["event_cursor"]),
    }
    if request.headers.get("If-None-Match", "") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers=headers,
    )


@router.get("/{run_id}/checkpoints", summary="读取可恢复的运行检查点")
async def work_run_checkpoints(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    run = work_runtime.get_run(run_id, owner, after_seq=0, limit=1)
    if run is None:
        raise HTTPException(404, "work_run_not_found")
    return {"schema": "hashmm.run-checkpoint-feed.v1", "items": work_runtime.list_checkpoints(run_id, owner)}


@router.get("/{run_id}/workspace", summary="一项工作的统一画布")
async def work_run_workspace(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    payload = work_runtime.get_run(run_id, owner, after_seq=0, limit=250)
    if payload is None:
        raise HTTPException(status_code=404, detail="工作运行不存在")
    workspace = payload.get("workspace") or {}
    etag_value = str((workspace.get("sync") or {}).get("etag") or "")
    etag = f'"work-canvas-{etag_value}"'
    headers = {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Vary": "Authorization",
        "X-Work-Revision": str(payload.get("revision") or 0),
        "X-Work-Event-Cursor": str(payload.get("event_cursor") or 0),
    }
    if request.headers.get("If-None-Match", "") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        content=json.loads(json.dumps(workspace, ensure_ascii=False)),
        headers=headers,
    )


@router.post("/{run_id}/lease", summary="领取、续期或释放执行位置")
async def work_run_lease(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = str(body.get("action") or "acquire").strip().lower()
    try:
        generation = int(body.get("generation") or 0)
        expected_revision = int(body.get("expected_revision") or 0)
        ttl_seconds = int(body.get("ttl_seconds") or 90)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="执行租约参数无效")
    if action == "acquire":
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not _LEASE_ID.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="idempotency_key 格式无效")
        result = work_runtime.acquire_execution_lease(
            run_id,
            user_id=owner,
            holder_type=str(body.get("holder_type") or ""),
            holder_id=str(body.get("holder_id") or ""),
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            ttl_seconds=ttl_seconds,
        )
    else:
        lease_id = str(body.get("lease_id") or "").strip()
        if not _LEASE_ID.fullmatch(lease_id) or generation < 1:
            raise HTTPException(status_code=400, detail="执行租约标识无效")
        if action == "heartbeat":
            result = work_runtime.heartbeat_execution_lease(
                run_id,
                user_id=owner,
                lease_id=lease_id,
                generation=generation,
                ttl_seconds=ttl_seconds,
            )
        elif action == "release":
            result = work_runtime.release_execution_lease(
                run_id,
                user_id=owner,
                lease_id=lease_id,
                generation=generation,
            )
        else:
            raise HTTPException(status_code=400, detail="不支持的执行租约操作")
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作运行不存在")
    status_code = 200 if result.get("ok") else (
        409 if error in {
            "revision_conflict", "already_leased", "lease_expired_or_replaced",
        } else 400
    )
    return JSONResponse(
        status_code=status_code,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/{run_id}/placement", summary="选择在线电脑继续工作")
async def work_run_placement(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    try:
        body = await request.json()
        expected_revision = int(body.get("expected_revision") or 0)
    except Exception:
        raise HTTPException(status_code=400, detail="设备接力参数无效")
    requested_id = str(body.get("device_id") or "").strip()
    from hashmm.agent import dispatch

    devices = dispatch.runners_status(
        owner=owner, created_by=str(user.get("sub") or ""),
    )
    selected = next(
        (
            item for item in devices
            if bool(item.get("online"))
            and str(item.get("device_id") or item.get("id") or "") == requested_id
        ),
        None,
    )
    if selected is None:
        # Missing and another owner's device are intentionally indistinguishable.
        raise HTTPException(status_code=404, detail="执行设备不可用")
    result = work_runtime.request_execution_device(
        run_id,
        user_id=owner,
        device_id=requested_id,
        device_label=str(selected.get("name") or "在线电脑"),
        expected_revision=expected_revision,
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作运行不存在")
    return JSONResponse(
        status_code=200 if result.get("ok") else 409,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/{run_id}/annotations", summary="给成果指定区域提出修改要求")
async def work_run_annotation(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    annotation_id = str(body.get("annotation_id") or "").strip()
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    artifact_id = str(body.get("artifact_id") or "").strip()
    target = body.get("target") if isinstance(body.get("target"), dict) else {}
    note = str(body.get("note") or "")
    try:
        artifact_revision = int(body.get("artifact_revision") or 0)
        expected_revision = int(body.get("expected_revision") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="成果批注参数无效")
    if not _ANNOTATION_ID.fullmatch(annotation_id):
        raise HTTPException(status_code=400, detail="annotation_id 格式无效")
    if not _LEASE_ID.fullmatch(idempotency_key):
        raise HTTPException(status_code=400, detail="idempotency_key 格式无效")
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise HTTPException(status_code=400, detail="artifact_id 格式无效")
    if expected_revision < 1 or not note.strip():
        raise HTTPException(status_code=400, detail="批注内容或版本无效")
    result = work_runtime.add_artifact_annotation(
        run_id,
        user_id=owner,
        annotation_id=annotation_id,
        idempotency_key=idempotency_key,
        artifact_id=artifact_id,
        artifact_revision=artifact_revision,
        target=target,
        note=note,
        expected_revision=expected_revision,
    )
    error = str(result.get("error") or "")
    if error in {"not_found", "artifact_not_found"}:
        raise HTTPException(status_code=404, detail="工作运行或成果不存在")
    status_code = 200 if result.get("ok") else (
        409 if error == "revision_conflict" else 400
    )
    return JSONResponse(
        status_code=status_code,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/{run_id}/decisions", summary="验收成果或提出修改要求")
async def work_run_decision(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    decision_id = str(body.get("decision_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "")
    try:
        expected_revision = int(body.get("expected_revision") or 0)
    except (TypeError, ValueError):
        expected_revision = 0
    if not _DECISION_ID.fullmatch(decision_id):
        raise HTTPException(status_code=400, detail="decision_id 格式无效")
    if action not in {"accept_delivery", "request_changes"}:
        raise HTTPException(status_code=400, detail="不支持的验收操作")
    if expected_revision < 1:
        raise HTTPException(status_code=400, detail="expected_revision 必须为正整数")
    result = work_runtime.apply_decision(
        run_id,
        user_id=owner,
        decision_id=decision_id,
        action=action,
        expected_revision=expected_revision,
        note=note,
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail="工作运行不存在")
    status_code = 200
    if not result.get("ok"):
        status_code = 409 if error in {
            "revision_conflict", "unsupported", "decision_id_conflict",
            "verification_blocked",
        } else 400
    return JSONResponse(
        status_code=status_code,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/{run_id}/artifacts", summary="Register a content-addressed Artifact revision")
async def work_run_artifact(run_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    artifact_id = str(body.get("artifact_id") or "").strip()
    content_hash = str(body.get("content_hash") or "").strip().lower()
    media_type = str(
        body.get("media_type") or "application/octet-stream"
    ).strip()
    verification = str(body.get("verification") or "pending").strip().lower()
    locator = body.get("locator") if isinstance(body.get("locator"), dict) else {}
    try:
        size_bytes = int(body.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size_bytes = -1
    try:
        expected_revision = int(body.get("expected_revision") or 0)
    except (TypeError, ValueError):
        expected_revision = 0
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise HTTPException(status_code=400, detail="invalid artifact_id")
    if not _SHA256.fullmatch(content_hash):
        raise HTTPException(status_code=400, detail="content_hash must be SHA-256")
    if size_bytes < 0 or expected_revision < 1:
        raise HTTPException(status_code=400, detail="invalid revision or file size")
    result = work_runtime.register_artifact_revision(
        run_id,
        user_id=owner,
        artifact_id=artifact_id,
        content_hash=content_hash,
        media_type=media_type,
        size_bytes=size_bytes,
        locator=locator,
        verification=verification,
        expected_run_revision=expected_revision,
    )
    state = str(result.get("state") or "")
    if state == "not_found":
        raise HTTPException(status_code=404, detail="work run not found")
    status_code = 200
    if state not in {"applied", "duplicate"}:
        status_code = 409 if state in {
            "revision_conflict", "artifact_id_conflict",
        } else 400
    return JSONResponse(
        status_code=status_code,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


@router.post("/{run_id}/commands", summary="幂等控制一项长任务")
async def work_run_command(run_id: str, request: Request):
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    command_id = str(body.get("command_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    try:
        expected_revision = int(body.get("expected_revision") or 0)
    except (TypeError, ValueError):
        expected_revision = 0
    if not _COMMAND_ID.fullmatch(command_id):
        raise HTTPException(status_code=400, detail="command_id 格式无效")
    if action not in {"pause", "resume", "cancel", "retry"}:
        raise HTTPException(status_code=400, detail="不支持的控制动作")
    if expected_revision < 1:
        raise HTTPException(status_code=400, detail="expected_revision 必须为正整数")
    result = await work_control.execute_command(
        run_id,
        user=user,
        command_id=command_id,
        action=action,
        expected_revision=expected_revision,
    )
    error = str(result.get("error") or "")
    if error == "not_found":
        # Foreign and missing work runs remain deliberately indistinguishable.
        raise HTTPException(status_code=404, detail="工作运行不存在")
    status_code = 200
    if not result.get("ok"):
        status_code = 409 if error in {
            "revision_conflict", "unsupported", "command_id_conflict",
            "executor_rejected", "executor_state_changed", "already_claimed_or_finished",
        } else 502
    return JSONResponse(
        status_code=status_code,
        content=json.loads(json.dumps(result, ensure_ascii=False)),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )
