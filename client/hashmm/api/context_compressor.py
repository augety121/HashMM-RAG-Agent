"""ContextWindow v15 — budget-based context management.

Like a CPU cache: fixed budget per layer, strategic allocation.
Never truncate blindly — always compress with key fact extraction.
"""
from __future__ import annotations
import re


# Token budget per layer
BUDGET = {
    "system_prompt": 1500,
    "workspace": 1000,
    "history_summary": 500,
    "recent_messages": 3000,
    "current_query": 500,
    "tool_buffer": 1500,
}
TOTAL_BUDGET = 8000


def estimate_tokens(text: str) -> int:
    """Rough token estimate."""
    if not text:
        return 0
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return int(chinese * 1.5 + len(text) * 0.4)


def truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit token budget."""
    if estimate_tokens(text) <= max_tokens:
        return text
    # Binary search for right length
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "\n...(截断)"


def compress_history(messages: list[dict], keep_recent: int = 4, max_summary_tokens: int = 500) -> list[dict]:
    """Compress old messages into summary, keep recent intact."""
    if len(messages) <= keep_recent + 1:
        return messages

    old = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    facts = []
    for m in old:
        role = m.get("role", "")
        content = m.get("content", "")

        if role == "user":
            facts.append(f"• 用户: {content[:100]}")
        elif role == "assistant":
            # Extract key info
            files = m.get("files", [])
            if files:
                fnames = [f.get("filename", "?") if isinstance(f, dict) else str(f) for f in files[:3]]
                facts.append(f"• 创建了: {', '.join(fnames)}")
            # First meaningful sentence
            sentences = re.split(r'[。.!！\n]', content)
            meaningful = [s.strip() for s in sentences if len(s.strip()) > 15]
            if meaningful:
                facts.append(f"• 回答: {meaningful[0][:80]}")
            if "执行成功" in content:
                facts.append("• 代码执行成功")

    if not facts:
        return recent

    summary = truncate_to_budget(
        "**对话摘要：**\n" + "\n".join(facts[-15:]),
        max_summary_tokens
    )
    return [{"role": "system", "content": summary}] + recent


def build_context_window(system_prompt: str, workspace_ctx: str,
                         history: list[dict], query: str) -> list[dict]:
    """Build messages array with budget-managed context."""
    messages = []

    # Layer 1: System prompt
    messages.append({"role": "system", "content": truncate_to_budget(
        system_prompt, BUDGET["system_prompt"])})

    # Layer 2: Workspace
    if workspace_ctx:
        messages.append({"role": "system", "content": truncate_to_budget(
            workspace_ctx, BUDGET["workspace"])})

    # Layer 3+4: Compressed history + recent
    compressed = compress_history(history)
    for msg in compressed:
        messages.append(msg)

    # Layer 5: Current query
    messages.append({"role": "user", "content": truncate_to_budget(
        query, BUDGET["current_query"])})

    return messages


async def llm_compress(messages: list[dict], llm_fn=None) -> str:
    """Use LLM to compress old messages into a precise summary.
    Falls back to rule-based if LLM unavailable.
    """
    if not llm_fn or not hasattr(llm_fn, 'stream'):
        # Fallback to rule-based
        compressed = compress_history(messages)
        return compressed[0]["content"] if compressed and compressed[0]["role"] == "system" else ""

    content_parts = []
    for m in messages:
        role = "用户" if m["role"] == "user" else "助手"
        text = m.get("content", "")[:200]
        content_parts.append(f"{role}: {text}")

    compress_prompt = """总结以下对话的关键信息（3-5 句话，不超过 200 字）：
保留：用户需求、创建的文件、重要决定、当前状态。"""

    try:
        summary = ""
        for token in llm_fn.stream([
            {"role": "system", "content": compress_prompt},
            {"role": "user", "content": "\n".join(content_parts[-20:])}
        ]):
            summary += token
        return summary.strip()[:500]
    except Exception as _e:
        # Fallback
        compressed = compress_history(messages)
        return compressed[0]["content"] if compressed and compressed[0]["role"] == "system" else ""


# ═══════════════════════════════════════════════════════════════════
# v25: Cross-session user memory (HA pattern)
# ═══════════════════════════════════════════════════════════════════

def extract_user_facts(messages: list[dict]) -> list[dict]:
    """Extract persistent facts about the user from conversation.
    
    Looks for patterns like:
    - "我是做 xxx 的" → preference
    - "我的项目是 xxx" → project info  
    - "我用 Python/PyTorch" → tech stack
    - "我导师是 xxx" → relationship
    """
    import re
    facts = []
    
    PATTERNS = [
        (r"我(?:是|在)(?:做|研究|搞)([一-鿿\w\s]{2,20})", "research_area", "研究方向"),
        (r"我(?:的)?(?:项目|课题|论文)(?:是|叫|关于)([一-鿿\w\s]{2,30})", "project", "项目"),
        (r"我(?:用|使用|偏好|喜欢)(Python|PyTorch|TensorFlow|JAX|C\+\+|Java|Go|Rust)", "tech_stack", "技术栈"),
        (r"我(?:的)?导师(?:是|叫)([一-鿿\w]{2,10})", "advisor", "导师"),
        (r"我(?:是|在)([一-鿿]{2,10}(?:大学|学院|研究所|实验室))", "affiliation", "单位"),
        (r"(?:名字|叫|称呼).{0,5}([一-鿿]{2,4})", "name", "称呼"),
    ]
    
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = msg.get("content", "")
        for pattern, category, label in PATTERNS:
            m = re.search(pattern, text)
            if m:
                value = m.group(1).strip()
                if len(value) >= 2:
                    facts.append({"category": category, "key": label, "value": value, "confidence": 0.8})
    
    return facts[:5]  # Max 5 facts per conversation


def build_user_memory_context(user_id: str) -> str:
    """Build context from stored user memories."""
    try:
        # Import database to get memories
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from database import get_user_memories
        memories = get_user_memories(user_id)
        if not memories:
            return ""
        
        lines = ["**关于用户：**"]
        for m in memories[:10]:
            lines.append(f"- {m.get('key', '')}: {m.get('value', '')}")
        return "\n".join(lines)
    except Exception as _e:
        return ""
