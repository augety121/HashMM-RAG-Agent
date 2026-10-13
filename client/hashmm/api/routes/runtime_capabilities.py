"""Authenticated, cacheable projection of the actual Agent runtime."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from hashmm.api.auth import require_auth

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/capabilities", summary="Chat/桌面端/App 共用的真实能力清单")
async def runtime_capabilities(request: Request):
    user = require_auth(request)
    from hashmm.agent.capabilities import build_runtime_capabilities

    # Importing an APIRouter is not proof that the live application mounted it.
    # Project the actual application assembly so cards cannot advertise an API
    # that was accidentally omitted from server startup.
    app = getattr(request, "app", None)
    mounted_paths = {
        str(getattr(route, "path", ""))
        for route in getattr(app, "routes", [])
        if getattr(route, "path", "")
    } if app is not None else None
    payload = build_runtime_capabilities(
        str(user.get("uid") or ""),
        mounted_paths=mounted_paths,
    )
    etag = f'"{payload["revision"]}"'
    headers = {"Cache-Control": "private, max-age=30", "ETag": etag, "Vary": "Authorization"}
    if request.headers.get("If-None-Match", "") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False)),
        headers=headers,
    )
