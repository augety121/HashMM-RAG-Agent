"""Predictive Prefetch v22 — anticipate user's next action.

Based on conversation patterns, predict what the user might do next
and prefetch relevant data (kb search, file scan, etc.)
"""
from __future__ import annotations


def predict_next_action(recent_messages: list[dict], workspace_files: list[str] = None) -> dict | None:
    """Predict what the user might do next."""
    if not recent_messages:
        return None

    last_assistant = ""
    last_user = ""
    for m in reversed(recent_messages):
        if m["role"] == "assistant" and not last_assistant:
            last_assistant = m.get("content", "")
        elif m["role"] == "user" and not last_user:
            last_user = m.get("content", "")
        if last_assistant and last_user:
            break

    # Pattern: just created code → user likely wants to run/test/modify
    if any(f".py" in (last_assistant or "") for f in [".py"]):
        if "创建" in last_assistant or "create_file" in last_assistant:
            return {"prediction": "run_or_test", "confidence": 0.7,
                    "suggested_prompts": ["运行一下", "写个测试", "优化性能"]}

    # Pattern: just answered knowledge → user likely wants deeper info or code
    if "知识" in str(recent_messages[-1:]) or len(last_assistant) > 500:
        return {"prediction": "deeper_or_code", "confidence": 0.5,
                "suggested_prompts": ["能详细解释吗", "给个代码示例", "做成PPT"]}

    # Pattern: workspace has many files → user likely wants project-level operation
    if workspace_files and len(workspace_files) > 5:
        return {"prediction": "project_operation", "confidence": 0.6,
                "suggested_prompts": ["分析项目结构", "找出问题", "重构代码"]}

    return None
