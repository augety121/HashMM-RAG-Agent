"""hashmm/agent/memory_reflect.py — 记忆自进化循环（面试资料 11.4.4，Hermes 标志性特性）。

思想（资料原文）："like back propagation but for prompts instead of model weights"——
像反向传播，但更新的不是模型权重，而是 Agent 的**记忆**。每隔约 N 次工具调用/若干轮对话，
Agent 主动暂停，回顾刚才发生了什么，把值得长期记住的东西**提炼进长期记忆**。
两条纪律直接抄资料：
  ① **周期触发**，不是每次都跑——"反思本身也花钱花时间，频繁反思就本末倒置"（11.4.4.1）。
  ② **updating，不是 creating**——同类记忆更新已有条目而不是新建，防记忆膨胀（11.4.4.3
     对 Skill 的原则，同样适用于记忆条目）。

落地到本项目：提炼产物写入既有 ``user_memory``（V277 已带 preference/behavior/topic/issue
四类），复用其持久化与注入链路——反思是"作用在记忆层之上的更新策略"，不是另一套存储。

工程纪律（与 conv_compact.py / debate.py 一致）：
纯逻辑 + 可注入（``llm_fn`` / ``remember_fn``）→ 无 LLM 也可单测；**永不抛错**（任何异常
返回空结果，绝不影响主对话）；有界（每次最多沉淀 3 条）；默认关（``HASHMM_MEMORY_REFLECT``）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.memory_reflect")

# 周期：约每 N 次工具调用 / 每 M 轮用户消息触发一次（资料说"每隔大约 10 次工具调用"）
REFLECT_EVERY_TOOL_CALLS = 10
REFLECT_EVERY_USER_TURNS = 12
MAX_ITEMS_PER_REFLECT = 3

_VALID_CATS = ("preference", "behavior", "topic", "issue")

_PROMPT = (
    "你是记忆提炼器。回顾下面这段最近的对话，判断有没有**值得跨会话长期记住**的用户信息。\n"
    "只提炼四类：preference(用户偏好，如喜欢简洁回答/中文/某框架)、behavior(行为模式，如常在深夜工作)、"
    "topic(持续关注的话题/项目)、issue(未解决的历史事项)。\n"
    "规则：\n"
    "1. 宁缺毋滥——闲聊、一次性问题、与用户本人无关的知识**不要**提炼；没有就输出空数组。\n"
    "2. 最多 {max_items} 条；key 用 2-8 字的稳定名词（如\"回答风格\"\"主项目\"），value 一句话。\n"
    "3. 只输出 JSON 数组，不要解释：\n"
    '[{{"category":"preference","key":"回答风格","value":"喜欢先给结论再给理由"}}]\n\n'
    "对话（最近片段）：\n{transcript}\n"
)


@dataclass
class ReflectResult:
    ran: bool = False               # 本次是否真的执行了反思
    extracted: int = 0              # LLM 提炼出的条数
    written: int = 0                # 实际写入（新建）条数
    updated: int = 0                # 命中已有 key 而更新的条数（updating not creating）
    items: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"ran": self.ran, "extracted": self.extracted, "written": self.written,
                "updated": self.updated, "items": self.items, "reason": self.reason}


def reflect_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_MEMORY_REFLECT")


def should_reflect(tool_calls: int = 0, user_turns: int = 0, *,
                   every_tools: int = REFLECT_EVERY_TOOL_CALLS,
                   every_turns: int = REFLECT_EVERY_USER_TURNS) -> bool:
    """周期触发判断（资料 11.4.4.1：不是每次都跑）。

    计数**恰好到达**周期倍数时触发一次（调用方在会话状态里维护计数）。
    """
    try:
        t = int(tool_calls or 0)
        u = int(user_turns or 0)
        if every_tools > 0 and t > 0 and t % every_tools == 0:
            return True
        if every_turns > 0 and u > 0 and u % every_turns == 0:
            return True
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return False


def _render_transcript(messages: list[dict], max_chars: int = 3000) -> str:
    """最近对话渲染成纯文本（用户/助手各截断，控制反思成本）。"""
    lines: list[str] = []
    for m in (messages or [])[-16:]:
        role = str(m.get("role") or "")
        content = str(m.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        who = "用户" if role == "user" else "助手"
        lines.append(f"{who}：{content[:400]}")
    out = "\n".join(lines)
    return out[-max_chars:]


def _parse_items(raw: str) -> list[dict]:
    """解析 LLM 输出的 JSON 数组；容忍 markdown 围栏/前后噪声；坏输出→空。"""
    s = str(raw or "").strip()
    if not s:
        return []
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category") or "").strip().lower()
        key = str(it.get("key") or "").strip()[:24]
        val = str(it.get("value") or "").strip()[:200]
        if not key or not val:
            continue
        if cat not in _VALID_CATS:
            cat = "preference"      # 与 user_memory 的兜底一致
        out.append({"category": cat, "key": key, "value": val})
        if len(out) >= MAX_ITEMS_PER_REFLECT:
            break
    return out


def reflect(user_id: str, messages: list[dict], llm_fn, *,
            remember_fn=None, recall_fn=None) -> ReflectResult:
    """执行一次反思：最近对话 → LLM 提炼 ≤3 条 → 写入长期记忆。**永不抛错**。

    ``remember_fn(uid, key, value, category=...)`` / ``recall_fn(uid) -> {key: value}``
    默认接项目 ``user_memory``；可注入以便单测。
    「updating not creating」：key 已存在 → 计入 updated（remember 本身是 upsert，
    这里区分只为可观测：反思应该收敛而不是膨胀）。
    """
    r = ReflectResult()
    try:
        if not callable(llm_fn) or not user_id:
            r.reason = "缺 llm_fn 或 user_id"
            return r
        transcript = _render_transcript(messages)
        if len(transcript) < 40:
            r.reason = "对话太短，无可提炼"
            return r
        if remember_fn is None or recall_fn is None:
            from hashmm.agent import user_memory as UM
            remember_fn = remember_fn or UM.remember
            recall_fn = recall_fn or UM.recall
        raw = llm_fn(_PROMPT.format(max_items=MAX_ITEMS_PER_REFLECT, transcript=transcript))
        items = _parse_items(raw)
        r.ran = True
        r.extracted = len(items)
        if not items:
            r.reason = "LLM 判定无需沉淀（宁缺毋滥）"
            return r
        existing = {}
        try:
            existing = recall_fn(user_id) or {}
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
        for it in items:
            try:
                res = remember_fn(user_id, it["key"], it["value"], category=it["category"])
                if isinstance(res, dict) and not res.get("ok", True):
                    continue
                if it["key"] in existing:
                    r.updated += 1      # updating, not creating
                else:
                    r.written += 1
                r.items.append(it)
            except TypeError:
                # 旧签名 remember(uid,k,v) 兼容
                try:
                    remember_fn(user_id, it["key"], it["value"])
                    if it["key"] in existing:
                        r.updated += 1
                    else:
                        r.written += 1
                    r.items.append(it)
                except Exception as e:  # noqa: BLE001
                    log_suppressed(logger, e)
            except Exception as e:  # noqa: BLE001
                log_suppressed(logger, e)
        r.reason = f"沉淀 {r.written} 新 + {r.updated} 更新"
        return r
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        r.reason = f"反思异常已吞：{type(e).__name__}"
        return r
