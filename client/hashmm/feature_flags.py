"""hashmm/feature_flags.py — agentic 特性开关的中心默认值（单一事实来源）。

为什么有这个文件
----------------
这些 agentic 能力（CRAG 自纠、KG 检索、查询自动路由、答案回炉、子代理、循环走本地 Qwen）
此前各自藏在自己的 ``HASHMM_*`` 环境变量后、且**全部默认关**——于是 ``python -m hashmm.agent.status``
显示「特性全关」，线上系统从不使用这些**已经写好**的能力。本模块把其中**安全、能优雅降级**的几个
翻成默认开，同时把成本重 / 依赖外部基建的保持「按需开」。

铁律：**环境变量永远优先**。任何一个开关，显式 ``set HASHMM_XXX=1`` / ``=0`` 都会覆盖这里的默认值——
所以这个改动不会夺走你手动控制的权力，只是把「什么都没设时」的默认行为，从「全关」改成「安全的全开」。

默认开的四个，都已逐一读代码确认「缺 KG / 检索本就良好时优雅降级、不抛错、不烧钱」：
  - KG 局部检索：缺图返回空候选（源码注释 never raises）；
  - CRAG 自纠：只在检索**明显差**时介入，好答案不动；
  - 答案回炉：迭代上限 2，成本有界；
  - 查询自动路由：只是一次轻量分类，开销极小。
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}

# 单一事实来源：每个开关「什么都没设时」的默认值。
FEATURE_DEFAULTS: dict[str, bool] = {
    # —— 默认开（安全、优雅降级、提升质量） ——
    "HASHMM_KG_AUTO":       True,   # 查询自动路由(100)：分类后决定 KG 策略，开销小
    "HASHMM_KG_RETRIEVAL":  True,   # KG 局部检索(95)：缺图返回空候选，零行为变化
    "HASHMM_CRAG":          True,   # CRAG 自纠错(101)：仅在检索明显差时纠正
    "HASHMM_EVAL_OPTIMIZE": True,   # 答案回炉+出处(103)：迭代上限 2，成本有界

    # —— 默认关（成本重 / 依赖外部基建，按需开） ——
    "HASHMM_KG_GLOBAL":          False,  # KG 社区全局(96)：需社区，较重；核心稳了再开
    "HASHMM_KG_PPR":             False,  # KG PPR 多跳(97)：较重
    "HASHMM_SUBAGENTS":          False,  # 子代理(104)：约 15× token，显式开
    "HASHMM_AGENTIC_LOCAL_LOOPS": False, # 循环走本地 Qwen(106)：需配好本地模型再开
}


def flag_enabled(name: str) -> bool:
    """特性是否开启。环境变量显式设置时**永远优先**，否则用中心默认值。"""
    raw = os.environ.get(name)
    if raw is not None:
        return raw.strip().lower() in _TRUE
    return bool(FEATURE_DEFAULTS.get(name, False))


def all_flags() -> dict[str, bool]:
    """当前所有开关的生效状态（已并入 env 覆盖），供 status / 诊断展示。"""
    return {name: flag_enabled(name) for name in FEATURE_DEFAULTS}
