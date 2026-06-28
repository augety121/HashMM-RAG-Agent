"""请求级 trace 上下文（P3-2）—— 用 contextvars 让 trace_id 贯穿一次请求的日志。

为什么：现有日志没有贯穿的 trace_id，排查一次请求跨多个模块的链路很难。
这里用标准库 contextvars 维护一个请求级 trace_id：
- 在请求入口 `new_trace()` 生成（或从上游头透传），后续任何模块 `current_trace_id()` 都能拿到。
- 提供 `with trace_context(tid):` 上下文管理器（自动设置/还原）。
- 零依赖、零侵入：不强制改现有 logger；需要时把 trace_id 拼进日志或错误体即可。

典型用法（在 API 入口）：
    from hashmm.trace_context import new_trace, current_trace_id
    tid = new_trace(request.headers.get("x-trace-id"))
    ...
    logger.info(f"[{current_trace_id()}] retrieving...")
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("hashmm_trace_id", default="")


def new_trace(inbound: str | None = None) -> str:
    """开始一个新 trace：优先透传上游 trace_id，否则生成一个。返回 trace_id。"""
    tid = (inbound or "").strip() or uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    return tid


def current_trace_id() -> str:
    """取当前请求的 trace_id（无则空串）。"""
    try:
        return _trace_id.get()
    except Exception:
        return ""


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid or "")


@contextlib.contextmanager
def trace_context(tid: str | None = None):
    """上下文管理器：进入时设 trace_id，退出时还原。"""
    token = _trace_id.set((tid or "").strip() or uuid.uuid4().hex[:16])
    try:
        yield _trace_id.get()
    finally:
        try:
            _trace_id.reset(token)
        except Exception:
            pass


def with_trace(record: dict | None = None) -> dict:
    """给一个日志/事件 dict 附加当前 trace_id（便于结构化日志）。"""
    d = dict(record or {})
    tid = current_trace_id()
    if tid:
        d.setdefault("trace_id", tid)
    return d
