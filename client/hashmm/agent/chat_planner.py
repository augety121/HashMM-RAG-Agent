"""Chat 智能规划器（V262）——把"资深工程师的思考方式"做进问答。

不是让模型"答前列个提纲"那么表面。要复刻的是这样一种思考方式：
  · 联系上下文：先回看这轮对话前面聊了什么，判断这次请求和之前是什么关系
    （延续 / 修正 / 追问 / 转换话题），抓住用户连续的真实意图，而不是把每句话
    当孤立的新问题。这是"听得懂人话"的核心——人说话是带着上文的。
  · 想透再答：读懂问题真正要什么（往往比字面更大或更具体）、需要分几步、
    有哪些坑和隐含前提、现有信息够不够。
  · 先确认再执行：信息缺到会让方向跑偏时，先问一句关键的，而不是猜着往下冲
    （对应作者反复强调的"我先读透了再动手，怕方向错了跑一大圈"）。

对外接口：
  assess(fn, query, history, ...) -> PlanResult
    返回 {mode, plan_text, clarify_question, context_note}。
    · mode="answer"：想清楚了，plan_text 注入回答 prompt，按规划作答。
    · mode="clarify"：缺关键信息，clarify_question 是要先问用户的那一句。
  needs_planning(query, task_type, history, file_context) -> bool
    是否值得走这套（简单/闲聊/纯查一个事实不走，不增延迟）。

纪律：一次轻量调用（带 harness 重试）；任何失败都降级为"直接答"（规划是增益不是
依赖）；产出广播进全局工作区（可解释）。settings『chat_planning=off』全局关闭；
settings『chat_clarify=off』只关澄清、保留规划。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger

logger = get_logger("hashmm.chat_planner")

_PLAN_SIGNALS = (
    "怎么做", "如何", "帮我写", "帮我做", "设计", "方案", "架构", "重构", "优化",
    "分析", "对比", "评估", "规划", "计划", "实现", "搭建", "开发", "整套", "完整",
    "一步步", "步骤", "流程", "为什么", "原理", "推导", "证明", "排查", "定位",
    "系统", "项目", "从零", "从头", "端到端", "全流程", "策略", "选型", "取舍",
    "plan", "design", "architect", "implement", "compare", "analyze", "refactor",
)
_SKIP_SIGNALS = ("你好", "hello", "hi", "谢谢", "在吗", "嗨", "你是谁", "hey")
# 上下文延续信号：这些词强烈暗示"这句话接着上文说"——即便本句很短也要联系上下文
_FOLLOWUP_SIGNALS = (
    "继续", "接着", "然后呢", "还有呢", "那", "这个", "它", "上面", "刚才", "之前",
    "再", "改成", "换成", "不对", "不是这个", "重来", "为什么", "怎么还", "还是",
    "这样", "那样", "你说的", "你刚", "第二", "第三", "下一", "接下来",
)


@dataclass
class PlanResult:
    mode: str = "answer"                 # answer | clarify
    plan_text: str = ""                  # 注入回答 prompt 的规划
    clarify_question: str = ""           # mode=clarify 时要先问的一句
    context_note: str = ""              # 与上下文关系的一句话（trace 展示）
    requirements: list = field(default_factory=list)   # V263: 拆出的诉求清单（收尾自审用）
    signals: dict = field(default_factory=dict)


def _get(key: str, default: str) -> str:
    try:
        from hashmm.api.settings_store import get_setting
        return (get_setting(key, default) or default).strip().lower()
    except Exception:
        return default


def _enabled() -> bool:
    return _get("chat_planning", "on") not in ("off", "0", "false")


def _clarify_enabled() -> bool:
    return _get("chat_clarify", "on") not in ("off", "0", "false")


def _has_context(history: list | None) -> bool:
    """这轮对话前面有没有实质内容（不含本条）。"""
    if not history:
        return False
    real = [h for h in history if h.get("role") in ("user", "assistant") and (h.get("content") or "").strip()]
    return len(real) >= 1


_INTENT_VERBS = ("写", "译", "翻译", "列", "总结", "分析", "改", "优化", "生成", "做", "画",
                 "查", "发", "整理", "归纳", "提炼", "输出", "给出", "设计", "实现", "搭建",
                 "评估", "对比", "排查", "部署", "安装", "下载", "导出", "转换", "汇总", "算")
_MI_CONN = ("然后", "接着", "并且", "同时", "顺便", "另外", "还要", "最后", "再")


def _multi_intent(q: str) -> bool:
    """并列结构多诉求（V281）：按分隔符/连接词切段，含动作动词的段 >=2 即多诉求。
    保守约束：段长 >=3；「再」仅在其后紧跟动作动词时才视为连接（避免"再见"误切）。"""
    try:
        text = str(q or "")
        for c in _MI_CONN:
            if c == "再":
                text = re.sub("再(?=[" + "".join(v[0] for v in _INTENT_VERBS) + "])", "\u0001", text)
            else:
                text = text.replace(c, "\u0001")
        segs = [x.strip() for x in re.split("[\u0001，,;；]", text) if len(x.strip()) >= 3]
        acts = sum(1 for seg in segs if any(v in seg for v in _INTENT_VERBS))
        return acts >= 2
    except Exception:  # noqa: BLE001
        return False


def needs_planning(query: str, task_type: str = "", history: list | None = None,
                   file_context: str = "") -> bool:
    if not _enabled():
        return False
    q = (query or "").strip()
    ql = q.lower()
    if len(q) < 12 and any(s in ql for s in _SKIP_SIGNALS):
        return False
    # 明确的代码/文档/复杂类任务：一定走
    if task_type in ("code_task", "doc_task", "complex_task"):
        return True
    # 带上下文 + 本句像是接着上文说（追问/修正/延续）→ 走：这正是"联系上下文"最该发力处，
    # 哪怕本句很短（"改成 Java""那并发怎么办""为什么"）也要回看历史才答得对。
    if _has_context(history) and any(s in ql for s in _FOLLOWUP_SIGNALS):
        return True
    if len(q) < 10:
        return False
    if len(q) >= 80:
        return True
    # V281 多诉求并列结构（资料 5.1：多个并列诉求要拆解逐条落实）——
    # 「写总结，翻译成英文，再列三条建议」这类连接词串联多个动作，关键词命中数低但确是多诉求。
    if _multi_intent(q):
        return True
    hits = sum(1 for s in _PLAN_SIGNALS if s in ql)
    return hits >= (1 if file_context else 2)


def _fmt_history(history: list | None, max_turns: int = 6, budget: int = 1600) -> str:
    """把最近几轮对话整理成可读上下文（给规划器看的）。"""
    if not history:
        return ""
    real = [h for h in history if h.get("role") in ("user", "assistant") and (h.get("content") or "").strip()]
    real = real[-max_turns:]
    lines = []
    total = 0
    for h in real:
        who = "用户" if h.get("role") == "user" else "助手"
        c = re.sub(r"\s+", " ", str(h.get("content") or "")).strip()[:400]
        seg = f"{who}：{c}"
        total += len(seg)
        if total > budget:
            break
        lines.append(seg)
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def assess(fn, query: str, *, history: list | None = None, task_type: str = "",
           file_context: str = "", rag_hint: str = "", user_id: str = "") -> PlanResult:
    """产出规划或澄清。失败返回 mode=answer 且 plan_text 为空（＝直接答）。"""
    if fn is None or not _enabled():
        return PlanResult()

    hist_txt = _fmt_history(history)
    ctx = ""
    if hist_txt:
        ctx += f"\n【本轮对话前面的内容（务必联系它理解本次请求）】\n{hist_txt}\n"
    if file_context:
        ctx += f"\n【用户带的文件/上下文片段】\n{file_context[:1000]}\n"
    if rag_hint:
        ctx += f"\n【知识库可能相关的片段】\n{rag_hint[:700]}\n"

    allow_clarify = _clarify_enabled()
    # 上一条已经是助手在追问，就别再追问了（防来回打转）
    if history:
        last = next((h for h in reversed(history) if h.get("role") == "assistant"), None)
        if last and ("?" in (last.get("content") or "") or "？" in (last.get("content") or "")):
            allow_clarify = False

    prompt = (
        "你是一个做事很稳的资深工程师。在正式回答用户前，先做一次扎实的思考（这是内部"
        "思考，不是最终答案）。像真正听得懂人话的人那样：先回看上文，弄清这次到底在问"
        "什么、和前面什么关系，再决定怎么答。严格按下面的 JSON 输出，不要输出别的：\n"
        "{\n"
        '  "context_relation": "本次请求与上文的关系：延续/修正上一步/追问细节/换新话题/无上文，一句话说清它承接了什么",\n'
        '  "real_intent": "用户真正想达成什么（往往比字面更大或更具体，结合上文判断）",\n'
        '  "requirements": ["把用户这次提出的每一个独立诉求逐条列出来（一句话里常夹带多个要求，逐条拆开，一个都别漏；只有一个诉求就一条）"],\n'
        '  "steps": ["把这件事办成要分的几步（复杂3-6步，简单1-2步）。每步是一个【业务子目标】'
        '（如：查档期/选技师/确认时间/下单），不是界面操作——严禁写打开应用、点击按钮、'
        '填写表单这类微步骤；用户话里显式或隐含的每个子目标都必须独立成一步，一个都不能漏'
        '（预约类任务通常隐含：查可约时段、选服务人员、和用户确认时间、正式下单）"],\n'
        '  "pitfalls": ["容易出错的点、隐含前提、要区分的情形、别过度扩大的范围"],\n'
        '  "need_clarify": false,\n'
        '  "clarify_question": "只有当缺少关键信息、不问就很可能答偏时才填这一句最关键的问题；否则留空",\n'
        '  "self_check": ["回答后要回头核对的几条（每个诉求都覆盖了吗、代码能跑吗、数字对吗）"]\n'
        "}\n\n"
        f"用户本次的话：{query}\n{ctx}\n"
        + ("" if allow_clarify else "注意：本轮不要提出澄清问题（need_clarify 一律 false），直接想清楚怎么答。\n")
        + "只输出上面的 JSON。"
    )

    try:
        from hashmm.agent.harness import run_llm
        raw = run_llm(fn, prompt, tag="chat:plan", retries=1, min_len=10)
    except Exception as e:  # noqa: BLE001
        logger.debug("[chat_planner] 规划调用失败：%s", e)
        return PlanResult()
    if not raw:
        return PlanResult()

    data = None
    m = _JSON_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group())
        except Exception:
            data = None
    if not isinstance(data, dict):
        # 解析失败：把原文当规划注入（仍是增益），不澄清
        return PlanResult(mode="answer", plan_text=_wrap_plan_freeform(raw.strip()))

    relation = str(data.get("context_relation") or "").strip()
    intent = str(data.get("real_intent") or "").strip()
    reqs = [str(s).strip() for s in (data.get("requirements") or []) if str(s).strip()]
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    pitfalls = [str(s).strip() for s in (data.get("pitfalls") or []) if str(s).strip()]
    checks = [str(s).strip() for s in (data.get("self_check") or []) if str(s).strip()]
    need_clarify = bool(data.get("need_clarify")) and allow_clarify
    clarify_q = str(data.get("clarify_question") or "").strip()

    try:
        from hashmm.agent.global_workspace import broadcast
        _rn = f"{len(reqs)} 项诉求" if len(reqs) > 1 else (relation[:20] or "独立问题")
        broadcast("chat", "plan", f"已想清楚（{_rn}）：{query[:40]}",
                  salience=0.35, user=user_id)
    except Exception:
        pass

    if need_clarify and clarify_q:
        return PlanResult(mode="clarify", clarify_question=clarify_q,
                          context_note=relation, signals={"intent": intent})

    # 组装注入回答 prompt 的规划文本
    parts = ["## 你的内部思考（已想清楚，务必据此作答）"]
    if relation:
        parts.append(f"**与上文关系**：{relation}（据此承接，别把它当孤立的新问题）")
    if intent:
        parts.append(f"**真正意图**：{intent}")
    if len(reqs) > 1:
        # 多诉求任务：这是最容易漏做的地方——逐条列清、明确要求条条落实、答完核对。
        # 对应"逐条完善、一个都不漏、办完回头核对"的做事方式。
        parts.append(
            "**用户这次夹带了多个诉求，逐条列在下面。你的回答必须逐条落实，一个都不能漏——"
            "宁可回答长一点，也不能只挑容易的做、把其余的忽略掉**：\n"
            + "\n".join(f"（{i+1}）{r}" for i, r in enumerate(reqs))
        )
    if steps:
        parts.append("**执行步骤**：\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)))
    if pitfalls:
        parts.append("**注意的坑**：" + "；".join(pitfalls))
    if checks:
        parts.append("**答完自查**：" + "；".join(checks))
    if len(reqs) > 1:
        parts.append(
            "生成回答后，在心里对照上面每一条诉求核一遍是否都落实了，漏了的补上——"
            "这是交付前的最后一道关。"
        )
    parts.append(
        "现在依据以上思考给出完整、到位、可直接使用的回答：联系上文承接好，"
        "把该做的做完做到底（不要只给开头或提纲），覆盖每个诉求；涉及具体外部"
        "数字/事实时如实标注来源或说明需核实，绝不编造。"
    )
    return PlanResult(mode="answer", plan_text="\n".join(parts),
                      context_note=relation, requirements=reqs,
                      signals={"intent": intent, "steps": len(steps), "reqs": len(reqs)})


def _wrap_plan_freeform(text: str) -> str:
    return (
        "## 你的内部思考（已想清楚，务必据此作答）\n" + text[:1200]
        + "\n\n现在依据以上思考给出完整、到位、可直接使用的回答，联系上文承接好，"
        "覆盖每个诉求，绝不编造。"
    )


def review_and_patch(fn, query: str, answer: str, requirements: list, *,
                     user_id: str = "") -> str:
    """交付前自审补救（V263）——文档里"回头核一遍，漏的补上"的系统级落地。

    回答生成后，以"资深工程师验收"的眼光对照诉求清单核一遍：每条诉求这个回答
    是否真的落实了？只在**发现真实遗漏/明显错误**时返回一段补救内容（追加到回答
    末尾），补齐漏掉的诉求。没问题就返回空串（绝大多数情况，不画蛇添足、不啰嗦）。

    只对多诉求任务启用（单诉求靠回答本身的自查即可，不值当再调一次模型）。
    失败返回空串。settings『chat_review=off』可关。
    """
    if fn is None or len(requirements) < 2 or not (answer or "").strip():
        return ""
    if _get("chat_review", "on") in ("off", "0", "false"):
        return ""
    # 回答太短基本是拒答/澄清，不审
    if len(answer.strip()) < 80:
        return ""

    req_lines = "\n".join(f"{i+1}. {r}" for i, r in enumerate(requirements))
    prompt = (
        "你是严格的验收员。用户提了多个诉求，下面是助手的回答。逐条核对：每个诉求"
        "回答里是否真的落实了？有没有明显错误或自相矛盾？\n\n"
        f"用户的诉求清单：\n{req_lines}\n\n"
        f"助手的回答：\n{answer[:5000]}\n\n"
        "判断：\n"
        "- 如果每条诉求都落实了、没有明显错误 → 只输出两个字：通过\n"
        "- 如果有诉求被漏掉或做错了 → 先输出「补救：」，然后直接写出补齐这些遗漏"
        "所需的内容（就是要追加到回答末尾给用户看的实质内容，不要说教、不要复述"
        "清单，直接给缺的那部分）。\n"
        "只有确实有遗漏才补救；拿不准就输出通过。"
    )
    try:
        from hashmm.agent.harness import run_llm
        verdict = run_llm(fn, prompt, tag="chat:review", retries=0, min_len=2)
    except Exception as e:  # noqa: BLE001
        logger.debug("[chat_planner] 自审失败：%s", e)
        return ""
    if not verdict:
        return ""
    v = verdict.strip()
    # 通过：不补
    if v.startswith("通过") or v[:6] == "通过" or "补救" not in v[:20]:
        return ""
    # 提取补救内容
    patch = v.split("补救：", 1)[-1].split("补救:", 1)[-1].strip()
    if len(patch) < 30:   # 补救内容太短，可能是误报，不追加
        return ""
    try:
        from hashmm.agent.global_workspace import broadcast
        broadcast("chat", "review", f"自审发现遗漏并补救：{query[:40]}", salience=0.4, user=user_id)
    except Exception:
        pass
    return "\n\n---\n\n**补充**（自审时发现上面漏了几点，补齐）：\n\n" + patch[:2500]


def review_each(fn, query: str, answer: str, requirements: list, *,
                user_id: str = "") -> str:
    """V269 深思档逐条独立验收——对齐 Anthropic《A harness for every task》点名的
    两大失败模式的系统级对策：
      · 偷懒提前收工（agentic laziness）：一次性核对整份清单时，模型常"整体扫一眼就说通过"。
        这里改为**确定性地逐条循环**：每条诉求必然被单独核到，程序保证不漏项。
      · 自我偏好（self-preferential bias）：每条诉求用**单独的一次干净上下文**裁决——
        裁决调用里只有这一条诉求和回答本身，不带前几条的裁决结果，互不影响。
    与 review_and_patch 的关系：标准档核整份清单（一次调用、省 token）；深思档走这里
    （N 次小调用、稳）。单诉求也验收。补救最多取 3 条、总量封顶，不啰嗦。
    受同一开关 settings『chat_review=off』控制。失败返回空串。"""
    if fn is None or not requirements or not (answer or "").strip():
        return ""
    if _get("chat_review", "on") in ("off", "0", "false"):
        return ""
    if len(answer.strip()) < 80:   # 太短基本是拒答/澄清，不审
        return ""
    try:
        from hashmm.agent.harness import run_llm
    except Exception:
        return ""
    patches: list[str] = []
    for req in list(requirements)[:6]:   # 护栏：最多核 6 条（超长清单核前 6 条高优）
        r = str(req or "").strip()
        if not r:
            continue
        prompt = (
            "你是严格的验收员。只核对下面**这一条**诉求：助手的回答是否真的落实了它？\n\n"
            f"诉求：{r}\n\n"
            f"助手的回答：\n{answer[:5000]}\n\n"
            "判断：\n"
            "- 这条诉求已落实、无明显错误 → 只输出两个字：通过\n"
            "- 这条被漏掉或做错了 → 先输出「补救：」，然后直接写出补齐这条所需的内容"
            "（就是要追加给用户看的实质内容，不要说教、不要复述诉求）。\n"
            "拿不准就输出通过。"
        )
        try:
            verdict = run_llm(fn, prompt, tag="chat:review_each", retries=0, min_len=2)
        except Exception as e:  # noqa: BLE001
            logger.debug("[chat_planner] 逐条验收失败：%s", e)
            continue
        v = (verdict or "").strip()
        if not v or v.startswith("通过") or "补救" not in v[:20]:
            continue
        patch = v.split("补救：", 1)[-1].split("补救:", 1)[-1].strip()
        if len(patch) >= 30:
            patches.append(patch[:800])
        if len(patches) >= 3:   # 补救过多说明回答整体跑偏，取前 3 条最要紧的即可
            break
    if not patches:
        return ""
    try:
        from hashmm.agent.global_workspace import broadcast
        broadcast("chat", "review", f"逐条验收发现 {len(patches)} 处遗漏并补救：{query[:40]}",
                  salience=0.4, user=user_id)
    except Exception:
        pass
    body = "\n\n".join(patches)
    return "\n\n---\n\n**补充**（逐条验收时发现上面有遗漏，补齐）：\n\n" + body[:2500]
