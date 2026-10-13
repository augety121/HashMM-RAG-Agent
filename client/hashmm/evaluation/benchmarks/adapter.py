"""HashMMAgentAdapter — 把 HashMM 的 agent 包装成各基准驱动需要的统一接口。

基准驱动只关心"给一个任务，拿回结果"。本适配器提供三种粒度：
  · answer(prompt)          —— 单轮文本(长对话/问答类基准)
  · tool_decision(prompt, tools) —— 让 agent 决定调哪个工具/参数(函数调用类基准)
  · run_task(query, scorers)—— 走真实 AgentLoop 完成一个带工具执行的任务(SWE-bench/终端类的适配点)

全部基于仓库已有的 llm_fn / agent_bench harness，不引入新依赖。llm_fn 缺失时安全报错(交由上层 SKIP)。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from typing import Any


_BASELINE_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "hashmm_benchmark_baseline_override", default=None
)


class HashMMAgentAdapter:
    def __init__(self, llm_fn: Any = None):
        if llm_fn is None:
            try:
                from hashmm.api.model_manager import get_active_llm_fn
                llm_fn, _ = get_active_llm_fn()
            except Exception:
                llm_fn = None
        self.llm_fn = llm_fn

    @property
    def ready(self) -> bool:
        return callable(self.llm_fn) or hasattr(self.llm_fn, "call_with_tools")

    def answer(self, prompt: str) -> str:
        fn = self.llm_fn
        if fn is None:
            raise RuntimeError("no llm_fn")
        # ★ 修分数异常：原来写死 max_tok=800。很多 instruct/推理模型在写代码前会先输出一段
        # 思维链，800 token 被推理吃掉 → 代码写到函数签名/一半就被截断 → HumanEval/Kotlin
        # 报"函数体缺失/半截签名"判错，白白压低分数。代码生成给足额度（默认 4096，可用
        # HASHMM_BENCH_ANSWER_MAXTOK 覆盖）。这是上限不是强制长度，对短答案无副作用。
        import os as _os
        _mt = int(_os.environ.get("HASHMM_BENCH_ANSWER_MAXTOK", "4096"))
        qc = getattr(fn, "quick_call", None)
        if callable(qc):
            return str(qc("你是严谨的助手。", prompt, max_tok=_mt) or "").strip()
        return str(fn(prompt) or "").strip()

    def tool_decision(self, prompt: str, tools_desc: str) -> dict:
        """让 agent 就一个请求决定调用哪个工具、什么参数。返回 {tool, args}（tool 可为 None）。"""
        fn = self.llm_fn
        if fn is None:
            raise RuntimeError("no llm_fn")
        p = (f"{tools_desc}\n用户请求：{prompt}\n"
             "若需要工具，只输出 JSON {\"tool\": 名称, \"args\": {...}}；"
             "若不需要任何工具，只输出 JSON {\"tool\": null}。不要解释。")
        raw = self.answer(p) if not callable(getattr(fn, "quick_call", None)) else \
            str(fn.quick_call("只输出 JSON。", p, max_tok=200) or "")
        m = re.search(r"\{[\s\S]*\}", str(raw))
        try:
            return json.loads(m.group(0)) if m else {"tool": None}
        except Exception:
            return {"tool": None, "_parse_error": str(raw)[:120]}

    def run_task(self, query: str, scorers: list, *, task_id: str = "bench", max_seconds: int = 120) -> dict:
        """走真实 AgentLoop 完成一个任务并按 scorers 评分。复用 agent_bench.run_task。

        ★ V327 基线模式（HASHMM_BENCH_BASELINE=1）：绕过 AgentLoop，改为裸模型一次直答——
        用于消融对照：同一批题跑一次基线 + 一次正常，差值 = 你的 agent 脚手架的贡献。
        （2026 对标口径：Artificial Analysis 同一 GAIA 任务，裸模型 44.8% vs 完整工具栈 74.6%。）
        返回结构与 AB.run_task 对齐（id/category/status/answer/tools_used/failures/elapsed_s）。
        """
        if baseline_mode():
            import time as _t
            _t0 = _t.time()
            try:
                txt = self.answer(query)
                return {"id": task_id, "category": "bench", "status": "OK", "steps": 1,
                        "answer": txt, "tools_used": [], "failures": [],
                        "elapsed_s": round(_t.time() - _t0, 1)}
            except Exception as e:  # noqa: BLE001
                return {"id": task_id, "category": "bench", "status": "ERROR", "steps": None,
                        "answer": "", "tools_used": [],
                        "failures": [f"{type(e).__name__}: {str(e)[:120]}"],
                        "elapsed_s": round(_t.time() - _t0, 1)}
        from hashmm.tools import agent_bench as AB
        task = AB.Task(id=task_id, category="bench", turns=[query], requires=set(),
                       scorers=scorers, max_seconds=max_seconds)
        return AB.run_task(task, self.llm_fn)


def baseline_mode() -> bool:
    """基线（裸模型直答）开关：HASHMM_BENCH_BASELINE=1。

    ★ 注意作用范围：它只影响【走 AgentLoop 的基准】（GAIA/SWE-bench/Terminal 等
    adapter.run_task / AB.run_task 路径）。HumanEval/Kotlin/BFCL 本来就是模型直答口径，
    基线开不开分数一样——这正说明那几个基准测的是底座模型，不是你的 agent。
    """
    override = _BASELINE_OVERRIDE.get()
    if override is not None:
        return override
    import os
    return (os.environ.get("HASHMM_BENCH_BASELINE") or "").strip() in ("1", "true", "yes")


@contextmanager
def baseline_scope(enabled: bool):
    """Temporarily select baseline/agent mode without mutating process-wide state.

    Benchmark batches run in thread pools. Toggling ``os.environ`` for a paired
    baseline run would leak into unrelated requests and corrupt attribution.
    A ContextVar keeps the mode local to the current worker and is reset even
    when a benchmark raises.
    """
    token = _BASELINE_OVERRIDE.set(bool(enabled))
    try:
        yield
    finally:
        _BASELINE_OVERRIDE.reset(token)
