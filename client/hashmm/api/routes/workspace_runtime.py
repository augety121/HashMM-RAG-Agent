"""Authenticated control plane for provider-neutral workspace executions."""
from __future__ import annotations

import hashlib
import json
import re

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.work.workspace_kernel import get_workspace_run, resolve_workspace
from hashmm.workspace_runtime import (
    CloudflareComputerClient,
    CloudflareComputerError,
)
from hashmm.workspace_runtime import store
from hashmm.workspace_runtime.cloudflare_computer import PROVIDER_ID


router = APIRouter(prefix="/api/v3/runtime", tags=["workspace-runtime"])
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_EXECUTABLE_STATES = {"draft", "planned", "ready", "running", "change_requested", "interrupted"}


def _owner(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    owner = str(user.get("uid") or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="authentication required")
    return user, owner


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="workspace execution not found")


@router.get("/providers")
async def providers(request: Request):
    _owner(request)
    return {
        "protocol": "hashmm-workspace-runtime/1.0",
        "providers": [CloudflareComputerClient().status()],
    }


@router.get("/providers/cloudflare-computer/health")
async def provider_health(request: Request):
    _owner(request)
    client = CloudflareComputerClient()
    try:
        return await anyio.to_thread.run_sync(client.health)
    except CloudflareComputerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@router.post("/workspaces/{workspace_id}/executions")
async def create_execution(workspace_id: str, request: Request):
    user, owner = _owner(request)
    if not resolve_workspace(owner, workspace_id):
        raise _not_found()
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    idempotency_key = str(request.headers.get("idempotency-key") or body.get("idempotency_key") or "").strip()
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise HTTPException(status_code=400, detail="valid idempotency key required")
    run_id = str(body.get("run_id") or "").strip()
    if not _OBJECT_ID.fullmatch(run_id):
        raise HTTPException(status_code=422, detail="run_id required")
    run = get_workspace_run(owner, workspace_id, run_id)
    if not run:
        raise _not_found()
    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision != int(run.get("revision") or 0):
        raise HTTPException(status_code=409, detail={"code": "revision_conflict", "current_revision": int(run.get("revision") or 0)})
    if str(run.get("state") or "") not in _EXECUTABLE_STATES:
        raise HTTPException(status_code=409, detail="run is not in an executable state")
    client = CloudflareComputerClient()
    if not bool(client.status().get("configured")):
        raise HTTPException(status_code=503, detail="provider_not_configured")
    argv = body.get("argv")
    cwd = body.get("cwd", "/workspace")
    if not isinstance(argv, list) or not argv or len(argv) > 64:
        raise HTTPException(status_code=422, detail="argv must contain 1-64 items")
    if any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\x00" in arg for arg in argv):
        raise HTTPException(status_code=422, detail="argv is invalid")
    request_record = {"argv": argv, "cwd": cwd, "run_id": run_id, "expected_revision": expected_revision}
    execution, created = store.create_or_get(
        owner, workspace_id, run_id, PROVIDER_ID, idempotency_key, request_record,
    )
    if not created:
        return JSONResponse(content=json.loads(json.dumps(execution, ensure_ascii=False)), headers={"Idempotent-Replay": "true"})
    execution_id = str(execution.get("id") or "")
    try:
        store.update(execution_id, owner, workspace_id, "running")
        result = await anyio.to_thread.run_sync(lambda: client.exec(owner, workspace_id, argv, cwd))
        result["stdout_sha256"] = hashlib.sha256(str(result.get("stdout") or "").encode("utf-8")).hexdigest()
        result["stderr_sha256"] = hashlib.sha256(str(result.get("stderr") or "").encode("utf-8")).hexdigest()
        state = "completed" if int(result.get("exit_code") or 0) == 0 else "failed"
        store.update(execution_id, owner, workspace_id, state, result=result,
                     error_code="" if state == "completed" else "nonzero_exit")
        db.audit(owner, str(user.get("sub") or owner), "workspace_execution", execution_id)
    except CloudflareComputerError as exc:
        store.update(execution_id, owner, workspace_id, "unknown" if exc.code == "upstream_timeout" else "failed",
                     error_code=exc.code)
    return JSONResponse(
        status_code=201,
        content=json.loads(json.dumps(store.get(execution_id, owner, workspace_id), ensure_ascii=False)),
        headers={"Location": f"/api/v3/runtime/workspaces/{workspace_id}/executions/{execution_id}"},
    )


@router.get("/workspaces/{workspace_id}/executions/{execution_id}")
async def execution_detail(workspace_id: str, execution_id: str, request: Request):
    _, owner = _owner(request)
    if not resolve_workspace(owner, workspace_id):
        raise _not_found()
    item = store.get(execution_id, owner, workspace_id)
    if not item:
        raise _not_found()
    return item


@router.get("/workspaces/{workspace_id}/executions/{execution_id}/events")
async def execution_events(workspace_id: str, execution_id: str, request: Request, after: int = 0):
    _, owner = _owner(request)
    if not store.get(execution_id, owner, workspace_id):
        raise _not_found()
    return {"execution_id": execution_id, "events": store.events(execution_id, owner, after)}
