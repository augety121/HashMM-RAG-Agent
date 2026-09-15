"""hashmm/agent/arch_advisor.py — Agent 架构选型顾问（V274）。

面试资料反复强调却最容易被忽视的一课：**多 Agent 不一定比单 Agent 好**。本模块把
资料 6.1（四种架构的复杂度指标）+ 6.2（Google《Towards a Science of Scaling Agent
Systems》的选型原则）+ 6.5（金融/网页两个选型模板）固化成**可调用的决策函数**，
让系统在派活前先问一句"这个任务到底该用什么架构"，而不是无脑上多 Agent。

四种架构（资料 6.1 原文指标）：
  SAS 单体           : LLM O(T) / 通信 0  / 并行 1   —— 最简最稳；单体基线≥45% 再加协调常为负
  MAS 独立多体        : LLM O(N·T)+O(1) / 并行 N     —— 并行最高但错误放大最severe(17.2×)
  MAS 中心化(Orchestr): LLM O(R·N) / 通信 R·N        —— 稳健，错误放大压到 4.4×；可并行任务 +80.9%
  MAS 去中心化辩论     : 点对点多轮辩论 → 共识          —— 高熵探索/需互相纠错，协调税高

决策规则（资料 6.2 的三条 + 6.5 两模板）：
  1. 强顺序 / 长推理链（一步错步步错）→ SAS（多 Agent 反而放大错误）
  2. 单体基线已强（≥45%）/ 工具密集且预算固定 → SAS（协调税吞 token）
  3. 可拆分 + 需统一口径与质量门控（财务核对、批量报告、数据清洗）→ 中心化
  4. 高熵 + 路径不唯一 + 可并行覆盖（网页调研、多源搜集）→ 去中心化/独立多体
  5. 容错要求低的独立样本批处理（并行评审/方案生成）→ 独立多体

纯启发式、零 LLM、永不抛错——返回结构化建议供编排层参考或直接路由。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.arch_advisor")

# 各架构的资料原文指标卡（用于把"为什么"讲清楚，面试可直接背）
ARCH_CARDS = {
    "SAS": {"name": "单体架构 SAS", "llm": "O(T)", "comm": "0", "parallel": "1",
            "amplify": "—", "why": "最简最稳；顺序长链一步错步步错时最安全",
            "executor": "hashmm.agent.loop（单体 AgentLoop）"},
    "MAS_INDEP": {"name": "独立多体 MAS(Independent)", "llm": "O(N·T)+O(1)", "comm": "1(仅聚合)",
                  "parallel": "N", "amplify": "17.2×", "why": "并行度最高、协调最低，但错误放大最严重",
                  "executor": "hashmm.agent.subagents（并行独立子代理 + 聚合）"},
    "MAS_CENTRAL": {"name": "中心化 MAS(Orchestrator)", "llm": "O(R·N)", "comm": "R·N",
                    "parallel": "N", "amplify": "4.4×", "why": "权威收敛+质量门控，可并行任务提升可达 +80.9%",
                    "executor": "hashmm.agent.staff / orchestrator（中心化编排）"},
    "MAS_DECENTRAL": {"name": "去中心化辩论 MAS", "llm": "O(R·N)", "comm": "P2P·R",
                      "parallel": "N", "amplify": "中", "why": "点对点辩论互相纠错，适合高熵探索",
                      "executor": "hashmm.agent.debate（去中心化对等辩论）"},
}

_SEQ_HINT = re.compile(r"(依次|逐步|按顺序|串行|先.*再.*然后|step\s*by\s*step|一步一步)", re.I)
_PARALLEL_HINT = re.compile(r"(对比|比较|多个|各自|分别|并行|批量|多源|多家|汇总|搜集|调研)", re.I)
_TOOLHEAVY_HINT = re.compile(r"(调用|工具|api|检索|查询|执行|运行|抓取|爬)", re.I)
_HIGH_ENTROPY = re.compile(r"(搜索|网页|浏览|开放|探索|不确定|找出|查找.*资料|research)", re.I)
_STRICT_CONSISTENCY = re.compile(r"(财务|报表|核对|对账|合规|一致|口径|清洗|校验|审计|估值)", re.I)


@dataclass
class ArchAdvice:
    arch: str                       # SAS | MAS_INDEP | MAS_CENTRAL | MAS_DECENTRAL
    reason: str                     # 为什么选它（引用规则）
    n_agents: int = 1               # 建议 agent 数
    confidence: float = 0.6
    card: dict = field(default_factory=dict)
    alternatives: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"arch": self.arch, "arch_name": self.card.get("name", self.arch),
                "reason": self.reason, "n_agents": self.n_agents,
                "confidence": round(self.confidence, 2), "metrics": self.card,
                "executor": self.card.get("executor", ""),
                "alternatives": self.alternatives}


def advise(task: str, *, single_agent_baseline: float | None = None,
           tool_budget_fixed: bool = False, subtask_count: int | None = None) -> ArchAdvice:
    """给任务推荐架构。可选传入单体基线分（0-1）、预算是否固定、可拆子任务数。"""
    t = str(task or "")
    seq = bool(_SEQ_HINT.search(t))
    parallel = bool(_PARALLEL_HINT.search(t))
    toolheavy = bool(_TOOLHEAVY_HINT.search(t))
    high_entropy = bool(_HIGH_ENTROPY.search(t))
    strict = bool(_STRICT_CONSISTENCY.search(t))
    n_sub = subtask_count or (2 if parallel else 1)

    # 规则 1：强顺序 → SAS（多 Agent 放大错误）
    if seq and not parallel:
        return ArchAdvice("SAS", "任务是强顺序/长推理链（一步错步步错），单体最安全——"
                          "引入多 Agent 会放大错误、增加协调税。规则 6.2-1。",
                          1, 0.8, ARCH_CARDS["SAS"], ["MAS_CENTRAL"])

    # 规则 2：单体基线已强 或 工具密集+预算固定 → SAS
    if (single_agent_baseline is not None and single_agent_baseline >= 0.45) or (toolheavy and tool_budget_fixed):
        why = ("单体基线已≥45%，再加协调收益递减或为负" if (single_agent_baseline or 0) >= 0.45
               else "工具密集且预算固定，多 Agent 的协调沟通会吞掉有效 token")
        return ArchAdvice("SAS", f"{why}。规则 6.2-2。", 1, 0.75, ARCH_CARDS["SAS"], ["MAS_CENTRAL"])

    # 规则 3：需统一口径/质量门控（财务/核对/对账/清洗/审计）→ 中心化。
    # 这类任务天然分而治之（资料 6.5.2 金融推理模板），即便用户没写明「对比/多个」，
    # 也应交给权威 Orchestrator 收敛——此前误加 (parallel or n_sub>=2) 门槛，导致
    # 「对账核对财务报表统一口径」这种没有并行关键词的 strict 任务掉到默认 SAS（已修）。
    if strict:
        return ArchAdvice("MAS_CENTRAL", "任务可拆分且需要统一口径与质量门控（财务/核对/清洗类），"
                          "中心化 Orchestrator 有权威收敛，错误放大最低(4.4×)、可并行任务提升可达 +80.9%。"
                          "资料 6.5.2 金融推理模板。", min(max(n_sub, 2), 4), 0.8,
                          ARCH_CARDS["MAS_CENTRAL"], ["SAS", "MAS_DECENTRAL"])

    # 规则 4：高熵 + 可并行覆盖 → 去中心化/独立多体
    if high_entropy and parallel:
        return ArchAdvice("MAS_DECENTRAL", "高熵、路径不唯一、可并行覆盖（网页调研/多源搜集），"
                          "去中心化能并行加速、减少中心串行瓶颈，Agent 通过共享黑板发布/认领任务。"
                          "资料 6.5.1 动态网页浏览模板。", min(max(n_sub, 2), 4), 0.7,
                          ARCH_CARDS["MAS_DECENTRAL"], ["MAS_CENTRAL", "MAS_INDEP"])

    # 规则 5：容错要求低的独立样本批处理 → 独立多体
    if parallel and not strict:
        return ArchAdvice("MAS_INDEP", "独立样本批处理（并行评审/方案生成），并行度最高、协调最低；"
                          "注意错误放大最严重(17.2×)，仅在容错要求不高时用。资料 6.1-2。",
                          min(max(n_sub, 2), 4), 0.6, ARCH_CARDS["MAS_INDEP"], ["MAS_CENTRAL"])

    # 默认：单体（资料的一贯取舍——能一个人干完就一个人干完）
    return ArchAdvice("SAS", "无明显并行/拆分收益，单体最简最稳（能一个人干完就一个人干完）。"
                      "默认取舍。", 1, 0.65, ARCH_CARDS["SAS"], ["MAS_CENTRAL"])
