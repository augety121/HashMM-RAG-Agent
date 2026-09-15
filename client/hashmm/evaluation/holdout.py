"""Eval 保真 —— held-out 切分 + overfit_gap 监控（防"刷穿金标"）。

问题：当只用一份金标反复调参 / 默认开特性时，很容易"过拟合到金标"——
分数涨了，但其实只是把答案往这 344 道题上对齐，真实泛化没变好。大厂 eval 的
标准防作弊手段是 **held-out**：把金标切成 train / holdout 两份，只在 train 上
观察、调参，holdout 当"考试题"从不参与调参；若 train 通过率明显高于 holdout
（overfit_gap 过大），说明在刷题而非真改进 —— 门禁应当判不过。

设计（遵守铁律：默认关 / 可注入 / 永不抛错 / 不改 run_gate 既有签名与调用方）：
  - **确定性切分**：按 case ``id`` 的稳定哈希分桶，同一 case 永远落在同一边，
    跨进程、跨次运行完全可复现（不用 random.shuffle，避免漂移）。
  - **纯函数 + 注入**：``run_gate_holdout`` 复用现有 ``gate.run_gate``，
    answer_fn / retrieve_fn 由调用方注入，无 GPU / 无网络也能测。
  - **不改 run_gate**：现有 5 处调用方与 phase 测试零影响。

用法（真机，可接进 /eval/compare 或离线跑）：
    from hashmm.evaluation import gate, holdout
    cases = gate.load_cases()
    rep = holdout.run_gate_holdout(cases, answer_fn, retrieve_fn=retr_fn,
                                   holdout_frac=0.3, max_overfit_gap=0.10)
    print(rep["passed"], rep["overfit_gap"])
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from hashmm.evaluation import gate as _gate


def _stable_bucket(case_id: str, buckets: int = 1000) -> int:
    """case_id → [0, buckets) 的稳定哈希桶。用 md5 而非内置 hash()，
    因为内置 hash 受 PYTHONHASHSEED 影响、跨进程不稳定。"""
    h = hashlib.md5(str(case_id).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % buckets


def split_holdout(cases: list[dict], holdout_frac: float = 0.3,
                  seed: int = 0) -> tuple[list[dict], list[dict]]:
    """确定性切分为 (train, holdout)。

    - holdout_frac: holdout 占比（0~1）。
    - seed: 改变切分而不破坏确定性（同 seed 同切分）。
    缺 id 的 case 用其在列表中的位置兜底，保证每个 case 都能稳定归边。
    """
    frac = max(0.0, min(1.0, float(holdout_frac)))
    cut = int(round(frac * 1000))
    train, hold = [], []
    for i, c in enumerate(cases):
        cid = c.get("id") or f"__pos_{i}"
        b = _stable_bucket(f"{seed}:{cid}")
        (hold if b < cut else train).append(c)
    return train, hold


def overfit_gap(train_rate: float, holdout_rate: float) -> float:
    """train 通过率 − holdout 通过率。>0 表示在 train 上更"会做"，
    即过拟合的方向；越大越可疑。"""
    return round(float(train_rate) - float(holdout_rate), 4)


def run_gate_holdout(cases: list[dict], answer_fn: Callable | None, *,
                     retrieve_fn: Callable | None = None,
                     thresholds: dict | None = None,
                     baseline: dict | None = None,
                     holdout_frac: float = 0.3,
                     seed: int = 0,
                     max_overfit_gap: float = 0.10) -> dict:
    """在 train / holdout 上分别跑 gate，并以 overfit_gap 作为额外门禁。

    返回 dict（不破坏 GateReport 结构，便于直接 JSON 化接进 /eval/compare）：
      {
        train:   {...gate.to_dict...},
        holdout: {...gate.to_dict...},
        overfit_gap: float,
        max_overfit_gap: float,
        overfit_flagged: bool,     # gap 是否超阈值
        passed: bool,              # holdout 自身过门禁 且 未过拟合
        n_train, n_holdout: int,
      }

    门禁口径：以 **holdout** 的通过率为准（考试题），再叠加 overfit_gap 检查。
    train 仅用于观察/调参，不直接决定是否放行。永不抛错。
    """
    try:
        train, hold = split_holdout(cases, holdout_frac, seed)
        # holdout 为空（frac=0 或样本太少）时退化为整体跑，gap 记 0。
        if not hold:
            rep = _gate.run_gate(cases, answer_fn, retrieve_fn=retrieve_fn,
                                 thresholds=thresholds, baseline=baseline)
            d = rep.to_dict()
            return {
                "train": d, "holdout": d,
                "overfit_gap": 0.0, "max_overfit_gap": max_overfit_gap,
                "overfit_flagged": False, "passed": d["passed"],
                "n_train": d["n"], "n_holdout": 0,
                "note": "holdout 为空（样本太少或 frac=0），已退化为整体评估",
            }

        rep_train = _gate.run_gate(train, answer_fn, retrieve_fn=retrieve_fn,
                                   thresholds=thresholds, baseline=baseline)
        rep_hold = _gate.run_gate(hold, answer_fn, retrieve_fn=retrieve_fn,
                                  thresholds=thresholds, baseline=baseline)
        dt, dh = rep_train.to_dict(), rep_hold.to_dict()

        gap = overfit_gap(dt["overall_pass_rate"], dh["overall_pass_rate"])
        flagged = gap > max_overfit_gap
        # 放行条件：holdout 自己过了 gate，且没有过拟合迹象。
        passed = bool(dh["passed"] and not flagged)

        return {
            "train": dt, "holdout": dh,
            "overfit_gap": gap, "max_overfit_gap": max_overfit_gap,
            "overfit_flagged": flagged,
            "passed": passed,
            "n_train": dt["n"], "n_holdout": dh["n"],
        }
    except Exception as e:  # 永不抛错：评估失败不应把上层流程带崩
        return {
            "train": None, "holdout": None,
            "overfit_gap": None, "max_overfit_gap": max_overfit_gap,
            "overfit_flagged": False, "passed": False,
            "n_train": 0, "n_holdout": 0,
            "error": f"{type(e).__name__}: {e}",
        }


def enabled() -> bool:
    """是否启用 held-out 门禁（默认关）。接进在线流程时用它做开关。"""
    return os.environ.get("HASHMM_EVAL_HOLDOUT", "0").strip().lower() in {"1", "true", "yes", "on"}
