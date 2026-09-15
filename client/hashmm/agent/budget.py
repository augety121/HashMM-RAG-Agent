"""budget — 每次请求的 token / 成本预算闸（V103.27）。

完善方案点名的真实弱项之一：AgentLoop 有步数上限（max_iterations）和墙钟截止（deadline），
但没有「单次请求累计 token / 成本」的总量闸——loop 多轮迭代可能不断烧 token 没有总预算。
这里补上，对标大厂 agent 的 per-request budget：累计用量超预算就提前收尾。

配置（环境变量；0 或未设 = 不限，保持原行为）：
  HASHMM_AGENT_MAX_TOKENS   单次请求累计 token 上限（prompt + completion）
  HASHMM_AGENT_MAX_COST     单次请求累计成本上限（元，按 hashmm.api.usage 价格表估算）

设计为「永不抛错、未配置即透明」：拿不到价格只让成本闸失效，token 闸照常工作。
"""
from __future__ import annotations

import os


def _int_env(name: str) -> int:
    try:
        return int(float(os.environ.get(name, "0") or "0"))
    except Exception:
        return 0


def _float_env(name: str) -> float:
    try:
        return float(os.environ.get(name, "0") or "0")
    except Exception:
        return 0.0


def budget_limits() -> dict:
    """读取当前预算配置。0 = 不限。"""
    return {
        "max_tokens": _int_env("HASHMM_AGENT_MAX_TOKENS"),
        "max_cost": _float_env("HASHMM_AGENT_MAX_COST"),
    }


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str = "") -> float:
    """按 usage 价格表估算成本（元）。复用规范的 compute_cost；未知 model 走 default 价。"""
    try:
        from hashmm.api.usage import compute_cost
        return compute_cost(model or "default", int(prompt_tokens or 0), int(completion_tokens or 0))
    except Exception:
        return 0.0


def check_budget(prompt_tokens: int, completion_tokens: int, model: str = "") -> tuple[bool, str]:
    """返回 (是否已超预算, 原因)。未配置预算则恒为 (False, "")，即保持原行为。"""
    lim = budget_limits()
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    if lim["max_tokens"] and total >= lim["max_tokens"]:
        return True, f"token 预算用尽（{total}/{lim['max_tokens']}）"
    if lim["max_cost"]:
        cost = estimate_cost(prompt_tokens, completion_tokens, model)
        if cost > 0 and cost >= lim["max_cost"]:
            return True, f"成本预算用尽（¥{cost:.4f}/¥{lim['max_cost']:.2f}）"
    return False, ""
