"""hashmm/agent/planning.py — V300 第三期：规划与反思深度。

两个能力（对标 Codex/Claude 的"想得周全"）：
  1) Plan Mode 一等化：复杂任务先产出【结构化计划】（步骤 + 每步验收标准），
     可展示给用户确认/编辑，再执行。计划本身作为 DoD 依据。
  2) Reflexion（反思-重规划）：执行若干步或遇到失败后，插入"反思"节点——
     回顾"离目标还有多远、当前策略是否有效、要不要换路"，据此调整，
     而不是硬着头皮沿错误路径跑到底。

设计（与项目风格一致）：
  · 纯函数 + 依赖注入的 llm_fn（沙箱可 mock，真机传模型）；永不抛错；
  · 计划/反思都要求模型返回 JSON，稳健解析（容错 markdown 代码块包裹）；
  · 判定"是否需要计划/反思"用轻量启发式，不无脑对每个任务都规划（省 token、不啰嗦）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.planning")

# 开关：默认开；HASHMM_PLAN_MODE=0 关闭计划先行（只保留 reflexion 亦可单独关）
_PLAN_ON = os.environ.get("HASHMM_PLAN_MODE", "1") != "0"
_REFLEXION_ON = os.environ.get("HASHMM_REFLEXION", "1") != "0"
# 反思触发：每执行这么多步插一次反思（默认 5；避免太频繁打断）
_REFLEXION_EVERY = int(os.environ.get("HASHMM_REFLEXION_EVERY", "5") or "5")

# 复杂任务信号：多步/有副作用/跨工具/研究类 → 值得先出计划
_COMPLEX_HINT = re.compile(
    r"(然后|接着|之后|再|依次|分别|批量|整理|迁移|重构|部署|搭建|实现|开发|"
    r"多个|所有|全部|每个|步骤|计划|方案|流程|对比分析|调研|研究报告|"
    r"and then|step by step|first.*then|migrate|refactor|deploy|implement)")
# 简单任务信号：单一动作/问答/翻译 → 不必规划
_SIMPLE_HINT = re.compile(
    r"^(什么是|谁是|解释|翻译|总结这|定义|多少|哪年|是不是|对吗|查一下|search|what is|who is|define|translate)")


@dataclass
class PlanStep:
    n: int
    action: str                       # 这一步做什么
    acceptance: str = ""              # 这一步的验收标准（做到什么算完成）
    status: str = "pending"          # pending | doing | done | skipped


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    needs_plan: bool = True

    def to_dict(self) -> dict:
        return {"goal": self.goal, "needs_plan": self.needs_plan,
                "steps": [{"n": s.n, "action": s.action, "acceptance": s.acceptance, "status": s.status}
                          for s in self.steps]}

    def render(self) -> str:
        """把计划渲染成给用户看 / 注入上下文的文本。"""
        if not self.steps:
            return ""
        lines = [f"【任务计划】目标：{self.goal}"]
        for s in self.steps:
            acc = f"（验收：{s.acceptance}）" if s.acceptance else ""
            lines.append(f"{s.n}. {s.action}{acc}")
        return "\n".join(lines)


def needs_planning(query: str) -> bool:
    """判断这个任务是否值得先出结构化计划。保守：明确复杂才规划，简单/问答不规划。"""
    if not _PLAN_ON:
        return False
    q = (query or "").strip()
    if not q or _SIMPLE_HINT.search(q):
        return False
    # 复杂信号命中 → 规划（即便句子短，如"搭建博客并部署"）；
    # 无复杂信号时，仅当句子较长（>40 字，通常含多要求）才规划。
    if _COMPLEX_HINT.search(q):
        return len(q) >= 6   # 太短的碎片（<6）不规划
    return len(q) > 40


def _parse_json(text: str) -> Optional[dict | list]:
    """稳健解析模型返回的 JSON（容忍 markdown 代码块、前后缀噪声）。"""
    if not text:
        return None
    t = text.strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", t)
    if m:
        t = m.group(1).strip()
    # 尝试直接解析；失败则截取第一个 { 或 [ 到最后一个 } 或 ]
    for attempt in (t,):
        try:
            return json.loads(attempt)
        except Exception:
            pass
    for lb, rb in (("[", "]"), ("{", "}")):
        i, j = t.find(lb), t.rfind(rb)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    return None


def make_plan(query: str, llm_fn: Callable[[str], str], *, max_steps: int = 8,
              context: str = "") -> Plan:
    """让模型把任务拆成结构化计划（步骤 + 每步验收）。llm_fn(prompt)->text。永不抛错。
    V265: context 传入最近对话，让多步任务的规划也联系上下文（延续/修正前面的工作）。"""
    plan = Plan(goal=query.strip()[:200], needs_plan=True)
    if not llm_fn:
        return plan
    _ctx = ""
    if context:
        _ctx = ("\n【本轮对话前面的内容，务必联系它理解本次任务是延续还是修正】\n"
                + context[:1400] + "\n")
    prompt = (
        "把下面的任务拆成一个可执行的结构化计划。要求：\n"
        f"- 最多 {max_steps} 步，每步是一个明确、可验证的动作；\n"
        "- 每步给出\"验收标准\"（做到什么才算这步完成）；\n"
        "- 只输出 JSON，格式：{\"steps\":[{\"action\":\"...\",\"acceptance\":\"...\"}]}；\n"
        "- 不要输出计划以外的任何解释。\n"
        f"{_ctx}\n"
        f"任务：{query.strip()[:600]}")
    try:
        raw = llm_fn(prompt)
        data = _parse_json(raw)
        steps_data = []
        if isinstance(data, dict):
            steps_data = data.get("steps") or []
        elif isinstance(data, list):
            steps_data = data
        for i, sd in enumerate(steps_data[:max_steps], start=1):
            if isinstance(sd, dict):
                action = str(sd.get("action") or sd.get("step") or "").strip()
                acc = str(sd.get("acceptance") or sd.get("verify") or "").strip()
            else:
                action, acc = str(sd).strip(), ""
            if action:
                plan.steps.append(PlanStep(n=i, action=action[:300], acceptance=acc[:200]))
    except Exception as e:
        log_suppressed(logger, e, "planning.make_plan")
    return plan


@dataclass
class Reflection:
    on_track: bool                    # 当前是否走在正轨上
    assessment: str = ""             # 简短评估
    adjustment: str = ""             # 若偏离，建议的调整/换路
    should_replan: bool = False      # 是否需要整体重规划

    def to_dict(self) -> dict:
        return {"on_track": self.on_track, "assessment": self.assessment,
                "adjustment": self.adjustment, "should_replan": self.should_replan}


def should_reflect(step_count: int, had_failure: bool) -> bool:
    """判断此刻是否该反思：遇到失败立即反思；否则每 N 步一次。"""
    if not _REFLEXION_ON:
        return False
    if had_failure:
        return True
    return step_count > 0 and step_count % _REFLEXION_EVERY == 0


def reflect(goal: str, recent_actions: list[str], llm_fn: Callable[[str], str]) -> Reflection:
    """反思节点：回顾进展，判断是否在正轨、要不要换路。llm_fn(prompt)->text。永不抛错。

    保守默认：解析失败时返回 on_track=True（不轻易打断正常流程）。
    """
    if not llm_fn:
        return Reflection(on_track=True)
    recent = "\n".join(f"- {a}" for a in recent_actions[-8:] if a)
    prompt = (
        "你是一个任务执行的反思者。基于目标和最近的动作，判断当前是否走在正轨上。\n"
        "只输出 JSON：{\"on_track\":true/false,\"assessment\":\"一句话评估\","
        "\"adjustment\":\"若偏离给出具体调整，否则空\",\"should_replan\":true/false}\n"
        "判断从严但克制：只有明显在原地打转、反复失败、或偏离目标时才 on_track=false。\n\n"
        f"目标：{goal[:300]}\n\n最近的动作：\n{recent or '（暂无）'}")
    try:
        raw = llm_fn(prompt)
        data = _parse_json(raw)
        if isinstance(data, dict):
            return Reflection(
                on_track=bool(data.get("on_track", True)),
                assessment=str(data.get("assessment", "")).strip()[:200],
                adjustment=str(data.get("adjustment", "")).strip()[:300],
                should_replan=bool(data.get("should_replan", False)))
    except Exception as e:
        log_suppressed(logger, e, "planning.reflect")
    return Reflection(on_track=True)


def build_reflection_prompt(refl: Reflection) -> str:
    """把反思结论转成给模型的调整指令（注入下一轮）。"""
    if refl.should_replan:
        return ("🔄 反思：当前策略似乎偏离了目标或在原地打转。" +
                (f"评估：{refl.assessment}。" if refl.assessment else "") +
                "请停下来重新想一个更有效的整体思路，" +
                (f"可参考：{refl.adjustment}。" if refl.adjustment else "") +
                "换个更直接的路径继续，而不是沿原路走。")
    return ("💡 阶段反思：" + (refl.assessment or "继续推进") +
            (f"。建议调整：{refl.adjustment}" if refl.adjustment else "") +
            "。据此继续。")
