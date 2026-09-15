"""TokenBudgetBuilder v6.0 — dynamic context window (token-budget) management.

Solves: when conversation is long + retrieval results are many + file is large,
the total token count exceeds the model's context window. Instead of blindly
truncating, this module allocates a token budget across components.

Budget allocation (default 8000 tokens total):
  System prompt:     ~800 tokens (10%)
  Retrieval results: ~2500 tokens (31%) — highest priority for RAG
  Conversation history: ~2000 tokens (25%)
  File context:      ~2000 tokens (25%)
  User query:        ~700 tokens (9%)

Usage:
    from hashmm.context_builder import TokenBudgetBuilder
    builder = TokenBudgetBuilder(total_budget=8000)
    messages = builder.build(
        task_type="knowledge_task",
        query="小米2024年营收",
        history=[...],
        retrieval_chunks=[...],
        file_context="...",
        user_profile={...},
    )
"""
from __future__ import annotations
from hashmm.utils import get_logger

logger = get_logger("hashmm.context_builder")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 Chinese char ≈ 1.5 tokens, 1 English word ≈ 1 token."""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cn_chars
    return int(cn_chars * 1.5 + other * 0.3)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    if not text or max_tokens <= 0:
        return ""
    # Rough: 1 token ≈ 1.5 chars for mixed CJK/English
    max_chars = int(max_tokens * 1.5)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…（已截断）"


class TokenBudgetBuilder:
    """Build LLM messages with intelligent token budget management."""

    def __init__(self, total_budget: int = 8000):
        self.total_budget = total_budget

    def build(self, *,
              task_type: str,
              query: str,
              system_prompt: str,
              history: list[dict] | None = None,
              retrieval_injection: str = "",
              file_context: str = "",
              user_profile: str = "",
              output_hint: str = "",
              ) -> list[dict]:
        """Build optimized messages list within token budget.

        Args:
            task_type: "knowledge_task", "code_task", etc.
            query: Current user query
            system_prompt: Base system prompt for this task type
            history: Conversation history
            retrieval_injection: Formatted retrieval results
            file_context: Uploaded file content
            user_profile: User preferences from memory
            output_hint: Format hint from QueryPlanner

        Returns:
            messages list ready for LLM
        """
        history = history or []

        # Step 1: Calculate budget allocation
        budget = self._allocate_budget(task_type, bool(retrieval_injection),
                                       bool(file_context), len(history))

        # Step 2: Build system prompt (within budget)
        sys_parts = [_truncate_to_tokens(system_prompt, budget["system"])]
        if user_profile:
            sys_parts.append(_truncate_to_tokens(user_profile, 200))
        if retrieval_injection:
            sys_parts.append(_truncate_to_tokens(retrieval_injection, budget["retrieval"]))
        if output_hint:
            sys_parts.append(output_hint)

        messages = [{"role": "system", "content": "\n\n".join(p for p in sys_parts if p)}]

        # Step 3: Compress history (within budget)
        if history:
            hist_budget = budget["history"]
            hist_tokens_used = 0

            checkpoint = next((h for h in history
                               if h.get("role") == "system" and
                               str(h.get("content") or "").startswith(
                                   "[会话压缩检查点 / durable context checkpoint]")), None)
            dialogue = [h for h in history if h is not checkpoint]
            if checkpoint:
                cp = _truncate_to_tokens(str(checkpoint.get("content") or ""),
                                         max(300, hist_budget // 2))
                if cp:
                    messages.append({"role": "system", "content": cp})
                    hist_tokens_used += _estimate_tokens(cp)

            # Always keep last 2 turns
            recent = dialogue[-4:] if len(dialogue) > 4 else dialogue
            older = dialogue[:-4] if len(dialogue) > 4 else []

            # Add older history (compressed)
            if older:
                summary_parts = []
                for h in older[-6:]:
                    prefix = "Q" if h["role"] == "user" else "A"
                    summary_parts.append(f"{prefix}: {h['content'][:60]}")
                summary = "之前的对话：" + " | ".join(summary_parts)
                summary = _truncate_to_tokens(summary, hist_budget // 3)
                if summary:
                    messages.append({"role": "system", "content": summary})
                    hist_tokens_used += _estimate_tokens(summary)

            # Add recent history
            for h in recent:
                remaining = hist_budget - hist_tokens_used
                if remaining < 50:
                    break
                content = _truncate_to_tokens(h["content"], remaining // max(len(recent), 1))
                messages.append({
                    "role": "user" if h["role"] == "user" else "assistant",
                    "content": content,
                })
                hist_tokens_used += _estimate_tokens(content)

        # Step 4: Build user message
        user_parts = []
        if file_context:
            fc = _truncate_to_tokens(file_context, budget["file"])
            user_parts.append(f"用户上传的文件内容：\n{fc}")
        user_parts.append(query)
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        total_tokens = sum(_estimate_tokens(m["content"]) for m in messages)
        logger.debug(f"Context built: {len(messages)} messages, ~{total_tokens} tokens "
                     f"(budget: {self.total_budget})")

        return messages

    def _allocate_budget(self, task_type: str, has_retrieval: bool,
                         has_file: bool, history_len: int) -> dict:
        """Dynamically allocate token budget based on task type and content."""
        total = self.total_budget

        if task_type == "knowledge_task" and has_retrieval:
            # RAG task: prioritize retrieval results
            return {
                "system": int(total * 0.10),
                "retrieval": int(total * 0.35),
                "history": int(total * 0.20),
                "file": int(total * 0.05),
                "query": int(total * 0.10),
            }
        elif has_file:
            # File analysis: prioritize file content
            return {
                "system": int(total * 0.10),
                "retrieval": int(total * 0.05),
                "history": int(total * 0.15),
                "file": int(total * 0.45),
                "query": int(total * 0.10),
            }
        elif task_type == "code_task":
            # Code generation: maximize history (for iterative development)
            return {
                "system": int(total * 0.10),
                "retrieval": int(total * 0.05),
                "history": int(total * 0.50),
                "file": int(total * 0.05),
                "query": int(total * 0.10),
            }
        else:
            # General / chat
            return {
                "system": int(total * 0.10),
                "retrieval": int(total * 0.10),
                "history": int(total * 0.40),
                "file": int(total * 0.10),
                "query": int(total * 0.10),
            }
