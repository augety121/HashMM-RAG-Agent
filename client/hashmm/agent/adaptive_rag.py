"""hashmm/agent/adaptive_rag.py — 自适应 RAG 路由（V311）。

2026 Adaptive RAG 范式：RAG 不再"一刀切每问必检索一次"，而是先用一个零成本的
查询复杂度分类器，把每个查询路由到匹配的检索策略（业内共识的决策树）：

    no_retrieval  寒暄 / 纯生成任务（写代码、翻译、起草）   → 直接回答，0 次检索
    single        简单事实 / 定义类单跳问题                → 1 轮检索，top_k=5（成本最低）
    multi_hop     因果链 / 分步推理（先…再…、原因→方案）    → 迭代检索，top_k=8，≤3 轮
    complex       对比 / 列举 / 汇总（需跨多文档聚合）      → 宽检索，top_k=12，≤5 轮

迭代上限直接来自生产约束（大多数教程不讲的三个硬约束之一）：
  · 检索迭代上限通常设 3 轮，很少有理由超过 5 轮——复杂查询 4 轮检索 + 全量重排
    可能比单轮贵 20-40 倍；
  · 配套 EarlyExit：自评分数连续两轮偏高就提前退出，省 token 也省延迟。

与既有模块的分工（不重复）：
  · hashmm.kg.kg_router.classify_query —— 判"用哪种 KG 检索模式"（意图路由）；
  · hashmm.agent.agentic_rag         —— 检索【后】的 CRAG 纠错编排流；
  · 本模块                            —— 检索【前】的复杂度路由：要不要检索、
                                         检索多宽（top_k）、最多迭代几轮。
    路由决策可直接喂给 agentic_rag 的 plan 阶段或 AutoRetriever（已接入后者）。

开关：HASHMM_ADAPTIVE_RAG=0 关闭 —— route() 恒返回 single（top_k=5、1 轮），
与旧链路"命中即单轮检索"零差异。classify() 是纯函数、不受开关影响，便于测试。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.adaptive_rag")

__all__ = ["RouteStrategy", "RouteDecision", "classify", "route", "enabled", "EarlyExit"]


class RouteStrategy(str, Enum):
    NO_RETRIEVAL = "no_retrieval"
    SINGLE = "single"
    MULTI_HOP = "multi_hop"
    COMPLEX = "complex"


# 每档策略的 (top_k, 最大检索轮数)。可用环境变量微调宽度/轮数上限。
_BUDGETS: dict[RouteStrategy, tuple[int, int]] = {
    RouteStrategy.NO_RETRIEVAL: (0, 0),
    RouteStrategy.SINGLE: (int(os.environ.get("HASHMM_RAG_TOPK_SINGLE", "5")), 1),
    RouteStrategy.MULTI_HOP: (int(os.environ.get("HASHMM_RAG_TOPK_MULTIHOP", "8")), 3),
    RouteStrategy.COMPLEX: (int(os.environ.get("HASHMM_RAG_TOPK_COMPLEX", "12")), 5),
}


@dataclass
class RouteDecision:
    strategy: RouteStrategy
    top_k: int
    max_iterations: int
    reason: str

    @property
    def should_retrieve(self) -> bool:
        return self.strategy is not RouteStrategy.NO_RETRIEVAL


def _decision(strategy: RouteStrategy, reason: str) -> RouteDecision:
    k, iters = _BUDGETS[strategy]
    return RouteDecision(strategy=strategy, top_k=k, max_iterations=iters, reason=reason)


# ---------------------------------------------------------------- 模式库
# 寒暄/客套/确认——整句匹配才算（避免"你好，帮我查合同"被误杀）。
_SMALLTALK = re.compile(
    r"^(你好|您好|哈喽|嗨|hi|hello|hey|在吗|在不在|"
    r"谢谢|多谢|感谢|辛苦了|"
    r"再见|拜拜|晚安|早上好|中午好|下午好|晚上好|"
    r"好的|好|嗯+|哦+|行|收到|明白|ok|okay|thanks|thank you|没事|不客气)"
    r"[!！。.~～?？\s]*$",
    re.IGNORECASE,
)

# 纯生成任务：写/画/翻译/润色等创作动词（带量词或典型宾语形态）。
# 若同时出现知识库指向词（见 _DOC_REF），则不算纯生成——"根据文档写方案"仍要检索。
_GENERATION = re.compile(
    r"(写|编写|撰写|生成|创建|起草|画|做)一?[个份段篇首张套幅]"
    r"|翻译|改写|重写|润色|续写|扩写|缩写|校对一下",
    re.IGNORECASE,
)

# 知识库/上下文指向词：出现即说明答案依赖已有资料。
_DOC_REF = re.compile(
    r"文档|文件|论文|报告|合同|资料|知识库|手册|说明书|规范|条款|附件|"
    r"数据|记录|日志|里面|其中|上面提到|前面说|"
    r"根据|按照|参考|依据|结合",
    re.IGNORECASE,
)

# 对比/列举/汇总——需要跨多文档聚合的宽检索信号。
_COMPLEX = re.compile(
    r"对比|比较|区别|差异|异同|优缺点|优劣|孰优|vs\.?|versus|"
    r"列举|穷举|罗列|盘点|汇总|排名|排行|top\s*\d+|"
    r"(所有|全部|哪些).{0,10}?(格式|类型|方法|方式|选项|功能|工具|文件|接口|字段|情况|种类|渠道)|"
    r"一共有(哪些|什么|多少)",
    re.IGNORECASE,
)

# 分步/因果推理——需要"检索→思考→再检索"的迭代信号。
_MULTI_HOP = re.compile(
    r"先.{0,14}?(再|然后|接着)|"
    r"首先.{0,24}?(其次|然后|接着|最后)|"
    r"为什么.{0,24}?(怎么|如何|方案|解决|对策)|"
    r"原因.{0,16}?(方案|解决|对策|建议)|"
    r"(分析|诊断|排查|定位).{0,12}?(并|再|然后).{0,10}?(给出|提出|提供|输出)|"
    r"step\s*by\s*step|一步一步|逐步分析",
    re.IGNORECASE,
)

# 单跳问句信号。
_QUESTION = re.compile(
    r"什么|多少|哪|谁|何时|几点|几个|多久|怎么|如何|为什么|为何|是否|能否|可不可以|行不行|吗|呢|\?|？",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- 分类与路由
def classify(query: str) -> RouteDecision:
    """按复杂度分类（纯函数，不看开关）。判断顺序即优先级。"""
    q = (query or "").strip()
    if len(q) < 2:
        return _decision(RouteStrategy.NO_RETRIEVAL, "超短输入")
    if _SMALLTALK.match(q):
        return _decision(RouteStrategy.NO_RETRIEVAL, "寒暄/客套")
    if _GENERATION.search(q) and not _DOC_REF.search(q):
        # 纯创作（写排序、翻译一段）不需要知识库；带资料指向的生成仍会落到下面的检索档。
        return _decision(RouteStrategy.NO_RETRIEVAL, "纯生成任务，无知识库指向")
    if _COMPLEX.search(q):
        return _decision(RouteStrategy.COMPLEX, "对比/列举/汇总 → 需跨文档宽检索")
    if _MULTI_HOP.search(q):
        return _decision(RouteStrategy.MULTI_HOP, "分步/因果推理 → 迭代检索")
    if _DOC_REF.search(q) or _QUESTION.search(q) or len(q) >= 30:
        return _decision(RouteStrategy.SINGLE, "事实/定义类单跳问题")
    return _decision(RouteStrategy.NO_RETRIEVAL, "无检索信号（短陈述/指令）")


def enabled() -> bool:
    """自适应路由是否开启（默认开；HASHMM_ADAPTIVE_RAG=0/false/off 关闭）。"""
    return (os.environ.get("HASHMM_ADAPTIVE_RAG", "1").strip().lower()
            not in ("0", "false", "off", "no"))


def route(query: str) -> RouteDecision:
    """带开关的路由入口。关闭时恒返回 single（旧行为：命中即单轮 top_k=5）。"""
    if not enabled():
        return _decision(RouteStrategy.SINGLE, "adaptive 关闭 → 旧行为固定单轮检索")
    d = classify(query)
    logger.debug("adaptive-rag route: %s (top_k=%d, iters=%d) %.30s",
                 d.strategy.value, d.top_k, d.max_iterations, query or "")
    return d


# ---------------------------------------------------------------- 提前退出
@dataclass
class EarlyExit:
    """迭代检索的提前退出判定（生产约束：自评分连续 patience 轮 ≥ threshold 就停）。

    用法（在迭代检索循环里）：
        ee = EarlyExit()                       # 默认 0.8 分、连续 2 轮
        scores: list[float] = []
        for i in range(decision.max_iterations):
            ... 检索 + 生成 + 自评 -> s ...
            scores.append(s)
            if ee.should_stop(scores):
                break
    """
    threshold: float = float(os.environ.get("HASHMM_RAG_EARLY_EXIT_SCORE", "0.8"))
    patience: int = int(os.environ.get("HASHMM_RAG_EARLY_EXIT_PATIENCE", "2"))

    def should_stop(self, scores) -> bool:
        s = [float(x) for x in (scores or [])]
        if len(s) < max(1, self.patience):
            return False
        return all(x >= self.threshold for x in s[-self.patience:])
