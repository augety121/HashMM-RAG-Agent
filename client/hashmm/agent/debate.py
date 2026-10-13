"""hashmm/agent/debate.py — 去中心化辩论执行器（面试资料 6.1 §4「MAS(Decentralized)」落地）。

背景：``arch_advisor`` 会对「高熵、路径不唯一、可并行覆盖」的任务推荐 ``MAS_DECENTRAL``，
但项目此前只有中心化编排（staff / orchestrator）与并行子代理（subagents），**没有真正的
去中心化辩论执行器**——推荐了却没法执行。本模块补齐这一形态。

辩论模型（对齐资料原文的「点对点交流/辩论 → 多轮互相质询与改进 → 多数表决或共识」）：
  · 第 0 轮：N 个 agent **各自独立**作答（互不可见），得到 N 份初始答案。
  · 第 1..R 轮：每个 agent **看到其余 agent 上一轮的答案**，据此质询并修正自己的答案
    （这就是「辩论」——用别人的视角纠自己的错，去中心化架构没有中心裁判在中间串行）。
  · 收敛：若某一轮各答案高度一致（签名去重后只剩 1 类）即判定收敛、提前停止；
    否则跑满 R 轮，最后由 ``judge_fn`` 仲裁出共识答案（无 judge 时用「多数/最长」兜底）。

与已有形态的分工（三者互补、不重复）：
  · subagents  = orchestrator-worker，**并行分工**（每人查一个子问题），主代理汇总。
  · staff/team = 中心化角色编排，有权威收敛与质量门控（财务/核对类）。
  · debate（本模块） = **去中心化对等辩论**，无中心裁判、靠互相纠错收敛（高熵调研/开放问题）。

工程纪律（完全对齐 subagents.py）：
  纯逻辑 + 可注入（``agent_fn`` / ``judge_fn``）→ 无 GPU/LLM 也能单测；有界（N≤4、R≤3）；
  **永不抛错**——任何异常降级为「单 agent 直答」，绝不拖垮调用方。默认关（``HASHMM_DEBATE``）。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.debate")

_MAX_AGENTS = 4
_MAX_ROUNDS = 3


def debate_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_DEBATE")


def _n_agents(requested: int | None) -> int:
    try:
        n = int(requested if requested is not None else os.environ.get("HASHMM_DEBATE_AGENTS", "3"))
    except (TypeError, ValueError):
        n = 3
    return max(2, min(_MAX_AGENTS, n))


def _n_rounds(requested: int | None) -> int:
    try:
        r = int(requested if requested is not None else os.environ.get("HASHMM_DEBATE_ROUNDS", "2"))
    except (TypeError, ValueError):
        r = 2
    return max(1, min(_MAX_ROUNDS, r))


# 给每个对等 agent 一个轻微不同的立场提示，制造有意义的分歧（辩论才有价值）。
_ROLE_HINTS = [
    "严谨务实，优先可验证的事实与数据，指出对方论证里没有依据的部分",
    "全局视角，关注被忽略的前提、边界条件与反例",
    "批判质疑，主动寻找对方结论的漏洞与风险",
    "综合权衡，在多方观点间找共识与最优折衷",
]


def _sig(text: str) -> str:
    """答案签名：用于判断各 agent 是否已收敛到同一结论（粗粒度、抗措辞差异）。"""
    s = "".join(ch for ch in str(text or "").lower() if ch.isalnum())
    # 取首尾指纹 + 长度桶，措辞微调不影响、结论不同则不同
    return f"{len(s)//40}:{s[:60]}:{s[-40:]}"


def _converged(answers: list[str]) -> bool:
    sigs = {_sig(a) for a in answers if str(a or "").strip()}
    return len(sigs) <= 1 and len(answers) >= 2


def _majority(candidates: list[str]) -> str:
    """无 judge_fn 时的兜底共识：出现最多的签名取其一；并列则取信息量最大的。"""
    cands = [c for c in candidates if str(c or "").strip()]
    if not cands:
        return ""
    from collections import Counter
    by_sig: dict[str, list[str]] = {}
    for c in cands:
        by_sig.setdefault(_sig(c), []).append(c)
    counts = Counter({k: len(v) for k, v in by_sig.items()})
    top = counts.most_common()
    best_n = top[0][1]
    tied_sigs = [k for k, n in top if n == best_n]
    pool = [c for sg in tied_sigs for c in by_sig[sg]]
    return max(pool, key=lambda x: len(str(x)))


def _round0(question: str, agent_fn: Callable, n: int) -> list[str]:
    """第 0 轮：N 个 agent 独立作答（并行；互不可见）。"""
    out: list[str] = [""] * n
    def one(i: int) -> tuple[int, str]:
        try:
            return i, str(agent_fn(_ROLE_HINTS[i % len(_ROLE_HINTS)], question, "") or "")
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return i, ""
    if n == 1:
        return [one(0)[1]]
    with ThreadPoolExecutor(max_workers=n) as ex:
        for fut in as_completed([ex.submit(one, i) for i in range(n)]):
            try:
                i, ans = fut.result()
                out[i] = ans
            except Exception as e:  # noqa: BLE001
                log_suppressed(logger, e)
    return out


def _peer_context(answers: list[str], me: int) -> str:
    """把「其余 agent 上一轮的答案」组织成给第 me 个 agent 的辩论材料。"""
    lines = []
    for j, a in enumerate(answers):
        if j == me or not str(a or "").strip():
            continue
        lines.append(f"【其他参与者 {j + 1} 的观点】\n{str(a)[:900]}")
    return "\n\n".join(lines)


def debate(question: str, agent_fn: Callable, judge_fn: Callable | None = None, *,
           n_agents: int | None = None, rounds: int | None = None) -> dict:
    """执行一场去中心化辩论，返回结构化结果。**永不抛错**。

    Args:
        question: 辩论议题 / 问题。
        agent_fn: ``agent_fn(role_hint: str, question: str, peer_context: str) -> str``。
                  peer_context 为空表示独立作答；非空表示看到同侪观点后修正。
                  建议把这些对等调用路由到便宜/本地模型（与 subagents 同款「多并行调用走
                  local Qwen、只有最终仲裁走付费模型」的省钱取舍）。
        judge_fn: 可选 ``judge_fn(question, candidates: list[str]) -> str``，仲裁共识答案。
                  不传则用「多数/最长」兜底。
        n_agents: 参与者数（2..4，默认 3）。
        rounds:   辩论轮数（1..3，默认 2）。

    Returns:
        {answer, converged, agents, rounds_run, transcript:[{round, answers}]}。
        任何异常 → 降级为单 agent 直答：{answer, degraded:True}。
    """
    q = str(question or "").strip()
    if not q or not callable(agent_fn):
        return {"answer": "", "converged": False, "agents": 0, "rounds_run": 0, "transcript": []}

    n = _n_agents(n_agents)
    r = _n_rounds(rounds)
    transcript: list[dict] = []
    try:
        answers = _round0(q, agent_fn, n)
        transcript.append({"round": 0, "answers": list(answers)})
        if _converged(answers):
            ans = _majority(answers) if judge_fn is None else _safe_judge(judge_fn, q, answers)
            return {"answer": ans, "converged": True, "agents": n, "rounds_run": 1, "transcript": transcript}

        rounds_run = 1
        for rd in range(1, r + 1):
            new_answers: list[str] = list(answers)
            def revise(i: int) -> tuple[int, str]:
                ctx = _peer_context(answers, i)
                try:
                    prompt_q = (f"议题：{q}\n\n你上一轮的答案：\n{str(answers[i])[:900]}\n\n"
                                f"下面是其他参与者的观点，请对照质询：如果你被说服了就修正，"
                                f"如果你坚持就给出更有力的依据。给出你这一轮修订后的完整答案：")
                    return i, str(agent_fn(_ROLE_HINTS[i % len(_ROLE_HINTS)], prompt_q, ctx) or answers[i])
                except Exception as e:  # noqa: BLE001
                    log_suppressed(logger, e)
                    return i, answers[i]
            with ThreadPoolExecutor(max_workers=n) as ex:
                for fut in as_completed([ex.submit(revise, i) for i in range(n)]):
                    try:
                        i, ans = fut.result()
                        new_answers[i] = ans
                    except Exception as e:  # noqa: BLE001
                        log_suppressed(logger, e)
            answers = new_answers
            rounds_run = rd + 1
            transcript.append({"round": rd, "answers": list(answers)})
            if _converged(answers):
                break

        final = _majority(answers) if judge_fn is None else _safe_judge(judge_fn, q, answers)
        return {"answer": final, "converged": _converged(answers), "agents": n,
                "rounds_run": rounds_run, "transcript": transcript}
    except Exception as e:  # noqa: BLE001 —— 兜底：辩论整体失败就退回单 agent 直答
        log_suppressed(logger, e)
        try:
            single = str(agent_fn(_ROLE_HINTS[0], q, "") or "")
        except Exception:
            single = ""
        return {"answer": single, "converged": False, "agents": 1, "rounds_run": 1,
                "transcript": transcript, "degraded": True}


def _safe_judge(judge_fn: Callable, question: str, candidates: list[str]) -> str:
    try:
        v = str(judge_fn(question, [c for c in candidates if str(c or "").strip()]) or "")
        return v if v.strip() else _majority(candidates)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return _majority(candidates)
