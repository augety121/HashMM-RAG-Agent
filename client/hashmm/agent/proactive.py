"""proactive — 主动式助理智能（V103.26）。

目标：让 HashMM 在「生活/真实世界任务」上更懂用户的**深层需求**、主动**多想一步**、并用记忆做**个性化**，
对标 Hermes/Harness 的「帮你做外出规划」体验——但做成可注入 system prompt 的**脚手架 + 偏好个性化**，
而不是一套独立的分发架构（那套 Handler/orchestrator 已被 generate_sse_async 的内联逻辑取代并删除）。

为什么这样做（大厂做法）：让 Agent「更聪明」不是靠更多分支，而是靠
  ① 推断用户没说出口的真实目标；② 主动覆盖该任务的全部关键维度（不漏项）；
  ③ 用已知偏好做个性化；④ 给带利弊的选项、帮用户做选择；⑤ 预判下一步。
本模块把这五条压成一段精炼指令，注入到 Agent 的系统提示里。

对外两个函数：
- detect_life_task(query)         -> {"kind","label","dimensions"} | None
- build_proactive_scaffold(query, user_id="") -> str   （非生活任务返回 ""，不污染普通提示）
"""
from __future__ import annotations

# 每类生活任务「该主动考虑的维度」——这是「多想一步」的核心：把用户没说但会在意的因素列全。
_TASK_SPECS: list[dict] = [
    {
        "kind": "trip",
        "label": "出行/旅行规划",
        "kw": ["行程", "旅游", "旅行", "出游", "攻略", "景点", "自驾", "出差", "周末去", "玩几天", "几日游"],
        "dimensions": ["天气与穿衣", "交通与路线（耗时/拥堵/班次）", "时间安排（每日节奏、缓冲）",
                       "预算与性价比", "餐饮（当地特色/口味偏好）", "住宿（位置/价位）", "随身物品提醒"],
    },
    {
        "kind": "booking",
        "label": "预订/订票",
        "kw": ["订票", "订机票", "订酒店", "订餐", "预订", "买票", "抢票", "订房", "订一", "订张", "订个",
               "高铁票", "火车票", "机票", "车票", "船票", "门票", "演唱会票", "订民宿", "约车", "打车"],
        "dimensions": ["时间/班次选择", "价格对比与省钱方案", "退改签与灵活度", "位置/座位偏好", "证件与预订须知"],
    },
    {
        "kind": "planning",
        "label": "计划/日程安排",
        "kw": ["规划", "安排", "计划一下", "日程", "安排行程", "帮我计划", "怎么安排", "时间规划"],
        "dimensions": ["目标拆解与优先级", "时间冲突与可行性", "精力/节奏分配", "预留缓冲与应急", "提醒与跟进"],
    },
    {
        "kind": "recommendation",
        "label": "推荐/帮做选择",
        "kw": ["推荐", "推荐一下", "选哪个", "哪个好", "帮我选", "该买", "值不值", "对比一下", "怎么选"],
        "dimensions": ["你的偏好与使用场景", "预算区间", "2-3 个候选的利弊对比", "明确的首选建议+理由"],
    },
    {
        "kind": "life",
        "label": "日常生活协助",
        "kw": ["帮我想", "帮我看看", "怎么办", "如何处理", "帮我搞定", "帮我弄", "省心", "便利"],
        "dimensions": ["真实目标与约束", "可选方案及利弊", "最省心的一步", "可能被忽略的注意点"],
    },
]


def detect_life_task(query: str) -> dict | None:
    """判断是否生活/真实世界任务；是则返回该任务画像（含要主动考虑的维度）。"""
    q = query or ""
    for spec in _TASK_SPECS:
        if any(k in q for k in spec["kw"]):
            return {"kind": spec["kind"], "label": spec["label"], "dimensions": spec["dimensions"]}
    return None


def _user_preferences(user_id: str, limit: int = 12) -> list[str]:
    """从用户记忆里取偏好（个性化的来源）。永不抛错；取不到就返回空。"""
    if not user_id or user_id == "anonymous":
        return []
    try:
        from hashmm.api import database as db
        rows = db.get_user_memories(user_id, limit=limit)
    except Exception:
        return []
    prefs: list[str] = []
    for r in rows or []:
        try:
            key = (r.get("key") or "").strip()
            val = (r.get("value") or "").strip()
            if not val:
                continue
            prefs.append(f"{key}：{val}" if key else val)
        except Exception:
            continue
    return prefs[:limit]


def build_proactive_scaffold(query: str, user_id: str = "") -> str:
    """生活任务 → 返回注入 system prompt 的「主动式助理」指令（含用户偏好）。否则返回空串。"""
    task = detect_life_task(query)
    if not task:
        return ""

    dims = "；".join(task["dimensions"])
    lines = [
        f"\n\n## 主动式助理模式（{task['label']}）",
        "用户在请你帮忙处理一件生活中的事。不要只回答字面问题——像一个贴心、靠谱的私人助理那样：",
        "1. 先推断用户**没说出口的真实目标与约束**（预算、时间、同行人、口味、出行方式等），缺关键信息时"
        "用一句话**主动追问最关键的 1 个点**，其余用合理默认并**说明你的假设**。",
        f"2. **主动把以下维度替用户想全**（不要漏项，也不要啰嗦）：{dims}。",
        "3. 帮用户**做选择**：给 2-3 个带利弊对比的具体方案，并给出**明确的首选建议+理由**，而不是丢一堆选项让用户自己纠结。",
        "4. **多想一步**：主动提示用户可能忽略但会在意的点（如天气带伞、退改签、提前预订更便宜），并在结尾给出**最该做的下一步**。",
    ]

    prefs = _user_preferences(user_id)
    if prefs:
        lines.append("5. **结合该用户的已知偏好做个性化**（来自跨会话记忆，优先满足）：" + "；".join(prefs) + "。")

    # 主动澄清的结构化输出：决定追问时，在回复**末尾**附上这一行（用户端会渲染成可点选项）。
    lines.append(
        "\n【追问格式】如果你按第 1 点决定要追问，请在回复的最后单独附上一行："
        "[[ASK]]你的问题|建议回答1|建议回答2|建议回答3[[/ASK]]"
        "（竖线分隔：第一段是问题，其余是用户可能的回答，给 2-4 个；不需要追问就不要输出这一行）。"
    )

    return "\n".join(lines)


def parse_clarify(text: str) -> dict | None:
    """从回复里解析 [[ASK]]问题|选项1|选项2[[/ASK]] 块 → {question, options}。无则 None。"""
    if not text or "[[ASK]]" not in text:
        return None
    import re
    m = re.search(r"\[\[ASK\]\](.+?)\[\[/ASK\]\]", text, re.S)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
    if len(parts) < 2:
        return None
    question, options = parts[0], parts[1:5]
    if not question or not options:
        return None
    return {"question": question, "options": options}
