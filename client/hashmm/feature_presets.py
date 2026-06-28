"""HashMM 功能预设（feature presets）。

背景：HashMM 后端做了大量企业级高级功能（agentic 检索、HyDE、多查询、上下文压缩、
LLM 路由、记忆服务、用户记忆、verifier 评判器、并行工具、调度、工具审计、缓存、KG 增强……），
但它们各自藏在 `HASHMM_*` 环境变量里、且**绝大多数默认关**。普通用户既不知道有、也无从集中
开启，于是线上跑的是基础路径——“代码很深、体感很浅”的根因就在这里。

本模块把这些开关**编目**，并提供三档**预设**，一个开关即可解锁：

    HASHMM_PRESET=basic        # 全部高级功能关（= 历史默认行为，零风险）
    HASHMM_PRESET=recommended  # 低风险增益：缓存/时间线/审计/用户记忆/记忆服务
    HASHMM_PRESET=max          # 全功能：在 recommended 基础上 + 评判器/HyDE/多查询/agentic 检索…

设计原则（严谨、不破坏现状）：
1. **默认 basic**——不主动改变你当前能跑的行为；想要深度，显式设 `HASHMM_PRESET`。
2. **显式优先**——若某个 `HASHMM_X` 已被用户在环境/.env 里显式设过，预设**绝不覆盖**它。
3. **只设开关、不碰功能本体**——功能代码是已有的，本模块只决定“开不开”。
4. 任何异常都不应影响启动（调用方用 try/except 包裹）。

用法：在 config.py 加载 .env 之后调用 `apply_preset()` 一次（早于各模块读取这些开关）。
"""
from __future__ import annotations

import os
from typing import Dict, Optional

# ── 高级开关编目：flag -> 一句话说明（给用户/UI 看，知道开了什么）────────────────
FLAG_DOCS: Dict[str, str] = {
    "HASHMM_PROMPT_CACHE":        "提示缓存：重复/相似提示走缓存，省 token 与延迟",
    "HASHMM_PIPELINE_CACHE":      "检索管线缓存：重复查询直接命中缓存",
    "HASHMM_AGENT_TIMELINE":      "Agent 时间线：把内部步骤结构化输出，前端可视",
    "HASHMM_AUDIT_TOOLS":         "工具调用审计：每次工具调用落审计日志，可回溯",
    "HASHMM_USER_MEMORY":         "用户记忆：记住用户关注领域/偏好，跨会话注入",
    "HASHMM_MEMORY_SERVICE":      "记忆服务：情景/工作记忆，召回过往成功策略",
    "HASHMM_VERIFIER":            "验证器/评判器：对产出做独立校验（对应 loop 的验证动作）",
    "HASHMM_PARALLEL_TOOLS":      "并行工具：多工具并发执行，加速",
    "HASHMM_CONTEXT_COMPACTION":  "上下文压缩：长对话自动压缩，省窗口、抗失忆",
    "HASHMM_LLM_ROUTING":         "模型路由：按任务难度选模型 / 失败降级",
    "HASHMM_HYDE":                "HyDE：先让 LLM 假想答案再检索，提召回（+1 次 LLM 调用）",
    "HASHMM_MULTIQUERY":          "多查询：一个问题拆成多个检索式并召回（+LLM 调用）",
    "HASHMM_AGENTIC_RETRIEVAL":   "代理式检索：agent 自主多轮检索直到证据充分",
    "HASHMM_AGENTIC_RAG":         "代理式 RAG：检索-推理交错的 agent 流程",
    "HASHMM_CONFIDENCE":          "置信度门控：低置信时自动触发更强检索/升级",
    "HASHMM_CONTEXTUAL_RETRIEVAL":"上下文检索：入库时为每块附文档级上下文（Anthropic 同款）",
    "HASHMM_KG_GLEANINGS":        "知识图谱补抽：多轮补抽实体/关系，图谱更全",
    "HASHMM_MMR_DIVERSITY":       "检索去冗多样化：top_k 内剔除讲同一件事的冗余块，用更少格子覆盖更多信息",
    "HASHMM_QUERY_FOCUSED_CHUNKS":"查询聚焦截断：块超长时取与问题最相关的那段，避免答案落在尾部被截掉",
    "HASHMM_UNCERTAINTY_GATE":    "不确定性闸（V103.53）：多跳检索用量化置信度判停——够强提前收手、不足自动补检索（Search-R1+UncertaintyRAG 免训练融合）",
    "HASHMM_CORRECTIVE_RETRIEVAL":"纠正检索（CRAG）：初检偏弱时改写查询、在本地知识库重检索一轮，仍弱才联网兜底",
}

# ── 三档预设：开哪些（值统一为 "1"）────────────────────────────────────────────
# basic = 不在此列 = 全关。
_RECOMMENDED = [
    # 低风险增益：要么对答案逻辑透明（缓存/审计/时间线），要么优雅降级（记忆）。
    "HASHMM_PROMPT_CACHE",
    "HASHMM_PIPELINE_CACHE",
    "HASHMM_AGENT_TIMELINE",
    "HASHMM_AUDIT_TOOLS",
    "HASHMM_USER_MEMORY",
    "HASHMM_MEMORY_SERVICE",
]
_MAX_EXTRA = [
    # 重智能：显著提质，但多花 LLM 调用 / 延迟，或动既有路径——满血、按需开。
    "HASHMM_VERIFIER",
    "HASHMM_PARALLEL_TOOLS",
    "HASHMM_CONTEXT_COMPACTION",
    "HASHMM_LLM_ROUTING",
    "HASHMM_HYDE",
    "HASHMM_MULTIQUERY",
    "HASHMM_AGENTIC_RETRIEVAL",
    "HASHMM_AGENTIC_RAG",
    "HASHMM_CONFIDENCE",
    "HASHMM_CONTEXTUAL_RETRIEVAL",
    "HASHMM_KG_GLEANINGS",
    "HASHMM_MMR_DIVERSITY",
    "HASHMM_QUERY_FOCUSED_CHUNKS",
]

# V103.53（P0）：「智能检索」档——只打开「带不确定性闸的 agentic 多跳检索回路」这一束，
# 不含 HyDE/多查询/verifier 等更重、更易拖慢的项。给想验证 / 默认开 P0 增益、又不想上满血的人。
# = recommended（低风险增益）+ 检索回路三件套（回路本体 + 置信门控 + 不确定性闸）+ 纠正检索。
_AGENTIC_EXTRA = [
    "HASHMM_AGENTIC_RETRIEVAL",     # 多跳回路本体（承载不确定性闸）
    "HASHMM_CONFIDENCE",            # 置信门控（喂闸的量化信号来源）
    "HASHMM_UNCERTAINTY_GATE",      # 不确定性闸（默认即开，这里显式登记以便 describe/审计）
    "HASHMM_CORRECTIVE_RETRIEVAL",  # 弱检索时改写重检索（CRAG），与回路互补
]

PRESETS: Dict[str, Dict[str, str]] = {
    "basic": {},
    "recommended": {flag: "1" for flag in _RECOMMENDED},
    "agentic": {flag: "1" for flag in (_RECOMMENDED + _AGENTIC_EXTRA)},
    "max": {flag: "1" for flag in (_RECOMMENDED + _MAX_EXTRA + _AGENTIC_EXTRA)},
}
# 同义词，宽容输入
_ALIASES = {"": "basic", "off": "basic", "none": "basic", "default": "basic",
            "rec": "recommended", "recommend": "recommended", "full": "max", "all": "max",
            "search": "agentic", "retrieval": "agentic", "smart": "agentic", "p0": "agentic"}


def resolve_preset_name(name: Optional[str]) -> str:
    """把用户输入归一到 basic/recommended/max。无法识别则回退 basic。"""
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in PRESETS else "basic"


def apply_preset(name: Optional[str] = None,
                 environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """应用预设：为预设包含、且**尚未被显式设置**的开关写入 "1"。

    - name 为 None 时取环境变量 `HASHMM_PRESET`（再无则 basic）。
    - 已存在的同名环境变量一律**不覆盖**（用户显式设置优先）。
    - 返回“本次实际设置的 {flag: value}”，供日志/测试核对。
    """
    env = environ if environ is not None else os.environ
    preset = resolve_preset_name(name if name is not None else env.get("HASHMM_PRESET"))
    flags = PRESETS.get(preset, {})
    applied: Dict[str, str] = {}
    for flag, value in flags.items():
        if env.get(flag) in (None, ""):     # 仅当用户没显式设过才写
            env[flag] = value
            applied[flag] = value
    return applied


def describe_preset(name: Optional[str] = None) -> str:
    """返回某预设会开启哪些功能的可读说明（给 UI / 日志 / 文档用）。"""
    preset = resolve_preset_name(name)
    flags = PRESETS.get(preset, {})
    if not flags:
        return f"预设 [{preset}]：不开启任何高级功能（基础路径）。"
    lines = [f"预设 [{preset}] 将开启 {len(flags)} 项高级功能："]
    for flag in flags:
        lines.append(f"  · {flag} —— {FLAG_DOCS.get(flag, '(无说明)')}")
    return "\n".join(lines)
