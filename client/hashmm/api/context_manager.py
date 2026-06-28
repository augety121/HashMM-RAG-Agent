"""Context Window Manager — fit messages into LLM token budget.

Ensures system prompt + tools + history + query + retrieval context
all fit within the model's context window.

P1-5 enhancements:
  - Per-model context sizes (DeepSeek 128K, GPT-4o 128K, Claude 200K)
  - History compression: old messages auto-summarized
  - Retrieval context injection slot
"""
from __future__ import annotations
import json
from hashmm.utils import get_logger

logger = get_logger("hashmm.context_manager")

# Model context window sizes (input tokens)
MODEL_CONTEXT_SIZES = {
    "deepseek-v4-pro": 128000, "deepseek-v4-flash": 128000,
    "deepseek-chat": 128000, "deepseek-reasoner": 64000,
    "gpt-4o": 128000, "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000, "o1": 200000,
    "claude-sonnet-4-20250514": 200000, "claude-opus-4-20250514": 200000,
    "gemini-2.5-pro": 1000000, "gemini-2.5-flash": 1000000,
    "glm-4-plus": 128000, "glm-4-flash": 128000,
    "moonshot-v1-128k": 128000, "moonshot-v1-32k": 32000,
}

DEFAULT_MAX_CONTEXT = 32000
DEFAULT_RESERVED_OUTPUT = 8000


class ContextWindowManager:
    """Manages LLM context window token budget."""

    def __init__(self, max_context: int = DEFAULT_MAX_CONTEXT,
                 reserved_output: int = DEFAULT_RESERVED_OUTPUT):
        self.max_context = max_context
        self.reserved_output = reserved_output

    @classmethod
    def for_model(cls, model_name: str, reserved_output: int = 8000) -> "ContextWindowManager":
        """Create a ContextWindowManager auto-configured for a specific model."""
        ctx_size = MODEL_CONTEXT_SIZES.get(model_name, DEFAULT_MAX_CONTEXT)
        return cls(max_context=ctx_size, reserved_output=reserved_output)

    @property
    def budget(self) -> int:
        return self.max_context - self.reserved_output

    def count_tokens(self, text: str) -> int:
        """Estimate token count. ~3.5 chars per token for Chinese/English mix."""
        if not text:
            return 0
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other = len(text) - cjk
        return int(cjk * 1.5 + other / 3.5) + 4

    def count_messages(self, messages: list[dict]) -> int:
        """Count total tokens across all messages."""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(c) for c in content)
            total += self.count_tokens(str(content))
        return total

    def compress_history(self, messages: list[dict],
                         keep_recent: int = 6,
                         llm_fn=None) -> list[dict]:
        """Compress old history messages to fit within budget.

        Strategy:
        - Keep system message always
        - Keep last `keep_recent` messages verbatim
        - Summarize older messages into one summary block
        - If no LLM available, just truncate old messages
        """
        if len(messages) <= keep_recent + 1:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= keep_recent:
            return messages

        recent = non_system[-keep_recent:]
        old = non_system[:-keep_recent]

        # Try to summarize old messages with LLM
        if llm_fn and len(old) >= 3:
            old_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else '助手'}: {str(m.get('content', ''))[:200]}"
                for m in old
            )
            try:
                summary = llm_fn(f"请用 2-3 句话概括以下对话历史的要点：\n\n{old_text[:2000]}")
                if summary and len(summary) > 10:
                    summary_msg = {
                        "role": "system",
                        "content": f"[对话历史摘要] {summary}",
                    }
                    return system_msgs + [summary_msg] + recent
            except Exception:
                pass

        # Fallback: just truncate old messages to first line each
        compressed = []
        for m in old[-3:]:  # Keep last 3 old messages, truncated
            content = str(m.get("content", ""))[:100]
            compressed.append({"role": m["role"], "content": content + "..."})

        return system_msgs + compressed + recent
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += self.count_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count_tokens(part.get("text", ""))
        return total

    def fit(self, *,
            system: str,
            history: list[dict],
            query: str,
            tools: list[dict] | None = None,
            context: str = "") -> list[dict]:
        """Fit all content into the token budget.

        Returns assembled messages list ready for LLM.

        Priority (high to low):
        1. System prompt (always included)
        2. Query (always included)
        3. Tools schema (always included if provided)
        4. Context (workspace/RAG, truncated if too long)
        5. History (from newest to oldest, oldest dropped first)
        """
        # Calculate fixed costs
        sys_tokens = self.count_tokens(system)
        query_tokens = self.count_tokens(query)
        tool_tokens = self.count_tokens(json.dumps(tools)) if tools else 0
        ctx_tokens = self.count_tokens(context) if context else 0

        fixed = sys_tokens + query_tokens + tool_tokens

        # If context is too large, truncate it
        if context and fixed + ctx_tokens > self.budget * 0.7:
            # Give context at most 30% of budget
            max_ctx = int(self.budget * 0.3)
            context = self._truncate(context, max_ctx)
            ctx_tokens = self.count_tokens(context)

        fixed += ctx_tokens
        history_budget = self.budget - fixed

        if history_budget < 200:
            # Extreme case: even without history we're tight
            logger.warning(f"Very tight budget: {self.budget} - {fixed} = {history_budget} for history")
            history_budget = max(200, history_budget)

        # Fit history from newest to oldest
        fitted_history = self._fit_history(history, history_budget)

        # Assemble messages
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"## 参考上下文\n{context}"})
        messages.extend(fitted_history)
        messages.append({"role": "user", "content": query})

        total = self.count_messages(messages) + tool_tokens
        logger.info(f"Context: {total}/{self.budget} tokens "
                    f"(sys={sys_tokens} tools={tool_tokens} ctx={ctx_tokens} "
                    f"history={len(fitted_history)}/{len(history)} query={query_tokens})")

        return messages

    def _fit_history(self, history: list[dict], budget: int) -> list[dict]:
        """Fit history messages into token budget, newest first."""
        if not history:
            return []

        fitted = []
        used = 0
        for msg in reversed(history):
            msg_tokens = self.count_tokens(msg.get("content", ""))
            if used + msg_tokens > budget:
                if not fitted:
                    # At least keep the most recent message (truncated)
                    truncated = self._truncate(msg.get("content", ""), budget - used)
                    fitted.insert(0, {**msg, "content": truncated})
                break
            fitted.insert(0, msg)
            used += msg_tokens

        return fitted

    def _truncate(self, text: str, max_tokens: int) -> str:
        """Truncate text, keeping head and tail."""
        if self.count_tokens(text) <= max_tokens:
            return text

        # Rough char estimate
        max_chars = int(max_tokens * 3)
        if len(text) <= max_chars:
            return text

        head_chars = max_chars * 2 // 3
        tail_chars = max_chars - head_chars
        return (text[:head_chars]
                + "\n\n... (中间省略) ...\n\n"
                + text[-tail_chars:])


# Singleton
_manager: ContextWindowManager | None = None


def get_context_manager(max_context: int = DEFAULT_MAX_CONTEXT) -> ContextWindowManager:
    """Get or create the singleton ContextWindowManager."""
    global _manager
    if _manager is None or _manager.max_context != max_context:
        _manager = ContextWindowManager(max_context)
    return _manager
