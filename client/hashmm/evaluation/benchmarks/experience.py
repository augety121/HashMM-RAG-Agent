"""hashmm/evaluation/benchmarks/experience.py —— 基准任务的经验闭环（V315）。

对应 2026 行业指南的 Hermes 方向：**任务经验持久化 → 同类任务自动带上以往成功
策略**（Hermes 报告持久 skill 让同类任务提速/提分显著）。机制其实早就齐了——
agent_bench.run_task 的 mem/inject_hints 钩子、EpisodicMemory 的 reward 打标
（[好评]/[待改进]）——只是从没在外部基准上打开。本模块补上最后一公里：

  跑题前   run_task(mem=bench_memory(), user=f"bench:{类别}", inject_hints=True)
           → EpisodicMemory.get_strategy_hint 把同类别高 reward 经验注入系统提示
  判分后   record_outcome(类别, 任务文本, passed, 摘要)
           → 用【官方判分的真实结果】写 reward=1.0/0.0（不用 run_task 内置的
             record——那是 scorer 口径，外部基准的真相在判分之后）

隔离与安全：经验落在 bench_home()/bench-experience.sqlite3，与业务 episodes 库
完全分离；HASHMM_BENCH_EXPERIENCE=0 一键关闭（关闭时 mem=None、零行为差异）。
经验条目自动衰减（EpisodicMemory.decay 的 90 天/500 条上限同样适用）。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path

from ._paths import bench_home

__all__ = ["enabled", "bench_memory", "bench_user", "record_outcome", "hint_kwargs"]


def enabled() -> bool:
    return (os.environ.get("HASHMM_BENCH_EXPERIENCE", "1").strip().lower()
            not in ("0", "false", "off", "no"))


class _FileEpisodeDB:
    """文件级 sqlite 的 db_module 适配（EpisodicMemory 只依赖 _conn 上下文管理器）。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (bench_home() / "bench-experience.sqlite3")

    def _conn(self):
        @contextlib.contextmanager
        def _cm():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        return _cm()


_MEM = None


def bench_memory(db_path: Path | None = None):
    """基准专属 EpisodicMemory（进程内复用；db_path 供测试注入临时库）。"""
    global _MEM
    if db_path is not None:
        from hashmm.evolution.episodic_memory import EpisodicMemory
        return EpisodicMemory(db_module=_FileEpisodeDB(db_path))
    if _MEM is None:
        from hashmm.evolution.episodic_memory import EpisodicMemory
        _MEM = EpisodicMemory(db_module=_FileEpisodeDB())
    return _MEM


def bench_user(category: str) -> str:
    return f"bench:{category}"


def hint_kwargs(category: str) -> dict:
    """给 AB.run_task 的经验注入参数（闸关闭时返回空 dict = 旧行为零差异）。"""
    if not enabled():
        return {}
    try:
        return {"mem": bench_memory(), "user": bench_user(category), "inject_hints": True}
    except Exception:  # noqa: BLE001  经验系统故障绝不拦评测
        return {}


def record_outcome(category: str, task_text: str, passed: bool,
                   detail: str = "", elapsed_ms: int = 0) -> None:
    """判分后回录真实结果（reward 用官方判分，不用 scorer 占位分）。永不抛错。"""
    if not enabled():
        return
    try:
        bench_memory().record(
            user_id=bench_user(category),
            query=str(task_text or "")[:400],
            query_type="bench",
            strategy=category,
            outcome="success" if passed else "failure",
            answer=str(detail or "")[:200],
            elapsed_ms=int(elapsed_ms or 0),
            reward=1.0 if passed else 0.0,
        )
    except Exception:  # noqa: BLE001
        pass
