"""Agent Reflection — post-task quality self-check.

After completing a task with 2+ tool calls, the agent can optionally
reflect on the result quality and trigger a fix pass if needed.
"""
from __future__ import annotations
import json
import re
from typing import Any


REFLECTION_PROMPT = """你刚完成了以下任务。请快速检查结果质量。

用户请求：{query}

执行步骤：
{steps_summary}

最终输出预览（前500字）：
{output_preview}

请检查：
1. 任务是否完全完成？
2. 输出中有没有明显错误（代码语法？文档结构？数据准确性？）
3. 有没有遗漏？

回复纯JSON（不要markdown）：
{{"complete": true/false, "quality": "good"/"acceptable"/"poor", "issues": ["issue1", "issue2"], "fix_suggestion": "..."}}

如果一切正常，直接返回 {{"complete": true, "quality": "good", "issues": []}}"""


def should_reflect(task_type: str, steps: list[dict], elapsed_ms: int = 0) -> bool:
    """Decide whether reflection is warranted.

    Only reflect on complex tasks (2+ tool calls) that took real effort.
    Simple Q&A and single-tool calls don't need reflection.
    """
    if task_type in ("direct_task",):
        return False
    if len(steps) < 2:
        return False
    # Don't reflect on very fast tasks (likely simple)
    if elapsed_ms > 0 and elapsed_ms < 2000:
        return False
    # Check if any step had errors
    has_errors = any(s.get("status") == "error" for s in steps)
    # Reflect if errors occurred or task was complex
    return has_errors or len(steps) >= 3


def build_reflection_prompt(query: str, steps: list[dict],
                            output: str) -> tuple[str, str]:
    """Build system + user prompts for the reflection call.

    Returns (system_prompt, user_prompt).
    """
    steps_summary = "\n".join(
        f"  {i+1}. [{s.get('tool', '?')}] {s.get('status', '?')}"
        f"{' — ' + s.get('detail', '')[:80] if s.get('detail') else ''}"
        for i, s in enumerate(steps)
    )

    user_prompt = REFLECTION_PROMPT.format(
        query=query[:300],
        steps_summary=steps_summary,
        output_preview=output[:500],
    )

    return "你是质量检查员。只输出JSON，不要任何其他内容。", user_prompt


def parse_reflection(raw: str) -> dict[str, Any]:
    """Parse the LLM reflection response into a structured result.

    Returns a dict with: complete, quality, issues, fix_suggestion.
    Falls back to "complete: True" if parsing fails.
    """
    # Strip think tags
    cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

    # Try to extract JSON
    # Handle ```json fences
    cleaned = re.sub(r'^```json\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    try:
        result = json.loads(cleaned)
        return {
            "complete": result.get("complete", True),
            "quality": result.get("quality", "good"),
            "issues": result.get("issues", []),
            "fix_suggestion": result.get("fix_suggestion", ""),
        }
    except (json.JSONDecodeError, ValueError):
        # If we can't parse, assume it's fine
        return {
            "complete": True,
            "quality": "acceptable",
            "issues": [],
            "fix_suggestion": "",
        }


def format_reflection_for_user(reflection: dict[str, Any]) -> str | None:
    """Format reflection result as user-visible text.

    Returns None if no issues found (don't show anything).
    Returns a short note if there are issues.
    """
    if reflection["complete"] and reflection["quality"] in ("good", "acceptable"):
        if not reflection["issues"]:
            return None

    parts = []
    if not reflection["complete"]:
        parts.append("⚠️ 任务可能未完全完成")
    if reflection["issues"]:
        for issue in reflection["issues"][:3]:
            parts.append(f"• {issue}")
    if reflection["fix_suggestion"]:
        parts.append(f"💡 建议: {reflection['fix_suggestion']}")

    return "\n".join(parts) if parts else None
