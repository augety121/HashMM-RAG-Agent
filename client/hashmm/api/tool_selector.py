"""Tool Selector — filter tools by task type to reduce token usage.

Instead of passing all tools to the LLM (~3000 tokens), only pass the tools
relevant to the current task type.

V104: 修了一个真问题——deep_search / deep_research 这两个最强的检索工具此前不在任何
分组里，一旦按任务过滤就被丢掉、模型根本看不到。现在已纳入相关分组；并新增 research_task、
放宽安全网（kb_search + web_search 始终可用），让"调研/对比/多跳"类任务真的用上深度检索。
"""
from __future__ import annotations
from hashmm.utils import get_logger

logger = get_logger("hashmm.tool_selector")

# Task type → allowed tool names
TOOL_GROUPS: dict[str, list[str]] = {
    "direct_task": [],  # No tools needed for direct Q&A

    "knowledge_task": [
        "kb_search", "web_search", "deep_search", "deep_research",
        "get_datetime", "calculator",
    ],

    "research_task": [   # 调研/综述/对比/多跳：以深度检索为主，可顺带产出文档
        "kb_search", "web_search", "deep_search", "deep_research",
        "create_document", "create_file", "execute_code",
        "get_datetime", "calculator",
    ],

    "code_task": [
        "create_file", "execute_code", "read_file", "read_file_range",
        "str_replace", "insert_lines", "search_files", "file_tree",
        "run_shell", "list_files", "kb_search", "web_search",
    ],

    "doc_task": [
        "create_document", "create_pptx_from_plan", "create_xlsx",
        "create_pdf", "convert_file", "inspect_office", "kb_search", "web_search",
        "deep_search", "get_datetime",
    ],

    "data_task": [
        "execute_code", "create_file", "read_file", "create_xlsx",
        "kb_search", "web_search", "deep_search", "calculator", "get_datetime",
    ],

    "modify_task": [
        "read_file", "read_file_range", "str_replace", "insert_lines",
        "search_files", "file_tree", "execute_code", "list_files", "kb_search",
    ],

    "web_task": [
        "web_search", "kb_search", "deep_search", "deep_research", "get_datetime",
    ],
}

# Tools that should always be available when ANY toolset is selected (safety net).
# 让模型任何分类任务下都能取到本地知识与实时信息，避免因分类不准而拿不到关键工具。
ALWAYS_AVAILABLE = {"kb_search", "web_search"}


def select_tools(task_type: str, all_tools: list[dict]) -> list[dict]:
    """Filter tools based on task type.

    Args:
        task_type: The classified task type (e.g., "code_task", "doc_task")
        all_tools: Full list of tool definitions (OpenAI function calling format)

    Returns:
        Filtered list of tool definitions for the given task type.
        Returns all tools if task_type is not recognized.
    """
    allowed_names = TOOL_GROUPS.get(task_type)

    if allowed_names is None:
        # Unknown task type → give all tools
        logger.info(f"Unknown task type '{task_type}', using all {len(all_tools)} tools")
        return all_tools

    if not allowed_names:
        # direct_task → no tools
        return []

    names = set(allowed_names) | ALWAYS_AVAILABLE
    filtered = [t for t in all_tools if t.get("function", {}).get("name") in names]

    logger.info(f"Task '{task_type}': {len(filtered)}/{len(all_tools)} tools selected")
    return filtered


def get_tool_group(task_type: str) -> list[str]:
    """Get the list of tool names for a task type."""
    return TOOL_GROUPS.get(task_type, [])


def suggest_task_type(query: str) -> str | None:
    """Quick heuristic to suggest task type from query keywords.

    顺序很重要：先判更具体的（研究/代码/文档/数据/修改），再到泛化的（搜索）。
    命中不了返回 None（= 给全部工具，最安全）。
    """
    q = (query or "").lower()

    def has(*ws: str) -> bool:
        return any(w in q for w in ws)

    # 研究/综述/对比/多跳 —— 最具体，优先；这类要用 deep_search/deep_research
    if has("调研", "综述", "研究报告", "深度研究", "对比", "比较", "优缺点", "哪个更", "哪个好",
           "区别", "评测", "横向", "利弊", "research", "compare", "versus", " vs ", "pros and cons",
           "trade-off", "tradeoff", "多个", "几种", "综合分析"):
        return "research_task"

    # 修改/修复已有内容 —— 比"写代码"更具体，先判（避免"修复代码"被当成新写代码）
    if has("修改", "编辑", "替换", "修复", "改一下", "改成", "bug", "fix", "报错", "调试", "debug", "优化代码"):
        return "modify_task"

    # 写代码/实现
    if has("写代码", "实现", "编程", "函数", "class ", "def ", "算法", ".py", ".js", ".ts",
           ".java", ".cpp", "脚本", "代码", "function", "implement", "leetcode", "重构", "refactor"):
        return "code_task"

    # 文档产出
    if has("ppt", "演示文稿", "幻灯", "word", "文档", "报告", "论文", "简历", "周报", "方案",
           "excel", "表格", "pdf", "slides", "presentation", "report", "生成一份"):
        return "doc_task"

    # 数据分析
    if has("分析", "统计", "csv", "数据", "图表", "可视化", "画图", "趋势", "回归", "分布",
           "analysis", "statistics", "chart", "plot", "dataset", "数据集"):
        return "data_task"

    # 搜索/实时信息
    if has("搜索", "查找", "查一下", "最新", "新闻", "实时", "现在", "今天", "目前", "近况",
           "search", "latest", "news", "current", "实况", "价格", "股价", "天气"):
        return "web_task"

    return None
