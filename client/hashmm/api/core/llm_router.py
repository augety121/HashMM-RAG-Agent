"""LLMRouter — select LLM parameters by task intent.

Solves the "same question, same answer" problem by routing different
task types to different temperature / top_p profiles. Also injects
a timestamp suffix so even identical queries produce variation.

Usage:
    from hashmm.api.core.llm_router import LLMRouter
    router = LLMRouter()
    params = router.get_params("analytical")
    # → {"temperature": 0.5, "top_p": 0.95, "instruction": "...", "system_suffix": "..."}
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any


class LLMRouter:
    """Intelligent LLM parameter routing — picks optimal settings per task."""

    PROFILES: dict[str, dict[str, Any]] = {
        "factual": {
            "temperature": 0.1,
            "top_p": 0.9,
            "instruction": "请准确回答，引用来源。",
        },
        "analytical": {
            "temperature": 0.5,
            "top_p": 0.95,
            "instruction": "请从多个角度深入分析。",
        },
        "creative": {
            "temperature": 0.8,
            "top_p": 0.98,
            "instruction": "请提供有创意和洞察力的回答。",
        },
        "code": {
            "temperature": 0.0,
            "top_p": 1.0,
            "instruction": "请写出正确、高效的代码。",
        },
        "chitchat": {
            "temperature": 0.7,
            "top_p": 0.95,
            "instruction": "",
        },
    }

    # Map from task_type (from intent engine) to profile key
    TASK_MAP: dict[str, str] = {
        "rag_task": "factual",
        "code_task": "code",
        "doc_task": "analytical",
        "analysis_task": "analytical",
        "modify_task": "code",
        "direct_task": "chitchat",
        "compare": "analytical",
    }

    def get_params(
        self,
        intent_or_task: str,
        user_prefs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return LLM params for the given intent/task type.

        Args:
            intent_or_task: Either a profile key ("factual") or task_type ("rag_task").
            user_prefs: Optional user overrides (e.g. {"temperature": 0.3}).

        Returns:
            Dict with temperature, top_p, instruction, system_suffix.
        """
        profile_key = self.TASK_MAP.get(intent_or_task, intent_or_task)
        profile = dict(self.PROFILES.get(profile_key, self.PROFILES["analytical"]))

        if user_prefs:
            if "temperature" in user_prefs:
                profile["temperature"] = float(user_prefs["temperature"])
            if "top_p" in user_prefs:
                profile["top_p"] = float(user_prefs["top_p"])

        profile["system_suffix"] = self._build_diversity_suffix()

        return profile

    @staticmethod
    def _build_diversity_suffix() -> str:
        """Generate a randomized suffix that encourages diverse answers.

        Claude's answer diversity comes from 4 layers:
        1. temperature > 0 (sampling randomness) — handled by profiles
        2. timestamp in system prompt — handled here
        3. perspective rotation — randomized angle prompt
        4. opening variation hint — avoid starting with same phrase every time
        """
        import random

        # Perspective rotation — different angle each time
        perspectives = [
            "请从你认为最有价值的角度来回答。",
            "请先给出核心结论，再展开论述。",
            "请用简洁的方式回答，突出关键信息。",
            "如果有多个维度，请选择最有洞察力的切入点。",
            "请从数据出发，用事实说话。",
            "请站在专业分析师的角度回答。",
            "请兼顾全面性和可读性。",
            "请注重逻辑链条，让推理过程清晰可见。",
        ]

        # Opening hint — avoid "根据知识库..." every time
        openings = [
            "",
            "回答时不要以'根据'开头。",
            "直接给出答案，不需要前置说明。",
            "可以从一个有趣的切入点开始回答。",
        ]

        return (
            f"\n当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"\n{random.choice(perspectives)}"
            f"\n{random.choice(openings)}"
        )

    @classmethod
    def classify_style(cls, query: str) -> str:
        """Quick rule-based style classification for the settings panel."""
        q = query.lower()
        if any(kw in q for kw in ["代码", "实现", "code", "函数", "class", "def ", "import"]):
            return "code"
        if any(kw in q for kw in ["分析", "对比", "比较", "原因", "影响", "趋势"]):
            return "analytical"
        if any(kw in q for kw in ["写", "创作", "设计", "头脑风暴", "想法"]):
            return "creative"
        if any(kw in q for kw in ["是多少", "什么时候", "谁", "哪个", "数据"]):
            return "factual"
        return "analytical"


# Module-level singleton
_router = LLMRouter()


def get_llm_params(task_type: str, user_prefs: dict | None = None) -> dict:
    """Convenience function for route handlers."""
    return _router.get_params(task_type, user_prefs)
