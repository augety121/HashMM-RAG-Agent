"""hashmm/api/routes/mem_layered.py — 分层记忆 + 符号化卸载 REST（V294）。

把移植自 TencentDB Agent Memory 的两套能力暴露给前端 / 画布 / 桌面端：
  · 分层记忆（L0→L1→L2→L3）：查画像/原子/情境、按对话手动触发抽取、再生成画像。
  · 符号化卸载：看某会话的 Mermaid 符号地图、节点清单、省下的 token、按 node_id 下钻回原文。

登录即可用；所有读操作对空数据返回空结构；功能未启用时如实告知开关。永不 500。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("mem_layered")
router = APIRouter(prefix="/api/memory", tags=["LayeredMemory"])


# ── 分层记忆（L1/L2/L3）────────────────────────────────────────────────
@router.get("/layered/overview", summary="分层记忆总览（画像/原子/情境快照）")
async def layered_overview(request: Request):
    user = require_auth(request)
    try:
        from hashmm.memory import layered as L
        uid = user["uid"]
        st = L.stats(uid)
        return {"ok": True, "enabled": L.enabled(), "stats": st,
                "persona": L.read_persona(uid)[:4000]}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": f"分层记忆不可用：{type(e).__name__}", "enabled": False}


@router.get("/layered/atoms", summary="L1 原子事实清单")
async def layered_atoms(request: Request, limit: int = 100):
    user = require_auth(request)
    try:
        from hashmm.memory import layered as L
        atoms = L.read_atoms(user["uid"])[: max(1, min(400, limit))]
        return {"ok": True, "count": len(atoms),
                "atoms": [a.to_dict() for a in atoms]}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": str(type(e).__name__), "atoms": []}


@router.get("/layered/scenes", summary="L2 情境块清单（按热度）")
async def layered_scenes(request: Request):
    user = require_auth(request)
    try:
        from hashmm.memory import layered as L
        scenes = sorted(L.read_scenes(user["uid"]), key=lambda s: -s.heat)
        return {"ok": True, "count": len(scenes),
                "scenes": [{"name": s.name, "summary": s.summary, "heat": s.heat,
                            "updated": s.updated, "content": s.content[:2000]} for s in scenes]}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": str(type(e).__name__), "scenes": []}


@router.post("/layered/extract", summary="按对话手动触发分层抽取（L0→L1→L2）")
async def layered_extract(request: Request):
    """从某会话近端消息抽出分层记忆。body: {conv_id, limit?}。需已配 LLM 且功能开启。"""
    user = require_auth(request)
    body = await request.json()
    conv_id = str(body.get("conv_id") or "").strip()
    if not conv_id:
        return {"ok": False, "detail": "需要 conv_id"}
    try:
        from hashmm.memory import layered as L
        if not L.enabled():
            return {"ok": False, "detail": "分层记忆未启用（设 HASHMM_LAYERED_MEMORY=1）"}
        from hashmm.api import database as db
        from hashmm.api.model_manager import get_active_llm_fn
        rows = db.get_messages(conv_id) if hasattr(db, "get_messages") else []
        msgs = [{"id": str(r.get("id") or i), "role": r.get("role", "user"),
                 "content": r.get("content", "")}
                for i, r in enumerate(rows or [])]
        if not msgs:
            return {"ok": False, "detail": "该会话暂无消息可抽取"}
        fn, _ = get_active_llm_fn()
        r = L.extract(user["uid"], msgs, fn)
        return {"ok": r.ok, **r.to_dict()}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": f"抽取异常：{type(e).__name__}"}


@router.post("/layered/regen-persona", summary="再生成 L3 用户画像")
async def layered_regen_persona(request: Request):
    user = require_auth(request)
    try:
        from hashmm.memory import layered as L
        if not L.enabled():
            return {"ok": False, "detail": "分层记忆未启用（设 HASHMM_LAYERED_MEMORY=1）"}
        from hashmm.api.model_manager import get_active_llm_fn
        try:
            fn, _ = get_active_llm_fn()
        except Exception:
            fn = None
        r = L.regenerate_persona(user["uid"], fn)
        return {"ok": r.get("ok", False), **r}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": f"画像生成异常：{type(e).__name__}"}


# ── 符号化卸载（Mermaid 符号地图 + node_id 下钻）─────────────────────────
@router.get("/offload/view", summary="某会话的符号地图 + 节点清单 + 省 token")
async def offload_view(request: Request, session: str = ""):
    require_auth(request)
    sess = str(session or "").strip() or "default"
    try:
        from hashmm.agent import context_offload as CO
        return {"ok": True, **CO.context_view(sess)}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": str(type(e).__name__), "nodes": 0, "mermaid": ""}


@router.get("/offload/drilldown", summary="按 node_id 下钻回完整原文")
async def offload_drilldown(request: Request, session: str = "", node_id: str = ""):
    require_auth(request)
    sess = str(session or "").strip() or "default"
    try:
        from hashmm.agent import context_offload as CO
        raw = CO.drilldown(sess, node_id)
        return {"ok": bool(raw), "node_id": node_id, "raw": raw[:20000],
                "detail": "" if raw else "未找到该 node_id 的原文"}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": str(type(e).__name__), "raw": ""}
