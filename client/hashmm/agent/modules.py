"""能力模块化（路线图阶段 A）——把 Agent 的工具按能力归组成可插拔模块。

核心心智模型：**项目是一个 Agent，RAG 只是它众多能力模块中的一个**。
每个模块声明自己提供哪些工具（按工具名匹配 loop 的 schema/executor），
是否启用由环境变量开关，并自带健康检查。故障边界 = 模块边界：
某模块 health() 失败只禁用该模块（对应工具从 Agent 工具列表消失），
不拖垮主循环。

与现有体系的关系（非侵入）：
- 不改工具的 schema/executor 定义（仍在 loop.AGENT_TOOLS / tool_registry）；
- 只在 loop 组装工具列表时，按"启用模块提供的工具名集合"做一层过滤；
- 模块全开 = 现状（零行为变化）；关掉某模块 = 该模块的工具被摘掉。

开关（环境变量，默认全开）：
    HASHMM_MODULE_RAG=0          拔掉知识库检索（kb_search / kg_query）
    HASHMM_MODULE_COMPUTER=0     拔掉本机文件/代码能力（file_tree/read/create/str_replace/execute_code）
    HASHMM_MODULE_WEB=0          拔掉联网（web_search / fetch_url）
    HASHMM_MODULE_MEMORY=0       拔掉长期记忆（remember_preference）
    HASHMM_MODULE_DISPATCH=0     拔掉子任务/派活（spawn_worker）
    HASHMM_MODULE_IMAGE=0        拔掉图片检索（image_search，若注册）
（core 模块 update_todo 等永远在，不可关。）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.modules")


@dataclass
class ToolModule:
    """一个能力模块：一组工具 + 开关 + 健康检查。"""
    key: str                                   # 模块标识（= 环境变量后缀，小写）
    title: str                                 # 展示名
    tools: set[str]                            # 本模块提供的工具名集合
    desc: str = ""                             # 一句话说明
    core: bool = False                         # core=True 不可关闭
    health_fn: Callable[[], tuple[bool, str]] | None = field(default=None, repr=False)

    def enabled(self) -> bool:
        """core 恒开；否则看环境变量（默认开，=0 才关）。"""
        if self.core:
            return True
        return (os.environ.get(f"HASHMM_MODULE_{self.key.upper()}", "1") or "1") != "0"

    def health(self) -> tuple[bool, str]:
        """模块健康：未定义检查则视为健康。失败安全（异常=不健康但不抛）。"""
        if self.health_fn is None:
            return True, "ok"
        try:
            return self.health_fn()
        except Exception as e:
            log_suppressed(logger, e, f"module.health({self.key})")
            return False, f"健康检查异常: {str(e)[:80]}"


# ── 各模块健康检查（轻量、失败安全）────────────────────────────────────
def _health_rag() -> tuple[bool, str]:
    try:
        from hashmm.retriever_bridge import get_pipeline
        return (True, "检索管线就绪") if get_pipeline() else (False, "检索管线未初始化")
    except Exception as e:
        return False, f"检索不可用: {str(e)[:60]}"


def _health_web() -> tuple[bool, str]:
    # 联网能力只要模块可导入即视为可用（真实连通性交给调用时处理）
    try:
        import hashmm.tools.fetch_url  # noqa: F401
        return True, "联网工具就绪"
    except Exception as e:
        return False, f"联网工具缺失: {str(e)[:60]}"


def _health_image() -> tuple[bool, str]:
    try:
        from hashmm.retrieval.image_store import search_text  # noqa: F401
        return True, "图片索引就绪"
    except Exception as e:
        return False, f"图片库不可用: {str(e)[:60]}"


# ── 模块清单（单一事实来源）────────────────────────────────────────────
_MODULES: list[ToolModule] = [
    ToolModule("core", "核心", {"update_todo"}, "任务清单等主循环基础能力", core=True),
    ToolModule("rag", "知识库检索", {"kb_search", "kg_query"},
               "从知识库/知识图谱检索（RAG——可整体拔除的工具模块）", health_fn=_health_rag),
    ToolModule("computer", "文件与受控执行", {
        "execute_code", "run_shell", "create_file", "read_file", "edit_file", "list_files",
        "clean_workspace", "create_document", "create_xlsx", "create_pdf", "convert_file",
        "read_file_range", "str_replace", "insert_lines", "search_files", "file_tree",
        "file_versions", "file_restore", "pptx_summary", "pptx_edit_slide",
        "create_pptx_from_plan", "inspect_office",
    },
               "读写本机文件、生成文档、执行代码"),
    ToolModule("web", "联网检索", {"web_search", "fetch_url", "video_transcript"},
               "联网搜索与抓取网页", health_fn=_health_web),
    ToolModule("browser", "浏览器", {"browser_open", "browser_act", "browser_read", "browser_screenshot"},
               "Playwright/lite 双引擎网页浏览：打开→点击/填表→读全文/截图。页面内容自动进不可信包裹（防间接注入）。HASHMM_MODULE_BROWSER=0 拔掉"),
    ToolModule("memory", "长期记忆", {"remember_preference", "memory_recall"},
               "跨会话记住用户偏好 + 联邦召回（长期打法/教训·经验回放·画像·图谱）"),
    ToolModule("dispatch", "子任务/派活", {"spawn_worker"},
               "派专员做子任务 / 云上派活"),
    ToolModule("image", "图片检索", {"image_search"},
               "以文搜图（图2-④图片资源库）", health_fn=_health_image),
]

_BY_KEY = {m.key: m for m in _MODULES}


def all_modules() -> list[ToolModule]:
    return list(_MODULES)


def get_module(key: str) -> ToolModule | None:
    return _BY_KEY.get(key)


def enabled_tool_names() -> set[str]:
    """当前启用且真实装配完成的内置工具名并集。

    健康检查只说明依赖能 import，不能证明工具已进入 Chat。schema 或 executor
    任一缺失时必须对【该工具】fail closed，避免模块卡片和实际执行路径不一致。
    同一模块里的其他已完整装配工具仍可使用；否则一个可选工具的装配故障会把
    create_file 等无关核心能力一起摘掉，并让测试顺序或插件装载状态改变 Chat 行为。
    """
    schema_names, executor_names = _registry_inventory()
    allowed: set[str] = set()
    for m in _MODULES:
        if not m.enabled():
            continue
        ok, _ = m.health()
        if not ok and not m.core:
            logger.info(f"模块 {m.key} 健康检查未通过，本轮禁用其工具：{m.tools}")
            continue
        missing_schema = m.tools - schema_names
        missing_executor = (m.tools & schema_names) - executor_names
        if missing_schema or missing_executor:
            logger.info(
                "模块 %s 部分工具未接入 Chat，仅禁用缺失工具：schema=%s executor=%s",
                m.key, sorted(missing_schema), sorted(missing_executor),
            )
        # 安全边界以工具为粒度：模型只能看到同时拥有 schema 与 executor 的工具。
        # 不能因为同模块另一个工具缺失就误伤已验证能力，也不能为保模块完整而
        # 放行只有 schema、没有执行器的“展示型能力”。
        allowed |= (m.tools & schema_names & executor_names)
    return allowed


def filter_tools(tools: list[dict]) -> tuple[list[dict], list[str]]:
    """按启用模块过滤 loop 的工具 schema 列表。

    返回 (过滤后的工具列表, 被移除的工具名列表)。
    未被任何模块声明的工具（如 custom/MCP 动态工具）默认保留——
    模块系统只治理内置能力，不误伤用户自配工具。
    """
    governed: set[str] = set()
    for m in _MODULES:
        governed |= m.tools
    allowed = enabled_tool_names()

    kept: list[dict] = []
    removed: list[str] = []
    for t in tools:
        name = _tool_name(t)
        if name in governed and name not in allowed:
            removed.append(name)
            continue
        kept.append(t)
    return kept, removed


def _registry_inventory() -> tuple[set[str], set[str]]:
    """返回未经过模块过滤的内置 schema/executor 名单。"""
    schema_names: set[str] = set()
    executor_names: set[str] = set()
    try:
        from hashmm.agent.loop import AGENT_TOOLS
        schema_names |= {_tool_name(t) for t in AGENT_TOOLS}
    except Exception as e:
        log_suppressed(logger, e, "modules.agent_tools")
    try:
        from hashmm.api.tool_registry import TOOL_DEFS, get_executor_map
        schema_names |= {_tool_name(t) for t in TOOL_DEFS}
        executor_names |= set(get_executor_map())
    except Exception as e:
        log_suppressed(logger, e, "modules.tool_registry")
    # 这些工具由 AgentLoop 在中央执行入口显式处理，不走 executor map。
    executor_names |= {"update_todo", "spawn_worker", "remember_preference", "memory_recall"}
    return schema_names, executor_names


def status() -> list[dict]:
    """给 UI / 诊断用的模块状态总览。

    健康不再只看“模块能 import”。每个声明工具还必须同时存在 schema 和
    executor（主循环内建 special 工具除外），否则明确报未接入 Chat。
    """
    schema_names, executor_names = _registry_inventory()

    out = []
    for m in _MODULES:
        en = m.enabled()
        base_ok, base_msg = (m.health() if en else (True, "已关闭"))
        missing_schema = sorted(m.tools - schema_names)
        missing_executor = sorted((m.tools & schema_names) - executor_names)
        wired = not missing_schema and not missing_executor
        ok = bool(base_ok and wired) if en else True
        if en and not wired:
            parts = []
            if missing_schema:
                parts.append("缺少 schema: " + ", ".join(missing_schema))
            if missing_executor:
                parts.append("缺少执行器: " + ", ".join(missing_executor))
            msg = "未完整接入 Chat（" + "；".join(parts) + "）"
        else:
            msg = base_msg
        active_tools = sorted(
            m.tools & schema_names & executor_names
        ) if en and base_ok else []
        out.append({"key": m.key, "title": m.title, "desc": m.desc,
                    "enabled": en, "core": m.core,
                    "healthy": ok, "wired": wired, "health_msg": msg,
                    "tools": sorted(m.tools),
                    "active_tools": active_tools,
                    "missing_tools": sorted(set(missing_schema + missing_executor))})
    return out


def _tool_name(t: dict) -> str:
    """兼容两种 schema 形态：{'function': {'name': ...}} 或 {'name': ...}。"""
    if isinstance(t.get("function"), dict):
        return str(t["function"].get("name", ""))
    return str(t.get("name", ""))
