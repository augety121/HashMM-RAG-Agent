"""llm_raw — 原生 LLM tools 调用通道（V92）。

主界面「电脑操作」模式的后端半边：桌面渲染进程跑 Computer-Use 工具循环
（工具在**本机**执行，hashmmCU 桥），每一步的 LLM 决策打到这里——用后端
当前配置的模型做一次带 tools 的 chat.completions，把 message（含
tool_calls）原样回给前端。这样用户登录后端即可用 CU，无需再单独填 Key。

另带 /cu_save：CU 对话不走 /stream 主链（纯本地循环），这里把
user+assistant 两条消息落进会话，刷新后历史可见。

依赖注入设计：run_tools_chat(model_getter, client_factory) 可换桩，
纯函数路径在沙箱无 fastapi 也能测。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hashmm.api import database as db
from hashmm.api.auth import get_current_user
from hashmm.api.routes.conversations import require_conv_access
from hashmm.api.llm_tools_core import run_tools_chat
from hashmm.utils import get_logger

logger = get_logger("hashmm.llm_raw")
router = APIRouter(prefix="/api/llm", tags=["llm"])

MAX_PAYLOAD = 300_000        # messages 序列化上限（防滥用）


@router.post("/tools")
async def llm_tools_chat(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "未登录")
    body = await request.json()
    messages = body.get("messages") or []
    tools = body.get("tools") or []
    if not messages:
        raise HTTPException(400, "messages required")
    if len(json.dumps(messages, ensure_ascii=False)) > MAX_PAYLOAD:
        raise HTTPException(413, "对话过长，请精简后重试")
    try:
        msg = await run_in_threadpool(run_tools_chat, messages, tools)
    except Exception as e:
        logger.warning(f"[llm_raw] tools chat failed: {e}")
        raise HTTPException(502, f"模型调用失败：{e}")
    return {"ok": True, "message": msg}


@router.post("/cu_save")
async def cu_save(request: Request):
    """电脑操作模式的会话落库：user + assistant 两条进指定会话。"""
    body = await request.json()
    conv_id = (body.get("conv_id") or "").strip()
    user_content = (body.get("user_content") or "").strip()
    assistant_content = (body.get("assistant_content") or "").strip()
    work_method = body.get("work_method")
    if not conv_id or not assistant_content:
        raise HTTPException(400, "conv_id 与 assistant_content 必填")
    require_conv_access(request, conv_id)     # 复用既有属主校验
    if user_content:
        db.create_message(conv_id, "user", user_content)
    mid = db.create_message(conv_id, "assistant", assistant_content)
    # Local Computer Use used to persist only prose, which made App and the
    # shared work feed blind to real desktop work.  Record an owner-bound,
    # deliberately unverified delivery: prose alone is not an execution receipt.
    work_run_id = ""
    try:
        from hashmm.agent import work_runtime
        user = get_current_user(request) or {}
        run = work_runtime.create_run(
            user_id=str(user.get("uid") or ""),
            kind="computer",
            source_id=f"cu:{mid}",
            conv_id=conv_id,
            title=user_content[:120] or "电脑工作",
            status="delivered",
            snapshot={
                "goal": user_content,
                "execution_mode": "desktop_local_computer_use",
                "work_method": (
                    work_method if isinstance(work_method, dict)
                    else {"run_mode": "computer"}
                ),
                "local_execution": {
                    "reported": True,
                    "verified_receipts": 0,
                    "model_prose_is_execution_evidence": False,
                },
            },
        )
        work_runtime.append_event(
            run["id"],
            user_id=str(user.get("uid") or ""),
            event_type="local_execution_reported",
            status="delivered",
            summary="桌面端已回传结果，等待用户验收",
            payload={"verified_receipts": 0},
        )
        work_run_id = str(run.get("id") or "")
    except Exception as exc:
        logger.warning(
            "[llm_raw] computer work projection failed: %s",
            type(exc).__name__,
        )
    return {"ok": True, "message_id": mid, "work_run_id": work_run_id}
