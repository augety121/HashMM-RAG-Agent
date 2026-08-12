"""并行工具执行（P1-2）—— 对标 Codex 的 FuturesOrdered：并发执行独立工具、保序返回结果。

为什么需要：现在 agent loop 串行执行多工具。当 LLM 一次请求多个【只读、无依赖】的工具
（如同时查多个 kb_search / kg_query），串行会累加延迟。

安全设计（关键）：
- **只并发只读工具**（_PARALLELIZABLE：kb_search/kg_query/web_search 等无副作用工具）。
  有副作用的工具（create_file/execute_code/render_design）永远串行，避免竞态/副作用乱序。
- **保序**：并发执行，但结果严格按原 tool_calls 顺序返回（对标 FuturesOrdered），
  使事件流、对话历史与串行时完全一致。
- **默认关**：HASHMM_PARALLEL_TOOLS 未开 → 走原串行路径，零行为变化。
- **永不抛错**：单个工具失败被隔离成 error 结果，不影响其他。

用法（在 loop 内）：
    from hashmm.agent.parallel_tools import should_parallelize, run_tools_ordered
    if should_parallelize(tool_calls):
        results = await run_tools_ordered(tool_calls, execute_one)
        # results 与 tool_calls 一一对应、保序
"""
from __future__ import annotations

import asyncio
import os

# 可安全并发的只读工具（无副作用）。与 security_policy._SAFE_TOOLS 对齐。
_PARALLELIZABLE = frozenset({
    # Keep this list at the permission system's READ level. Network tools such
    # as web_search can require human approval in strict mode and therefore
    # must never run in the pre-guard prefetch phase.
    "kb_search", "kg_query", "read_file", "list_files",
})

# 并发上限，防资源爆炸
def _max_concurrency() -> int:
    try:
        from hashmm import settings
        return settings.get_int("HASHMM_PARALLEL_TOOLS_MAX", 4)
    except Exception:
        return int(os.environ.get("HASHMM_PARALLEL_TOOLS_MAX", "4") or "4")


def parallel_enabled() -> bool:
    # 默认【关闭】（唯一产品决策，对齐 settings.py 默认 "0"、docs/CONFIG.md 与测试约定）。
    # 理由：当前工具体系含高权限工具，默认并行会改变工具执行顺序，非幂等工具可能并发，
    # 用户未显式开启不应进入更高风险模式。仅当显式设 HASHMM_PARALLEL_TOOLS=1（或 settings 开）
    # 才对【声明为只读、幂等、无共享状态】的工具（见 _PARALLELIZABLE）开放并发。
    v = os.environ.get("HASHMM_PARALLEL_TOOLS")
    if v is not None and v.strip() != "":
        return v.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from hashmm import settings
        return bool(settings.get_bool("HASHMM_PARALLEL_TOOLS"))
    except Exception:
        return False


def _tool_name(tc) -> str:
    try:
        return tc.function.name
    except Exception:
        return ""


def should_parallelize(tool_calls: list, *, pre_hooks_active: bool = False) -> bool:
    """仅当开关开启、且这批调用【全是只读工具且数量≥2】时才并发。否则走串行（更安全）。"""
    if not parallel_enabled():
        return False
    # A pre-tool hook is allowed to deny a call. Prefetch happens before the
    # full dispatch pipeline, so enabling it here would execute a call before
    # that denial. Fall back to the guarded serial path instead.
    if pre_hooks_active:
        return False
    if not tool_calls or len(tool_calls) < 2:
        return False
    return all(_tool_name(tc) in _PARALLELIZABLE for tc in tool_calls)


async def run_tools_ordered(tool_calls: list, execute_one) -> list:
    """并发执行 tool_calls，结果按【原顺序】返回（对标 Codex FuturesOrdered）。

    execute_one: async callable(tool_call) -> result。单个失败被隔离，不影响其他。
    """
    sem = asyncio.Semaphore(_max_concurrency())

    async def _guarded(tc):
        async with sem:
            try:
                return await execute_one(tc)
            except Exception as e:
                return {"status": "error", "message": f"工具执行失败: {str(e)[:200]}"}

    # asyncio.gather 保序：返回顺序与输入顺序一致
    return await asyncio.gather(*[_guarded(tc) for tc in tool_calls])
