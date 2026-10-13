"""hashmm/evaluation/benchmarks/parallel.py —— 基准并发执行助手 · V324。

问题：外部基准逐题串行跑太慢——每题一次 LLM 调用（几秒~几十秒），50 题串起来就是几十分钟。
但**每道题彼此独立**，而本地模型服务（vLLM/Qwen）天然支持并发请求（内部会 batch），所以
把题目并行发出去能成倍提速——这正是"为什么 Codex 后台一堆进程"。

用法（把"逐题 for 循环"换成"并行 map"）：
    from .parallel import run_parallel
    results = run_parallel(tasks, worker_fn)   # worker_fn(task)->任意结果；返回与 tasks 同序
    # 然后在主线程里聚合 results（计数/统计），worker 内**不要**改共享变量。

并发度：HASHMM_BENCH_CONCURRENCY（默认 4）。1=退回串行。慢速单卡别调太高（4~8 合适，
过高会显存/排队反而更慢，也可能触发云 API 限流）。

线程安全约定：worker_fn 里只做 LLM 调用（HTTP，线程安全）+ 用**独立临时文件**的代码执行；
不共享可变状态。异常在 worker 内被捕获，不会炸整批。
"""
from __future__ import annotations

import os
from typing import Callable, TypeVar

__all__ = ["bench_concurrency", "run_parallel"]

T = TypeVar("T")
R = TypeVar("R")


def bench_concurrency() -> int:
    """基准并发度。默认 4；HASHMM_BENCH_CONCURRENCY 可调；1=串行。"""
    try:
        n = int(os.environ.get("HASHMM_BENCH_CONCURRENCY", "4"))
    except Exception:  # noqa: BLE001
        n = 4
    return max(1, min(n, 32))       # 上限 32，防手滑设成几百把机器打挂


def run_parallel(items: list[T], worker_fn: Callable[[T], R],
                 concurrency: int | None = None) -> list[R]:
    """对每个 item 调 worker_fn(item)，并行执行，返回**与 items 同序**的结果列表。

    worker_fn 内部异常被捕获 → 对应位置返回 {"_error": "..."}，不影响其它题。
    并发度 <=1 或题数 <=1 时退回串行（零开销）。
    """
    n = concurrency if concurrency is not None else bench_concurrency()

    def _safe(it: T) -> R:
        try:
            return worker_fn(it)
        except Exception as e:  # noqa: BLE001
            return {"_error": f"{type(e).__name__}: {e}"}  # type: ignore[return-value]

    if n <= 1 or len(items) <= 1:
        return [_safe(it) for it in items]

    from concurrent.futures import ThreadPoolExecutor
    # ThreadPoolExecutor.map 保序：返回顺序与 items 一致，方便主线程按序聚合。
    with ThreadPoolExecutor(max_workers=min(n, len(items))) as ex:
        return list(ex.map(_safe, items))
