"""Conversation Memory Manager — 五层记忆体系。

L1 工作记忆：当前对话历史（messages[]）
L2 会话记忆：对话结束时自动生成摘要
L3 长期记忆：用户画像 + 偏好 + 查询模式
L4 情景记忆：成功/失败经验 → 指导策略
L5 知识记忆：FAISS + BM25 + KG（外部）

对标 Claude Code 的 context compaction + disk persistence。
"""
from __future__ import annotations

import json
import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.memory")


class ConversationMemory:
    """管理单个对话的记忆生命周期。

    职责：
    1. 对话过长时自动压缩（compact）
    2. 对话结束时生成摘要存入 L2
    3. 开始新对话时注入 L2/L3/L4 记忆
    """

    # 超过此长度自动压缩
    COMPACT_THRESHOLD = 12  # 消息数
    # 压缩后保留最近几轮
    COMPACT_KEEP_RECENT = 4

    def __init__(self, user_id: str, conv_id: str):
        self.user_id = user_id
        self.conv_id = conv_id

    def should_compact(self, messages: list[dict]) -> bool:
        """检查是否需要压缩上下文。"""
        user_msgs = [m for m in messages if m.get("role") == "user"]
        return len(user_msgs) > self.COMPACT_THRESHOLD

    def compact(self, messages: list[dict], llm_fn: Any = None) -> list[dict]:
        """压缩对话历史。

        策略：保留 system + 摘要 + 最近 N 轮。
        如果有 LLM，用 LLM 生成摘要；否则用规则摘要。
        """
        if len(messages) <= self.COMPACT_KEEP_RECENT + 2:
            return messages

        system = messages[0] if messages[0].get("role") == "system" else None
        recent = messages[-self.COMPACT_KEEP_RECENT * 2:]  # 最近 N 轮（user+assistant）

        # 中间的消息生成摘要
        middle = messages[1:-self.COMPACT_KEEP_RECENT * 2] if system else messages[:-self.COMPACT_KEEP_RECENT * 2]

        summary = self._summarize(middle, llm_fn)

        result = []
        if system:
            result.append(system)
        if summary:
            result.append({
                "role": "system",
                "content": f"[对话历史摘要]\n{summary}",
            })
        result.extend(recent)

        logger.info(f"Compacted {len(messages)} → {len(result)} messages")
        return result

    def generate_session_summary(self, messages: list[dict], llm_fn: Any = None) -> str:
        """对话结束时生成会话摘要（L2 记忆）。

        Returns:
            摘要文本，存入数据库。
        """
        if len(messages) < 3:
            return ""

        # 提取关键信息
        user_queries = []
        topics = []
        for m in messages:
            if m.get("role") == "user":
                q = m.get("content", "")[:100]
                user_queries.append(q)
            elif m.get("role") == "assistant":
                content = m.get("content", "")
                # 提取关键词
                for kw in ["营收", "利润", "PPT", "代码", "分析", "论文", "对比"]:
                    if kw in content and kw not in topics:
                        topics.append(kw)

        if llm_fn and hasattr(llm_fn, "quick_call"):
            try:
                text = "\n".join(f"用户: {q}" for q in user_queries[:5])
                summary = llm_fn.quick_call(
                    "用一两句话总结这段对话的主要内容和结论。",
                    text,
                    max_tok=100,
                )
                return summary.strip()
            except Exception as _e:
                log_suppressed(logger, _e)

        # 规则摘要
        parts = []
        if user_queries:
            parts.append(f"用户问了: {', '.join(user_queries[:3])}")
        if topics:
            parts.append(f"涉及: {', '.join(topics[:5])}")
        return "; ".join(parts)

    def get_memory_injection(self) -> str:
        """获取要注入到 Agent 上下文的记忆内容。

        合并 L2（历史会话摘要）+ L3（用户画像）+ L4（经验提示）。
        """
        parts = []

        # L2: 最近会话摘要
        try:
            from hashmm.api import database as db
            recent_convs = db.list_conversations(user_id=self.user_id, limit=5)
            summaries = []
            for conv in recent_convs:
                s = conv.get("summary", "")
                if s and conv.get("id") != self.conv_id:
                    title = conv.get("title", "")
                    summaries.append(f"- {title}: {s}")
            if summaries:
                parts.append("## 近期对话\n" + "\n".join(summaries[-3:]))
        except Exception as _e:
            log_suppressed(logger, _e)

        # L3: 用户画像
        try:
            from hashmm.evolution.user_model import get_user_model
            um = get_user_model()
            profile = um.get_profile(self.user_id)
            if profile:
                interests = profile.get("interests", [])
                expertise = profile.get("expertise_level", "")
                if interests:
                    parts.append(f"## 用户兴趣\n{', '.join(interests[:5])}")
                if expertise:
                    parts.append(f"专业水平: {expertise}")
        except Exception as _e:
            log_suppressed(logger, _e)

        # L3b: 显式记住的用户偏好（越用越智能的关键）
        # 这些是用户在过往对话里明确表达的偏好/事实，例如"喜欢高铁""预算敏感"
        # "爱吃川菜"。每次都注入，让助手自然带出用户习惯。
        try:
            from hashmm.api import database as db
            prefs = db.get_user_memories(self.user_id, limit=12)
            if prefs:
                lines = []
                for p in prefs:
                    cat = p.get("category", "")
                    key = p.get("key", "")
                    val = p.get("value", "")
                    if val:
                        lines.append(f"- {key}：{val}" if key else f"- {val}")
                if lines:
                    parts.append("## 用户偏好（记住这些，主动应用）\n" + "\n".join(lines))
        except Exception as _e:
            log_suppressed(logger, _e)

        # L4: 经验提示
        try:
            from hashmm.evolution.episodic_memory import get_episodic_memory
            hint = get_episodic_memory().get_strategy_hint(self.user_id, "")
            if hint:
                parts.append(f"## 经验提示\n{hint}")
        except Exception as _e:
            log_suppressed(logger, _e)

        return "\n\n".join(parts) if parts else ""

    def save_session_summary(self, summary: str):
        """保存会话摘要到数据库。"""
        if not summary:
            return
        try:
            from hashmm.api import database as db
            db.update_conversation(self.conv_id, {"summary": summary})
        except Exception as e:
            logger.warning(f"Failed to save session summary: {e}")

    def extract_and_save_preferences(self, messages: list[dict], llm_fn: Any = None):
        """从对话中提取【持久的用户偏好/事实】并保存（越用越智能的核心）。

        例如用户说"我一般坐高铁""预算控制在2000以内""我爱吃川菜"，
        这些会被抽取成结构化偏好存入 user_memory，下次对话自动注入。

        只抽取【稳定、可复用】的偏好，不存一次性的具体问题。
        失败静默——绝不影响主流程。
        """
        if not llm_fn or not messages:
            return
        # 只看用户说的话（偏好来自用户，不来自助手）
        user_texts = [str(m.get("content", "")) for m in messages
                      if m.get("role") == "user" and m.get("content")]
        if not user_texts:
            return
        convo = "\n".join(user_texts[-10:])[:3000]
        prompt = (
            "从以下用户的话里，提取【稳定、可复用的个人偏好或事实】（如交通/饮食/预算偏好、"
            "专业领域、惯用工具、关注的公司等）。不要提取一次性的具体问题或临时信息。\n"
            "只返回 JSON 数组，每项 {\"category\":\"\",\"key\":\"\",\"value\":\"\"}；没有可提取的就返回 []。\n\n"
            f"用户的话：\n{convo}\n\nJSON："
        )
        try:
            if hasattr(llm_fn, "quick_call"):
                raw = llm_fn.quick_call("你是偏好提取器，只输出JSON。", prompt, 400)
            elif hasattr(llm_fn, "chat"):
                raw = llm_fn.chat([{"role": "user", "content": prompt}], max_tok=400)
            else:
                return
            raw = (raw or "").strip()
            # 容错解析 JSON 数组
            start = raw.find("[")
            end = raw.rfind("]")
            if start < 0 or end < 0:
                return
            prefs = json.loads(raw[start:end + 1])
            if not isinstance(prefs, list):
                return
            from hashmm.api import database as db
            saved = 0
            for p in prefs[:8]:
                if not isinstance(p, dict):
                    continue
                key = str(p.get("key", "")).strip()
                val = str(p.get("value", "")).strip()
                cat = str(p.get("category", "")).strip()
                if val:
                    db.save_user_memory(self.user_id, cat, key or val[:20], val)
                    saved += 1
            if saved:
                logger.info(f"[Memory] 为用户 {self.user_id} 保存了 {saved} 条偏好")
        except Exception as e:
            logger.debug(f"preference extraction skipped: {e}")

    def _summarize(self, messages: list[dict], llm_fn: Any = None) -> str:
        """生成消息列表的摘要。"""
        if not messages:
            return ""

        # 提取用户问题和助手回答的关键片段
        parts = []
        for m in messages:
            role = m.get("role", "")
            content = str(m.get("content", ""))[:150]
            if role == "user":
                parts.append(f"用户问: {content}")
            elif role == "assistant" and not m.get("tool_calls"):
                parts.append(f"回答要点: {content}")
            elif role == "tool":
                parts.append(f"[工具结果片段]")

        return "\n".join(parts[-8:])  # 最多 8 条摘要
