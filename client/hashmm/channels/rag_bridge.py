"""hashmm/channels/rag_bridge.py — 把渠道消息接到 HashMM 的 RAG-Agent。

这是渠道层与"大脑"的唯一耦合点：归一化的入站消息 → 驱动 AgentLoop → 收集 token 成完整答复。
按 `channel:peer_id` 维持有界会话历史（多轮续上下文，借鉴 fanbox 的按会话续场）。
答复函数可注入（set_answer_fn），渠道处理器只依赖它 → 完全可单测；默认实现复用既有
AgentLoop + app_state.llm_fn，零重写。永不抛错（异常返回友好兜底语）。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from hashmm.utils import get_logger, log_suppressed
from hashmm.channels.replies import MOBILE_PERSONA

logger = get_logger("hashmm.channels.rag_bridge")

_MAX_TURNS = 8            # 每个渠道会话保留的最近轮数（注入给 Agent 续上下文）
_HISTORY: dict[str, list] = {}
_FALLBACK = "抱歉，我暂时无法回答，请稍后再试。"

# 可注入的答复实现：answer_fn(text, *, conv_id, user_id, history, mobile) -> {"text","sources"}
_ANSWER_FN: Optional[Callable] = None


def set_answer_fn(fn: Optional[Callable]) -> None:
    """覆盖默认答复实现（测试注入假实现，或接别的生成路径）。传 None 恢复默认。"""
    global _ANSWER_FN
    _ANSWER_FN = fn


def _push_history(conv_id: str, role: str, content: str) -> None:
    if not content:
        return
    h = _HISTORY.setdefault(conv_id, [])
    h.append({"role": role, "content": content})
    if len(h) > _MAX_TURNS * 2:
        del h[: len(h) - _MAX_TURNS * 2]


async def _maybe_await(v):
    if asyncio.iscoroutine(v):
        return await v
    return v


async def _default_answer(text: str, *, conv_id: str, user_id: str, history: list, mobile: bool) -> dict:
    """默认实现：构造 AgentLoop（复用 app_state.llm_fn）→ run() 收集 token → 完整答复。"""
    try:
        from hashmm.api import app_state
        llm_fn = getattr(app_state, "llm_fn", None)
        if llm_fn is None:
            return {"text": _FALLBACK, "sources": []}
        from hashmm.agent.loop import AgentLoop
        loop = AgentLoop(
            llm_fn=llm_fn,
            system_prompt=(MOBILE_PERSONA if mobile else ""),
            temperature=0.2,
            user_id=user_id or conv_id,
            conv_id=conv_id,
        )
        full = ""
        async for ev_type, ev_data in loop.run(query=text, history=history, user_id=user_id or conv_id):
            if ev_type == "token" and ev_data:
                full += ev_data
        return {"text": full.strip() or _FALLBACK, "sources": []}
    except Exception as e:
        log_suppressed(logger, e)
        return {"text": _FALLBACK, "sources": []}


async def answer(text: str, *, channel: str, peer_id: str, user_id: str = "", mobile: bool = True) -> dict:
    """渠道入口：给定用户文本 + 渠道/对端，返回 {"text","sources"}。永不抛错。
    维护 `channel:peer_id` 的有界历史，让多轮对话能续上下文。"""
    text = (text or "").strip()
    if not text:
        return {"text": "", "sources": []}
    conv_id = f"{channel}:{peer_id}"
    history = list(_HISTORY.get(conv_id, []))[-_MAX_TURNS * 2:]
    fn = _ANSWER_FN or _default_answer
    try:
        out = await _maybe_await(fn(text, conv_id=conv_id, user_id=user_id, history=history, mobile=mobile))
    except Exception as e:
        log_suppressed(logger, e)
        out = {"text": _FALLBACK, "sources": []}
    if not isinstance(out, dict):
        out = {"text": str(out or _FALLBACK), "sources": []}
    reply = (out.get("text") or _FALLBACK).strip()
    _push_history(conv_id, "user", text)
    _push_history(conv_id, "assistant", reply)
    out["text"] = reply
    out.setdefault("sources", [])
    return out


def reset_history(conv_id: str = "") -> None:
    """清会话历史（conv_id 为空则清全部）。"""
    if conv_id:
        _HISTORY.pop(conv_id, None)
    else:
        _HISTORY.clear()
