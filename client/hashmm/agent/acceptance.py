"""通用交付验收标准（路线图阶段 C）——把"质量闸"从代码/RAG 推广到每类任务。

现状：loop.py 已有 verify-fix（代码编译校验）、DoD（todo 完成度）、引用接地
校验（RAG 幻觉引用）——但都是特定任务专用。阶段 C 把"完成 ≠ 完成得好"这件事
标准化：**每类任务都有一份验收要点**，模型宣布完成时对照自检，缺项给一次
修正机会，交付高质量结果而非"能跑就行"。

设计：
- 按任务类型（写作/调研/文档提炼/翻译/规划…）内置验收要点清单；
- `check(task_type, query, answer)` 返回 (是否达标, 缺失要点列表)——
  纯启发式（长度/结构/是否覆盖关键要素），够用即可，不追求 NLU 完美；
- 与现有 verify/DoD/引用校验**同点同模式、互补不重叠**：那三个管代码/todo/引用，
  这个管"内容层面的交付质量"；
- 失败安全：检查异常一律视为达标（不因质检把正常交付卡住）。

只触发一次（loop 侧用 flag 控制，防死循环）。
"""
from __future__ import annotations

import os
import re

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.acceptance")

_ON = (os.environ.get("HASHMM_ACCEPTANCE_CHECK", "1") or "1") != "0"


def _task_kind(task_type: str, query: str) -> str:
    """把 task_type + query 归一到验收类别。"""
    q = (query or "")
    tt = (task_type or "")
    if re.search(r"翻译|translate|译成", q):
        return "translation"
    if re.search(r"调研|research|全面(了解|分析)|综述|市场分析", q) or tt == "analysis_task":
        return "research"
    if re.search(r"提炼|要点|总结|摘要|归纳|梳理", q):
        return "summarize"
    if re.search(r"计划|方案|规划|安排|步骤|roadmap|路线", q):
        return "planning"
    if re.search(r"写(一)?(篇|段|个)?(文案|稿|邮件|文章|介绍|说明|周报|报告)|起草|润色", q) or tt == "doc_task":
        return "writing"
    return "generic"


# ── 各类任务的验收要点（返回 缺失要点 列表；空=达标）────────────────────
def _check_writing(query: str, ans: str) -> list[str]:
    miss = []
    if len(ans.strip()) < 40:
        miss.append("篇幅过短，正文不完整")
    # 要求分点/结构却是一坨 → 缺结构
    if re.search(r"分点|条理|结构化|要点", query) and not re.search(r"(\n\s*[-*·]|\n\s*\d+[.、)]|##)", ans):
        miss.append("未按要求分点/结构化呈现")
    return miss


def _check_research(query: str, ans: str) -> list[str]:
    miss = []
    if len(ans.strip()) < 120:
        miss.append("调研结论过于单薄，不成体系")
    # 调研应有多来源/多角度痕迹
    if not re.search(r"(来源|参考|根据|数据|其一|其二|首先|其次|一方面|综合)", ans):
        miss.append("缺少多源/多角度的综合，更像单点信息")
    return miss


def _check_summarize(query: str, ans: str) -> list[str]:
    miss = []
    if len(ans.strip()) < 40:
        miss.append("提炼内容过少")
    if re.search(r"清单|列表|要点|分点", query) and not re.search(r"(\n\s*[-*·]|\n\s*\d+[.、)])", ans):
        miss.append("要求清单形式，但未列成条目")
    return miss


def _check_translation(query: str, ans: str) -> list[str]:
    miss = []
    if len(ans.strip()) < 5:
        miss.append("译文为空或过短")
    # 要求译成中文却整段仍是英文（粗判：ASCII 字母占比过高）
    if re.search(r"译成中文|翻译成中文|中文", query):
        ascii_ratio = sum(c.isascii() and c.isalpha() for c in ans) / max(1, len(ans))
        if ascii_ratio > 0.6:
            miss.append("要求译成中文，但译文仍以外文为主")
    return miss


def _check_planning(query: str, ans: str) -> list[str]:
    miss = []
    # 计划类应有明确步骤
    if not re.search(r"(\n\s*\d+[.、)]|第[一二三四五六]步|步骤|阶段|首先|然后|最后)", ans):
        miss.append("缺少清晰的步骤/阶段划分")
    return miss


_CHECKERS = {
    "writing": _check_writing,
    "research": _check_research,
    "summarize": _check_summarize,
    "translation": _check_translation,
    "planning": _check_planning,
}

# 验收要点的人话描述（给模型看的修正提示）
_CRITERIA_DESC = {
    "writing": "完整成文、若要求分点则分点、语气契合场景",
    "research": "多源交叉、有依据、成体系而非单点",
    "summarize": "覆盖关键要点、若要求清单则列成条目",
    "translation": "目标语言正确、完整、通顺",
    "planning": "有清晰的步骤/阶段、可执行",
    "generic": "完整回应了用户的请求",
}


def check(task_type: str, query: str, answer: str) -> tuple[bool, list[str], str]:
    """交付质量自检。返回 (是否达标, 缺失要点列表, 该类任务的验收标准描述)。

    失败安全：任何异常都返回达标（不卡正常交付）。
    """
    if not _ON:
        return True, [], ""
    try:
        kind = _task_kind(task_type, query)
        desc = _CRITERIA_DESC.get(kind, _CRITERIA_DESC["generic"])
        checker = _CHECKERS.get(kind)
        if checker is None:
            return True, [], desc
        missing = checker(query or "", answer or "")
        return (len(missing) == 0), missing, desc
    except Exception as e:  # pragma: no cover
        # A quality gate is evidence, not an optional decoration.  Returning
        # ``passed`` when the checker itself crashed makes the outer loop emit
        # an unqualified completion trace and is indistinguishable from a real
        # acceptance.  Keep the answer deliverable (the loop gets one bounded
        # repair turn) but make the uncertainty explicit and auditable.
        logger.warning("acceptance.check unavailable; withholding pass: %s", e)
        return False, ["交付质量检查器异常，无法确认本次结果"], (
            "交付质量检查器必须成功运行；若无法运行，不应把结果标记为已验收"
        )


def build_fix_prompt(missing: list[str], desc: str) -> str:
    """据缺失要点生成给模型的修正指令。"""
    return (
        "⚠️ 交付质量自检未通过。本类任务的验收标准是：" + desc + "。\n"
        "当前回答存在以下问题，请修正后重新给出完整答案（不要只解释，直接产出达标的结果）：\n- "
        + "\n- ".join(missing)
    )
