"""Authenticated self-service management API for V820 access keys."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hashmm.api import platform_access
from hashmm.api.auth import require_auth
from hashmm.api import database as db


router = APIRouter(prefix="/api/platform", tags=["api-platform"])


def _owner(request: Request) -> tuple[dict[str, Any], str]:
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("id") or user.get("sub") or "").strip()
    if not owner:
        # A valid session without a stable subject must not create orphan keys.
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="authenticated identity has no stable subject")
    return user, owner


def _error(exc: platform_access.AccessError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status,
        headers={"Cache-Control": "private, no-store"},
    )


async def _json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        value = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "invalid JSON body"}}, status_code=400
        )
    if not isinstance(value, dict):
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "JSON body must be an object"}},
            status_code=400,
        )
    return value


def _audit(user: dict[str, Any], owner: str, action: str, value: dict[str, Any], request: Request) -> None:
    detail = json.dumps({
        "key_id": value.get("id", ""), "prefix": value.get("prefix", ""),
        "status": value.get("status", ""), "revision": value.get("revision", 0),
    }, ensure_ascii=False, separators=(",", ":"))
    ip = request.client.host if request.client else ""
    db.audit(owner, str(user.get("username") or user.get("email") or ""), action, detail, ip)


@router.get("/keys")
async def list_api_keys(request: Request):
    _, owner = _owner(request)
    return {"object": "list", "data": platform_access.list_keys(owner)}


@router.post("/keys")
async def create_api_key(request: Request):
    user, owner = _owner(request)
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        value = platform_access.create_key(owner, body)
        _audit(user, owner, "api_key.create", value, request)
        return JSONResponse(value, status_code=201, headers={"Cache-Control": "private, no-store"})
    except platform_access.AccessError as exc:
        return _error(exc)


@router.get("/keys/{key_id}")
async def get_api_key(key_id: str, request: Request):
    _, owner = _owner(request)
    value = platform_access.get_key(owner, key_id)
    if value is None:
        return JSONResponse(
            {"error": {"code": "not_found", "message": "API key not found"}}, status_code=404
        )
    return value


@router.patch("/keys/{key_id}")
async def patch_api_key(key_id: str, request: Request):
    user, owner = _owner(request)
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        revision = int(body.pop("revision", 0) or 0)
        value = platform_access.update_key(owner, key_id, body, expected_revision=revision)
        if value is None:
            return JSONResponse(
                {"error": {"code": "not_found", "message": "API key not found"}}, status_code=404
            )
        _audit(user, owner, "api_key.update", value, request)
        return value
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "revision must be an integer"}},
            status_code=400,
        )
    except platform_access.AccessError as exc:
        return _error(exc)


@router.delete("/keys/{key_id}")
async def delete_api_key(key_id: str, request: Request):
    user, owner = _owner(request)
    value = platform_access.revoke_key(owner, key_id)
    if value is None:
        return JSONResponse(
            {"error": {"code": "not_found", "message": "API key not found"}}, status_code=404
        )
    _audit(user, owner, "api_key.revoke", value, request)
    return value


@router.get("/keys/{key_id}/usage")
async def api_key_usage(key_id: str, request: Request, days: int = 30):
    _, owner = _owner(request)
    value = platform_access.usage_for_key(owner, key_id, days=days)
    if value is None:
        return JSONResponse(
            {"error": {"code": "not_found", "message": "API key not found"}}, status_code=404
        )
    return value

