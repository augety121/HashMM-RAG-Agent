"""语音任务编排 REST（路线图阶段 D）——语音一句话 → 编排决策。

POST /api/voice/orchestrate  {text, has_desktop}  →  编排结果（clarify/local/dispatch）
    App 把语音转写文本发来，拿到"该澄清/本地跑/派桌面端"的决策与理由；
    dispatch 时可紧接着调 /api/dispatch 把任务派给桌面端 runner（P2-9）。

不直接执行——决策与执行分离，便于 UI 展示"我判断这是电脑任务，已派给你的桌面端"。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger

logger = get_logger("hashmm.api.routes.voice_ops")

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/orchestrate", summary="语音任务编排：转写文本 → 决策(澄清/本地/派活)")
async def orchestrate(request: Request):
    user = require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    has_desktop = bool(body.get("has_desktop", False))
    user_prefs = body.get("user_prefs") if isinstance(body.get("user_prefs"), dict) else None

    from hashmm.agent.voice_orchestration import orchestrate as _orch
    result = _orch(text, history=None, user_prefs=user_prefs, has_desktop=has_desktop)

    # dispatch 决策时，如指定了在线 runner，可直接落队列（一步到位）；否则只回决策由前端处理
    runner = str(body.get("runner", "")).strip()
    if result.action == "dispatch" and runner:
        try:
            from hashmm.agent import dispatch as _dq
            # V208 阶段D收尾：透传会话 ID —— 桌面 runner 执行完把结果回帖到该对话，
            # 经 Supabase 同步回 App，形成「App 说 → 桌面做 → 结果回 App」闭环。
            conv_id = str(body.get("conv_id", "")).strip()
            if conv_id:
                result.dispatch_payload["conv_id"] = conv_id
            tid = _dq.create_task(runner, result.dispatch_kind, result.dispatch_payload,
                                  created_by=user.get("sub", ""))
            d = result.to_dict()
            d["task_id"] = tid
            return d
        except Exception as e:
            logger.debug(f"auto-dispatch failed: {e}")
    return result.to_dict()
