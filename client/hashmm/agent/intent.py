"""意图理解与主动澄清（路线图阶段 B）——从"执行字面命令"到"理解真正意图"。

大厂 Agent 与 demo 的最大差距不在能力，在"懂不懂用户"。本模块在 Agent 正式
开跑前做一次轻量意图分析：把用户输入解析成三层，评估置信度，**只在真正模糊
时**提一个关键澄清问题，而不是猜错方向跑一大圈。

三层意图（对标产品经理拆需求的方式）：
    surface  表层请求：用户字面说的
    goal     深层目标：用户真正想达成的
    constraints 隐含约束：没明说但必须遵守的（"别删我还要的""带出处"…）

设计铁律：
- **不啰嗦**：只在置信度低于阈值、且能问出一个有效问题时才澄清；
  高置信度直接放行（大部分请求都应直接执行，不打断）。
- **纯函数、失败安全**：分析失败一律按"高置信度、直接执行"处理，绝不因为
  意图模块把正常请求卡住。
- **可被用户画像增强**：传入 user_prefs（来自 evolution/user_model）时，
  把用户偏好作为隐含约束自动补齐，减少反复询问。

主入口：
    analyze(query, history, user_prefs) -> IntentResult
      .needs_clarification / .clarifying_question / .confidence
      .surface / .goal / .constraints / .to_system_hint()
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.intent")

# 置信度阈值：低于此值且能问出有效问题才澄清（默认 0.55，偏向"直接执行不打断"）
_CLARIFY_THRESHOLD = float(os.environ.get("HASHMM_CLARIFY_THRESHOLD", "0.55") or "0.55")
# 关闭开关：HASHMM_INTENT_CLARIFY=0 时永不主动澄清（只做意图标注，不打断）
_CLARIFY_ON = (os.environ.get("HASHMM_INTENT_CLARIFY", "1") or "1") != "0"


@dataclass
class IntentResult:
    surface: str
    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    confidence: float = 1.0
    needs_clarification: bool = False
    clarifying_question: str = ""
    ambiguity_reason: str = ""

    def to_system_hint(self) -> str:
        """把理解到的意图注入 system prompt，让 Agent 带着目标/约束干活。"""
        if not self.goal and not self.constraints:
            return ""
        parts = ["## 对用户意图的理解（据此规划，不要偏离）"]
        if self.goal:
            parts.append(f"- 深层目标：{self.goal}")
        if self.constraints:
            parts.append("- 需遵守的约束：" + "；".join(self.constraints))
        return "\n".join(parts)


# ── 模糊信号：这些特征叠加会拉低置信度 ──────────────────────────────────
# 指代词（缺主语/宾语，可能指上文，短对话里易歧义）
_PRONOUN = re.compile(r"(那个|这个|它|他们|上面(那|的)|刚才(那|的)|之前(那|的)|前面(那|的))")
# 太短又没动词（"帮我弄一下"这类）
_VAGUE_VERB = re.compile(r"(弄|搞|处理|整|看看|试试|优化)一下?$")
# 多义高危词（"整理""处理"可能删可能改，需确认范围/是否可逆）
_DESTRUCTIVE_HINT = re.compile(r"(整理|清理|删除|去重|归类|合并|重命名|移动)")
# 明确的可执行信号（有这些反而加分：给了 URL、明确文件类型、明确产出格式）
_CLEAR_SIGNAL = re.compile(r"(https?://|\.pdf|\.docx?|\.xlsx?|\.csv|\.py|\.md|表格|清单|报告|代码|翻译成|总结成)")


def _extract_goal_and_constraints(query: str) -> tuple[str, list[str]]:
    """从字面请求粗解深层目标与隐含约束（启发式，够用即可，不追求完美）。"""
    q = query.strip()
    goal = ""
    constraints: list[str] = []

    # 破坏性操作 → 隐含约束：可逆 + 先确认
    if _DESTRUCTIVE_HINT.search(q):
        constraints.append("涉及改动文件时，先列出将影响的项让用户确认，不擅自删除不可恢复的内容")

    # 明确要出处 / 结构化 → 约束
    if re.search(r"(出处|引用|来源|依据)", q):
        constraints.append("回答需标注来源/依据")
    if re.search(r"(表格|清单|列表|结构化|分点)", q):
        constraints.append("以结构化形式（表格/清单/分点）呈现")
    if re.search(r"(简洁|简短|精简|一句话)", q):
        constraints.append("尽量简洁")

    # 深层目标：破坏性操作的真实目标通常是"找到/整理到位"，而非机械执行
    if re.search(r"整理|清理", q):
        goal = "把目标内容整理到用户真正可用的状态，而不仅是机械执行动作"
    elif re.search(r"深度调研|全面了解|系统(地)?学习", q):
        goal = "多源交叉、得到可信且成体系的结论，而非单点信息"

    return goal, constraints


def _score_confidence(query: str) -> tuple[float, str]:
    """给"我是否看得懂这个请求"打分（0~1）。返回 (置信度, 主要模糊原因)。"""
    q = query.strip()
    score = 0.8  # 基线：大部分请求是清楚的
    reason = ""

    # 太短（<6 字）且无明确信号 → 大幅降分
    if len(q) < 6 and not _CLEAR_SIGNAL.search(q):
        score -= 0.35
        reason = "请求过短、缺少可执行的具体信息"

    # 含指代词但历史很短（无从解析指代）→ 降分（历史在 analyze 里判断）
    if _PRONOUN.search(q):
        score -= 0.15
        reason = reason or "含指代词，指向不明确"

    # "弄一下/搞一下"这类无实义动词收尾 → 降分
    if _VAGUE_VERB.search(q):
        score -= 0.25
        reason = reason or "动作笼统、未说明具体要做什么"

    # 破坏性操作但没说范围/对象 → 降分（宁可问一句，别误删）
    if _DESTRUCTIVE_HINT.search(q) and not re.search(r"(文件夹|目录|文件|这些|全部|所有|下载|桌面|文档)", q):
        score -= 0.2
        reason = reason or "涉及改动但未说明作用范围/对象"
        # V209：短句 + 完全没给对象（"帮我整理一下"）→ 无从下手，必须先问——再降一档
        if len(q) <= 8:
            score -= 0.1

    # 有明确信号（URL/文件类型/产出格式）→ 加分
    if _CLEAR_SIGNAL.search(q):
        score += 0.2

    return max(0.0, min(1.0, score)), reason


def _make_question(reason: str, query: str) -> str:
    """据模糊原因生成一个具体、好回答的澄清问题（避免开放式空泛提问）。"""
    if "过短" in reason or "笼统" in reason:
        return "想让我具体做什么呢？比如查资料、整理文件、写文案还是跑一段代码——说一句目标我就开始。"
    if "指代" in reason:
        return "你指的是哪一个？（可以把对象名称或关键词说一下，我好准确处理）"
    if "作用范围" in reason or "改动" in reason:
        return "这个操作会改动文件，想确认下范围：具体针对哪个文件夹/哪些文件？需要我先列出将影响的项给你过目吗？"
    return "为了更准确地帮你，能补充一句你的具体目标或范围吗？"


def analyze(query: str, history: list[dict] | None = None,
            user_prefs: dict | None = None) -> IntentResult:
    """意图分析主入口。失败安全：任何异常都返回"高置信度、直接执行"。"""
    try:
        q = (query or "").strip()
        if not q:
            return IntentResult(surface=q, confidence=1.0)

        goal, constraints = _extract_goal_and_constraints(q)
        conf, reason = _score_confidence(q)

        # 指代词但有足够历史 → 认为可从上下文解析，回补置信度
        if _PRONOUN.search(q) and history and len([h for h in history if h.get("role") == "user"]) >= 1:
            conf = min(1.0, conf + 0.15)
            if "指代" in reason:
                reason = ""

        # 用户画像作为隐含约束自动补齐（减少反复询问）
        if user_prefs:
            style = user_prefs.get("answer_style") or user_prefs.get("style")
            if style == "concise" and "尽量简洁" not in constraints:
                constraints.append("尽量简洁（用户长期偏好）")
            if user_prefs.get("cite_sources") and "回答需标注来源/依据" not in constraints:
                constraints.append("回答需标注来源/依据（用户长期偏好）")

        needs = bool(_CLARIFY_ON and conf < _CLARIFY_THRESHOLD and reason)
        question = _make_question(reason, q) if needs else ""

        return IntentResult(
            surface=q, goal=goal, constraints=constraints,
            confidence=round(conf, 2),
            needs_clarification=needs, clarifying_question=question,
            ambiguity_reason=reason,
        )
    except Exception as e:  # pragma: no cover
        logger.debug(f"intent.analyze failed, fallback to direct-run: {e}")
        return IntentResult(surface=query or "", confidence=1.0)
