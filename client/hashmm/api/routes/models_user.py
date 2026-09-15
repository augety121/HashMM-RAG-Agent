"""用户模型管理（V261）——普通用户也能添加自己的 API 模型并在对话中使用。

此前模型管理是管理员专属（/api/admin/models），普通用户既看不到也加不了自己的
API Key。本路由开放用户级能力，与管理员模型井水不犯河水：

  GET    /api/models/mine          我添加的模型列表（api_key 脱敏）+ 当前选用
  POST   /api/models/mine          添加一个我的模型（存同一张 models 表，created_by=我）
  DELETE /api/models/mine/{id}     删除我自己添加的模型（不能删别人的/全局默认）
  POST   /api/models/mine/prefer   选用某个我的模型用于我的对话（空=用系统默认）

选用关系存 settings『user_model:{uid}』；问答主链在生成前读取并覆盖本次 llm_fn
（失败自动回落系统默认，绝不因用户模型配错而答不了话）。
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.model_providers import (
    ProviderConfigError,
    list_provider_specs,
    normalize_model_config,
    parse_model_options,
    provider_credentials_ready,
)

router = APIRouter(prefix="/api/models", tags=["User Models"])


def _mask(key: str) -> str:
    k = str(key or "")
    return (k[:3] + "***" + k[-4:]) if len(k) > 8 else ("***" if k else "")


class MyModelCreate(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model_name: str
    temperature: float = 0.3
    max_tokens: int = 4096
    wire_api: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


@router.get("/providers", summary="当前支持的模型服务商与协议")
async def user_model_providers(request: Request):
    """Authenticated users may configure providers without admin-only metadata."""
    require_auth(request)
    return {"providers": list_provider_specs()}


@router.post("/test", summary="测试我的模型配置")
async def test_my_model(req: MyModelCreate, request: Request):
    """Test an owner-supplied provider without granting admin model access.

    The configuration is never persisted by this endpoint and the API key is
    never returned or logged.  Ordinary users previously had to call the
    admin-only test route, which made the App work for administrators while
    returning 403 for every other account.
    """
    require_auth(request)
    provider_config = dict(req.config or {})
    if req.wire_api:
        provider_config["wire_api"] = req.wire_api
    try:
        normalized = normalize_model_config({**req.model_dump(), "config": provider_config})
    except ProviderConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    spec = normalized["provider_spec"]
    if spec.auth != "optional" and not req.api_key.strip():
        raise HTTPException(400, "该服务商需要 API Key")
    from hashmm.api.model_manager import test_model_connection
    return test_model_connection({
        **req.model_dump(),
        "config": provider_config,
    })


@router.get("/mine", summary="我的模型列表")
async def my_models(request: Request):
    user = require_auth(request)
    uid, sub = user["uid"], user.get("sub", "")
    from hashmm.api.settings_store import get_setting
    preferred = get_setting(f"user_model:{uid}", "") or ""
    items = []
    for m in db.list_models():
        if m.get("created_by") != sub:
            continue
        private = db.get_model(str(m.get("id") or "")) or {}
        has_api_key = bool(str(private.get("api_key") or "").strip())
        items.append({
            "id": m["id"], "name": m.get("name", ""), "provider": m.get("provider", ""),
            "base_url": m.get("base_url", ""), "model_name": m.get("model_name", ""),
            # Never return the stored secret. This marker only tells the UI
            # whether a credential exists.
            "api_key": "***" if has_api_key else "",
            "has_api_key": has_api_key,
            "wire_api": parse_model_options(m).get("wire_api", "chat_completions"),
            "is_preferred": m["id"] == preferred,
        })
    return {"models": items, "preferred": preferred}


@router.post("/mine", summary="添加我的模型")
async def add_my_model(req: MyModelCreate, request: Request):
    user = require_auth(request)
    if not req.name.strip() or not req.model_name.strip():
        raise HTTPException(400, "配置名称和模型 ID 为必填")
    provider_config = dict(req.config or {})
    if req.wire_api:
        provider_config["wire_api"] = req.wire_api
    try:
        normalized = normalize_model_config({**req.model_dump(), "config": provider_config})
    except ProviderConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    spec = normalized["provider_spec"]
    if spec.auth != "optional" and not req.api_key.strip():
        raise HTTPException(400, "该服务商需要 API Key")
    # 用户级模型数量护栏（防滥用）
    mine = [m for m in db.list_models() if m.get("created_by") == user.get("sub", "")]
    if len(mine) >= 10:
        raise HTTPException(429, "最多添加 10 个模型，先删掉不用的")
    model = db.create_model(
        req.name.strip()[:60], normalized["provider"],
        normalized["base_url"][:300], req.api_key.strip(),
        req.model_name.strip()[:80], created_by=user.get("sub", ""),
        temperature=req.temperature, max_tokens=req.max_tokens,
        config={**provider_config, "wire_api": normalized["wire_api"]},
    )
    from hashmm.api.model_manager import invalidate_model_catalog_cache
    invalidate_model_catalog_cache()
    db.audit(user["uid"], user.get("sub", ""), "user_add_model", req.name.strip()[:60])
    return {"ok": True, "id": model.get("id", "")}


@router.delete("/mine/{model_id}", summary="删除我的模型")
async def del_my_model(model_id: str, request: Request):
    user = require_auth(request)
    m = db.get_model(model_id)
    if not m:
        raise HTTPException(404, "没有这个模型")
    if m.get("created_by") != user.get("sub", ""):
        raise HTTPException(403, "只能删除自己添加的模型")
    if m.get("is_default"):
        raise HTTPException(400, "该模型是系统默认模型，不能删除")
    ok = db.delete_model(model_id)
    if not ok:
        raise HTTPException(400, "删除失败")
    from hashmm.api.model_manager import invalidate_model_catalog_cache
    invalidate_model_catalog_cache()
    # 若删的是自己选用的，顺手清掉偏好（回系统默认）
    try:
        from hashmm.api.settings_store import get_setting, set_setting
        if get_setting(f"user_model:{user['uid']}", "") == model_id:
            set_setting(f"user_model:{user['uid']}", "")
    except Exception:
        pass
    db.audit(user["uid"], user.get("sub", ""), "user_del_model", model_id)
    return {"ok": True}


@router.post("/mine/prefer", summary="选用我的某个模型（空=系统默认）")
async def prefer_my_model(request: Request):
    user = require_auth(request)
    body = await request.json()
    model_id = str(body.get("model_id") or "").strip()
    if model_id:
        m = db.get_model(model_id)
        if not m:
            raise HTTPException(404, "没有这个模型")
        if m.get("created_by") != user.get("sub", "") and not m.get("is_default"):
            raise HTTPException(403, "只能选用自己添加的模型或系统默认")
        if not provider_credentials_ready(m):
            raise HTTPException(400, "该模型没有配置 API Key")
    from hashmm.api.settings_store import set_setting
    set_setting(f"user_model:{user['uid']}", model_id)
    return {"ok": True, "preferred": model_id}
