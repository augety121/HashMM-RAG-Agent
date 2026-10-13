"""hashmm/evaluation/multiturn_eval.py — 多轮交互评测（V284，严格按资料 3.3.4.3）。

资料原文方法（龙猫独家深入）：**固定"环境 + 隐藏用户目标 + 评分 Rubric"，让用户模拟器动态生成
多轮对话，被测 Agent 正常应对，最后用"终态 + Rubric"判断任务是否完成。** 关键点：
  · 被测 Agent **看不到**隐藏目标卡和 Rubric，只能像线上一样根据用户模拟器一句句说的话应对；
  · 对话**不是写死的**，用户模拟器拿隐藏目标扮演真实用户（含糊表达、追问、改主意）；
  · 判分**不比对标准对话**，而是看**终态 + 关键行为**是否满足 Rubric（结构化终态用代码判、
    语义行为用 LLM judge 判）；
  · **Pass^k**：同一任务跑 k 次（每次对话路径可能不同），k 次全过才算 Agent 真稳定。

落地：三方都用 LLM 扮演——user_llm（拿隐藏目标当用户）、agent_llm（被测，只见对话）、judge_llm（拿
Rubric 判终态）。纯逻辑 + 可注入；**永不抛错**。这是"测好不好用"而非"能不能用"的核心武器。
"""
from __future__ import annotations

import json
import re

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation.multiturn_eval")


def _json_obj(raw: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {}


# 多轮评测任务包：环境(三方可见) + 隐藏目标(仅用户模拟器) + Rubric(仅判分器)
MULTITURN_TASKS = [
    {
        "name": "订餐-中途改主意",
        "environment": "你是餐厅点餐助手。菜单：麻婆豆腐(45元)、宫保鸡丁(50元)、清蒸鱼(88元)、番茄蛋汤(20元)。营业到21:00，最快30分钟送达。",
        "hidden_goal": ("你想点一道价格不超过50元的主菜，一开始你会含糊地说'想吃点辣的'，"
                        "如果助手推荐了宫保鸡丁，你会在第2轮改主意说'还是要麻婆豆腐吧'。"
                        "你希望19:00前送到。你不会一次说全，要让助手追问。"),
        "rubric": {
            "structured": {"final_dish": "麻婆豆腐", "max_price": 50},
            "behavioral": ["下单前有没有复述确认订单", "有没有正确处理用户中途从宫保鸡丁改成麻婆豆腐",
                           "有没有擅自臆测用户没说的信息"],
        },
    },
    {
        "name": "查资料-含糊追问",
        "environment": "你是技术问答助手，可以检索知识库回答 RAG、向量检索、Agent 等技术问题。",
        "hidden_goal": ("你想搞懂'为什么要用混合检索而不是只用向量检索'。但你一开始只会含糊地问"
                        "'检索有哪些方法'，等助手回答后，你再追问'那到底该用哪个'，最后才问出真正的问题。"
                        "你希望得到有对比、有理由的回答。"),
        "rubric": {
            "structured": {},
            "behavioral": ["最终有没有解释混合检索相对纯向量的优势", "有没有跟住用户逐步聚焦的追问",
                           "回答是否有对比和理由（而非泛泛而谈）"],
        },
    },
]


def _run_dialogue(task: dict, user_llm, agent_llm, max_turns: int = 6) -> list[dict]:
    """用户模拟器 ↔ 被测 Agent 动态对话，返回消息列表。被测 Agent 只见对话历史。**永不抛错**。"""
    history: list[dict] = []
    try:
        user_sys = (f"【环境】{task['environment']}\n【你的隐藏目标】{task['hidden_goal']}\n"
                    "你在扮演真实用户和一个助手对话。像真人一样一句一句说，不要一次说全需求，"
                    "可以含糊、追问、改主意。如果你的目标已经达成（助手正确完成/回答了），"
                    "就说'好的谢谢'结束。只输出你这一轮要说的话，不要解释、不要加引号。")
        agent_sys = (
            f"【你的角色】{task['environment']}\n"
            "你是助手，请根据用户的话自然应对、必要时追问澄清、完成任务。"
            "如果用户已经补齐关键条件，本轮必须给出明确收束：复述已确认事项、说明下一步；"
            "涉及下单、付款、发布或其他真实副作用时，不得谎称已执行，应明确等待授权或调用真实工具。"
        )

        def call_agent(prompt: str) -> str:
            """短暂空响应重试一次；始终不把空白伪装成助手回复。"""
            for attempt in range(2):
                suffix = "" if attempt == 0 else "\n上一请求返回空白，请现在给出可见且完整的回复。"
                reply = str(agent_llm(prompt + suffix) or "").strip()[:600]
                if reply:
                    return reply
            return ""

        for turn in range(max_turns):
            # 用户说话
            u_prompt = user_sys + "\n\n【对话记录】\n" + _fmt(history) + "\n\n现在轮到你（用户）说："
            user_msg = str(user_llm(u_prompt) or "").strip()[:300]
            if not user_msg:
                break
            history.append({"role": "user", "content": user_msg})
            if any(w in user_msg for w in ("谢谢", "好的谢", "就这样", "没问题了", "可以了")) and turn >= 1:
                break
            # 助手应对
            a_prompt = agent_sys + "\n\n【对话记录】\n" + _fmt(history) + "\n\n现在轮到你（助手）回复："
            agent_msg = call_agent(a_prompt)
            if agent_msg:
                history.append({"role": "assistant", "content": agent_msg})
            else:
                break
        # 达到轮次上限时也不能把对话停在用户刚补齐信息的位置。额外做一次收束调用，
        # 这不是“多跑一轮用户模拟器”，只是确保被测助手完成当前回合。
        if history and history[-1]["role"] == "user" and not any(
                w in history[-1]["content"] for w in ("谢谢", "好的谢", "就这样", "没问题了", "可以了")):
            a_prompt = agent_sys + "\n\n【对话记录】\n" + _fmt(history) + (
                "\n\n用户刚补齐了信息。请完成当前回合并明确下一步；"
                "没有真实工具结果时不要声称已经下单、付款或提交："
            )
            agent_msg = call_agent(a_prompt)
            if agent_msg:
                history.append({"role": "assistant", "content": agent_msg})
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return history


def _fmt(history: list[dict]) -> str:
    return "\n".join(f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}" for m in history) or "（对话开始）"


def _judge_terminal(task: dict, history: list[dict], judge_llm) -> tuple[bool, float, str]:
    """终态 + Rubric 判分：结构化终态 + 语义行为（LLM judge）。返回 (pass, 0..1, 说明)。**永不抛错**。"""
    try:
        transcript = _fmt(history)
        rubric = task["rubric"]
        behavioral = rubric.get("behavioral", [])
        structured = rubric.get("structured", {})
        prompt = (
            "你是多轮对话判分器。根据下面整段对话的**最终状态和关键行为**判断助手是否合格完成任务。\n"
            f"【任务环境】{task['environment']}\n"
            f"【结构化终态要求】{json.dumps(structured, ensure_ascii=False) if structured else '无'}\n"
            f"【关键行为检查项】\n" + "\n".join(f"  - {b}" for b in behavioral) + "\n\n"
            f"【完整对话】\n{transcript}\n\n"
            "逐项检查后打分。必须先写理由再给结论。只输出 JSON：\n"
            '{"rationale": "逐项分析", "checks_passed": 通过的检查项数, "checks_total": 总检查项数, '
            '"structured_ok": true/false, "pass": true/false}'
        )
        d = _json_obj(str(judge_llm(prompt) or ""))
        cp = int(d.get("checks_passed", 0) or 0)
        ct = int(d.get("checks_total", len(behavioral)) or len(behavioral)) or 1
        struct_ok = bool(d.get("structured_ok", True)) if structured else True
        passed = bool(d.get("pass", False)) and struct_ok
        score = (cp / ct) * (1.0 if struct_ok else 0.6)
        rationale = str(d.get("rationale", ""))
        return (passed, max(0.0, min(1.0, score)),
                f"行为{cp}/{ct}｜终态{'✓' if struct_ok else '✗'}｜{rationale[:80]}", rationale)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return (False, 0.0, f"判分异常 {type(e).__name__}", "")


def run_multiturn(agent_llm, user_llm=None, judge_llm=None, k: int = 3):
    """多轮交互评测：每个任务跑 k 次（Pass^k），每次动态生成对话、终态判分。返回 SuiteReport。

    user_llm/judge_llm 默认复用 agent_llm（同一个模型分饰三角，靠不同 system 提示区隔）。**永不抛错**。
    """
    from hashmm.evaluation.deep_eval import SuiteReport, run_case_ntimes, RunOutcome
    rep = SuiteReport("多轮交互(用户模拟器·Pass^k)")
    if not callable(agent_llm):
        for t in MULTITURN_TASKS:
            rep.add(run_case_ntimes(t["name"], None, k, skip_reason="未配 LLM"))
        return rep
    user_llm = user_llm or agent_llm
    judge_llm = judge_llm or agent_llm
    for t in MULTITURN_TASKS:
        def run_once(t=t):
            history = _run_dialogue(t, user_llm, agent_llm)
            if len(history) < 2:
                return RunOutcome(False, 0.0, "对话未生成", "用户模拟器/Agent 无有效输出",
                                  trace={"任务环境": t.get("environment", ""),
                                         "隐藏用户目标": t.get("hidden_goal") or t.get("goal", ""),
                                         "对话记录": "（用户模拟器/Agent 未产出有效对话）"})
            passed, score, msg, rationale = _judge_terminal(t, history, judge_llm)
            mode = "" if passed else "多轮任务未达成"
            # 富轨迹：隐藏目标(只判分器/模拟器可见) → 逐轮对话(思考过程) → 裁判逐项理由 → 定位
            dialogue = [f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}" for m in history]
            trace = {
                "任务环境": t.get("environment", ""),
                "隐藏用户目标(Agent不可见)": t.get("hidden_goal") or t.get("goal", ""),
                "评分Rubric": t.get("rubric", {}),
                "多轮对话全程": dialogue,
                "裁判逐项理由": rationale or msg,
                "问题定位": ["多轮任务达成"] if passed else [f"多轮任务未达成：{msg}"],
            }
            return RunOutcome(passed, score, mode, f"{len(history)}轮｜{msg}", trace=trace)
        rep.add(run_case_ntimes(t["name"], run_once, k))
    return rep
