"""user_memory — 用户记忆与画像 API（V96，对标 Hermes 跨会话用户建模）。

Hermes 的核心支柱之一是"建立对你的深化模型，跨会话留存"——它用
Honcho 做 dialectic user modeling。HashMM 已有 user_memory / user_profiles
存储基础设施（database.py），此前没有给用户看与管理的入口。这里补上：
- GET  /api/memory          查看自己的记忆条目（管理员可查指定用户）
- DELETE /api/memory/{id}   删除一条记忆
- GET  /api/memory/profile  画像摘要（按 category 聚合）

记忆的"写入"由对话流程在后台进行（既有 save_user_memory），本模块只做
读取与管理——符合 Hermes "agent-curated memory" 的人可审计原则。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import get_current_user

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "未登录")
    return user


@router.get("")
async def list_memory(request: Request, user_id: str = "", limit: int = 50):
    """列出记忆条目。默认本人；管理员可传 user_id 查他人。"""
    user = _require_user(request)
    target = user["uid"]
    if user_id and user_id != user["uid"]:
        if user.get("role") != "admin":
            raise HTTPException(403, "仅管理员可查看他人记忆")
        target = user_id
    rows = db.get_user_memories(target, limit=min(limit, 200))
    # 按 category 分组，便于前端画像式展示
    grouped: dict[str, list] = {}
    for r in rows:
        cat = r.get("category") or "其他"
        grouped.setdefault(cat, []).append({
            "id": r["id"], "key": r.get("key", ""), "value": r.get("value", ""),
            "confidence": r.get("confidence", 0.8), "last_used": r.get("last_used"),
        })
    return {"ok": True, "total": len(rows), "groups": grouped}


@router.post("")
async def add_memory(request: Request):
    """手动写入一条记忆（「教它记住」）。Body: {category?, key, value}。同 key 覆盖（upsert）。
    符合 Hermes「agent-curated + 人可编辑」原则：用户能直接塑造跨会话个性化。"""
    user = _require_user(request)
    body = await request.json()
    key = (body.get("key") or "").strip()
    value = (body.get("value") or "").strip()
    category = (body.get("category") or "用户主动").strip()
    if not key or not value:
        raise HTTPException(400, "key 和 value 不能为空")
    mid = db.save_user_memory(user["uid"], category, key[:120], value[:500])
    return {"ok": True, "id": mid}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str):
    """删除一条记忆（本人记忆或管理员）。"""
    user = _require_user(request)
    # 校验归属：非管理员只能删自己的
    if user.get("role") != "admin":
        own = {m["id"] for m in db.get_user_memories(user["uid"], limit=200)}
        if memory_id not in own:
            raise HTTPException(403, "只能删除自己的记忆")
    ok = db.delete_user_memory(memory_id)
    if not ok:
        raise HTTPException(404, "记忆不存在")
    return {"ok": True}


@router.get("/profile")
async def memory_profile(request: Request):
    """画像摘要：记忆条数、覆盖的类别、最近更新。"""
    user = _require_user(request)
    rows = db.get_user_memories(user["uid"], limit=200)
    cats: dict[str, int] = {}
    for r in rows:
        cat = r.get("category") or "其他"
        cats[cat] = cats.get(cat, 0) + 1
    last = max((r.get("last_used") or 0 for r in rows), default=0)
    return {"ok": True, "count": len(rows), "categories": cats, "last_updated": last}


@router.get("/search")
async def search_sessions(request: Request, q: str = "", limit: int = 20):
    """跨会话记忆搜索（Hermes FTS5 session search）：搜自己的历史对话内容。"""
    user = _require_user(request)
    from hashmm.api.session_search import search
    return search(user["uid"], q, limit=min(limit, 50))
