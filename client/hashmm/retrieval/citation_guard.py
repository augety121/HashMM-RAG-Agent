"""hashmm/rag/citation_guard.py —— 引用锚定校验（V317）。

对应 2026 行业指南 §五·三大生产硬约束之【幻觉】：
    "Agentic RAG 引入了新的失败模式——agent 把两个讲不同事情的 chunk 拼在一起，
     生成一个两边都不支持的结论。解法是引用锚定：要求每条断言标注具体 chunk ID，
     **没有引用的断言送人工审核**，这一条就能消掉大部分合成幻觉。"

项目现状：`_build_retrieval_injection` 已经要求模型给事实句加角标 [1][2]（输入侧
约束）。但**输出侧从来没校验过**——模型到底加没加、角标指向的 chunk 存不存在、
带数字的关键断言有没有引用支撑，全靠模型自觉。这是合成幻觉的漏网处。

本模块补输出侧这一环（纯本地、零 LLM 调用）：
  · 角标有效性：引用的编号必须落在实际检索到的来源范围内（[5] 但只召回了 3 条 = 幻觉引用）
  · 断言覆盖率：含数字/比例/日期的"硬事实句"是否带引用（这类句子最容易被编造）
  · 风险分级：ok / warn（部分断言无引用）/ risk（引用无效或硬事实全裸奔）

设计原则：**只诊断不拦截**。RAG 回答是流式输出的，事后拦截会破坏体验；这里产出
结构化诊断，交给上层决定怎么用（trace 展示 / 评测扣分 / 高风险时提示人工复核）。
"""
from __future__ import annotations

import re

__all__ = ["check_citations", "CitationReport"]

# 硬事实句：含数字、百分比、年份、金额——最容易被编造，必须有引用支撑。
_HARD_FACT = re.compile(
    r"\d+\s*(?:%|％|万|亿|元|美元|人|次|天|年|月|个|倍|分|名|位|条|款|项)"    # 数量/金额/单位
    r"|\d{4}\s*年"                                            # 年份
    r"|\d+\.\d+"                                              # 小数（比率/版本/指标）
    r"|第[一二三四五六七八九十\d]+"                            # 排名/序数（第一、第3）
)
# ★ V318 归因性断言：声称"有来源"却最容易被凭空编造的话（"研究表明"但没有引用 =
# 典型合成幻觉）。这类句子有引用才可信，没引用比裸数字更危险（伪装成有据可查）。
_ATTRIBUTION = re.compile(
    r"研究(表明|显示|发现|指出)|数据(显示|表明)|据.{0,8}(报告|研究|统计|调查|报道)"
    r"|调查(显示|发现)|报告(指出|显示|称)|专家(认为|指出|表示)"
    r"|according to|studies?\s+(show|indicate|suggest)|research\s+(shows?|indicates?)"
    r"|data\s+shows?|reports?\s+(show|indicate|say)",
    re.IGNORECASE)
# 角标：[1] [12] [1,2] [1、2]
_CITE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")
# 句子切分（中英文）。★ 逗号/分号也要切：中文一个句号内常并列多条断言
# （"营收增长了35%，用户数达到2.3亿"是两个独立的硬事实），只按句号切会漏掉
# 其中一条的引用缺失。
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+|，(?=[^，]{6,})")


class CitationReport(dict):
    """引用校验报告（dict 子类，便于直接进 trace / JSON）。"""

    @property
    def level(self) -> str:
        return self.get("level", "ok")

    @property
    def ok(self) -> bool:
        return self.get("level") == "ok"

    @property
    def detail(self) -> str:
        return self.get("detail", "")

    @property
    def coverage(self) -> float:
        return float(self.get("coverage", 1.0))


def _cited_ids(text: str) -> set[int]:
    out: set[int] = set()
    for m in _CITE.finditer(text or ""):
        for part in re.split(r"[,，、]", m.group(1)):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
    return out


def check_citations(answer: str, n_sources: int) -> CitationReport:
    """校验回答的引用锚定质量。

    answer:    模型的回答文本
    n_sources: 本轮实际检索到的来源条数（角标必须落在 1..n_sources 内）

    返回 CitationReport：
      level        ok / warn / risk
      cited        引用到的编号
      invalid      无效编号（超出来源范围 = 幻觉引用，模型编了个不存在的出处）
      hard_facts   硬事实句总数
      uncited_facts 没有引用支撑的硬事实句（指南说的"送人工审核"对象）
      detail       人话说明
    """
    answer = (answer or "").strip()
    rep = CitationReport({
        "level": "ok", "cited": [], "invalid": [], "hard_facts": 0,
        "uncited_facts": [], "coverage": 1.0, "detail": "",
    })
    if not answer or n_sources <= 0:
        rep["detail"] = "无检索来源，不适用引用校验"
        return rep

    cited = _cited_ids(answer)
    invalid = sorted(i for i in cited if i < 1 or i > n_sources)
    rep["cited"] = sorted(cited)
    rep["invalid"] = invalid

    # 逐句检查硬事实的引用覆盖
    uncited: list[str] = []
    n_hard = 0
    for sent in _SENT_SPLIT.split(answer):
        s = sent.strip()
        is_hard = bool(_HARD_FACT.search(s))
        is_attrib = bool(_ATTRIBUTION.search(s))
        # 数字类需要句子有一定长度（避免误伤"3个"这类碎片）；归因类不设长度门槛
        # （"据报告"虽短，但它标志整个断言声称有来源，正是要抓的）。
        if is_hard and len(s) < 6:
            continue
        if not is_hard and not is_attrib:
            continue
        n_hard += 1
        if not _CITE.search(s):
            tag = "[归因无据] " if is_attrib and not is_hard else ""
            uncited.append(tag + s[:56])
    rep["hard_facts"] = n_hard
    rep["uncited_facts"] = uncited[:5]
    rep["coverage"] = round(1.0 - len(uncited) / n_hard, 2) if n_hard else 1.0

    # 分级
    if invalid:
        rep["level"] = "risk"
        rep["detail"] = (f"⚠️ 引用了不存在的来源 {invalid}（本轮只检索到 {n_sources} 条）"
                         "——这是典型的幻觉引用，回答中的相关论断不可信。")
    elif n_hard and not cited:
        rep["level"] = "risk"
        rep["detail"] = (f"⚠️ 回答含 {n_hard} 句带数字的硬事实，但**一个引用角标都没有**"
                         "——无法判断这些数字来自文档还是模型编造，建议人工复核。")
    elif uncited:
        rep["level"] = "warn"
        rep["detail"] = (f"部分硬事实缺引用（{len(uncited)}/{n_hard} 句）："
                         f"{uncited[0][:30]}… 这类断言最易被编造，建议核对来源。")
    else:
        rep["detail"] = (f"引用锚定良好：{n_hard} 句硬事实全部有引用支撑"
                         if n_hard else "回答未涉及需引用的硬事实")
    return rep
