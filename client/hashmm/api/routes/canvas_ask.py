"""画布轻量问答（V222，画布 2.0 三件套之「选中即问」）。

定位：谷歌式小问小答——用户在工作画布里划选一行代码/一段话，就地问一句，
小 token 单轮解决，**不进会话历史、不打断主对话**。与主问答（/api/chat/stream，
带检索/工具/记忆的重链路）刻意分离：这里只有"当前选中 + 少量上下文 + 一个问题"。

复用 model_manager.get_active_llm_fn() 返回的 quick_call（planning/健康检查同款
轻通道），max_tok 默认 400；无可用模型回 503，前端气泡里明说。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.canvas_ask")

router = APIRouter(prefix="/api/canvas", tags=["canvas"])

_Q_CAP = 500          # 问题上限（字符）
_CTX_CAP = 2000       # 选中+上下文上限
_SYSTEM = (
    "你是工作画布里的就地小助教。用户划选了画布中的一小段内容并提了一个小问题。"
    "请用中文简明回答（通常 3-6 句），能给可直接粘贴的修正片段就给；"
    "不确定就直说；不要展开成长篇教程。"
)


@router.post("/ask")
async def canvas_ask(request: Request):
    require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "需要 JSON 请求体")
    q = str((body or {}).get("question") or "").strip()[:_Q_CAP]
    ctx = str((body or {}).get("context") or "").strip()[:_CTX_CAP]
    if not q:
        raise HTTPException(400, "question 不能为空")
    try:
        from hashmm.api.model_manager import get_active_llm_fn
        fn, model = get_active_llm_fn()
    except Exception as e:
        log_suppressed(logger, e)
        fn, model = None, None
    if fn is None or not hasattr(fn, "quick_call"):
        raise HTTPException(503, "后端暂无可用模型（管理后台配置默认模型后即可用）")
    user_prompt = (f"【选中内容】\n{ctx}\n\n【问题】{q}" if ctx else q)
    try:
        ans = await run_in_threadpool(fn.quick_call, _SYSTEM, user_prompt, 400)
    except Exception as e:
        log_suppressed(logger, e)
        raise HTTPException(502, "模型调用失败，请稍后重试")
    return {"answer": (ans or "").strip(), "model": (model or {}).get("name", "")}
