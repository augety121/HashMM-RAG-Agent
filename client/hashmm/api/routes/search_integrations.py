"""User-facing search integration settings."""
from __future__ import annotations

import time

import anyio
from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.search_integrations import (
    default_search_config,
    delete_search_integration,
    get_search_integration,
    list_search_providers,
    set_search_integration,
)

router = APIRouter(prefix="/api/integrations/search", tags=["search-integrations"])


def _owner(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    owner_id = str(user.get("uid") or "").strip()
    if not owner_id:
        raise HTTPException(status_code=401, detail="登录状态无效")
    return user, owner_id


@router.get("")
async def list_integrations(request: Request):
    _, owner_id = _owner(request)
    return {"providers": [
        {**item, "integration": get_search_integration(owner_id, item["provider"])}
        for item in list_search_providers()
    ]}


@router.get("/{provider}")
async def read_search_integration(provider: str, request: Request):
    _, owner_id = _owner(request)
    try:
        item = get_search_integration(owner_id, provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="搜索服务不存在") from exc
    return {
        "provider": provider,
        "enabled": bool(item and item["enabled"]),
        "configured": bool(item and item["configured"]),
        "masked_api_key": str(item.get("masked_api_key") or "") if item else "",
        "config": item.get("config") if item else default_search_config(provider),
        "updated_at": float(item.get("updated_at") or 0) if item else 0,
        "notice": ("兼容接口（Beta）。服务能力、额度和计费以密钥提供方条款为准。"
                   if provider.strip().lower() == "doubao" else
                   "凭据仅在服务端加密保存；搜索结果属于外部不可信证据。"),
    }


@router.put("/{provider}")
async def write_search_integration(provider: str, request: Request):
    user, owner_id = _owner(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="设置格式无效")
    try:
        item = set_search_integration(
            owner_id,
            provider,
            api_key=body.get("api_key"),
            enabled=bool(body.get("enabled", True)),
            config=body.get("config"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.audit(
        owner_id,
        str(user.get("sub") or owner_id),
        "search_integration_update",
        provider,
    )
    return {"ok": True, "integration": item}


@router.delete("/{provider}")
async def remove_search_integration(provider: str, request: Request):
    user, owner_id = _owner(request)
    try:
        removed = delete_search_integration(owner_id, provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="搜索服务不存在") from exc
    if not removed:
        raise HTTPException(status_code=404, detail="搜索服务尚未配置")
    db.audit(
        owner_id,
        str(user.get("sub") or owner_id),
        "search_integration_delete",
        provider,
    )
    return {"ok": True}


@router.post("/{provider}/test")
async def test_search_integration(provider: str, request: Request):
    """Probe the saved owner-scoped integration without exposing its secret.

    The probe deliberately uses the same adapter as Chat so a green result is
    evidence for the actual product path, rather than a separate demo request.
    """
    user, owner_id = _owner(request)
    item = get_search_integration(owner_id, provider)
    if not item or not item.get("configured") or not item.get("enabled"):
        raise HTTPException(status_code=409, detail="请先保存并启用搜索服务")

    from hashmm.retrieval_fabric.providers import search

    started = time.perf_counter()
    try:
        results = await anyio.to_thread.run_sync(
            lambda: search(provider.strip().lower(), owner_id, "HashMM", 1, {"mode": "fast", "locale": "zh-CN"})
        )
    except Exception as exc:
        # Remote SDK messages are untrusted and may contain request details.
        # Return a stable user-facing category; full exception stays out of the
        # API response and audit log.
        db.audit(
            owner_id,
            str(user.get("sub") or owner_id),
            "search_integration_test_failed",
            provider,
        )
        raise HTTPException(
            status_code=502,
            detail="搜索服务连接失败，请检查 API Key、服务地址和当前额度",
        ) from exc
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    db.audit(
        owner_id,
        str(user.get("sub") or owner_id),
        "search_integration_test",
        provider,
    )
    return {
        "ok": True,
        "provider": provider,
        "result_count": len(results or []),
        "latency_ms": elapsed_ms,
    }
