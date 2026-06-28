"""Agent Run 轨迹时间线 —— 后端基础。

目标（MASTER_PLAN 第 3 步可视化收口的后端那一半）：把流式过程中散落的 trace
事件（classify / retrieve / rerank / tool / sub_agent / generate …）串成**一条
可回放的时间线**。前端 AgentRunTimeline 只要拿到每个事件稳定的 ``phase``（大阶段）
和单调递增的 ``step_id``，就能把整轮 agent run 按阶段分组、按序回放。

设计（遵守铁律：默认关 / 关闭时 _sse 零行为变化 / 永不抛错 / 纯增量字段）：
  - **不改 46 处 trace 调用**：在 ``api/streaming.py:_sse`` 里集中调用本模块的
    ``annotate_trace``。默认关（``HASHMM_AGENT_TIMELINE`` 未开）时直接原样返回，
    前端收到的事件与现在**逐字节一致**。
  - **纯增量**：开启后也只是给 trace 的每个 step **新增** ``phase`` / ``step_id``
    两个字段，不删不改原有 ``node`` / ``detail``，老前端忽略新字段即可，完全兼容。
  - **每轮独立计数**：step_id 在一次请求内单调递增。用 contextvar 让并发请求互不串号。

开启方式：``HASHMM_AGENT_TIMELINE=1``。
"""
from __future__ import annotations

import os
import contextvars

# node → 5 个 deep-research 风大阶段。未知 node 归到 "other"，永不报错。
_NODE_PHASE = {
    "classify": "understand",
    "skill_match": "understand",
    "memory_recall": "understand",
    "safety_check": "understand",
    "context_build": "understand",
    "retrieve": "retrieve",
    "rerank": "retrieve",
    "decompose": "reason",
    "sub_agent": "reason",
    "evolution": "reason",
    "tool": "act",
    "generate": "synthesize",
    "done": "synthesize",
}

# 每个请求一个独立计数器（contextvar 在 async 下也能隔离并发请求）。
_step_counter: contextvars.ContextVar[int] = contextvars.ContextVar("hashmm_timeline_step", default=0)


def enabled() -> bool:
    return os.environ.get("HASHMM_AGENT_TIMELINE", "0").strip().lower() in {"1", "true", "yes", "on"}


def reset() -> None:
    """每轮请求开始时调用，归零 step_id。永不抛错。"""
    try:
        _step_counter.set(0)
    except Exception:
        pass


def _next_step_id() -> int:
    try:
        n = _step_counter.get() + 1
        _step_counter.set(n)
        return n
    except Exception:
        return 0


def phase_of(node: str) -> str:
    return _NODE_PHASE.get(node, "other")


def annotate_trace(data: dict) -> dict:
    """给一个 trace 事件的 data 注入 phase / step_id（仅当开启）。

    输入形如 ``{"steps": [{"node": "...", "detail": "..."}]}``。
    关闭时原样返回（_sse 零变化）；开启时**就地新增**字段并返回同一个 dict。
    任何异常都吞掉、返回原 data —— 时间线是锦上添花，绝不能把主流程带崩。
    """
    if not enabled():
        return data
    try:
        steps = data.get("steps")
        if not isinstance(steps, list):
            return data
        for s in steps:
            if isinstance(s, dict):
                s.setdefault("phase", phase_of(s.get("node", "")))
                s.setdefault("step_id", _next_step_id())
    except Exception:
        pass
    return data
