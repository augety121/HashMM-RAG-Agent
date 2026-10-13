"""hashmm/agent/mas_guard.py — 多 Agent 编排的硬约束闸（对齐大厂标准）。

SubAgentOrchestrator.execute_plan 原本会把计划里的所有子任务一路跑完，缺三道硬约束：
  ① 子任务数量上限（模型可能规划出过多子任务，可靠性 0.95^N 掉得很快）；
  ② 整体墙钟时限（多个子任务各调 LLM，累计可能拖很久甚至挂死）；
  ③ 终止条件——连续多个子任务失败还硬往下跑（没有"停止打转"）。

本模块提供 MasBudget，作为编排层的单一约束对象。配置（环境变量，0/未设=用默认）：
  HASHMM_MAS_MAX_SUBTASKS      子任务数量上限（默认 8）
  HASHMM_MAS_DEADLINE_S        整体墙钟时限（秒，默认 180；0=不限）
  HASHMM_MAS_MAX_ERROR_STREAK  连续失败子任务数上限（默认 3；0=不限）

纯逻辑 + 时间判定，**永不抛错**，无 GPU/网络可单测。
"""
from __future__ import annotations

import os
import time


def _env_int(name: str, default: int) -> int:
    try:
        v = int(float(os.environ.get(name, "") or 0))
        return v if v > 0 else default
    except Exception:  # noqa: BLE001
        return default


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "")
        if raw == "":
            return default
        v = float(raw)
        return v if v >= 0 else default
    except Exception:  # noqa: BLE001
        return default


class MasBudget:
    """多 Agent 编排硬约束：子任务上限 + 墙钟时限 + 连续失败终止。"""

    def __init__(self, max_subtasks: int | None = None, deadline_s: float | None = None,
                 max_error_streak: int | None = None):
        self.max_subtasks = max_subtasks if max_subtasks is not None else _env_int("HASHMM_MAS_MAX_SUBTASKS", 8)
        self.deadline_s = deadline_s if deadline_s is not None else _env_float("HASHMM_MAS_DEADLINE_S", 180.0)
        self.max_error_streak = (max_error_streak if max_error_streak is not None
                                 else _env_int("HASHMM_MAS_MAX_ERROR_STREAK", 3))
        self.t0 = time.time()
        self.done = 0
        self.error_streak = 0

    def record(self, ok: bool) -> None:
        """登记一个子任务的结果（ok=是否成功），用于连续失败终止判定。"""
        self.done += 1
        self.error_streak = 0 if ok else (self.error_streak + 1)

    def elapsed(self) -> float:
        return time.time() - self.t0

    def should_stop(self, next_index: int) -> tuple[bool, str]:
        """在开始第 next_index（0基）个子任务前调用；返回 (是否停止, 原因)。**永不抛错**。"""
        try:
            if self.max_subtasks and next_index >= self.max_subtasks:
                return True, f"已达子任务数量上限（{self.max_subtasks}），停止扩张"
            if self.deadline_s and self.elapsed() > self.deadline_s:
                return True, f"已达整体墙钟时限（{self.deadline_s:.0f}s），提前收尾"
            if self.max_error_streak and self.error_streak >= self.max_error_streak:
                return True, f"连续 {self.error_streak} 个子任务失败，停止打转"
            return False, ""
        except Exception:  # noqa: BLE001
            return False, ""

    def snapshot(self) -> dict:
        return {"max_subtasks": self.max_subtasks, "deadline_s": self.deadline_s,
                "max_error_streak": self.max_error_streak,
                "done": self.done, "error_streak": self.error_streak,
                "elapsed_s": round(self.elapsed(), 1)}
