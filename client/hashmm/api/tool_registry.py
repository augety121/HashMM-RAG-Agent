"""HashMM-RAG v10.0 — Tool Registry for Agent Loop + Specialized Handlers.

Defines tool schemas (OpenAI function calling format) and executors.
v10.0: Per-conversation file directories, JSON plan PPT builder.
"""
from __future__ import annotations
from hashmm.utils import get_logger, log_suppressed
import json, re, time, os
from pathlib import Path
from typing import Any

logger = get_logger("hashmm.tools.registry")

# ─── File paths ───
# 锚定到 database 的绝对数据根，确保 create_file 存盘目录与下载读取目录一致
# （否则相对路径在 execute_code 改变 cwd 后会错位 → 预览200、下载404）。
from hashmm.api.database import DATA_ROOT as _DATA_ROOT, CONV_FILES_ROOT  # noqa: E402
FILES_DIR = (_DATA_ROOT / "files").resolve()           # Legacy global dir (backward compat)
UPLOAD_DIR = (_DATA_ROOT / "uploads").resolve()
WORKSPACE_DIR = (_DATA_ROOT / "workspace").resolve()

for _d in (FILES_DIR, UPLOAD_DIR, WORKSPACE_DIR, CONV_FILES_ROOT):
    _d.mkdir(parents=True, exist_ok=True)


def get_files_dir(conv_id: str | None = None) -> Path:
    """Get the file directory for a conversation, or the global fallback."""
    if conv_id:
        d = CONV_FILES_ROOT / conv_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    return FILES_DIR


# ═══════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS  (OpenAI / DeepSeek function calling format)
# ═══════════════════════════════════════════════════════════════════

TOOL_DEFS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": (
                "搜索学术知识库，用于回答涉及论文方法、技术概念、数据集、实验结果的问题。"
                "返回相关文献片段。当问题包含学术概念或需要引用依据时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索查询——应是具体的技术术语或概念短语",
                    },
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "可选：复杂问题传 2-3 个查询变体（同义改写/子问题/实体名），"
                            "一次调用并行检索并融合去重（RRF）——比连续多次调用省预算且覆盖更全"
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果条数（默认 5）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": (
                "在安全沙箱中运行 Python 代码并返回 stdout/stderr。"
                "用于验证代码正确性、做数值计算、运行数据处理。15 秒超时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（默认 15）",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "创建一个文件并保存到工作区，用户可下载。"
                "支持代码文件（.py .js .ts 等）、文档（.md）、配置（.yaml .json）等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名（含扩展名，如 main.py、report.md）",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件的完整内容",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取工作区或用户上传目录中的文件内容。"
                "用于理解用户上传的代码/文档，或查看之前创建的文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "文件路径（相对于工作区，或 data/files/ 下的文件名）",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "编辑已有文件中的指定内容。使用精确匹配替换：old_str 必须在文件中唯一出现。"
                "如果不确定文件内容，请先用 read_file 查看。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要编辑的文件路径",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "要被替换的原始字符串（必须精确匹配且唯一）",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "替换后的新字符串",
                    },
                },
                "required": ["filepath", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出工作区和文件目录中的文件。"
                "用于了解用户上传了什么文件、或之前创建了哪些文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（默认为工作区根目录）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索互联网获取最新信息。"
                "用于查找最新论文、技术文档、API 文档、新闻、不在知识库中的信息。"
                "当用户的问题涉及最新进展、特定工具/库的用法、或知识库未覆盖的内容时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询（建议用英文以获得更好的结果）",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "返回结果数（默认 5）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "读取网页或PDF的文本内容。支持 arxiv 论文、PDF 文件链接、普通网页。"
                "当用户提供 URL 链接，或要求读取/分析某个网页内容时使用。"
                "对于 arxiv 论文，会自动提取标题、作者、摘要和全文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要读取的 URL 地址（支持 https://arxiv.org/abs/xxx、PDF链接、普通网页）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_workspace",
            "description": (
                "清理工作区中的旧文件。删除指定文件或批量清理。"
                "当工作区文件过多、或用户要求清理时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "操作类型：delete_file(删除指定文件)、clean_old(清理7天前的文件)、clean_all(清空工作区)",
                        "enum": ["delete_file", "clean_old", "clean_all"],
                    },
                    "filename": {
                        "type": "string",
                        "description": "要删除的文件名（仅 delete_file 时需要）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": (
                "创建格式化文档文件。支持 .docx (Word) 和 .pptx (PowerPoint)。"
                "内容用 Markdown 格式传入，工具自动转换为对应格式。"
                "对于 .docx：支持标题、段落、代码块、列表。"
                "对于 .pptx：每个 ## 标题生成一张幻灯片，内容为要点。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名（如 report.docx 或 slides.pptx）",
                    },
                    "content": {
                        "type": "string",
                        "description": "文档内容（Markdown 格式）",
                    },
                    "title": {
                        "type": "string",
                        "description": "文档标题（可选）",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    # v11: New tools
    {
        "type": "function",
        "function": {
            "name": "create_xlsx",
            "description": "创建 Excel 电子表格(.xlsx)。传入 JSON 数据（含 sheets/headers/rows）或 Markdown 表格文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "JSON 格式的表格数据或 Markdown 表格文本"},
                    "title": {"type": "string", "description": "文件标题（用于文件名）"},
                },
                "required": ["data", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": "创建 PDF 文档。传入 Markdown 格式内容，自动排版为 PDF。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Markdown 格式的文档内容"},
                    "title": {"type": "string", "description": "文档标题"},
                },
                "required": ["content", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_file",
            "description": "文件格式转换。支持 md→docx, csv→xlsx 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源文件名"},
                    "target_format": {"type": "string", "description": "目标格式: docx/xlsx/pdf"},
                },
                "required": ["source", "target_format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": "读取文件的指定行范围（不读全部，适合大文件）。返回带行号的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径（相对于工作区）"},
                    "start_line": {"type": "integer", "description": "起始行号（从1开始）"},
                    "end_line": {"type": "integer", "description": "结束行号"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "精确字符串替换：在文件中查找 old_str 并替换为 new_str（只改匹配的部分，不重写整个文件）。old_str 必须在文件中唯一存在。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径"},
                    "old_str": {"type": "string", "description": "要替换的原始文本（必须精确匹配）"},
                    "new_str": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["filepath", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_lines",
            "description": "在文件指定行后插入新内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径"},
                    "after_line": {"type": "integer", "description": "在第N行后插入"},
                    "content": {"type": "string", "description": "要插入的内容"},
                },
                "required": ["filepath", "after_line", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "在工作区所有文件中搜索文本，返回文件名:行号:内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索文本（不区分大小写）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_tree",
            "description": "获取当前工作区的目录树结构。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "执行 Shell 命令。支持 pip/python/node/gcc/git/ls/cat 等。用于安装依赖、运行脚本、查看文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 Shell 命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 60"},
                },
                "required": ["command"],
            },
        },
    },
    # ── Tools whose executors existed but were never exposed to the LLM
    #    (registered but missing from TOOL_DEFS → the model could never call
    #    them). Surfacing them activates already-built capability. ──
    {
        "type": "function",
        "function": {
            "name": "kg_query",
            "description": (
                "查询知识图谱：给定一个实体，返回它的相关实体与关系（A→关系→B）。"
                "适合回答实体之间的关系、归属、人物任职等结构化问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "要查询的实体名称（公司/人物/产品等）"},
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_versions",
            "description": "列出某个工作区文件的历史版本（用于查看可回滚的版本）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径或文件名"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_restore",
            "description": "把工作区文件回滚到指定的历史版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径或文件名"},
                    "version": {"type": "integer", "description": "要恢复到的版本号（见 file_versions）"},
                },
                "required": ["filepath", "version"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pptx_summary",
            "description": "读取一个 PPTX 文件的结构摘要（每页标题与要点），用于后续编辑前了解内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PPTX 文件名"},
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pptx_edit_slide",
            "description": "编辑 PPTX 指定页的内容（按 slide_index 定位，changes 描述修改）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "PPTX 文件名"},
                    "slide_index": {"type": "integer", "description": "要编辑的页索引（从 0 开始）"},
                    "changes": {"type": "string", "description": "对该页的修改说明或新内容"},
                },
                "required": ["filename", "slide_index", "changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pptx_from_plan",
            "description": "根据结构化大纲(plan)一次性生成完整 PPTX 演示文稿。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string", "description": "演示文稿的结构化大纲（JSON 或分点文本）"},
                },
                "required": ["plan"],
            },
        },
    },
]
# ═══════════════════════════════════════════════════════════════════

# Registry: tool_name → async executor function
_EXECUTORS: dict[str, Any] = {}


def register_executor(name: str, fn):
    """Register a tool executor function."""
    _EXECUTORS[name] = fn


def get_tool_definitions() -> list[dict]:
    """Return all tool definitions (for DeepSeek API `tools` param)."""
    return TOOL_DEFS


def _execute_tool_core(name: str, args: dict, ctx: dict | None = None):
    """执行工具并返回【原始结果】（dict 或 str），过 hooks + 遥测。

    这是 execute_tool / execute_tool_structured 的共同核心：
    - execute_tool 在其上 str() 包装（保持对 LLM 的字符串契约不变）。
    - execute_tool_structured 直接返回原始结果（保留 create_document 等的 file 字段，
      供 Agent Loop 发 "file" 事件）。
    返回 ("denied"/"error" 字符串) 或 executor 的原始返回值。
    """
    import time as _time
    executor = _EXECUTORS.get(name)
    if not executor:
        return f"Error: unknown tool '{name}'"
    _post = None
    try:
        from hashmm.hooks import run_pre_tool_hooks, run_post_tool_hooks
        _post = run_post_tool_hooks
        decision = run_pre_tool_hooks(name, args, ctx or {})
        if not decision.allow:
            return f"Error: 工具调用被安全策略拒绝（{decision.reason}）"
    except Exception as _e:
        log_suppressed(logger, _e)

    # S1-3: 统一安全策略三层收口（Guardian→sandbox→policy）。
    # 默认配置下全放行（与现状完全一致）；仅当显式开启 HASHMM_TOOL_APPROVAL 且高风险工具
    # 未获批准时才拒绝。永不抛错：评估自身异常时放行（不阻断正常工具）。
    try:
        from hashmm import security_policy as _sp
        _ctx = ctx or {}
        _decision = _sp.evaluate(
            name, args,
            user_id=str(_ctx.get("user_id", "")),
            approved=bool(_ctx.get("approved", False)),
        )
        if not _decision.allowed:
            return f"Error: 工具调用被安全策略拒绝（{_decision.layer}: {_decision.reason}）"
    except Exception as _e:
        log_suppressed(logger, _e)
    _t0 = _time.time()
    ok = False
    try:
        result = executor(args, ctx or {})
        ok = True
        return result
    except Exception as e:
        logger.warning(f"tool '{name}' failed: {type(e).__name__}: {e}")
        return f"Error: tool '{name}' failed ({type(e).__name__}). Please try a different approach."
    finally:
        _lat = round((_time.time() - _t0) * 1000)
        try:
            from hashmm import observability as _obs
            _obs.record_tool_call(name, _lat, ok)
        except Exception:  # nosem: observability-fallback
            pass
        try:
            if _post:
                _post(name, args, ctx or {}, ok, _lat)
        except Exception as _e:
            log_suppressed(logger, _e)


def execute_tool_structured(name: str, args: dict, ctx: dict | None = None):
    """像 execute_tool，但返回【原始结果】（保留 dict 的 file 等结构化字段）。
    供 Agent Loop 使用，使 create_document 等的 file 信息能触发 "file" 事件。"""
    return _execute_tool_core(name, args, ctx)


def execute_tool(name: str, args: dict, ctx: dict | None = None) -> str:
    """Run a tool and return a string result for the LLM. Synchronous.

    Hardened: records per-call telemetry (name/latency/success) and sanitises
    error messages so internal details (stack traces, absolute paths) are never
    leaked back into the LLM context. Telemetry is best-effort and never affects
    the result.
    """
    result = _execute_tool_core(name, args, ctx)
    # dict 结果（如 create_file/create_document 的结构化返回）→ 给 LLM 看 message 即可，
    # 不要把整个 dict 转成丑字符串。结构化 file 字段由 execute_tool_structured 路径供 Loop 用。
    if isinstance(result, dict):
        msg = result.get("message")
        if msg:
            return str(msg)
    return str(result)


# ── Built-in executors ──────────────────────────────────────────

def _save_file_version(filepath):
    """v25: Save file version before overwrite (undo support)."""
    from pathlib import Path
    import shutil
    p = Path(filepath)
    if p.exists():
        versions_dir = p.parent / ".versions"
        versions_dir.mkdir(exist_ok=True)
        import time
        ts = int(time.time())
        dest = versions_dir / f"{p.name}.{ts}"
        shutil.copy2(p, dest)
        # Keep max 10 versions per file
        versions = sorted(versions_dir.glob(f"{p.name}.*"), key=lambda x: x.stat().st_mtime)
        for old_v in versions[:-10]:
            old_v.unlink()

def _exec_create_file(args: dict, ctx: dict) -> str:
    # v29: Prevent empty or trivially short file creation
    content = args.get("content", "")
    if not content or not content.strip():
        return "Error: 文件内容为空，请提供有效内容后重试"
    if len(content.strip()) < 5:
        return f"Error: 文件内容过短（{len(content.strip())} 字符），请提供完整内容"
    filename = args.get("filename", "untitled.txt")
    # Sanitize filename — keep Chinese chars, alphanumeric, dots, hyphens
    filename = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', filename)[:80]
    if not filename or filename.startswith('.'):
        return "Error: 非法文件名"
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)
    fpath = fdir / filename
    _save_file_version(fpath)  # v25: version history
    fpath.write_text(content, encoding="utf-8")
    # Build download URL
    if conv_id:
        dl_url = f"/api/conversations/{conv_id}/download/{filename}"
    else:
        dl_url = f"/api/files/{filename}"
    n_lines = len(content.splitlines())
    import time as _time
    _now = _time.time()
    # 返回结构化 dict（含 file 字段），让 Agent Loop 发 "file" 事件、前端显示下载卡片
    # （对齐 create_document）。execute_tool 仍会 str() 包装以保持对 LLM 的字符串契约。
    return {
        "status": "ok",
        "message": f"✅ 文件 {filename} 已创建（{len(content)} 字符，{n_lines} 行）。下载链接: {dl_url}",
        "file": {
            "filename": filename,
            "download_url": dl_url,
            "mtime": _now,            # 生成时间（前端显示用）
            "created_at": _now,
            "lines": n_lines,
            "size": len(content.encode("utf-8")),
        },
    }


def _exec_read_file(args: dict, ctx: dict) -> str:
    filepath = args.get("filepath", "")
    # Search in multiple locations
    candidates = [
        FILES_DIR / filepath,
        UPLOAD_DIR / filepath,
        WORKSPACE_DIR / filepath,
        Path(filepath),
    ]
    resolved = None
    for c in candidates:
        try:
            p = c.resolve()
            # Security: must be under allowed directories
            allowed = [FILES_DIR.resolve(), UPLOAD_DIR.resolve(), WORKSPACE_DIR.resolve()]
            if any(str(p).startswith(str(a)) for a in allowed) and p.exists():
                resolved = p
                break
        except Exception as _e:
            continue

    if not resolved:
        return f"Error: 文件 '{filepath}' 不存在。请用 list_files 查看可用文件。"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as _e:
        return f"Error: 无法读取 '{filepath}'（可能是二进制文件）"

    lines = content.splitlines()
    total_lines = len(lines)
    # Truncate very long files
    if len(content) > 12000:
        content = content[:5000] + "\n\n... (省略中间部分) ...\n\n" + content[-3000:]

    return f"文件: {resolved.name} ({total_lines} 行, {len(content)} 字符)\n```\n{content}\n```"


def _exec_edit_file(args: dict, ctx: dict) -> str:
    filepath = args.get("filepath", "")
    old_str = args.get("old_str", "")
    new_str = args.get("new_str", "")

    candidates = [FILES_DIR / filepath, WORKSPACE_DIR / filepath]
    resolved = None
    for c in candidates:
        p = c.resolve()
        allowed = [FILES_DIR.resolve(), WORKSPACE_DIR.resolve()]
        if any(str(p).startswith(str(a)) for a in allowed) and p.exists():
            resolved = p
            break

    if not resolved:
        return f"Error: 文件 '{filepath}' 不存在。"

    content = resolved.read_text(encoding="utf-8")
    count = content.count(old_str)
    if count == 0:
        return f"Error: old_str 在文件中未找到。请先用 read_file 确认内容。"
    if count > 1:
        return f"Error: old_str 在文件中出现了 {count} 次，必须唯一匹配。请提供更多上下文。"

    new_content = content.replace(old_str, new_str, 1)
    resolved.write_text(new_content, encoding="utf-8")
    return f"OK: {resolved.name} 已更新（替换 {len(old_str)} → {len(new_str)} 字符）"


def _exec_list_files(args: dict, ctx: dict) -> str:
    lines = []
    total = 0
    for label, d in [("📁 已创建文件", FILES_DIR), ("📁 用户上传", UPLOAD_DIR)]:
        if d.exists():
            items = sorted(d.iterdir())
            files = [f for f in items if f.is_file()]
            if files:
                lines.append(f"{label} [{len(files)} 个]")
                for f in files[:50]:
                    sz = f.stat().st_size
                    sz_str = f"{sz/1024:.1f}K" if sz > 1024 else f"{sz}B"
                    lines.append(f"  {f.name} ({sz_str})")
                    total += 1
                lines.append("")
    if not lines:
        return "工作区为空。用户可上传文件，或用 create_file 创建。"
    return f"共 {total} 个文件:\n" + "\n".join(lines)


def _auto_install_missing(code: str):
    """v11: Auto pip install missing packages before code execution.

    Scans import statements, checks if packages are installed,
    installs missing ones (with allowlist for safety).
    """
    import ast as _ast, importlib

    ALLOWED_PACKAGES = {
        "numpy", "pandas", "matplotlib", "seaborn", "plotly", "scipy",
        "sklearn", "scikit-learn", "openpyxl", "xlsxwriter", "pillow",
        "requests", "beautifulsoup4", "lxml", "jieba", "wordcloud",
        "networkx", "sympy", "tabulate", "pyyaml", "tqdm",
    }
    # Map import name → pip package name
    IMPORT_TO_PIP = {
        "sklearn": "scikit-learn", "cv2": "opencv-python",
        "PIL": "pillow", "yaml": "pyyaml", "bs4": "beautifulsoup4",
    }

    needed = set()
    try:
        tree = _ast.parse(code)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    needed.add(pkg)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                pkg = node.module.split(".")[0]
                needed.add(pkg)
    except Exception:
        return

    for pkg in needed:
        pip_name = IMPORT_TO_PIP.get(pkg, pkg)
        if pip_name.lower() not in ALLOWED_PACKAGES:
            continue
        try:
            importlib.import_module(pkg)
        except ImportError:
            try:
                import subprocess, sys
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet",
                     "--break-system-packages", pip_name],
                    capture_output=True, timeout=60,
                )
                logger.info(f"[AutoInstall] Installed {pip_name}")
            except Exception as e:
                logger.warning(f"[AutoInstall] Failed to install {pip_name}: {e}")


def _exec_execute_code(args: dict, ctx: dict) -> str:
    """v30: Hardened code execution sandbox."""
    import subprocess, tempfile, ast as _ast
    code = args.get("code", "")
    timeout = min(args.get("timeout", 15), 30)

    if not code.strip():
        return "Error: 代码为空"

    # v30: AST-based static analysis
    BLOCKED_CALLS = {"os.system", "os.popen", "subprocess.run", "subprocess.call",
                     "subprocess.Popen", "shutil.rmtree", "eval", "exec",
                     "__import__", "importlib.import_module", "compile"}
    BLOCKED_IMPORTS = {"socket", "http.server", "http.client", "ftplib", "smtplib",
                       "ctypes", "webbrowser", "xmlrpc", "telnetlib", "imaplib",
                       "poplib", "nntplib"}
    # v10.0: Block dangerous file paths
    BLOCKED_PATHS = {"/etc/", "/proc/", "/sys/", "/root/", "/home/", "../", "~/.ssh"}
    try:
        tree = _ast.parse(code)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in BLOCKED_IMPORTS:
                        return f"Error: 安全限制——禁止 import {alias.name}"
            elif isinstance(node, _ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in BLOCKED_IMPORTS:
                    return f"Error: 安全限制——禁止 from {node.module} import"
            # v10.0: Block __import__ calls
            elif isinstance(node, _ast.Call):
                func = node.func
                if isinstance(func, _ast.Name) and func.id in ("__import__", "compile", "exec", "eval"):
                    return f"Error: 安全限制——禁止调用 {func.id}()"
    except SyntaxError:
        pass  # Let execution handle syntax errors

    # v10.0: String-level path escape detection
    for bp in BLOCKED_PATHS:
        if bp in code:
            return f"Error: 安全限制——禁止访问系统路径 {bp}"

    # v30: Auto-inject missing imports
    from hashmm.api.error_recovery import inject_auto_imports
    code = inject_auto_imports(code)

    # v12: Auto-save matplotlib figures if plt.show() is used but no savefig
    if "matplotlib" in code or "plt." in code:
        if "savefig" not in code:
            code += "\n\n# Auto-save chart\ntry:\n    import matplotlib.pyplot as _plt\n    if _plt.get_fignums():\n        _plt.savefig('chart_output.png', dpi=150, bbox_inches='tight')\n        print('[Auto-saved chart to chart_output.png]')\nexcept: pass\n"
        # Replace plt.show() with pass (non-interactive backend)
        code = code.replace("plt.show()", "pass  # plt.show() disabled in headless mode")

    # v11: Auto pip install for missing packages
    _auto_install_missing(code)

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            tmp_path = f.name

        import sys
        conv_id = ctx.get("session_id") or ctx.get("conv_id")
        cwd = str(get_files_dir(conv_id).resolve()) if conv_id else str(FILES_DIR.resolve())

        # v30: Resource limits (Linux only)
        def _set_limits():
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            except Exception as _e:
                log_suppressed(logger, _e)

        # Track files before execution
        import time as _time
        t_start = _time.time()

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd,
            env={**dict(os.environ), "PYTHONDONTWRITEBYTECODE": "1",
                 "PYTHONPATH": cwd, "MPLBACKEND": "Agg"},
            preexec_fn=_set_limits,
        )

        output_parts = []
        # V50: 显式退出码 + stderr 取【尾部】。Python traceback 的关键信息
        # （异常类型/出错行号）在 stderr 尾部，原先的截头 2000 字在长输出下恰好把它截掉，
        # 导致模型反复执行也定位不到错误（真机已观测的故障模式之一）。
        output_parts.append(f"[exit_code={result.returncode}]")
        if result.stdout:
            output_parts.append(result.stdout[:4000])
        if result.stderr and result.returncode != 0:
            output_parts.append("[STDERR 尾部]\n" + result.stderr[-2000:])

        # v30: Detect generated files
        try:
            cwd_path = Path(cwd)
            new_files = [f.name for f in cwd_path.iterdir()
                        if f.is_file() and f.stat().st_mtime > t_start
                        and f.name != Path(tmp_path).name]
            if new_files:
                output_parts.append(f"[生成文件] {', '.join(new_files[:10])}")
                # v12: Copy images to FILES_DIR for download + inline display
                for nf in new_files[:10]:
                    src = cwd_path / nf
                    if nf.endswith(('.png', '.jpg', '.jpeg', '.svg', '.pdf', '.html')):
                        try:
                            import shutil
                            dst = Path(FILES_DIR) / nf
                            shutil.copy2(str(src), str(dst))
                            output_parts.append(f"CHART_FILE:/api/files/download/{nf}")
                        except Exception:
                            output_parts.append(f"CHART_SAVED:{cwd}/{nf}")
                    elif nf.endswith(('.csv', '.xlsx', '.json', '.txt', '.md')):
                        try:
                            import shutil
                            dst = Path(FILES_DIR) / nf
                            shutil.copy2(str(src), str(dst))
                            output_parts.append(f"FILE_SAVED:/api/files/download/{nf}")
                        except Exception as _e:
                            log_suppressed(logger, _e)
        except Exception as _e:
            log_suppressed(logger, _e)

        Path(tmp_path).unlink(missing_ok=True)
        output = "\n".join(output_parts)

        if result.returncode == 0:
            return f"✅ 执行成功\n{output.strip() or '(无输出)'}"
        else:
            return (
                f"❌ 执行失败 (exit code {result.returncode})\n{output.strip()}\n\n"
                f"请分析错误原因并修复代码，然后再次用 execute_code 验证。"
            )
    except subprocess.TimeoutExpired:
        try: Path(tmp_path).unlink(missing_ok=True)
        except Exception: pass
        return f"❌ 执行超时（{timeout}s 限制）"
    except Exception as e:
        return f"❌ 执行异常: {repr(e)}"


# ── kb_search is registered externally by server.py (needs access to FAISS index) ──

def _exec_create_document(args: dict, ctx: dict) -> str:
    """Create formatted .docx or .pptx with professional styling."""
    filename = args.get("filename", "document.md")
    content = args.get("content", "")
    title = args.get("title", "")
    filename = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', filename)[:80]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "md"
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)

    if ext == "docx":
        return _create_docx(filename, content, title, fdir, conv_id)
    elif ext == "pptx":
        return _create_pptx(filename, content, title, fdir, conv_id)
    else:
        fpath = fdir / filename
        md = f"# {title}\n\n{content}" if title else content
        fpath.write_text(md, encoding="utf-8")
        dl = f"/api/conversations/{conv_id}/download/{filename}" if conv_id else f"/api/files/{filename}"
        return f"OK: 文件 {filename} 已创建。下载链接: {dl}"


def _exec_create_pptx_from_plan(args: dict, ctx: dict) -> str:
    """Create PPT from structured JSON plan (v10.0 DocumentHandler)."""
    from hashmm.api.pptx_builder import build_pptx_from_plan, build_pptx_from_markdown
    plan = args.get("plan", {})
    conv_id = args.get("conv_id") or ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)
    title = plan.get("title", "presentation")
    fname = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', title)[:40] + ".pptx"
    fpath = fdir / fname
    result = build_pptx_from_plan(plan, fpath)
    return result


def _create_docx(filename, content, title, fdir=None, conv_id=None):
    """Create a professional Word document using docx_builder."""
    if fdir is None:
        fdir = FILES_DIR
    from hashmm.api.docx_builder import build_docx
    fpath = fdir / filename
    result = build_docx(content, title, fpath)
    if conv_id:
        result = result.replace("/api/files/", f"/api/conversations/{conv_id}/files/")
    return result


def _create_pptx(filename, content, title, fdir=None, conv_id=None):
    """Create a professional PowerPoint presentation.

    Design: dark title slides, white content slides, blue accent bar,
    one idea per slide, proper typography and spacing.
    """
    try:
        if fdir is None: fdir = FILES_DIR
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        C_DARK = RGBColor(0x1A, 0x1A, 0x2E)
        C_ACCENT = RGBColor(0x00, 0x78, 0xD4)
        C_TITLE = RGBColor(0x1A, 0x1A, 0x2E)
        C_BODY = RGBColor(0x33, 0x33, 0x33)
        C_SUBTLE = RGBColor(0x99, 0x99, 0x99)

        def _bg(slide, color=RGBColor(0xFF, 0xFF, 0xFF)):
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = color

        def _title_slide(t, sub=""):
            s = prs.slides.add_slide(prs.slide_layouts[6])
            _bg(s, C_DARK)
            bar = s.shapes.add_shape(1, Inches(0), Inches(3.2), Inches(13.333), Inches(0.06))
            bar.fill.solid(); bar.fill.fore_color.rgb = C_ACCENT; bar.line.fill.background()
            tx = s.shapes.add_textbox(Inches(1.5), Inches(1.8), Inches(10), Inches(1.5))
            p = tx.text_frame.paragraphs[0]
            p.text = t; p.font.size = Pt(40); p.font.bold = True
            p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF); p.alignment = PP_ALIGN.LEFT
            if sub:
                p2 = tx.text_frame.add_paragraph()
                p2.text = sub; p2.font.size = Pt(18)
                p2.font.color.rgb = RGBColor(0xBB, 0xBB, 0xCC); p2.space_before = Pt(16)

        def _section_slide(t):
            s = prs.slides.add_slide(prs.slide_layouts[6])
            _bg(s, RGBColor(0xF0, 0xF4, 0xF8))
            tx = s.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10), Inches(2))
            p = tx.text_frame.paragraphs[0]
            p.text = t; p.font.size = Pt(36); p.font.bold = True
            p.font.color.rgb = C_ACCENT; p.alignment = PP_ALIGN.LEFT
            bar = s.shapes.add_shape(1, Inches(1.5), Inches(4.2), Inches(3), Inches(0.05))
            bar.fill.solid(); bar.fill.fore_color.rgb = C_ACCENT; bar.line.fill.background()

        def _content_slide(heading, bullets):
            s = prs.slides.add_slide(prs.slide_layouts[6])
            _bg(s)
            bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.1), Inches(7.5))
            bar.fill.solid(); bar.fill.fore_color.rgb = C_ACCENT; bar.line.fill.background()
            # Title
            tx = s.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.5), Inches(0.9))
            p = tx.text_frame.paragraphs[0]
            p.text = heading; p.font.size = Pt(26); p.font.bold = True; p.font.color.rgb = C_TITLE
            # Body
            body = s.shapes.add_textbox(Inches(0.8), Inches(1.6), Inches(11.5), Inches(5.2))
            tf = body.text_frame; tf.word_wrap = True
            for i, b in enumerate(bullets[:12]):
                b = re.sub(r'^[\-\*•]\s*', '', b).strip()
                if not b: continue
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                bm = re.match(r'\*\*(.+?)\*\*(.*)', b)
                if bm:
                    r1 = p.add_run(); r1.text = "● " + bm.group(1)
                    r1.font.bold = True; r1.font.size = Pt(16); r1.font.color.rgb = C_TITLE
                    if bm.group(2).strip():
                        r2 = p.add_run(); r2.text = bm.group(2)
                        r2.font.size = Pt(16); r2.font.color.rgb = C_BODY
                else:
                    r = p.add_run(); r.text = "● " + b
                    r.font.size = Pt(16); r.font.color.rgb = C_BODY
                p.space_after = Pt(6); p.space_before = Pt(3)
            # Page number
            pn = s.shapes.add_textbox(Inches(12.5), Inches(7), Inches(0.7), Inches(0.3))
            pn.text_frame.paragraphs[0].text = str(len(prs.slides))
            pn.text_frame.paragraphs[0].font.size = Pt(10)
            pn.text_frame.paragraphs[0].font.color.rgb = C_SUBTLE
            pn.text_frame.paragraphs[0].alignment = PP_ALIGN.RIGHT

        # Build slides from markdown
        _title_slide(title or "演示文稿")
        sections = re.split(r'^#\s+', content, flags=re.MULTILINE)
        for sec in sections:
            if not sec.strip(): continue
            lines = sec.strip().split("\n")
            sec_title = lines[0].strip()
            rest = "\n".join(lines[1:])
            subsections = re.split(r'^##\s+', rest, flags=re.MULTILINE)
            has_content = any(s.strip() for s in subsections)
            if has_content:
                _section_slide(sec_title)
            for sub in subsections:
                if not sub.strip(): continue
                sub_lines = sub.strip().split("\n")
                heading = sub_lines[0].strip()
                body = [l for l in sub_lines[1:] if l.strip() and not l.strip().startswith("```")]
                if body:
                    _content_slide(heading, body)

        # End slide
        end = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(end, C_DARK)
        tx = end.shapes.add_textbox(Inches(3), Inches(2.5), Inches(7), Inches(2))
        p = tx.text_frame.paragraphs[0]
        p.text = "Thank You"; p.font.size = Pt(44); p.font.bold = True
        p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF); p.alignment = PP_ALIGN.CENTER

        fpath = fdir / filename
        prs.save(str(fpath))
        return f"OK: PPT {filename} 已创建（{len(prs.slides)} 页）。下载链接: /api/files/{filename}"
    except ImportError:
        filename = filename.rsplit(".", 1)[0] + ".md"
        fpath = fdir / filename
        fpath.write_text(f"# {title}\n\n{content}" if title else content, encoding="utf-8")
        return f"OK: python-pptx 未安装，已创建 Markdown: {filename}。下载链接: /api/files/{filename}"


_web_cache: dict[str, tuple[float, str]] = {}
_WEB_CACHE_TTL = 300  # 5 minutes

def _format_web_results(results: list, query: str) -> str:
    """Uniform formatting for results from any backend.

    results: [{"title":..., "url":..., "snippet":...}, ...]
    """
    if not results:
        return f"未找到与 '{query}' 相关的结果。请尝试换个关键词。"
    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        snippet = (r.get("snippet", "") or "")[:300]
        parts.append(f"[{i}] {title}\n    URL: {url}\n    {snippet}")
    return f"找到 {len(results)} 条结果：\n\n" + "\n\n".join(parts)


def _search_serper(query: str, num: int) -> list:
    """Serper.dev (Google results via API). Works in China containers.
    Set HASHMM_SERPER_API_KEY."""
    import os
    from hashmm.api.settings_store import get_setting
    key = get_setting("serper_api_key")
    if not key:
        return None  # not configured
    import json as _json
    import urllib.request
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=_json.dumps({"q": query, "num": num, "hl": "zh-cn"}).encode(),
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    out = []
    for r in (data.get("organic") or [])[:num]:
        out.append({"title": r.get("title", ""), "url": r.get("link", ""),
                    "snippet": r.get("snippet", "")})
    # answerBox / knowledgeGraph as a bonus top result
    if data.get("answerBox"):
        ab = data["answerBox"]
        out.insert(0, {"title": ab.get("title", "直接答案"),
                       "url": ab.get("link", ""),
                       "snippet": ab.get("answer") or ab.get("snippet", "")})
    return out


def _search_bing(query: str, num: int) -> list:
    """Bing Web Search API. Set HASHMM_BING_API_KEY."""
    import os
    from hashmm.api.settings_store import get_setting
    key = get_setting("bing_api_key")
    if not key:
        return None
    import json as _json
    import urllib.request
    import urllib.parse
    url = "https://api.bing.microsoft.com/v7.0/search?q=" + urllib.parse.quote(query) + f"&count={num}&mkt=zh-CN"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    out = []
    for r in (data.get("webPages", {}).get("value") or [])[:num]:
        out.append({"title": r.get("name", ""), "url": r.get("url", ""),
                    "snippet": r.get("snippet", "")})
    return out


def _search_tavily(query: str, num: int) -> list:
    """Tavily AI search API (LLM-optimized). Set HASHMM_TAVILY_API_KEY."""
    import os
    from hashmm.api.settings_store import get_setting
    key = get_setting("tavily_api_key")
    if not key:
        return None
    import json as _json
    import urllib.request
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=_json.dumps({"api_key": key, "query": query,
                          "max_results": num, "search_depth": "basic"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    out = []
    if data.get("answer"):
        out.append({"title": "Tavily 综合答案", "url": "", "snippet": data["answer"]})
    for r in (data.get("results") or [])[:num]:
        out.append({"title": r.get("title", ""), "url": r.get("url", ""),
                    "snippet": r.get("content", "")})
    return out


def _search_duckduckgo(query: str, num: int) -> list:
    """DuckDuckGo (no key, but often blocked/rate-limited in CN containers)."""
    try:
        from duckduckgo_search import DDGS
        out = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=num):
                out.append({"title": r.get("title", ""), "url": r.get("href", ""),
                            "snippet": r.get("body", "")})
        return out
    except ImportError:
        # last-ditch: scrape html endpoint
        import urllib.request, urllib.parse
        import html as html_mod
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page)
        snippets = re.findall(r'class="result__snippet">(.*?)</td>', page, re.DOTALL)
        out = []
        for t, s in zip(titles[:num], snippets[:num]):
            out.append({
                "title": html_mod.unescape(re.sub(r"<[^>]+>", "", t)),
                "url": "",
                "snippet": html_mod.unescape(re.sub(r"<[^>]+>", "", s)).strip(),
            })
        return out


# Backend priority: API-key backends first (reliable in CN containers),
# DuckDuckGo last (free but often blocked). Configure via HASHMM_SEARCH_BACKEND
# to force one, otherwise auto-tries in order.
_SEARCH_BACKENDS = {
    "serper": _search_serper,
    "bing": _search_bing,
    "tavily": _search_tavily,
    "duckduckgo": _search_duckduckgo,
}


def _exec_web_search(args: dict, ctx: dict) -> str:
    """v17 Phase 18d: multi-backend web search.

    Tries configured backends in order. API-key backends (Serper/Bing/Tavily)
    work reliably from China-based containers where DuckDuckGo is blocked.
    Set ONE of HASHMM_SERPER_API_KEY / HASHMM_BING_API_KEY / HASHMM_TAVILY_API_KEY,
    or HASHMM_SEARCH_BACKEND to force a specific one.
    """
    import os
    import time as _time
    query = args.get("query", "")
    num = min(args.get("num_results", 5), 8)
    if not query.strip():
        return "Error: 搜索查询为空"

    cache_key = f"{query}:{num}"
    if cache_key in _web_cache:
        ts, cached = _web_cache[cache_key]
        if _time.time() - ts < _WEB_CACHE_TTL:
            return cached

    # Determine backend order
    from hashmm.api.settings_store import get_setting
    forced = get_setting("search_backend").strip().lower()
    if forced and forced in _SEARCH_BACKENDS:
        order = [forced]
    else:
        order = ["serper", "bing", "tavily", "duckduckgo"]

    errors = []
    for backend_name in order:
        fn = _SEARCH_BACKENDS[backend_name]
        try:
            results = fn(query, num)
            if results is None:
                continue  # backend not configured (no key)
            if results:
                output = _format_web_results(results, query)
                output = f"[来源: {backend_name}]\n" + output
                _web_cache[cache_key] = (_time.time(), output)
                cutoff = _time.time() - _WEB_CACHE_TTL * 2
                for k in list(_web_cache.keys()):
                    if _web_cache[k][0] < cutoff:
                        del _web_cache[k]
                return output
        except Exception as e:
            errors.append(f"{backend_name}: {repr(e)[:80]}")
            continue

    # Nothing worked
    if errors:
        return ("搜索暂不可用：所有联网后端都失败或未配置。"
                "请设置 HASHMM_SERPER_API_KEY（推荐，国内可用）或 HASHMM_TAVILY_API_KEY。"
                f" 详情: {'; '.join(errors[:3])}")
    return ("搜索暂不可用：未配置任何联网搜索后端，且 DuckDuckGo 不可用。"
            "请设置 HASHMM_SERPER_API_KEY（serper.dev，国内容器可用）"
            "或 HASHMM_TAVILY_API_KEY，然后重启。")


def _exec_clean_workspace(args: dict, ctx: dict) -> str:
    """Clean up workspace files."""
    action = args.get("action", "")
    if action == "delete_file":
        fname = args.get("filename", "")
        if not fname:
            return "Error: 需要指定 filename"
        fpath = FILES_DIR / fname
        if fpath.exists():
            fpath.unlink()
            return f"OK: 已删除 {fname}"
        return f"Error: 文件 {fname} 不存在"
    elif action == "clean_old":
        import time as _time
        cutoff = _time.time() - 7 * 86400
        deleted = []
        for f in FILES_DIR.iterdir():
            if f.is_file() and f.name != "PROJECT.md" and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted.append(f.name)
        return f"OK: 已清理 {len(deleted)} 个 7 天前的文件" + (f": {', '.join(deleted[:10])}" if deleted else "")
    elif action == "clean_all":
        deleted = []
        for f in FILES_DIR.iterdir():
            if f.is_file() and f.name != "PROJECT.md":
                f.unlink()
                deleted.append(f.name)
        return f"OK: 已清空工作区（{len(deleted)} 个文件），仅保留 PROJECT.md"
    return f"Error: 未知操作 {action}"


# Register built-in executors
def _exec_create_xlsx(args: dict, ctx: dict) -> str:
    """Create Excel spreadsheet from JSON data or Markdown tables."""
    from hashmm.api.xlsx_builder import build_xlsx
    data = args.get("data", args.get("content", ""))
    title = args.get("title", "spreadsheet")
    fname = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', title)[:40] + ".xlsx"
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)
    fpath = fdir / fname
    # Try JSON parse
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError) as _e:
            log_suppressed(logger, _e)
    result = build_xlsx(data, fpath)
    if conv_id:
        result = result.replace("/api/files/", f"/api/conversations/{conv_id}/files/")
    return result


def _exec_create_pdf(args: dict, ctx: dict) -> str:
    """Create PDF from Markdown content."""
    content = args.get("content", "")
    title = args.get("title", "document")
    fname = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', title)[:40] + ".pdf"
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)
    fpath = fdir / fname
    try:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        # Try to add CJK font
        try:
            pdf.add_font('NotoSC', '', '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', uni=True)
            pdf.set_font('NotoSC', size=11)
        except Exception as _e:
            pdf.set_font('Helvetica', size=11)
        if title:
            pdf.set_font_size(18)
            pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT", align='C')
            pdf.ln(5)
            pdf.set_font_size(11)
        for line in content.split("\n"):
            if line.startswith("# "):
                pdf.set_font_size(16)
                pdf.cell(0, 10, line[2:], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font_size(11)
            elif line.startswith("## "):
                pdf.set_font_size(14)
                pdf.cell(0, 9, line[3:], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font_size(11)
            elif line.strip():
                pdf.multi_cell(0, 6, line)
            else:
                pdf.ln(3)
        fdir.mkdir(parents=True, exist_ok=True)
        pdf.output(str(fpath))
        dl = f"/api/conversations/{conv_id}/download/{fname}" if conv_id else f"/api/files/{fname}"
        return f"OK: PDF {fname} 已创建。下载链接: {dl}"
    except ImportError:
        # Fallback to .md
        fname = fname.rsplit(".", 1)[0] + ".md"
        fpath = fdir / fname
        fpath.write_text(f"# {title}\n\n{content}", encoding="utf-8")
        dl = f"/api/conversations/{conv_id}/download/{fname}" if conv_id else f"/api/files/{fname}"
        return f"OK: fpdf2 未安装，已创建 Markdown: {fname}。下载链接: {dl}"


def _exec_convert_file(args: dict, ctx: dict) -> str:
    """Convert file format: md→docx, csv→xlsx, etc."""
    source = args.get("source", "")
    target_format = args.get("target_format", "")
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fdir = get_files_dir(conv_id)
    
    # Find source file
    src_path = fdir / source if (fdir / source).exists() else FILES_DIR / source
    if not src_path.exists():
        return f"Error: 源文件 {source} 不存在"
    
    content = src_path.read_text(encoding="utf-8", errors="replace")
    base_name = source.rsplit(".", 1)[0]
    
    if target_format in ("docx", "word"):
        from hashmm.api.docx_builder import build_docx
        out = fdir / f"{base_name}.docx"
        return build_docx(content, base_name, out)
    elif target_format in ("xlsx", "excel"):
        from hashmm.api.xlsx_builder import build_xlsx
        out = fdir / f"{base_name}.xlsx"
        return build_xlsx(content, out)
    elif target_format in ("pdf",):
        return _exec_create_pdf({"content": content, "title": base_name}, ctx)
    else:
        return f"Error: 不支持转换为 {target_format} 格式"


# ═══════════════════════════════════════════════════════════════════
# Tool registration
# ═══════════════════════════════════════════════════════════════════


def _exec_render_design(args: dict, ctx: dict) -> str:
    """渲染设计：HTML 字符串 → PNG 截图（需 HASHMM_DESIGN_RENDER=1 + playwright）。
    默认关/工具缺失优雅降级/永不抛错。这是 huashu-design 的 HTML→图片导出能力。"""
    try:
        from hashmm.api import design_render as DR
        html = args.get("html", "") or args.get("content", "")
        if not html.strip():
            return "Error: 需要提供 html 内容"
        r = DR.html_to_image(html,
                             width=int(args.get("width", 1200) or 1200),
                             height=int(args.get("height", 900) or 900),
                             out_name=str(args.get("name", "")))
        if r.get("ok"):
            return f"已渲染设计截图：{r['image_path']}"
        return f"渲染未执行：{r.get('reason', '未知')}"
    except Exception as e:
        return f"渲染失败：{type(e).__name__}"


register_executor("create_file", _exec_create_file)
register_executor("render_design", _exec_render_design)
register_executor("read_file", _exec_read_file)
register_executor("edit_file", _exec_edit_file)
register_executor("list_files", _exec_list_files)
register_executor("execute_code", _exec_execute_code)
# NOTE: create_document is registered once at the bottom of this file
# (_exec_create_doc_v13). The legacy _exec_create_document above is kept only
# because its _create_docx/_create_pptx helpers are reused; it is NOT registered.
register_executor("create_pptx_from_plan", _exec_create_pptx_from_plan)
register_executor("create_xlsx", _exec_create_xlsx)
register_executor("create_pdf", _exec_create_pdf)
register_executor("convert_file", _exec_convert_file)
register_executor("web_search", _exec_web_search)
register_executor("clean_workspace", _exec_clean_workspace)

# v12: fetch_url tool — read webpages, arxiv papers, PDFs
def _exec_fetch_url(args: dict, ctx: dict) -> str:
    from hashmm.tools.fetch_url import execute as _fetch_execute
    return _fetch_execute(args, ctx)
register_executor("fetch_url", _exec_fetch_url)


# ═══════════════════════════════════════════════════════════════════
# v11.3: Shell Execution (with security whitelist)
# ═══════════════════════════════════════════════════════════════════

_SHELL_WHITELIST = {
    "pip", "pip3", "python", "python3", "node", "npm", "npx",
    "gcc", "g++", "javac", "java", "go", "rustc", "cargo",
    "ls", "cat", "head", "tail", "wc", "grep", "find", "file",
    "mkdir", "cp", "mv", "pwd", "echo", "which", "whoami",
    "git", "curl", "wget", "cd", "touch", "chmod",
}

_SHELL_BLOCKED = {
    "rm -rf /", "rm -rf /*", "dd if=", "mkfs", ":(){ :|:& };:",
    "chmod 777 /", "shutdown", "reboot", "init 0", "halt",
    "> /dev/sda", "fork bomb",
}


def _exec_shell(args: dict, ctx: dict) -> str:
    """Execute shell command with security restrictions."""
    import subprocess
    cmd = args.get("command", "").strip()
    timeout = min(args.get("timeout", 30), 60)

    if not cmd:
        return "Error: 命令为空"

    # Security: blocked patterns
    for blocked in _SHELL_BLOCKED:
        if blocked in cmd.lower():
            return f"Error: 危险命令被拦截 ({blocked})"

    # Security: whitelist first command
    first_word = cmd.split()[0].split("/")[-1] if cmd.split() else ""
    if first_word not in _SHELL_WHITELIST:
        return f"Error: 命令 '{first_word}' 不在白名单中。允许的命令: {', '.join(sorted(_SHELL_WHITELIST))}"

    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    cwd = str(get_files_dir(conv_id)) if conv_id else "."

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        output = result.stdout[-3000:] if result.stdout else ""
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
            if result.stderr:
                output += f"\n{result.stderr[-1000:]}"
        return output.strip() or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"Error: 命令超时（{timeout}s）"
    except Exception as e:
        return f"Error: {repr(e)[:200]}"


register_executor("run_shell", _exec_shell)


# ═══════════════════════════════════════════════════════════════════
# v13: Workspace tools (CC-level file operations)
# ═══════════════════════════════════════════════════════════════════

def _exec_read_file_range(args: dict, ctx: dict) -> str:
    from hashmm.api.workspace import read_file_range
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    return read_file_range(conv_id, args["filepath"],
                           args.get("start_line", 1), args.get("end_line", 100))

def _exec_str_replace(args: dict, ctx: dict) -> str:
    from hashmm.api.workspace import str_replace_in_file
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    return str_replace_in_file(conv_id, args["filepath"], args["old_str"], args["new_str"])

def _exec_insert_lines(args: dict, ctx: dict) -> str:
    from hashmm.api.workspace import insert_after_line
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    return insert_after_line(conv_id, args["filepath"], args["after_line"], args["content"])

def _exec_search_files(args: dict, ctx: dict) -> str:
    from hashmm.api.workspace import search_in_workspace
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    return search_in_workspace(conv_id, args["pattern"])

def _exec_file_tree(args: dict, ctx: dict) -> str:
    from hashmm.api.workspace import file_tree
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    return file_tree(conv_id)

register_executor("read_file_range", _exec_read_file_range)
register_executor("str_replace", _exec_str_replace)
register_executor("insert_lines", _exec_insert_lines)
register_executor("search_files", _exec_search_files)
register_executor("file_tree", _exec_file_tree)


# ═══════════════════════════════════════════════════════════════════
# v25: File version management tools
# ═══════════════════════════════════════════════════════════════════

def _exec_file_versions(args: dict, ctx: dict) -> str:
    """List version history of a file."""
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    filepath = args.get("filepath", "")
    fdir = get_files_dir(conv_id)
    versions_dir = fdir / ".versions"
    if not versions_dir.exists():
        return "没有版本历史"
    
    versions = sorted(versions_dir.glob(f"{filepath}.*"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not versions:
        return f"文件 {filepath} 没有版本历史"
    
    import time
    lines = [f"文件 {filepath} 的版本历史（{len(versions)} 个）："]
    for v in versions[:10]:
        ts = v.suffix[1:]  # timestamp
        try:
            dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
        except ValueError:
            dt = ts
        lines.append(f"  {dt} ({v.stat().st_size}B)")
    return "\n".join(lines)

def _exec_file_restore(args: dict, ctx: dict) -> str:
    """Restore a file to a previous version."""
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    filepath = args.get("filepath", "")
    version_index = args.get("version", 0)  # 0 = latest backup
    
    fdir = get_files_dir(conv_id)
    versions_dir = fdir / ".versions"
    versions = sorted(versions_dir.glob(f"{filepath}.*"), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not versions or version_index >= len(versions):
        return f"Error: 版本 {version_index} 不存在"
    
    import shutil
    target = fdir / filepath
    source = versions[version_index]
    _save_file_version(target)  # Save current before restore
    shutil.copy2(source, target)
    return f"OK: 已恢复 {filepath} 到上一个版本"

register_executor("file_versions", _exec_file_versions)
register_executor("file_restore", _exec_file_restore)


# ═══════════════════════════════════════════════════════════════════
# v25: PPT editing tools
# ═══════════════════════════════════════════════════════════════════

def _exec_pptx_summary(args: dict, ctx: dict) -> str:
    from hashmm.api.pptx_editor import get_pptx_summary
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fname = args.get("filename", "")
    fpath = str(get_files_dir(conv_id) / fname)
    return get_pptx_summary(fpath)

def _exec_pptx_edit(args: dict, ctx: dict) -> str:
    from hashmm.api.pptx_editor import edit_pptx_slide
    conv_id = ctx.get("session_id") or ctx.get("conv_id")
    fname = args.get("filename", "")
    fpath = str(get_files_dir(conv_id) / fname)
    return edit_pptx_slide(fpath, args.get("slide_index", 0), args.get("changes", {}))

register_executor("pptx_summary", _exec_pptx_summary)
register_executor("pptx_edit_slide", _exec_pptx_edit)


# ═══════════════════════════════════════════════════════════════════
# v13: Agent Loop support — kb_search executor + executor map
# ═══════════════════════════════════════════════════════════════════

def _rrf_fuse(result_lists: list, top_k: int = 5, k: int = 60) -> list:
    """V74: Reciprocal Rank Fusion——多查询结果融合去重（RAG-Fusion 轻量版）。

    每个结果按其在各查询结果里的排名累加 1/(k+rank+1)；相同片段
    （filename+page+文本前 80 字签名）合并计分。纯函数可单测。
    """
    scores: dict = {}
    meta: dict = {}
    for results in result_lists:
        for rank, src in enumerate(results or []):
            sig = (str(src.get("filename", "")), str(src.get("page", "")),
                   str(src.get("text", ""))[:80])
            scores[sig] = scores.get(sig, 0.0) + 1.0 / (k + rank + 1)
            if sig not in meta:
                meta[sig] = src
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [dict(meta[sig], _rrf=round(sc, 4)) for sig, sc in ranked[:top_k]]


def _exec_kb_search(args: dict, ctx: dict) -> str:
    """Knowledge base search — wraps the retrieval pipeline for Agent Loop.

    V74: 支持 queries=[多个查询变体]——一次调用并行检索 + RRF 融合去重
    （RAG-Fusion 轻量版；3 个查询仍只算 1 次 search call，省预算）。
    """
    query = args.get("query", "")
    mode = args.get("mode", "mix")
    # ── 多查询归一化：query + queries 合并去重，最多 3 个 ──
    _qs = args.get("queries") or []
    if isinstance(_qs, str):
        _qs = [_qs]
    _qs = [str(q).strip() for q in _qs if str(q).strip()]
    if query and str(query).strip():
        _qs.insert(0, str(query).strip())
    _qs = list(dict.fromkeys(_qs))[:3]
    if not _qs:
        return "Error: 缺少 query 参数"
    query = _qs[0]

    # ── 多查询路径（≥2 个变体）：逐查询检索 → RRF 融合 ──
    if len(_qs) >= 2:
        try:
            from hashmm.chat_retrieval import get_chat_retrieval
            cr = get_chat_retrieval()
            lists = []
            for q in _qs:
                try:
                    _, srcs, _inj = cr.enhance(q, [], retrieval_mode=mode)
                    lists.append(srcs or [])
                except Exception:
                    lists.append([])
            fused = _rrf_fuse(lists, top_k=5)
            if not fused:
                return ("知识库中未找到与这些查询相关的信息："
                        + " / ".join(_qs))
            from hashmm.agent.context_pack import pack_sources
            body, _rep = pack_sources(fused, budget=4500)
            return f"[多查询融合] {len(_qs)} 个查询变体：" + " / ".join(_qs) + "\n\n" + body
        except Exception as e:
            return f"检索失败: {str(e)[:100]}"

    # v13: Check cache first
    try:
        from hashmm.agent.cache import get_cache
        cache = get_cache()
        cache_key = f"{query}:{mode}"
        cached = cache.get_retrieval(cache_key)
        if cached is not None:
            return cached  # Cache hit
    except Exception:
        cache = None

    try:
        from hashmm.chat_retrieval import get_chat_retrieval
        cr = get_chat_retrieval()
        _, sources, injection = cr.enhance(query, [], retrieval_mode=mode)
        if not sources:
            return f"知识库中未找到与'{query}'相关的信息。"
        # V79 上下文工程：预算装箱（最相关优先完整保留、整条丢弃不留残句）
        from hashmm.agent.context_pack import pack_sources
        result, _rep = pack_sources(sources[:8], budget=4500)

        # Cache the result
        if cache:
            try:
                cache.set_retrieval(cache_key, result)
            except Exception as _e:
                log_suppressed(logger, _e)

        return result
    except Exception as e:
        return f"检索失败: {str(e)[:100]}"

register_executor("kb_search", _exec_kb_search)


def _exec_kg_query(args: dict, ctx: dict) -> str:
    """Knowledge graph entity/relation query."""
    entity = args.get("entity", "")
    if not entity:
        return "Error: 缺少 entity 参数"
    try:
        from hashmm.kg.kg_retriever import get_kg_retriever
        kr = get_kg_retriever()
        if not kr.is_available:
            return "知识图谱未加载"
        res = kr.search(entity, mode="kg", top_k_entities=5, top_k_relations=10)
        parts = []
        for ent in res.entities[:5]:
            parts.append(f"实体: {ent.get('name', '')} ({ent.get('type', '')})")
        for rel in res.relations[:10]:
            parts.append(f"关系: {rel.get('head', '')} → {rel.get('relation', '')} → {rel.get('tail', '')}")
        return "\n".join(parts) if parts else f"未找到与'{entity}'相关的图谱信息"
    except Exception as e:
        return f"图谱查询失败: {str(e)[:100]}"

register_executor("kg_query", _exec_kg_query)


def _exec_create_doc_v13(args: dict, ctx: dict) -> dict:
    """v13: Document generation that returns file info for Agent Loop.

    Unlike the old create_document, this returns structured dict with file info.
    """
    doc_type = args.get("doc_type", "pptx")
    title = args.get("title", "报告")
    content = args.get("content", "")
    if not content:
        return {"status": "error", "message": "缺少 content 参数"}

    try:
        from hashmm.tools import get_doc_generator
        from hashmm.api import app_state
        gen = get_doc_generator(app_state.llm_fn)

        if doc_type == "pptx":
            result = gen.generate_pptx(title, data_context=content[:6000])
        elif doc_type == "docx":
            result = gen.generate_docx(title, data_context=content[:6000])
        elif doc_type == "xlsx":
            result = gen.generate_xlsx(title, data_context=content[:6000])
        else:
            return {"status": "error", "message": f"不支持的文档类型: {doc_type}"}

        if result and result.get("ok"):
            return {
                "status": "ok",
                "message": f"✅ {doc_type.upper()} 已生成: {result.get('filename', '')}",
                "file": {
                    "filename": result.get("filename", f"output.{doc_type}"),
                    "download_url": result.get("download_url", ""),
                    "size": result.get("size", 0),
                },
            }
        return {"status": "error", "message": result.get("message", "生成失败")}
    except Exception as e:
        return {"status": "error", "message": f"文档生成失败: {str(e)[:200]}"}

# Register v13 create_document (overrides old one)
register_executor("create_document", _exec_create_doc_v13)


def get_executor_map() -> dict:
    """Return the full tool name → executor function mapping."""
    return dict(_EXECUTORS)
