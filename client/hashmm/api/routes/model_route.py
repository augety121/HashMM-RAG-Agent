"""model_route — 模型容灾链管理 + 健康视图（V249，配套 hashmm/llm_failover.py）。

借鉴 OmniRoute 的组合路由与熔断器（见 llm_failover 模块头），这里只做管理面：
  GET  /api/admin/model-route/fallbacks   读容灾链（settings『model_fallbacks』+ 解析后的模型名）
  PUT  /api/admin/model-route/fallbacks   写容灾链（body: {ids: ["模型id", ...]}，≤5 个，校验存在性）
  GET  /api/admin/model-route/health      每模型熔断器状态（closed/open/half_open + 计数 + 最近错误）

前缀独立成 /model-route：admin.py 已有 PUT /api/admin/models/{model_id}，
若共用 /models 前缀，PUT /fallbacks 会被它按 path 参数先截胡。

设置为空 = 容灾关闭（get_active_llm_fn 行为与旧版完全一致）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_admin
from hashmm.api import settings_store
from hashmm.llm_failover import breaker_status, fallback_ids

router = APIRouter(prefix="/api/admin/model-route", tags=["models"])


def _name_map() -> dict[str, str]:
    return {str(m.get("id")): str(m.get("name") or m.get("model_name") or m.get("id"))
            for m in (db.list_models() or [])}


@router.get("/fallbacks", summary="读模型容灾链")
async def get_fallbacks(request: Request):
    require_admin(request)
    ids = fallback_ids()
    names = _name_map()
    default = db.get_default_model() or {}
    return {
        "ok": True,
        "enabled": bool(ids),
        "primary": {"id": default.get("id"), "name": default.get("name")},
        "fallbacks": [{"id": i, "name": names.get(i, i), "exists": i in names} for i in ids],
    }


@router.put("/fallbacks", summary="写模型容灾链（顺序即优先级，≤5）")
async def put_fallbacks(request: Request):
    require_admin(request)
    body = await request.json()
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(400, "body 需要 {ids: [模型id, ...]}（空数组=关闭容灾）")
    ids = [str(x).strip() for x in ids if str(x).strip()][:5]
    names = _name_map()
    unknown = [i for i in ids if i not in names]
    if unknown:
        raise HTTPException(400, f"模型不存在：{', '.join(unknown)}")
    default = db.get_default_model() or {}
    ids = [i for i in ids if i != str(default.get("id"))]   # 默认模型自动是链首，去重
    settings_store.set_setting("model_fallbacks", json.dumps(ids, ensure_ascii=False))
    return {"ok": True, "fallbacks": [{"id": i, "name": names.get(i, i)} for i in ids]}


@router.get("/health", summary="每模型熔断器状态")
async def models_health(request: Request):
    require_admin(request)
    names = _name_map()
    st = breaker_status()
    items = [{"id": mid, "name": names.get(mid, mid), **info} for mid, info in st.items()]
    return {"ok": True, "items": items, "note": "closed=正常 open=熔断中 half_open=冷却后探针"}
