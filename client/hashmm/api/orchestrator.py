"""Orchestrator v12 — bulletproof classification.

Priority logic (tested with 16 regression cases):
  1. Greeting → direct
  2. Code signals + format keyword → code (code wins over format)
  3. Code signals (no format) → code
  4. Knowledge signals + format keyword (no creation verb) → knowledge
  5. Format keyword + creation verb → doc
  6. Project signals + creation verb → code
  7. Data analysis → complex
  8. Doc signals + creation verb → doc
  9. Modify → complex
  10. Knowledge signals → knowledge
  11. File context → complex
  12. Default → knowledge
"""
from __future__ import annotations
import re
from typing import Any

from .handlers.base import BaseHandler
from .handlers.direct import DirectHandler
from .handlers.knowledge import KnowledgeHandler
from .handlers.document import DocumentHandler
from .handlers.code import CodeHandler
from .handlers.agent import AgentLoopHandler


_FORMAT_KW = {
    "ppt", "幻灯片", "slides", "presentation", "演示",
    "word", "docx", "excel", "xlsx", "表格文件", "pdf",
}
_CODE_SIGNALS = {
    "代码", "code", "实现", "implement", ".py", ".js", ".java", ".cpp",
    "函数", "class", "脚本", "script", "编程", "算法",
    "python", "javascript", "typescript", "java", "c++", "cpp",
    "pytorch", "tensorflow", "numpy", "pandas", "matplotlib",
    "golang", "rust", "matlab", "排序", "sort", "api", "爬虫", "系统", "system",
}
_DOC_SIGNALS = {"报告", "方案", "总结", "综述", "论文框架", "撰写", "草稿", "draft", "模板", "template"}
_CREATION_VERBS = {"写", "做", "制作", "创建", "生成", "create", "make", "build", "generate", "撰写", "编写", "给我", "帮我"}
_KB_SIGNALS = {"什么是", "解释", "对比", "原理", "how", "what", "compare", "explain", "区别", "是什么", "介绍", "vs", "why", "哪些", "方法", "模型", "论文", "技术", "概念", "综述", "教程", "tutorial"}
_DATA_SIGNALS = {"分析数据", "analyze data", "csv", "数据分析", "统计", "画图", "可视化", "visualize"}
_MODIFY_SIGNALS = {"修改", "改一下", "优化代码", "重构", "refactor", "fix", "修复", "调试", "debug"}
_PROJECT_SIGNALS = {"项目", "project", "框架", "scaffold", "多文件"}
_GREETING = {"你好", "hello", "hi", "谢谢", "嗨", "hey", "早上好", "晚上好", "在吗"}


def _has_any(text, signals):
    t = text.lower()
    return any(s in t for s in signals)


def classify(query: str, file_context: str = "") -> str:
    q = query.strip()
    ql = q.lower()

    has_format = any(f in ql for f in _FORMAT_KW)
    has_code = _has_any(q, _CODE_SIGNALS)
    has_kb = _has_any(q, _KB_SIGNALS)
    has_creation = _has_any(q, _CREATION_VERBS)
    has_doc = _has_any(q, _DOC_SIGNALS)
    has_data = _has_any(q, _DATA_SIGNALS)

    # 1. Greeting
    if len(q) < 10 and any(g in q for g in _GREETING):
        return "direct_task"
    if len(q) < 6:
        return "direct_task"

    # 2. Code signals + format keyword → CODE WINS
    #    "帮我用 Python 写一个生成 PPT 的脚本" → code (has python + 脚本)
    if has_code and has_format:
        return "code_task"

    # 3. Code signals (no format) → code
    if has_code:
        return "code_task"

    # 4. Knowledge signals + format keyword but NO creation verb → knowledge
    #    "Excel 公式教程" → knowledge (has 教程 + Excel but no 写/做)
    #    "给我一个 Excel 公式的教程" → knowledge (教程 is knowledge)
    if has_kb and has_format and not has_creation:
        return "knowledge_task"
    if has_kb and has_format and has_creation:
        # "帮我做个 PPT" → doc (做 is creation)
        # "给我一个 Excel 教程" → knowledge (教程 overrides)
        if any(k in ql for k in ["教程", "tutorial", "介绍", "解释", "是什么", "什么是"]):
            return "knowledge_task"
        return "doc_task"

    # 5. Format keyword + creation verb → doc
    if has_format and has_creation:
        return "doc_task"

    # 6. Format keyword alone (like "转成 PDF")
    if has_format:
        return "doc_task"

    # 7. Project signals + creation verb → code
    if any(s in ql for s in _PROJECT_SIGNALS) and has_creation:
        return "code_task"

    # 8. Data analysis → complex (needs tools)
    #    But NOT "数据分析报告" → that's doc_task
    if has_data and not has_doc:
        return "complex_task"

    # 9. Doc signals + creation verb → doc
    #    "帮我写一份数据分析报告" → doc (has 报告 + 写)
    if has_doc and has_creation:
        return "doc_task"

    # 10. Modify → complex
    if _has_any(q, _MODIFY_SIGNALS):
        return "complex_task"
    if file_context and _has_any(q, _MODIFY_SIGNALS):
        return "complex_task"

    # 11. Knowledge signals → knowledge
    if has_kb:
        return "knowledge_task"

    # 12. File context → complex
    if file_context:
        return "complex_task"

    # 13. Default
    if len(q) < 15:
        return "direct_task"
    return "knowledge_task"


def create_handler(task_type: str, **kwargs) -> BaseHandler:
    handlers = {
        "direct_task": DirectHandler,
        "knowledge_task": KnowledgeHandler,
        "doc_task": DocumentHandler,
        "code_task": CodeHandler,
        "complex_task": AgentLoopHandler,
    }
    cls = handlers.get(task_type, KnowledgeHandler)
    return cls(**kwargs)
