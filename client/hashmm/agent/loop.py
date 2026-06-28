"""Agent Loop — ReAct 工具循环引擎。

对标 Claude Code 的核心架构：
  while not done:
    LLM 思考 → 决定下一步 → 调用工具 → 获得结果 → 继续思考

DeepSeek V4 原生支持 function calling（OpenAI 兼容）。

Usage:
    loop = AgentLoop(llm_fn=llm_fn)
    async for event in loop.run(query, history, user_id):
        if event[0] == "token":   # 流式文字
        if event[0] == "trace":   # AgentLog 进度
        if event[0] == "file":    # 生成的文件
        if event[0] == "done":    # 完成
"""
from __future__ import annotations

import os
import asyncio
import json
import time
import re
import uuid
from typing import Any, AsyncGenerator
from pathlib import Path

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.loop")

# Max iterations to prevent infinite loops
MAX_ITERATIONS = 10  # V55: 6→10 支持长任务（V49-V53 三级预算+去重+短路兜底失控）
# Context token budget (roughly chars / 1.8)
MAX_CONTEXT_CHARS = 50000

# V56: 预算常量与工具集定义迁入 harness 层（tool_pipeline），此处 re-export
# 保持既有 import 路径（tests/bench/其他模块）全部兼容。
from hashmm.agent.tool_pipeline import (  # noqa: E402
    MAX_TOOL_CALLS, MAX_SEARCH_CALLS, MAX_EXEC_CALLS,
    _SEARCH_TOOLS, _EXEC_TOOLS, RETRYABLE_TOOLS,
    TurnState, ToolPipeline, is_transient_error,
)
from hashmm.agent.run_record import RunRecord  # noqa: E402
from hashmm.observability import log_suppressed  # noqa: E402  # V57: 存量缺失 import（4 处使用从未导入）


class _SFn:
    """V54 流式 shim：与 OpenAI tool_call.function 同形。"""
    __slots__ = ("name", "arguments")

    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _STC:
    __slots__ = ("id", "type", "function")

    def __init__(self, id, name, arguments):
        self.id, self.type, self.function = id, "function", _SFn(name, arguments)


class _SMsg:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls or None
        self.reasoning_content = ""


class _SResp:
    def __init__(self, msg):
        self.message = msg


_FD_FN_RE = re.compile(r'"filename"\s*:\s*"((?:\\.|[^"\\])*)"')
# content 串的合法转义单元：\uXXXX 完整 4 位或 \+单字符；半截 \u 不会被消费（留待下一片）
_FD_CT_RE = re.compile(r'"content"\s*:\s*"((?:\\u[0-9a-fA-F]{4}|\\[^u]|[^"\\])*)')


def _extract_file_delta(args_buf: str, already: int) -> tuple[str, str]:
    """V55: 从 create_file 的【参数增量缓冲】里提取 (文件名, 新增的未转义内容)。

    用于右栏逐字直播。正则只匹配完整转义单元，半截转义（流式分片边界）自然留待
    下一片；already 按"已发出的未转义字符数"计。任何异常返回空——直播是纯预览，
    绝不影响权威落盘流程。
    """
    try:
        m = _FD_FN_RE.search(args_buf)
        if not m:
            return "", ""
        fn = json.loads('"' + m.group(1) + '"')
        c = _FD_CT_RE.search(args_buf)
        if not c:
            return fn, ""
        text = json.loads('"' + c.group(1) + '"')
        return fn, (text[already:] if len(text) > already else "")
    except Exception:
        return "", ""


def _normalize_todo(raw) -> list[dict]:
    """V50: 任务清单载荷防御——模型给什么都不崩。

    - 非数组 → []（调用方返回错误提示教模型格式）
    - 每项钳制：text 去空白截 120 字；status 不在枚举内钳到 pending
    - 字符串项视为 pending 任务；其他垃圾项丢弃；最多 20 项
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw[:20]:
        if isinstance(it, dict):
            text = str(it.get("text", "")).strip()[:120]
            status = it.get("status", "pending")
        elif isinstance(it, str):
            text, status = it.strip()[:120], "pending"
        else:
            continue
        if not text:
            continue
        if status not in ("pending", "doing", "done"):
            status = "pending"
        out.append({"text": text, "status": status})
    return out
# Tools that produce a deliverable file
_DOC_TOOLS = {"create_document", "create_file", "create_xlsx", "create_pdf", "create_pptx_from_plan"}
# Max worker agents the manager may spawn per request. Capped low because
# reliability compounds badly: 0.95^N drops fast. Manager-does-it-itself is
# the default; workers are the exception.
MAX_WORKERS = 3


class _TextToolCall:
    """Mimics an OpenAI tool_call object parsed from text content."""
    class _Fn:
        def __init__(self, name: str, arguments: str):
            self.name = name
            self.arguments = arguments

    def __init__(self, name: str, arguments: str):
        self.id = f"call_{uuid.uuid4().hex[:8]}"
        self.type = "function"
        self.function = self._Fn(name, arguments)


def _normalize_tool_markup(content: str) -> str:
    """把模型吐出的工具调用标记变体归一化成标准 XML，便于统一解析。

    DeepSeek 等模型常输出带特殊分隔符的变体，例如：
        <｜｜DSML｜｜tool_calls>          （全角管道符 U+FF5C）
        <｜｜DSML｜｜invoke name="...">
        <｜｜DSML｜｜parameter name="url" string="true">...</｜｜DSML｜｜parameter>
    标准解析器只认 <tool_calls>/<invoke>/<parameter>。这里：
      1. 去掉 DSML 包裹和管道符分隔（全角 ｜ 和半角 |）。
      2. 去掉 parameter 标签里的 string="true" 之类多余属性。
    归一化后即可走现有正则。对标准格式无副作用（幂等）。
    """
    if not content or ("DSML" not in content and "｜" not in content):
        return content
    s = content
    # 去掉标签内的全角/半角管道符与 DSML 关键字：`< ｜｜ DSML ｜｜ invoke` → `<invoke`
    # 仅在 < 与 标签名(tool_calls/invoke/parameter) 之间做清理，避免误伤正文。
    s = re.sub(
        r'<\s*[｜|\s]*(?:DSML)?[｜|\s]*(/?)\s*(tool_calls|invoke|parameter)',
        r'<\1\2',
        s,
    )
    # 闭合标签里的 DSML 残留：`</ ｜ DSML ｜ parameter>` → `</parameter>`
    s = re.sub(r'(</?)[｜|\s]*(?:DSML)?[｜|\s]*(tool_calls|invoke|parameter)\s*>', r'\1\2>', s)
    return s


def _extract_urls(text: str) -> list[str]:
    """从文本中提取 http(s) URL（用于强约束 agent 使用用户给的确切链接）。"""
    if not text:
        return []
    try:
        urls = re.findall(r'https?://[^\s<>"）)】\]]+', text)
        # 去尾部标点，去重保序
        seen, out = set(), []
        for u in urls:
            u = u.rstrip('.,;:!?。，；：')
            if u not in seen:
                seen.add(u); out.append(u)
        return out
    except Exception:
        return []


_CODE_EXT = {
    "python": "py", "py": "py", "cpp": "cpp", "c++": "cpp", "c": "c",
    "java": "java", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
    "go": "go", "rust": "rs", "ruby": "rb", "php": "php", "swift": "swift",
    "kotlin": "kt", "scala": "scala", "sql": "sql", "bash": "sh", "shell": "sh",
    "sh": "sh", "html": "html", "css": "css", "json": "json", "yaml": "yaml",
    "yml": "yaml", "csharp": "cs", "cs": "cs", "r": "r", "matlab": "m",
}


def _extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """从 markdown 文本提取 ```lang ... ``` 代码块，返回 [(lang, code), ...]。"""
    if not text or "```" not in text:
        return []
    try:
        blocks = re.findall(r'```([a-zA-Z0-9_+#]*)\n(.*?)```', text, flags=re.DOTALL)
        out = []
        for lang, code in blocks:
            code = code.strip("\n")
            if code.strip():
                out.append((lang.strip().lower(), code))
        return out
    except Exception:
        return []


def _strip_big_code_blocks(text: str) -> str:
    """把正文里 ≥15 行的代码块替换成一句简短引用（代码已存成可下载文件，在右侧查看）。
    保留小代码片段（<15行，如调用示例）。"""
    if not text or "```" not in text:
        return text
    def _repl(m):
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        if len(code.strip("\n").splitlines()) >= 15:
            return "\n> 📄 完整代码已生成为文件，点击下方文件卡片可在右侧查看与下载。\n"
        return m.group(0)  # 小片段保留
    try:
        return re.sub(r'```([a-zA-Z0-9_+#]*)\n(.*?)```', _repl, text, flags=re.DOTALL)
    except Exception:
        return text


def _guess_code_filename(lang: str, code: str) -> str:
    """根据语言和代码内容猜一个合理文件名。"""
    ext = _CODE_EXT.get(lang, "txt")
    # 尝试从代码里的类名/函数名取名
    name = None
    try:
        m = (re.search(r'\bclass\s+([A-Za-z_]\w*)', code)
             or re.search(r'\b(?:def|func|function|void|int|fn)\s+([A-Za-z_]\w*)', code))
        if m:
            name = m.group(1)
    except Exception:
        pass
    base = (name or "code").lower()[:40]
    return f"{base}.{ext}"


def _strip_tool_markup_residue(text: str) -> str:
    """发给用户前的最终护栏：清掉任何工具调用标记残片（标准 + DSML 变体）。

    即使解析没能把内容识别成工具调用（格式过于异常），也绝不让 <invoke>/<｜DSML｜>/
    tool_calls/parameter 这类原始标记泄露到用户可见的文本里。永不抛错。
    """
    if not text:
        return text
    try:
        s = _normalize_tool_markup(text)
        s = re.sub(r'<tool_calls>.*?</tool_calls>', '', s, flags=re.DOTALL)
        s = re.sub(r'<invoke.*?</invoke>', '', s, flags=re.DOTALL)
        s = re.sub(r'<[｜|\s]*DSML[｜|\s]*[^>]*>', '', s)
        s = re.sub(r'</?[｜|\s]*(tool_calls|invoke|parameter)[^>]*>', '', s)
        return s.strip()
    except Exception:
        return text


def parse_text_tool_calls(content: str) -> tuple[list, str]:
    """Parse tool calls that a model emitted as TEXT instead of via the
    structured tool_calls field.

    Some models (e.g. DeepSeek-v4 under certain configs) return tool calls as
    XML-ish text inside `.content`, e.g.:

        <tool_calls>
        <invoke name="kb_search">
        <parameter name="query">小米2024营收</parameter>
        <parameter name="mode">mix</parameter>
        </invoke>
        </tool_calls>

    Without parsing, this leaks raw XML to the user and the tool never runs.

    Returns:
        (tool_calls, cleaned_text)
        tool_calls: list of _TextToolCall (empty if none found)
        cleaned_text: content with the tool-call blocks stripped out
    """
    if not content:
        return [], content

    # 归一化 DeepSeek 等模型的 DSML 变体标记，再解析。
    # 模型可能吐出 `<｜｜DSML｜｜invoke name="...">`（全角管道符 U+FF5C + DSML 包裹），
    # 而非标准 `<invoke name="...">`。先把这些变体规整成标准 XML。
    content = _normalize_tool_markup(content)
    if "<invoke" not in content:
        return [], content

    tool_calls: list[_TextToolCall] = []

    # Match each <invoke name="..."> ... </invoke> block
    invoke_pattern = re.compile(
        r'<invoke\s+name=["\']?([^"\'>\s]+)["\']?\s*>(.*?)</invoke>',
        re.DOTALL,
    )
    param_pattern = re.compile(
        r'<parameter\s+name=["\']?([^"\'>\s]+)["\']?[^>]*>(.*?)</parameter>',
        re.DOTALL,
    )

    for m in invoke_pattern.finditer(content):
        func_name = m.group(1).strip()
        body = m.group(2)
        args: dict[str, Any] = {}
        for pm in param_pattern.finditer(body):
            key = pm.group(1).strip()
            val = pm.group(2).strip()
            # Coerce obvious JSON values; otherwise keep string
            if val.lower() in ("true", "false"):
                args[key] = val.lower() == "true"
            else:
                try:
                    if val and (val[0] in "[{" or val.isdigit()):
                        args[key] = json.loads(val)
                    else:
                        args[key] = val
                except (json.JSONDecodeError, ValueError):
                    args[key] = val
        tool_calls.append(_TextToolCall(func_name, json.dumps(args, ensure_ascii=False)))

    # Strip all tool-call markup from the visible text
    cleaned = re.sub(r'<tool_calls>.*?</tool_calls>', '', content, flags=re.DOTALL)
    cleaned = re.sub(r'<invoke.*?</invoke>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'</?(tool_calls|DSML)>', '', cleaned)
    # 兜底：清掉任何残留的 DSML/管道符工具标记碎片（防归一化未覆盖的变体泄露给用户）
    cleaned = re.sub(r'<[｜|\s]*DSML[｜|\s]*[^>]*>', '', cleaned)
    cleaned = re.sub(r'</?[｜|\s]*(tool_calls|invoke|parameter)[^>]*>', '', cleaned)
    cleaned = cleaned.strip()

    return tool_calls, cleaned


class AgentLoop:
    """核心 Agent 循环 — 对标 Claude Code ReAct。

    核心理念：不是我们告诉 LLM 用什么工具，而是 LLM 自己决定。
    我们提供工具、上下文和安全边界，然后让 LLM 自己完成任务。

    集成：
      - ConversationMemory: 上下文压缩 + 记忆注入
      - PermissionSystem: 工具调用权限检查
      - CacheLayer: 检索结果缓存
    """

    def __init__(
        self,
        llm_fn: Any,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        max_iterations: int = MAX_ITERATIONS,
        temperature: float = 0.1,
        user_id: str = "",
        conv_id: str = "",
    ):
        self.llm_fn = llm_fn
        # V56: 工具守卫管线（harness 层）——权限/预算/去重的统一裁决链，可替换可扩展
        self.tool_pipeline = ToolPipeline()
        self.tools = tools or self._get_default_tools()
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.user_id = user_id
        self.conv_id = conv_id
        self._tool_executors = self._get_tool_executors()
        self._last_faithfulness_ratio = None   # V103.90: 最近一次 run 的忠实度接地率（奖励信号源）

        # v13: Integrated subsystems
        from hashmm.agent.memory import ConversationMemory
        from hashmm.agent.permissions import get_permissions
        from hashmm.agent.cache import get_cache
        self.memory = ConversationMemory(user_id, conv_id)
        self.permissions = get_permissions()
        self.cache = get_cache()

    @staticmethod
    def _repair_dangling_tool_calls(messages: list) -> list:
        """保证消息序列同时满足 deepseek 的两条 tool 协议约束：
        ① 每条 assistant.tool_calls 之后【紧跟】对其每个 tool_call_id 的 tool 回复（缺失则补占位）；
        ② 不存在【孤立】tool 消息——前面没有对应 tool_calls 的、或 id 不匹配的 tool 消息一律丢弃，
           否则报 400（"Messages with role 'tool' must be a response to a preceding ... 'tool_calls'"）。
        上下文压缩 / 守卫短路等都可能破坏配对，这里在每次 LLM 调用前重建消息列表修复；合法时为 no-op。"""
        try:
            out: list = []
            i, n = 0, len(messages)
            while i < n:
                m = messages[i]
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                # 孤立 tool 消息（未被任何 assistant.tool_calls 在下面的内层循环收编）→ 丢弃
                if role == "tool":
                    i += 1
                    continue
                out.append(m)
                tcs = m.get("tool_calls") if (isinstance(m, dict) and role == "assistant") else None
                if tcs:
                    needed = []
                    for tc in tcs:
                        tcid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if tcid:
                            needed.append(tcid)
                    needed_set = set(needed)
                    # 收拢紧随其后、且 id 属于本 assistant 的 tool 回复；id 不匹配或重复的丢弃
                    j = i + 1
                    responded = set()
                    while j < n and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
                        rid = messages[j].get("tool_call_id")
                        if rid in needed_set and rid not in responded:
                            out.append(messages[j])
                            responded.add(rid)
                        j += 1
                    # 缺失的 id → 在此处（紧跟 assistant 之后）补占位 tool 回复
                    for tcid in needed:
                        if tcid not in responded:
                            out.append({"role": "tool", "tool_call_id": tcid,
                                        "content": "（该工具调用未返回结果，已跳过）"})
                    i = j
                    continue
                i += 1
            return out
        except Exception:
            return messages

    async def run(
        self,
        query: str,
        history: list[dict] | None = None,
        user_id: str = "",
        retrieval_context: str = "",
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """执行 Agent 循环。

        Args:
            query: 用户输入
            history: 对话历史
            user_id: 用户 ID
            retrieval_context: 预检索的 RAG 上下文（可选）

        Yields:
            ("trace", {"node": str, "detail": str})
            ("thinking", {"content": str})   # DeepSeek reasoning_content（每轮，若有）
            ("token", str)
            ("file", {"filename": str, "download_url": str})
            ("tool_start", {"id": str, "name": str, "args": dict})
            ("tool_done", {"id": str, "name": str, "status": str,
                           "result": str, "elapsed_ms": int})
            ("done", {"iterations": int, "elapsed_ms": int})

        V49 事件协议说明（对标 Claude 的思考/说明/工具交错时间线）：
        - tool_start/tool_done 通过同一个 id 配对，前端据此把"运行中"原地更新为
          "完成/失败"，一个工具只占一行（不再出现 start/done 两条冗余）。
        - narrate trace 携带模型在调工具前的完整说明文字（不再截断到 200 字），
          前端把它渲染成正文段落，形成"说明→工具→说明→工具"的交错叙事。
        - thinking 事件携带 DeepSeek 的 reasoning_content 原文（每轮都发，截断 4000 字），
          老版本前端/streaming 会安全忽略未知事件类型。
        """
        t0 = time.time()

        # Bug2 防护：记下用户本轮提供的 URL，供 fetch_url 执行时做确定性纠偏（防幻觉链接）。
        self._user_urls = _extract_urls(query)

        # Build initial messages
        messages = self._build_messages(query, history, retrieval_context)
        iteration = 0
        files_generated = []
        # V56: 预算/去重状态收敛为单一 TurnState（取代散落局部变量），
        # 守卫只读、循环单一写者；运行遥测默认关（HASHMM_AGENT_TRACE=1 开启）。
        turn = TurnState()
        self._last_faithfulness_ratio = None   # V103.90: 本次 run 重置（防实例复用残值）
        turn.original_query = query            # V103.90 方案5：留底原始问题，供检索漂移检测对照
        turn.no_progress_count = 0             # V103.90 方案5：连续"零新增证据"的轮数（有界循环）
        _rec = RunRecord(self.conv_id, query)
        _run_t0 = time.time()
        verified_once = False   # V57: 验证-修复阶段最多触发一次（防死循环）
        dod_checked = False     # V72: 收尾 DoD 自检最多一次（todo 未完成不许悄悄交差）
        stop_reason = "max_iterations"   # V72: 结构化停止理由（Loop 工程：明确的停止条件）
        try:
            _deadline_s = float(os.environ.get("HASHMM_AGENT_DEADLINE_S", "0") or 0)
        except ValueError:
            _deadline_s = 0.0
        # V52: token 核算（model_manager 把 usage 挂在返回对象上；拿不到就保持 0）
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

        def _acc_usage(resp_obj):
            u = getattr(resp_obj, "_hashmm_usage", None)
            if isinstance(u, dict):
                usage_total["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
                usage_total["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        workers_spawned = 0
        self._workers_spawned = 0  # exposed for the executor to read/increment
        # Whether the user's request implies a downloadable document deliverable
        wants_document = any(
            w in query.lower()
            for w in ["ppt", "pptx", "幻灯片", "演示", "word", "docx", "文档",
                      "报告", "excel", "xlsx", "表格", "pdf", "导出"]
        )
        doc_produced = False
        produced_answer = False  # 是否已向用户产出过正文（防"空回答"）

        yield ("trace", {"node": "think", "detail": "理解任务..."})

        # Defensive guard: a misconfigured or not-yet-loaded LLM handle would
        # otherwise surface as a cryptic "'NoneType' object has no attribute
        # 'call_with_tools'". Fail clearly and actionably instead.
        if self.llm_fn is None or not hasattr(self.llm_fn, "call_with_tools"):
            yield ("token", "⚠️ LLM 未就绪：模型尚未加载完成或未在管理后台配置。请稍候重试，或检查 API Key / Base URL 设置。")
            yield ("trace", {"node": "done", "detail": "LLM 未就绪"})
            yield ("done", {"iterations": 0, "elapsed_ms": round((time.time() - t0) * 1000), "files": []})
            return

        while iteration < self.max_iterations:
            iteration += 1

            # V55: 上下文用量表（每轮开头上报一次；Step 4 处工具结果增长后还会再报）
            yield ("ctx", {
                "chars": sum(len(str(m.get("content", ""))) for m in messages),
                "budget": MAX_CONTEXT_CHARS,
            })

            # V57: 墙钟截止（HASHMM_AGENT_DEADLINE_S，默认 0=关）——超时不再开新迭代，
            # 走既有强制收尾产出总结，避免网关超时式的硬死。
            if _deadline_s and (time.time() - _run_t0) > _deadline_s:
                yield ("trace", {"node": "deadline",
                                 "detail": f"已达运行时限 {_deadline_s:.0f}s，停止新迭代并收尾"})
                stop_reason = "deadline"
                break

            # V103.27: 预算闸（HASHMM_AGENT_MAX_TOKENS / HASHMM_AGENT_MAX_COST，默认 0=关）——
            # 在步数/墙钟之外再加一道总量闸：累计 token 或估算成本超预算就停新迭代、走收尾，避免 loop 烧飞。
            try:
                from hashmm.agent.budget import check_budget as _chk_budget
                _over_budget, _budget_why = _chk_budget(
                    usage_total["prompt_tokens"], usage_total["completion_tokens"],
                    getattr(self.llm_fn, "model", "") or "")
                if _over_budget:
                    yield ("trace", {"node": "budget",
                                     "detail": f"预算闸触发：{_budget_why}，停止新迭代并收尾"})
                    stop_reason = "budget_exceeded"
                    break
            except Exception as _be:
                logger.debug(f"budget check skipped: {_be}")

            # ── Step 1: Call LLM with tools ──
            # Tool budgeting:
            #   - If total tool budget exhausted → force a text response.
            #   - If search budget exhausted → drop search tools so the model
            #     must move on to producing output (prevents endless searching).
            if turn.total_tool_calls >= MAX_TOOL_CALLS:
                use_tools = None
            else:
                use_tools = self.tools
                if turn.search_calls >= MAX_SEARCH_CALLS:
                    use_tools = [
                        t for t in use_tools
                        if t.get("function", {}).get("name") not in _SEARCH_TOOLS
                    ]
                    # If a document is owed but not yet produced, steer hard.
                    if wants_document and not doc_produced:
                        messages.append({
                            "role": "user",
                            "content": (
                                "已检索到足够信息。请【立即】调用 create_document 工具生成用户要求的文件"
                                "（PPT/Word/Excel），不要再检索。把已获得的数据整理成结构化内容传给工具。"
                            ),
                        })
                # V49: 代码执行预算耗尽 → 把 execute_code 从工具集移除，
                # 模型只能基于已有执行结果产出回答（治"反复执行直到超时"）。
                if turn.exec_calls >= MAX_EXEC_CALLS:
                    use_tools = [
                        t for t in use_tools
                        if t.get("function", {}).get("name") not in _EXEC_TOOLS
                    ]
                use_tools = use_tools or None
            streamed_turn = False  # V54: 本轮是否走了流式（决定是否发 delta_commit）
            try:
                # 每次 LLM 调用前补齐悬空的 tool_calls（防 deepseek 400），已配齐则 no-op。
                messages = self._repair_dangling_tool_calls(messages)
                resp = None
                if (os.environ.get("HASHMM_AGENT_STREAM") == "1"
                        and hasattr(self.llm_fn, "stream_with_tools")):
                    _holder: dict = {}
                    async for _ev in self._stream_llm_call(messages, use_tools, _holder):
                        yield _ev
                    resp = _holder.get("resp")
                    _m = getattr(resp, "message", None)
                    if (_holder.get("error") is not None or resp is None
                            or (not getattr(_m, "content", "") and not getattr(_m, "tool_calls", None))):
                        # 流式失败/空响应 → 回退非流式（绝不让流式毁掉回答）
                        if _holder.get("error") is not None:
                            logger.warning(f"agent stream fallback: {_holder['error']}")
                        resp = None
                    else:
                        streamed_turn = True
                if resp is None:
                    resp = await asyncio.to_thread(
                        self.llm_fn.call_with_tools,
                        messages,
                        use_tools,
                    )
                response = resp.message if hasattr(resp, "message") else resp
                _acc_usage(resp)
            except Exception as e:
                logger.error(f"LLM call failed (iter {iteration}): {e}")
                yield ("token", f"\n⚠️ LLM 调用失败: {str(e)[:100]}")
                stop_reason = "llm_error"
                break

            # ── Step 2: Check for tool calls ──
            tool_calls = getattr(response, "tool_calls", None)
            content = getattr(response, "content", "") or ""

            # V49: DeepSeek thinking 模式的 reasoning_content 每轮都实时上报
            # （此前只在最终轮发一条"深度推理 (N字)"摘要，思考过程对用户不可见）。
            # 顺序放在正文/工具事件之前 —— 模型确实是先推理再行动。
            _reasoning = getattr(response, "reasoning_content", None)
            if _reasoning:
                yield ("thinking", {"content": str(_reasoning)[:4000]})

            # Fallback: some models emit tool calls as XML text in .content
            # rather than via the structured tool_calls field. Parse them so the
            # tools actually run instead of leaking raw <invoke> markup to the user.
            text_parsed = False
            if not tool_calls and content and ("<invoke" in content or "DSML" in content
                                                or "tool_calls" in content):
                parsed_calls, cleaned = parse_text_tool_calls(content)
                if parsed_calls:
                    tool_calls = parsed_calls
                    content = cleaned
                    text_parsed = True
                    # V49: 这是内部兼容细节，不进用户时间线（避免时间线噪音），只记日志。
                    logger.info(f"parsed {len(parsed_calls)} text-form tool call(s) from content")

            if not tool_calls:
                # V57: 验证-修复阶段（对标 Codex 的 verify 循环）——模型宣布完成时，
                # 自动编译本轮生成的 .py：失败 → 丢弃该回答、注入修复指令、再给一轮
                # （最多一次，防死循环）；通过 → 通报后照常收尾。未生成 .py 零变化。
                _vexts = (".py", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".js", ".mjs",
                          ".html", ".htm", ".json")
                _py_files = [str(f.get("filename", "")) for f in files_generated
                             if str(f.get("filename", "")).lower().endswith(_vexts)]
                if _py_files:
                    _verrs = self._verify_generated_files(_py_files)
                    if _verrs and not verified_once:
                        verified_once = True
                        yield ("trace", {"node": "verify",
                                         "detail": "自动验证：编译失败 — " + "; ".join(_verrs)[:280]})
                        messages.append({"role": "assistant", "content": content or "（宣布完成）"})
                        messages.append({"role": "user", "content": (
                            "⚠️ 自动验证发现以下文件编译失败，请立即修复后再交付"
                            "（优先用 str_replace 精确修复，修完一句话说明即可）：\n"
                            + "\n".join(_verrs)[:600])})
                        continue
                    if not _verrs:
                        yield ("trace", {"node": "verify",
                                         "detail": f"自动验证通过：{len(set(_py_files))} 个代码文件语法检查通过（验证通过）"})
                    else:
                        yield ("trace", {"node": "verify",
                                         "detail": "自动验证：修复后仍有编译问题 — " + "; ".join(_verrs)[:200]})

                # V72: DoD 自检（Loop 工程的"停止条件=验收标准满足"）——模型宣布完成
                # 但任务清单还有未完成项 → 不许悄悄交差：要么完成、要么明确说明搁置原因。
                # 最多触发一次（防死循环），与 verify-fix 同点不同关。
                _todos = getattr(turn, "todo_items", None) or []
                _undone = [t.get("text", "") for t in _todos
                           if t.get("status") not in ("done", "completed", "skipped")]
                if _undone and not dod_checked:
                    dod_checked = True
                    yield ("trace", {"node": "dod",
                                     "detail": "完成度检查：仍有未完成任务 — " + "、".join(_undone)[:200]})
                    messages.append({"role": "assistant", "content": content or "（宣布完成）"})
                    messages.append({"role": "user", "content": (
                        "⚠️ 任务清单里以下条目还未标记完成：\n- "
                        + "\n- ".join(_undone)[:500]
                        + "\n请逐项处理：能完成的现在完成；确实无需做的，调用 update_todo "
                          "把它标记为 done 并在最终回答里说明原因。然后再交付。")})
                    continue
                elif _todos and not _undone:
                    yield ("trace", {"node": "dod", "detail": f"完成度检查：任务清单 {len(_todos)} 项全部完成 ✓"})

                # V75: 引用接地校验（RAG 管线最后一环）——终答引用了不存在的检索编号
                # → 幻觉引用，给一次修正机会（与 verify/DoD 同点同模式，防死循环）。
                _cit_max = getattr(turn, "kb_citation_max", 0)
                _bad_cits = self._citation_issues(content or "", _cit_max)
                if _bad_cits and not getattr(turn, "citation_checked", False):
                    turn.citation_checked = True
                    yield ("trace", {"node": "citation",
                                     "detail": f"引用校验：编号 {_bad_cits} 超出检索结果范围（最大 [{_cit_max}]）"})
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({"role": "user", "content": (
                        f"⚠️ 你的回答引用了 {['[%d]' % n for n in _bad_cits]}，"
                        f"但本次检索只返回了 [1]~[{_cit_max}]。"
                        "请修正：把无效引用改为真实存在的编号，或删除该引用；"
                        "不要编造检索结果里没有的出处。修正后直接给出最终回答。")})
                    continue
                elif _cit_max > 0 and not _bad_cits and re.search(r"(?<![A-Za-z0-9_\]])\[\d{1,3}\](?!\()", content or ""):
                    yield ("trace", {"node": "citation", "detail": "引用校验：全部引用编号有效 ✓"})

                # V103.90: 忠实度合约（引用门之上更强的一环）——不只查"编号没越界"，而是
                # 逐句核验"事实声明是否引用了、且所引证据真能支撑它"（对标 RAGAS faithfulness、
                # 企业级"每句可溯源"）。接不回证据 / 缺引用且本 turn 没修过 → 给一次修正机会
                # （与 verify/DoD/citation 同点同模式，防死循环）。判定刻意低假阳性、只对真问题
                # 开火；无论是否开火都把接地率写进 trace（可观测）。证据池为空则跳过（无依据不判）。
                _evidence = getattr(turn, "kb_evidence", None) or []
                if _evidence and content and not getattr(turn, "faithfulness_checked", False):
                    try:
                        from hashmm.evaluation import faithfulness as _fa
                        _frep = _fa.audit_faithfulness(content, _evidence,
                                                       judge_fn=self._build_faithfulness_judge())
                        # V103.90: 暴露接地率到实例属性——streaming 的 agent 分支据此把忠实度
                        # 作为奖励信号传给 episodic 记录（跨路径统一自我进化，且不双重记录）。
                        if _frep.checked:
                            self._last_faithfulness_ratio = _frep.ratio
                        if _fa.should_request_revision(_frep):
                            turn.faithfulness_checked = True
                            yield ("trace", {"node": "faithfulness",
                                             "detail": (f"忠实度校验：{len(_frep.unsupported)} 句证据不支持、"
                                                        f"{len(_frep.uncited)} 句缺引用（接地率 "
                                                        f"{round(_frep.ratio * 100)}%）→ 要求修正")})
                            messages.append({"role": "assistant", "content": content or ""})
                            messages.append({"role": "user",
                                             "content": _fa.build_revision_instruction(_frep)})
                            continue
                        elif _frep.checked and _frep.total_factual > 0:
                            yield ("trace", {"node": "faithfulness",
                                             "detail": (f"忠实度校验：{_frep.supported}/{_frep.total_factual} "
                                                        f"条事实声明可溯源（接地率 {round(_frep.ratio * 100)}%）✓")})
                    except Exception as _fe:
                        log_suppressed(logger, _fe)
                # No tool calls → LLM is done, output the text response
                if content:
                    # 最终护栏：发给用户前，绝不泄露任何工具调用标记残片（含 DSML 变体）。
                    if streamed_turn:
                        yield ("delta_commit", {"as": "answer"})
                    safe = _strip_tool_markup_residue(content)
                    if safe:
                        # ★对标 Claude：正文里的大代码块（≥15行）剥离成可下载文件，正文只留
                        # 一句"代码见右侧文件"。这样回答简洁、代码在右侧 artifact 查看，
                        # 不在正文堆大段代码。不依赖模型是否主动调 create_file。
                        try:
                            blocks = _extract_code_blocks(safe)
                            big_blocks = [(lang, code) for lang, code in blocks
                                          if lang in _CODE_EXT and len(code.splitlines()) >= 15]
                            if big_blocks:
                                for lang, code in big_blocks:
                                    # 已存在同内容文件就不重复存
                                    if any(code[:80] in (f.get("_preview") or "") for f in files_generated):
                                        continue
                                    fname = _guess_code_filename(lang, code)
                                    fres = await self._execute_tool(
                                        "create_file", {"filename": fname, "content": code}, user_id)
                                    if isinstance(fres, dict) and fres.get("file"):
                                        _fobj = dict(fres["file"]); _fobj["_preview"] = code[:80]
                                        # V103.48: 文件事件带上完整正文（对代码文件，完整
                                        # 代码就是答案）。前端可即时预览不必再请求一次；
                                        # eval 也能据此判 must_contain/min_length，不必去抓 URL。
                                        _fobj["_content"] = code
                                        _fname = _fobj.get("filename", "")
                                        files_generated[:] = [f for f in files_generated
                                                              if f.get("filename") != _fname]
                                        files_generated.append(_fobj)
                                        yield ("file", _fobj)
                                # 从正文剥离这些大代码块，替换为简短引用
                                safe = _strip_big_code_blocks(safe)
                        except Exception as _e:
                            log_suppressed(logger, _e)
                        if safe.strip():
                            yield ("token", safe)
                            produced_answer = True

                # （reasoning_content 已在本轮开头统一通过 thinking 事件上报）

                stop_reason = "completed"
                break  # Exit loop — task complete

            # ── Step 3: Execute tool calls ──
            # ★对标 Claude：模型调用工具【之前】的解释文字（如"我先查一下…"），作为一条
            # trace 事件穿插进执行流，形成 思考→说明→工具→说明→工具 的交错效果。
            if content and content.strip():
                try:
                    pre = _strip_tool_markup_residue(content)
                    pre = _strip_big_code_blocks(pre).strip()
                    if pre:
                        # V49: 发完整说明（仅 1200 字防爆护栏，正常说明远短于此）。
                        # 之前截到 200 字 + 前端渲染成灰色小字，导致"中间没有回答"的观感；
                        # 现在前端把 narrate 渲染为正文段落，交错在工具步骤之间。
                        snippet = pre if len(pre) <= 1200 else pre[:1200] + "…"
                        if streamed_turn:
                            yield ("delta_commit", {"as": "narrate"})
                        yield ("trace", {"node": "narrate", "detail": snippet})
                except Exception as _e:
                    log_suppressed(logger, _e)
            # Add assistant message with tool calls to history
            if text_parsed:
                # Build a synthetic assistant message that pairs with the parsed calls
                assistant_msg = {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            else:
                assistant_msg = self._serialize_assistant_msg(response)
            messages.append(assistant_msg)

            # P1-2: 并发预执行（对标 Codex FuturesOrdered）。仅当开关开 + 这批全是只读工具时，
            # 并发拿到结果（保序）；否则 _prefetched 为空，循环里走原串行 await。默认关=零变化。
            _prefetched: dict = {}
            try:
                from hashmm.agent.parallel_tools import should_parallelize, run_tools_ordered
                if should_parallelize(tool_calls):
                    async def _exec_one(tc):
                        try:
                            a = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, AttributeError):
                            a = {}
                        return await self._execute_tool(tc.function.name, a, user_id)
                    _results = await run_tools_ordered(tool_calls, _exec_one)
                    _prefetched = {id(tc): r for tc, r in zip(tool_calls, _results)}
            except Exception:
                _prefetched = {}

            for tc in tool_calls:
                async for _dev in self._dispatch_tool_call(
                        tc, iteration=iteration, turn=turn, messages=messages,
                        files_generated=files_generated, prefetched=_prefetched,
                        user_id=user_id, rec=_rec):
                    yield _dev

            # V103.90 方案5：有界循环——本轮所有检索都没带来新证据、且已连续 N 轮如此，
            # 就停止打转（break 后由下面的"防空回答兜底"逼模型用已有信息作答）。这给了
            # LangGraph 教程里"每轮必须有进展、否则停"的硬保证，不再只靠预算上限兜。
            if getattr(turn, "no_progress_count", 0) >= self.NO_PROGRESS_LIMIT:
                stop_reason = "no_progress"
                yield ("trace", {"node": "loop",
                                 "detail": f"连续 {turn.no_progress_count} 轮检索无新增证据，停止打转"})
                break

            # ── Step 4: Context management ──
            total_chars = sum(len(str(m.get("content", ""))) for m in messages)
            # V55: 上下文用量表（对标 Claude Code 的 context meter）
            yield ("ctx", {"chars": total_chars, "budget": MAX_CONTEXT_CHARS})
            if total_chars > MAX_CONTEXT_CHARS:
                # V50: 先温和老化（折叠旧工具结果，结构不动）；仍超限才一刀切压缩。
                messages = self._age_tool_results(messages)
                total_chars = sum(len(str(m.get("content", ""))) for m in messages)
                if total_chars > MAX_CONTEXT_CHARS:
                    messages = self._compact_context(messages)
                    yield ("trace", {"node": "compact", "detail": "上下文已压缩"})
                else:
                    yield ("trace", {"node": "compact", "detail": "旧工具结果已折叠（叙事保留）"})

        # ── 防"空回答"兜底：迭代耗尽但从未产出正文 → 强制让 LLM 基于已有信息总结 ──
        if not produced_answer:
            try:
                messages.append({
                    "role": "user",
                    "content": (
                        "请【立即】基于以上已经获取到的信息，直接给出完整的最终回答，不要再调用任何工具。"
                        "如果已生成文件，简要说明文件内容和用途。"
                    ),
                })
                final_resp = await asyncio.to_thread(self.llm_fn.call_with_tools, messages, None)
                final_msg = final_resp.message if hasattr(final_resp, "message") else final_resp
                _acc_usage(final_resp)
                final_text = _strip_tool_markup_residue(getattr(final_msg, "content", "") or "")
                if final_text:
                    yield ("token", final_text)
                    produced_answer = True
            except Exception as e:
                log_suppressed(logger, e)
            # 仍无正文 → 给明确兜底文案（有文件就说文件，没有就说没拿到信息），绝不空回答
            if not produced_answer:
                if files_generated:
                    names = "、".join(f.get("filename", "") for f in files_generated if f.get("filename"))
                    yield ("token", f"已为你生成文件：{names}。可点击下方卡片预览或下载。"
                                    "如需我调整内容或补充说明，告诉我即可。")
                else:
                    yield ("token", "抱歉，我在处理这个任务时没能获取到足够的信息来给出完整回答。"
                                    "请确认链接是否可访问，或换一种方式描述你的需求，我再试一次。")
                produced_answer = True

        # ── Done ──
        elapsed = round((time.time() - t0) * 1000)
        yield ("trace", {"node": "done", "detail": f"完成 ({iteration}轮, {elapsed}ms, {stop_reason})"})
        _rec.flush("done", iterations=iteration, stop_reason=stop_reason,
                   prompt_tokens=usage_total["prompt_tokens"],
                   completion_tokens=usage_total["completion_tokens"])
        total_tokens = usage_total["prompt_tokens"] + usage_total["completion_tokens"]
        yield ("done", {
            "stop_reason": stop_reason,
            "iterations": iteration,
            "elapsed_ms": elapsed,
            "files": files_generated,
            "usage": {**usage_total, "total_tokens": total_tokens} if total_tokens else None,
        })

    # ═══════════════════════════════════════════════════════════
    # Internal methods
    # ═══════════════════════════════════════════════════════════

    def _build_messages(
        self, query: str, history: list[dict] | None, retrieval_context: str
    ) -> list[dict]:
        """Build the initial message list for the LLM."""
        messages = []

        # System prompt
        sys_content = self.system_prompt or self._default_system_prompt()
        # V80: 用户长期偏好注入（HASHMM_USER_MEMORY=1 启用；默认关，永不抛错）
        try:
            from hashmm.agent.user_memory import inject_block
            _um = inject_block(self.user_id)
            if _um:
                sys_content += "\n\n" + _um
        except Exception:
            pass
        # V75: 经验回灌二期——遥测沉淀的负面规则（HASHMM_EXP_RULES=1 启用；默认关，永不抛错）
        try:
            from hashmm.agent.exp_rules import load_exp_rules
            _exp = load_exp_rules()
            if _exp:
                sys_content += "\n\n" + _exp
        except Exception:
            pass

        # Agent-loop directive. This OVERRIDES any passive "系统会自动把你的文字转成文件"
        # instruction that may have leaked in from the legacy post-processing flow.
        # In the agent loop the model MUST actively call tools.
        sys_content += (
            "\n\n## 你正在 Agent 工具循环中运行（重要）\n"
            "你拥有可调用的工具，必须【主动调用工具】来完成任务，而不是只输出文字描述。\n"
            "- 需要数据 → 调用 kb_search（最多搜索 2-3 次，够用就停）。\n"
            "- 用户要 PPT/Word/Excel/PDF → 检索到数据后【必须调用 create_document 工具】"
            "（参数 doc_type / title / content）来真正生成文件。绝不要只给文字、Python 代码或制作步骤。\n"
            "- 用户要【写代码/实现某个程序/某个类或算法】且代码较完整（超过约 30 行或是完整文件）→ "
            "【调用 create_file 工具】把代码保存成可下载文件（参数 filename 含正确扩展名如 main.py/rbtree.cpp，"
            "content 为完整代码）。\n"
            "  ★【默认排版·对标 Claude】把代码保存成文件后，正文【默认】不再整段重复粘贴完整代码"
            "（用户可点右侧文件卡片查看/下载）；正文只写：实现了什么、文件清单、关键设计要点、怎么用"
            "（可给几行调用示例）。这是为了避免长代码刷屏的【默认习惯】，不是铁律。\n"
            "  ★★【用户明确要时必须照办·最高优先级·别犯轴】只要用户明确说想在对话里看到代码——"
            "如『把代码贴到聊天框 / 输出到聊天栏 / 直接发给我 / 在对话里给我看 / 完整贴出来 / 别只给文件 / "
            "把上面生成的代码给我』等——就【必须】立刻在正文用 ```lang 代码块把完整代码贴出来"
            "（文件照常也保存，不冲突）。【绝对禁止】用『按我的工作规范/为了避免刷屏，所以不在聊天里贴代码』"
            "这类话去拒绝、推脱或敷衍——用户的明确指令永远高于上面的默认排版习惯。\n"
            "  这条背后是一条通用原则：【听懂并服从用户当下的明确要求，比固守任何默认套路都重要】。"
            "用户要简短就简短、要详细就展开、要换格式就换格式、要把东西直接给他就直接给——"
            "默认习惯只在用户没明确表态时才生效，一旦用户开口，照用户说的做。\n"
            "- 写完代码【不需要再调用 execute_code 去验证/找文件】，除非用户明确要求运行。直接保存文件并简要说明即可。\n"
            "\n## 工具使用策略（对标大厂 Agent，重要）\n"
            "- 【选最准的工具，能不调就不调】已确定知道的事直接答，别为用工具而用工具。要用就用最贴切的："
            "取实时/外部信息用 web_search，查本地知识库用 kb_search，多跳/对比/综述类问题用 deep_search 或 "
            "deep_research（一次深检索顶多次浅搜，别用一堆零散 kb_search 硬拼一个复杂问题）。\n"
            "- 【独立的只读调用一次并发发出】同一步要查多个互不依赖的东西时，在【同一轮】一次给出多个工具调用，"
            "让它们并发执行，而不是一个个串着来——更快、更像大厂助手。只有后一步要用到前一步结果、"
            "或带副作用（写文件/执行代码）才分步串行。\n"
            "- 【先看结果再决定下一步】每次拿到结果先读懂、判断够不够再行动；绝不重复发起完全相同的调用，"
            "也别拿到足够信息了还机械继续搜。\n"
            "- 【失败就自愈，不空转】工具报错先看错误信息：参数错就改参数重试一次，换查询词/换工具能解决就换；"
            "同类失败连续两次就换思路或如实说明卡点，绝不无意义反复重试。\n"
            "- 【产出后自检 + 给出处】调了写文件/生成文档的工具后，确认确实产出再宣布完成；"
            "来自 web_search/kb_search 的关键事实要标注来源。\n"
            "- 【一步到位】能本轮用并发 + 合适工具一次办完的，别拆成多轮挤牙膏。\n"
            "\n## 行为准则（对标产品级助手，重要）\n"
            "- 【为用户多想一步】完成任务后，主动给 1-2 条具体可执行的下一步建议"
            "（如'可以接着加单元测试'），而不是干巴巴地结束。\n"
            "- 【听懂言下之意】用户说'不对/有问题'时，先自查你最近的输出找具体原因，"
            "不要让用户复述；用户描述模糊时，做出最合理的假设并明说（'我按 X 理解，"
            "如果你要的是 Y 告诉我'），一次最多反问一个关键问题。\n"
            "- 【交付完整可用】默认交付能直接运行/使用的完整结果，不交半成品；"
            "代码默认带必要的错误处理和使用说明。\n"
            "- 【长对话记忆】历史中的 [早前对话摘要] 块是早前几十轮对话的压缩记录，"
            "回答'之前说过什么/最开始要什么/生成过哪些文件'时引用它，不要说不知道。\n"
            "- 【长任务不半途而废】用户要的是一件大事（多步骤/多文件/调研+产出）时，"
            "不要做了一两步就草草收尾问'还要继续吗'。按 update_todo 把整件事推到真正完成，"
            "中途遇到子问题自己想办法解决（再检索、换工具、拆步骤），只在真的缺关键信息时才停下问一句。"
            "一轮里能多做就多做，别把本可一次办完的活拆成十轮逼用户反复催。\n"
            "- 【语气】像靠谱的同事：自然、直接、不堆客套话。\n"
            "\n## 任务清单（update_todo，对用户可见）\n"
            "- 复杂任务（需要 3 步以上）→ 开始时先调用 update_todo 列出完整计划，"
            "之后每完成一项就再调用一次更新状态（完成标 done，进行中标 doing）。"
            "每次传完整清单（全量覆盖）。简单任务（一两步能完成）不要用。\n"
            "\n## 修改已有文件（V50 编辑纪律，重要）\n"
            "- 修改本会话已生成/已上传的文件 → 先 read_file_range 查看实际内容（含行号），"
            "再用 str_replace 做精确替换。old_str 必须与文件内容逐字一致（含缩进）且唯一。\n"
            "- 【禁止】为了局部改动用 create_file 整文件重写——既浪费又容易引入丢失。"
            "create_file 只用于创建新文件。\n"
            "- 多文件任务或续作前，先 file_tree 查看工作区已有什么，不要重复创建。\n"
            "- 注意参数名：这三个工具用 filepath；create_file 用 filename。\n"
            "- 工具调用请用标准 function calling，不要把工具调用写成文本里的 XML 标签。\n"
            "- 【像专业助手一样边做边说】：在调用工具【之前】，先用一两句完整的话承上启下："
            "简述上一步拿到了什么、接下来要做什么、为什么"
            "（如\"已拿到三季度数据，接下来整理成对比表格并保存为 Excel\"）。"
            "让用户全程看到你的思路，不要闷头调工具。\n"
            "- 完成后用一两句话总结你做了什么、生成了什么文件。\n"
            "\n## 多专员协作（spawn_worker）\n"
            "**流水线模式（V80）**：研究报告类任务用两段式——先派 research 专员收集材料，"
            "再派 writer 专员把材料写成文件（用 context 参数把第一阶段的结论传给它）。"
            "你负责审材料、定结构、验收产出。\n"
            "- 当任务涉及【多个相对独立的子问题】（如分别查两家公司、分别查多个年份），"
            "可以用 spawn_worker 派专员并行处理，每个专员独立查一件事，你负责汇总。\n"
            "- 默认你自己干；只有子问题确实独立、且各自需要多步检索时才派专员。最多派 3 个。\n"
            "\n## 主动理解意图，不要机械应答（重要）\n"
            "面对真实世界的任务，要像一个体贴的助手那样【主动想全】用户没明说但显然需要的东西，"
            "而不是字面回答。例如：\n"
            "- 用户说'订去北京的票' → 除了票，主动考虑：出发时间/偏好、目的地天气（带什么衣服）、"
            "市内交通路线、住宿、值得去的地方和吃饭的地方。\n"
            "- 用户说'分析这家公司' → 主动想到：财务、业务结构、增长、风险、对比同行。\n"
            "如果关键信息缺失（如出发地、日期、预算），【先简短澄清 1-2 个最关键的问题】再动手，"
            "宁可多问一句也不要瞎猜——让用户满意比快更重要。\n"
            "如果用户已经说清楚了，就直接动手，不要啰嗦地反复确认。\n"
        )

        # v13: Inject long-term memory (L2/L3/L4)
        memory_ctx = self.memory.get_memory_injection()
        if memory_ctx:
            sys_content += f"\n\n## 用户记忆\n{memory_ctx}"

        if retrieval_context:
            # V79: 边界感知截断（段落/句号边界收口+省略标注），替代 [:8000] 拦腰斩
            from hashmm.agent.context_pack import clip_at_boundary
            sys_content += "\n\n## 知识库预检索结果\n" + clip_at_boundary(retrieval_context, 8000)

        # Bug2 修复：从用户本轮消息提取 URL，强约束 agent 必须使用用户给的确切链接，
        # 杜绝"用户给 arxiv 链接、agent 却抓一个编造的无关链接"的幻觉。
        try:
            _urls = _extract_urls(query)
            if _urls:
                _url_list = "\n".join(f"  - {u}" for u in _urls)
                sys_content += (
                    "\n\n## 用户提供的链接（最高优先级，必须严格遵守）\n"
                    f"{_url_list}\n"
                    "1. 处理涉及链接的任务时，【第一步】就直接调用 fetch_url，参数 url 必须是上面用户给的【确切链接】。\n"
                    "2. 【严禁】编造、替换、猜测成任何其他网址（新闻站/博客/lesswrong/openai/cdn 等都不行）。\n"
                    "3. fetch_url 返回内容后，【立即基于该内容回答用户】，不要再去 web_search 或抓别的链接。\n"
                    "4. 若是 arxiv 链接，fetch_url 会自动返回论文标题/摘要/全文，直接据此拆解即可。\n"
                    "5. 只有当 fetch_url 明确失败（返回 Error）时，才考虑用 web_search 找替代来源。"
                )
        except Exception:
            pass

        # P1-3: 注入项目指令文件 HASHMM.md（对标 AGENTS.md/CLAUDE.md）。无文件时零变化。
        try:
            from hashmm.project_instructions import inject_into_system_prompt
            sys_content = inject_into_system_prompt(sys_content)
        except Exception:
            pass

        messages.append({"role": "system", "content": sys_content})

        # V51: 长对话压缩（对标 Claude compaction）——短对话零变化；长对话
        # 用一条结构化摘要锚定开场需求/文件清单，最近 6 条原样。
        # 此前这里硬切 [-6:]，长对话里模型完全看不到早期上下文（"聊久了失忆"）。
        if history:
            from hashmm.agent.conv_compact import compact_history, SUMMARY_MARK
            for h in compact_history(history, keep_recent=6, char_budget=16000):
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    cap = 4200 if str(content).startswith(SUMMARY_MARK) else 2000
                    messages.append({"role": role, "content": content[:cap]})

        # V58: 技能注入（含内置 huashu-design 设计技能）——触发词命中才拼进
        # system，未命中零变化；任何失败不抛（技能系统故障不拦主流程）。
        try:
            from hashmm.agent.builtin_skills import ensure_builtin_skills
            from hashmm.evolution.skill_manager import get_skill_manager
            ensure_builtin_skills()
            messages = get_skill_manager().inject_skill_context(query, messages)
        except Exception as _e:
            log_suppressed(logger, _e)

        # Current query
        messages.append({"role": "user", "content": query})
        return messages

    def _default_system_prompt(self) -> str:
        """Default system prompt for the agent."""
        return (
            "你是 HashMM-RAG 智能助手，具备知识检索、文件生成、代码执行、联网搜索等能力。\n\n"
            "## 工具使用原则（重要！）\n"
            "1. **高效搜索**：每个主题最多搜索 1-2 次，不要反复搜索相似的内容\n"
            "2. **搜索后就回答**：拿到数据后立即综合分析，不要继续搜索更多\n"
            "3. **主动生成文件**：用户要 PPT/Word/Excel 时，直接调用 create_document 工具\n"
            "4. **不要给代码建议**：用户要文件就直接生成文件，不要给 Python 代码让用户自己跑\n"
            "5. **数据不足时**：说明已有什么、缺什么，不要无限搜索\n\n"
            "## Agentic 工作准则（V69）\n"
            "- **坚持完成**：把用户请求彻底解决再交回。遇到工具报错先分析原因换路重试，"
            "不要轻易放弃或把半成品丢给用户；确实无法完成时明确说明卡在哪一步、试过什么。\n"
            "- **先计划后行动**：超过两步的任务先用 update_todo 列出计划再执行，"
            "每完成一步同步勾选；调用工具前用一句话说明你要做什么、为什么。\n"
            "- **检索自适应**：kb_search 结果为空或明显不相关时，不要照搬原词重试——"
            "换同义词、拆子问题、或改用实体名重新检索（你有预算限制，改写要一次到位）；"
            "检索结果好就立即作答，不要为了凑数继续搜。\n"
            "- **检索查询自包含**：多轮对话里检索时，把'它/这个/上面的'等指代词替换为"
            "具体实体名——检索引擎看不到对话历史。\n"
            "- **复杂问题多查询**：涉及多个概念或对比的问题，一次 kb_search 里传 "
            "queries=[2-3 个查询变体]（同义改写/子问题），系统会并行检索并融合去重，"
            "只算一次检索预算。\n\n"
            "## 回答原则\n"
            "- 先结论后展开，结构清晰\n"
            "- 关键数据用表格呈现\n"
            "- 所有数据标注来源 [文档名 p.页码]\n"
            "- 代码任务直接给完整可运行的代码\n"
        )

    def _serialize_assistant_msg(self, response) -> dict:
        """Serialize LLM response to message dict (for conversation history).

        DeepSeek V4 requires reasoning_content to be passed back.
        """
        msg: dict[str, Any] = {"role": "assistant"}

        content = getattr(response, "content", "") or ""
        if content:
            msg["content"] = content

        reasoning = getattr(response, "reasoning_content", None)
        if reasoning:
            msg["reasoning_content"] = reasoning

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]

        return msg

    async def _dispatch_tool_call(self, tc, *, iteration, turn, messages,
                                  files_generated, prefetched, user_id, rec):
        """V58: 单个工具调用的完整分发（loop 工程的 dispatch 阶段方法化）。

        职责链：配对事件(tool_start/done) → update_todo 直通 → 守卫管线裁决 →
        spawn_worker 子任务流式 → 并发预取复用 → 执行(瞬态重试) → 结果格式化 →
        文件事件 → post hooks → 去重状态更新 → 遥测记账。
        以 async 生成器形式向上游透传全部事件；从 run() 原样切片抽出，
        行为由既有回归测试冻结。
        """
        turn.total_tool_calls += 1
        func_name = tc.function.name
        if func_name in _SEARCH_TOOLS:
            turn.search_calls += 1
        try:
            func_args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            func_args = {}

        # V50: 任务清单（对标 Claude Code TodoWrite）——纯 UI 通道：
        # 发 todo 事件给前端渲染 checklist；不发 tool_start/tool_done
        # （清单本身就是它的 UI，工具卡是噪音）；不占工具预算（退还计数）。
        if func_name == "update_todo":
            turn.total_tool_calls -= 1
            items = _normalize_todo(func_args.get("items"))
            try:
                turn.todo_items = items   # V72: DoD 自检数据源
            except Exception:
                pass
            if items:
                yield ("todo", {"items": items})
                n_done = sum(1 for i in items if i["status"] == "done")
                todo_result = (f"✅ 任务清单已更新（{n_done}/{len(items)} 完成）。"
                               "继续执行未完成项；每完成一项就再次调用 update_todo 更新状态。")
            else:
                todo_result = ("Error: items 必须是 [{text, status}] 数组，"
                               "status ∈ pending/doing/done。")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": todo_result})
            return  # （原 for 循环 continue：update_todo 直通，本 tc 处理完毕）

        # V49: 每次工具调用生成稳定 id，tool_start/tool_done 用它配对，
        # 前端把同一 id 的"运行中"原地更新为"完成"——一个工具一行。
        # 此前 start/done 被压成两条无 id 的 trace，时间线满屏冗余对。
        call_id = f"tc{iteration}-{turn.total_tool_calls}-{uuid.uuid4().hex[:6]}"
        yield ("tool_start", {"id": call_id, "name": func_name, "args": func_args})

        # V56: 工具守卫管线（harness 层）。权限/exec预算/search预算/连续去重
        # 按显式顺序统一裁决（语义与 V49-V53 逐字一致，见 tool_pipeline.py），
        # 每个守卫独立可测，pre/post hooks 可扩展；计数器单一写者在本循环。
        try:
            _canon_args = json.dumps(func_args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            _canon_args = str(func_args)
        call_key = (func_name, _canon_args)
        decision = self.tool_pipeline.evaluate(
            func_name, func_args, call_key, turn,
            permissions=self.permissions, user_id=self.user_id or user_id)
        dedup_hit = bool(decision is not None and decision.guard == "dedup")
        if func_name in _EXEC_TOOLS:
            turn.exec_calls += 1

        if decision is not None:
            result = decision.result or {"status": "denied", "message": "已被守卫拦截"}
            if decision.guard == "permission":
                yield ("trace", {"node": "permission",
                                 "detail": result.get("message", "")})
            elapsed_tool = 0
        elif func_name == "spawn_worker":
            # V55: 子任务真·流式——worker 执行期间逐步透出 sub_agent trace
            # （同款 Queue 桥模式：worker 与主循环同事件循环，put_nowait 安全）。
            t_tool = time.time()
            _subq: asyncio.Queue = asyncio.Queue()

            def _on_sub(line):
                try:
                    _subq.put_nowait(str(line)[:160])
                except Exception:
                    pass

            _wtask = asyncio.ensure_future(
                self._spawn_worker_streaming(func_args, user_id, _on_sub))
            while True:
                try:
                    line = await asyncio.wait_for(_subq.get(), timeout=0.3)
                    yield ("trace", {"node": "sub_agent", "detail": line})
                except asyncio.TimeoutError:
                    if _wtask.done():
                        break
            while not _subq.empty():
                yield ("trace", {"node": "sub_agent", "detail": _subq.get_nowait()})
            try:
                result = _wtask.result()
            except Exception as e:
                result = {"status": "error", "message": f"子任务执行失败: {str(e)[:200]}"}
            elapsed_tool = round((time.time() - t_tool) * 1000)
        elif id(tc) in prefetched:
            # P1-2: 用并发预执行的结果（保序，事件流与串行一致）
            result = prefetched[id(tc)]
            elapsed_tool = 0
        else:
            # Execute tool（串行路径，默认）。
            # V56: 瞬态错误（超时/连接抖动/限流）且为幂等只读工具 → 自动重试一次；
            # 永久性错误（参数非法等）不重试，照常回给模型自行修正。
            t_tool = time.time()
            _attempt = 0
            while True:
                try:
                    result = await self._execute_tool(func_name, func_args, user_id)
                    break
                except Exception as e:
                    if (_attempt == 0 and func_name in RETRYABLE_TOOLS
                            and is_transient_error(e)):
                        _attempt += 1
                        logger.warning(
                            f"transient tool error, retry once: {func_name}: {e}")
                        await asyncio.sleep(0.5)
                        continue
                    result = {"status": "error",
                              "message": f"工具执行失败: {str(e)[:200]}"}
                    break
            elapsed_tool = round((time.time() - t_tool) * 1000)

        # Check if tool generated a file
        if isinstance(result, dict) and result.get("file"):
            _fobj = dict(result["file"])
            _fname = _fobj.get("filename", "")
            # V103.48: 文件事件带上完整正文（对 create_file 的代码/文本文件，
            # arguments 里的 content 就是文件内容）。前端可即时预览不必再请求一次；
            # eval 也能据此判 must_contain/min_length，不必去抓 URL 才看到代码。
            try:
                if func_name == "create_file" and func_args.get("content"):
                    _fobj["_content"] = func_args.get("content")
                    if not _fobj.get("_preview"):
                        _fobj["_preview"] = str(func_args.get("content"))[:80]
            except Exception:
                pass
            # 去重：同名文件（agent 反复 create_file 写同一文件）只保留最新一份，
            # 避免前端出现重复的下载卡片（如 rbtree.h 出现两次）。
            files_generated[:] = [f for f in files_generated if f.get("filename") != _fname]
            files_generated.append(_fobj)
            if func_name in _DOC_TOOLS:
                doc_produced = True
            yield ("file", _fobj)

        # Format result for LLM
        result_text = self._format_tool_result(result)

        # V49: 维护连续重复去重状态（复用命中时保留首次真实结果，不被提示语覆盖）
        self.tool_pipeline.notify_post(func_name, func_args, result)
        if not dedup_hit:
            turn.last_call_key = call_key
            turn.last_result_text = result_text

        # V49: 结构化状态 —— 前端据此把失败的工具标红，而不是一律打勾
        _status = "done"
        if isinstance(result, dict):
            _rs = str(result.get("status", "ok")).lower()
            if _rs in ("error", "failed", "fail"):
                _status = "error"
            elif _rs == "denied":
                _status = "denied"
        rec.add("tool", name=func_name,
                 status=(result or {}).get("status", "") if isinstance(result, dict) else "",
                 ms=elapsed_tool)
        yield ("tool_done", {
            "id": call_id,
            "name": func_name,
            "status": _status,
            "result": result_text[:200],
            "elapsed_ms": elapsed_tool,
        })

        # Add tool result to messages
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_text,
        })

        # V75: 引用接地数据源——记录本 turn 检索结果的最大 [N] 编号（挡幻觉引用）。
        # V103.90: 并累积每条检索证据的文本（供忠实度合约逐句核验"每句可溯源"）；
        #          deep_search 的来源同样带 [N]，一并纳入（原仅统计 kb_search）。
        if func_name in ("kb_search", "deep_search"):
            try:
                _nums = [int(m) for m in re.findall(r"^\[(\d+)\]", result_text or "", re.M)]
                if _nums:
                    turn.kb_citation_max = max(getattr(turn, "kb_citation_max", 0), max(_nums))
            except Exception:
                pass
            try:
                from hashmm.evaluation.faithfulness import parse_numbered_evidence
                _ev_blocks = parse_numbered_evidence(result_text or "")
                if _ev_blocks:
                    _pool = getattr(turn, "kb_evidence", None) or []
                    # V103.90 方案5：进度判定——本轮检索带来多少"新"证据（去重增量）。
                    _novel = self._evidence_novelty(_pool, _ev_blocks)
                    if _novel == 0:
                        turn.no_progress_count = getattr(turn, "no_progress_count", 0) + 1
                    else:
                        turn.no_progress_count = 0      # 有新证据 → 进度重置
                    _pool.extend(_ev_blocks)
                    turn.kb_evidence = _pool[-24:]      # 防爆：仅保留最近若干证据块
            except Exception:
                pass

        # V69: 检索自适应（Self-RAG 轻量版）——低质量检索结果立即注入改写指引，
        # 不浪费一轮模型猜测；受 SearchBudgetGuard 限额天然约束。
        guidance = self._retrieval_guidance(func_name, result_text, turn,
                                             query=str((func_args or {}).get("query", "")))
        if guidance:
            messages.append({"role": "system", "content": guidance})

        # V103.90 方案5：检索漂移控制——改写后的检索词若偏离原始问题意图，注入一次性拉回提示。
        if func_name in ("kb_search", "deep_search"):
            _dg = self._drift_guidance(getattr(turn, "original_query", ""),
                                       str((func_args or {}).get("query", "")), turn)
            if _dg:
                messages.append({"role": "system", "content": _dg})
            yield ("trace", {"node": "retrieval_adapt", "detail": "检索质量低，自动改写查询重试"})

        # V70: 工具错误恢复指引（Claude Code 式错误恢复）——非瞬态失败注入换路提示
        err_guidance = self._error_guidance(func_name, _status, result, turn)
        if err_guidance:
            messages.append({"role": "system", "content": err_guidance})
            yield ("trace", {"node": "error_recover", "detail": f"{func_name} 失败，自动换路重试"})

    _RETRIEVAL_BAD_MARKERS = ("未找到", "没有找到", "no results", "无相关", "0 条结果")

    @staticmethod
    def _lex_overlap(query: str, text: str) -> float:
        """V74: 查询与结果的词法重叠率（中文 2-gram + 英文词）。0~1，纯函数。"""
        def toks(t: str) -> set:
            t = (t or "").lower()
            out = set()
            import re as _re
            for m in _re.findall(r"[a-z0-9_]{2,}", t):
                out.add(m)
            for seg in _re.findall(r"[\u4e00-\u9fff]+", t):
                if len(seg) == 1:
                    out.add(seg)
                for i in range(len(seg) - 1):
                    out.add(seg[i:i + 2])
            return out
        q = toks(query)
        if not q:
            return 1.0          # 无法判定按"相关"处理（不触发指引）
        r = toks(text[:1500])
        return len(q & r) / len(q)

    @staticmethod
    def _citation_issues(answer: str, max_citation: int) -> list[int]:
        """V75: 找出终答里超出检索结果范围的引用编号（幻觉引用）。

        只匹配独立的 [N] 引用（排除 markdown 链接 [N](url) 与数组下标 a[0]）。
        max_citation<=0 表示本 turn 无检索结果——任何 [N] 引用都不校验（返回空，
        因为没有依据判定）。纯函数可单测。

        V103.90: 边界改用 ASCII-only 字符类（不用 \\w）。Python 的 \\w 在 Unicode 模式下
        **包含中文字符**，原 `(?<![\\w\\]])` 会把"营收26.7亿[1]"这类中文句末引用整段漏掉
        （中文是本项目主场景，意味着越界引用基本检测不到）——此为反幻觉缺陷，予以修正。
        """
        if max_citation <= 0 or not answer:
            return []
        try:
            nums = {int(m) for m in re.findall(r"(?<![A-Za-z0-9_\]])\[(\d{1,3})\](?!\()", answer)}
            return sorted(n for n in nums if n > max_citation)
        except Exception:
            return []

    def _build_faithfulness_judge(self):
        """V103.90: 可选的忠实度 judge——仅对词法弱支撑的少数句子兜底判定。

        默认关闭（纯词法判定，零额外延迟/成本）；置 env ``HASHMM_FAITHFULNESS_JUDGE=1``
        且 llm 具备 ``quick_call`` 时启用。返回 ``(sentence, evidence) -> Optional[bool]``：
        True=支持 / False=不支持 / None=不可用（上层按"支持"处理，绝不因 judge 故障误伤）。
        """
        if os.environ.get("HASHMM_FAITHFULNESS_JUDGE", "") not in ("1", "true", "True", "yes", "on"):
            return None
        fn = self.llm_fn
        if fn is None or not hasattr(fn, "quick_call"):
            return None

        def _judge(sentence: str, evidence: str):
            prompt = (
                "判断下面这句话是否被给定证据支持，只回一个词 YES 或 NO。\n\n"
                f"证据：\n{(evidence or '')[:2500]}\n\n句子：{sentence}\n\n"
                "若证据明确支持该句事实（数字/实体/结论一致）回 YES；"
                "若证据未提及或与之矛盾回 NO。"
            )
            try:
                raw = fn.quick_call("你是严格的事实核查员", prompt, max_tokens=4)
            except Exception:
                return None
            t = (raw or "").strip().upper()
            if t.startswith("Y") or "支持" in t:
                return True
            if t.startswith("N") or "不支持" in t or "矛盾" in t:
                return False
            return None

        return _judge

    def _retrieval_guidance(self, func_name: str, result_text: str, turn,
                            query: str = "") -> str | None:
        """V69/V74: 评估 kb_search 结果质量，差则生成一次性改写指引（每 turn 最多一次）。

        判定为低质量：结果为空 / 过短(<80字) / 含"未找到"类标记 /
        V74: 结果非空但与查询词法重叠率 < 10%（检索到了别的东西≠检索成功）。
        返回 None 表示不注入。纯逻辑无 IO，便于单测。
        """
        if func_name != "kb_search":
            return None
        if getattr(turn, "retrieval_guided", False):
            return None                          # 每 turn 只指导一次，防打转
        text = (result_text or "").strip()
        low = (not text) or len(text) < 80 or any(m in text.lower() or m in text
                                                   for m in self._RETRIEVAL_BAD_MARKERS)
        if not low and query:
            low = self._lex_overlap(query, text) < 0.10
        if not low:
            return None
        try:
            turn.retrieval_guided = True
        except Exception:
            pass
        return ("[检索质量提示] 刚才的 kb_search 结果为空或质量很低。"
                "请勿用相同关键词重试；改用以下策略之一后再搜一次："
                "1) 换同义词或上位词；2) 把问题拆成更小的子问题；"
                "3) 直接用文中可能出现的实体名/术语。"
                "若仍无结果，基于已知信息作答并明确说明知识库未覆盖该主题。")

    _ERROR_GUIDE_MAX_PER_TURN = 2

    # ── V103.90 方案5：检索漂移控制 + 有界循环（进度判定）──

    NO_PROGRESS_LIMIT = 2   # 连续这么多轮"零新增证据"即停（避免无意义打转）

    def _intent_drift(self, original_query: str, current_query: str,
                      min_overlap: float = 0.12) -> bool:
        """改写后的检索 query 是否已偏离原始问题意图（纯词法，可单测）。

        判据：当前 query 与原始 query 的词法重叠率过低 ⇒ 漂了（改写把原问题的核心
        实体/指标丢了）。两者为空、太短或几乎相同则不算漂移（不误报）。
        对标 SoK arXiv 2603.07379 列的"检索漂移"。
        """
        o = (original_query or "").strip()
        c = (current_query or "").strip()
        if not o or not c or o == c:
            return False
        if len(c) < 3:
            return False
        return self._lex_overlap(o, c) < min_overlap

    def _drift_guidance(self, original_query: str, current_query: str, turn) -> str | None:
        """检索 query 偏离原意时注入一次性"拉回"提示（每 run 最多一次）。纯逻辑可测。"""
        if getattr(turn, "drift_guided", False):
            return None
        if not self._intent_drift(original_query, current_query):
            return None
        try:
            turn.drift_guided = True
        except Exception:
            pass
        return ("[意图漂移提示] 当前检索关键词已明显偏离原始问题「"
                + (original_query or "")[:60]
                + "」。请回到原始问题的核心实体/指标重新检索，"
                  "围绕原意改写（换同义词/拆子问题可以，但别丢掉原问题主体），不要越改越偏。")

    @staticmethod
    def _evidence_novelty(prev_pool, new_blocks) -> int:
        """新证据块里有多少是已检索池里没有的（去重后的增量条数）。纯函数，可单测。

        用于"进度判定"：一轮检索若零增量（new==0），说明这轮没带来新证据，计一次无进展。
        以证据块前 160 字做指纹去重（同一 chunk 多次召回视为重复）。
        """
        seen = set()
        for b in (prev_pool or []):
            k = (b or "").strip()[:160]
            if k:
                seen.add(k)
        novel = 0
        for b in (new_blocks or []):
            k = (b or "").strip()[:160]
            if k and k not in seen:
                novel += 1
                seen.add(k)
        return novel

    def _error_guidance(self, func_name: str, status: str, result, turn) -> str | None:
        """V70: 工具非瞬态失败时注入一次性换路提示（每 turn 最多 2 次防刷屏）。

        瞬态错误已由管线自动重试过；走到这里的 error 是确定性失败——
        引导模型分析原因换方法，而不是原样重试或直接摆烂。纯逻辑可测。
        """
        if status != "error":
            return None
        guided = getattr(turn, "error_guided_count", 0)
        if guided >= self._ERROR_GUIDE_MAX_PER_TURN:
            return None
        try:
            turn.error_guided_count = guided + 1
        except Exception:
            pass
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("message", ""))[:200]
        return (f"[工具失败提示] {func_name} 执行失败"
                + (f"：{detail}" if detail else "")
                + "。这不是网络抖动（瞬态错误已自动重试过）。请先分析失败原因，"
                  "然后换一种方法：改参数 / 换工具 / 拆小步骤；"
                  "不要用完全相同的参数原样重试。若两次换路仍失败，"
                  "向用户说明卡点与已尝试的方案。")


    @staticmethod
    def _check_one_file(fpath) -> str:
        """V58: 单文件语法检查（按扩展名路由；只查语法不执行，零副作用）。

        - .py            → py_compile
        - .cpp/.cc/.cxx/.h/.hpp → g++ -fsyntax-only（环境无 g++ 自动跳过）
        - .js/.mjs       → node --check（环境无 node 自动跳过）
        - .ts/.tsx/.jsx  → 暂不查（无零依赖的可靠检查器，宁缺毋滥）
        返回错误描述（空串=通过/跳过）。
        """
        import shutil
        import subprocess
        suffix = fpath.suffix.lower()
        try:
            if suffix == ".py":
                import py_compile
                py_compile.compile(str(fpath), doraise=True)
            elif suffix in (".cpp", ".cc", ".cxx", ".h", ".hpp"):
                if shutil.which("g++"):
                    r = subprocess.run(["g++", "-fsyntax-only", "-x", "c++", str(fpath)],
                                       capture_output=True, text=True, timeout=15)
                    if r.returncode != 0:
                        return (r.stderr or r.stdout or "g++ 语法检查失败").strip()[:160]
            elif suffix in (".js", ".mjs"):
                if shutil.which("node"):
                    r = subprocess.run(["node", "--check", str(fpath)],
                                       capture_output=True, text=True, timeout=15)
                    if r.returncode != 0:
                        return (r.stderr or r.stdout or "node 语法检查失败").strip()[:160]
            elif suffix in (".html", ".htm"):
                # V70: design 技能产出质量保障——标准 html.parser 完整解析
                import html.parser
                html.parser.HTMLParser().feed(
                    fpath.read_text(encoding="utf-8", errors="replace"))
            elif suffix == ".json":
                json.loads(fpath.read_text(encoding="utf-8", errors="replace"))
            return ""
        except Exception as e:
            return str(e)[:160]

    def _verify_generated_files(self, filenames: list) -> list[str]:
        """V57/V58: 语法验证本轮生成的代码文件（去重后最多 3 个）。

        只查语法不执行（零副作用、秒级以内）；返回错误描述列表，永不抛错——
        验证器自身故障不能拦住交付。支持语言见 _check_one_file。
        """
        errs: list[str] = []
        try:
            from hashmm.api.database import CONV_FILES_ROOT
            for fn in list(dict.fromkeys(filenames))[:3]:
                fpath = CONV_FILES_ROOT / (self.conv_id or "") / fn
                if not fpath.exists():
                    continue
                msg = self._check_one_file(fpath)
                if msg:
                    errs.append(f"{fn}: {msg}")
        except Exception:
            return []
        return errs

    # 兼容旧名（V57 引入，外部可能引用）
    _verify_generated_py = _verify_generated_files

    async def _spawn_worker_streaming(self, func_args: dict, user_id, on_step):
        """V55: 子任务执行（带实时步骤回调）。测试可覆写本方法注入假 worker。"""
        from hashmm.agent.worker import Worker
        task = (func_args or {}).get("task", "")
        role = (func_args or {}).get("role", "general")
        if not task:
            return {"status": "error", "message": "spawn_worker 缺少 task 参数"}
        try:
            w = Worker(self.llm_fn, role=role, user_id=user_id or self.user_id)
            res = await w.run(task, parent_context=str((func_args or {}).get("context", "")),
                              on_step=on_step)
            trail = " → ".join(res.get("steps") or []) or "（未调用工具）"
            return {"status": "ok", "message": (
                f"[专员({role})完成子任务] {task}\n"
                f"执行轨迹: {trail}\n"
                f"结论：\n{res.get('summary', '')}"
            )}
        except Exception as e:
            return {"status": "error", "message": f"子任务执行失败: {str(e)[:200]}"}

    async def _stream_llm_call(self, messages, use_tools, holder: dict):
        """V54: 真·流式 LLM 调用桥（对标 Claude/Codex 的逐字流，复用自有 stream_with_tools）。

        同步的 stream_with_tools 在线程里跑，经 asyncio.Queue 桥接成 async 事件流：
        - 每个文本增量产出 ("delta", str)（纯直播预览；权威内容仍走原 narrate/token 事件）
        - 结束时 holder["resp"] = 与 call_with_tools 返回同形的 shim
        - 任何异常 → holder["error"] 置位，调用方回退非流式（流式绝不毁掉回答）
        """
        import threading

        q: asyncio.Queue = asyncio.Queue()
        aio = asyncio.get_running_loop()

        def _pump():
            parts: list[str] = []
            calls: list[tuple] = []
            try:
                tc_names: dict = {}
                tc_bufs: dict = {}
                fd_sent: dict = {}
                tc_order: list = []   # V58: start 顺序（缺 end 时按此重建）
                tc_ended: set = set()
                for ev in self.llm_fn.stream_with_tools(messages, use_tools):
                    t = ev.get("type")
                    if t == "text":
                        c = ev.get("content") or ""
                        if c:
                            parts.append(c)
                            aio.call_soon_threadsafe(q.put_nowait, ("delta", c))
                    elif t == "tool_call_start":
                        tc_names[ev.get("id")] = ev.get("name") or ""
                        tc_order.append(ev.get("id"))
                    elif t == "tool_call_delta":
                        cid = ev.get("id")
                        # V58: 所有工具的参数增量都累计（缺 tool_call_end 时据此重建）
                        tc_bufs[cid] = tc_bufs.get(cid, "") + (ev.get("args_partial") or "")
                        if tc_names.get(cid) == "create_file":
                            # V55: create_file 参数增量 → 右栏逐字直播（纯预览）
                            fn, new_text = _extract_file_delta(tc_bufs[cid], fd_sent.get(cid, 0))
                            if fn and new_text:
                                fd_sent[cid] = fd_sent.get(cid, 0) + len(new_text)
                                aio.call_soon_threadsafe(
                                    q.put_nowait,
                                    ("file_delta", {"filename": fn, "t": new_text}))
                    elif t == "tool_call_end":
                        tc_ended.add(ev.get("id"))
                        calls.append((ev.get("id") or f"sc{len(calls) + 1}",
                                      ev.get("name") or "", ev.get("args") or "{}"))

                # V58: 防御重建——流截断/缺 tool_call_end 的调用从增量缓冲恢复
                # （真机实测：工具调用被静默吞掉 → 模型"宣布要做"然后什么都没发生）。
                for cid in tc_order:
                    if cid not in tc_ended and tc_names.get(cid):
                        calls.append((cid or f"sc{len(calls) + 1}",
                                      tc_names[cid], tc_bufs.get(cid) or "{}"))
                holder["resp"] = _SResp(_SMsg("".join(parts),
                                              [_STC(i, n, a) for i, n, a in calls]))
            except Exception as e:  # noqa: BLE001 —— 回退路径需要捕获一切
                holder["error"] = e
            finally:
                aio.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=_pump, daemon=True, name="agent-stream").start()
        while True:
            item = await q.get()
            if item is None:
                return
            yield item   # ("delta", str) 或 ("file_delta", {...})

    async def _execute_tool(self, name: str, args: dict, user_id: str) -> dict | str:
        """Execute a tool by name THROUGH the central entry point so the hook
        boundary (permission / risk / role-scope / plan-mode / tenant-policy)
        applies to the Agent Loop path too. Previously this called the raw
        executor directly, bypassing all hooks — which let high-risk tools run
        without permission/plan-mode checks."""
        # ── Special case: spawn_worker delegates to an isolated Worker agent ──
        if name == "spawn_worker":
            return await self._spawn_worker(args, user_id)

        # ── V80: 用户长期记忆（默认关 HASHMM_USER_MEMORY=1；永不抛错） ──
        if name == "remember_preference":
            from hashmm.agent.user_memory import remember
            r = remember(user_id or self.user_id, args.get("key", ""), args.get("value", ""))
            return {"status": "ok" if r.get("ok") else "error",
                    "message": f"已记住偏好（共 {r.get('count', 0)} 条）" if r.get("ok")
                               else r.get("reason", "记忆失败")}

        ctx = {"user_id": user_id, "conv_id": self.conv_id,
               "plan_confirmed": getattr(self, "plan_confirmed", False)}

        # Bug2 确定性纠偏：用户本轮给了 URL，但 agent 调 fetch_url 填了【不同域名】的链接
        # （典型幻觉：用户给 arxiv，agent 却抓 lesswrong/openai）。强制改回用户给的 URL。
        if name == "fetch_url":
            try:
                user_urls = getattr(self, "_user_urls", []) or []
                if user_urls:
                    called = str(args.get("url", "")).strip()
                    def _host(u: str) -> str:
                        try:
                            return u.split("/")[2].lower() if len(u.split("/")) > 2 else ""
                        except Exception:
                            return ""
                    called_host = _host(called)
                    user_hosts = {_host(u) for u in user_urls}
                    # 调用的域名不在用户给的域名集合里 → 判定为幻觉，强制改回用户第一个 URL
                    if called_host and called_host not in user_hosts:
                        logger.warning(f"fetch_url 幻觉纠偏: {called} → {user_urls[0]}")
                        args = {**args, "url": user_urls[0]}
            except Exception as _e:
                log_suppressed(logger, _e)
        try:
            from hashmm.api.tool_registry import execute_tool_structured as _central
            result = await asyncio.to_thread(_central, name, args, ctx)
            # structured 返回原始结果：dict 直接用（保留 file 字段），str 才包装
            if isinstance(result, str):
                if result.startswith("Error:") and ("安全策略" in result or "计划模式" in result
                                                     or "无权" in result or "管理员" in result):
                    return {"status": "denied", "message": result[len("Error:"):].strip()}
                return {"status": "ok", "message": result}
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)[:300]}

    async def _spawn_worker(self, args: dict, user_id: str) -> dict:
        """Spawn an isolated Worker agent for a sub-task (capped).

        The worker runs its own short tool loop with a restricted toolset and
        its own context window; only its concise summary is returned to the
        manager, keeping the manager's context clean.
        """
        spawned = getattr(self, "_workers_spawned", 0)
        if spawned >= MAX_WORKERS:
            return {"status": "ok", "message": (
                f"（已达 worker 上限 {MAX_WORKERS}，请自己用 kb_search 完成剩余子任务，不要再派专员）"
            )}
        task = (args.get("task") or "").strip()
        if not task:
            return {"status": "error", "message": "spawn_worker 缺少 task 参数"}
        role = args.get("role", "research")

        self._workers_spawned = spawned + 1
        try:
            from hashmm.agent.worker import Worker
            w = Worker(self.llm_fn, role=role, user_id=user_id or self.user_id)
            res = await w.run(task)
            # V51: 子任务执行轨迹进结果——前端 spawn_worker 工具卡点开即可见
            trail = " → ".join(res.get("steps") or []) or "（未调用工具）"
            return {"status": "ok", "message": (
                f"[专员({role})完成子任务] {task}\n"
                f"执行轨迹: {trail}\n"
                f"结论：\n{res.get('summary', '')}"
            )}
        except Exception as e:
            logger.warning(f"spawn_worker failed: {e}")
            return {"status": "error", "message": f"专员执行失败: {str(e)[:150]}"}

    def _format_tool_result(self, result: Any) -> str:
        """Format tool result for LLM consumption."""
        if isinstance(result, str):
            return self._clip(result, 4200, 600)
        if isinstance(result, dict):
            msg = result.get("message", "")
            data = result.get("data", "")
            file_info = result.get("file", {})

            parts = []
            if msg:
                parts.append(str(msg)[:3000])
            if data:
                parts.append(str(data)[:2000])
            if file_info:
                parts.append(f"[文件已生成: {file_info.get('filename', '')} → {file_info.get('download_url', '')}]")
            return "\n".join(parts) if parts else self._clip(json.dumps(result, ensure_ascii=False), 2400, 500)
        return self._clip(str(result), 2400, 500)

    @staticmethod
    def _clip(text: str, head: int, tail: int) -> str:
        """V69: head+tail 截断——超长工具输出保头也保尾（尾部常是总结/报错），中段折叠。"""
        if len(text) <= head + tail + 50:
            return text
        omitted = len(text) - head - tail
        return text[:head] + f"\n…[中段省略 {omitted} 字]…\n" + text[-tail:]

    # ── V50: 上下文老化（_compact_context 的温和前置步骤）──
    _KEEP_RECENT_TOOL_RESULTS = 3
    _AGED_MARK = "[结果已折叠]"

    def _age_tool_results(self, messages: list[dict]) -> list[dict]:
        """折叠【旧的】工具结果正文，保留最近 K 条全文。

        与一刀切的 _compact_context 不同：
        - 消息条数 / 角色 / tool_call_id 配对一律不动（OpenAI 协议要求 tool 消息
          必须跟在对应 assistant tool_calls 之后，粗暴摘要会破坏配对导致 400）。
        - assistant 的叙述（narrate 来源）全文保留——它是任务的叙事主线。
        - 幂等：已折叠的不会二次折叠。
        """
        tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_idx) <= self._KEEP_RECENT_TOOL_RESULTS:
            return messages
        for i in tool_idx[:-self._KEEP_RECENT_TOOL_RESULTS]:
            content = str(messages[i].get("content", ""))
            if len(content) > 200 and self._AGED_MARK not in content:
                messages[i] = {**messages[i], "content":
                               content[:160] + f"\n…{self._AGED_MARK}（原 {len(content)} 字符，"
                               "如需细节可重新调用该工具）"}
        return messages

    def _compact_context(self, messages: list[dict]) -> list[dict]:
        """Compact context when it gets too long.

        Strategy: keep system + last 4 messages + tool results summary.
        """
        if len(messages) <= 5:
            return messages

        system = messages[0] if messages[0]["role"] == "system" else None
        result = [system] if system else []

        # Summarize middle messages
        middle = messages[1:-4] if system else messages[:-4]
        if middle:
            summary_parts = []
            for m in middle:
                role = m.get("role", "")
                content = str(m.get("content", ""))[:100]
                if role == "tool":
                    summary_parts.append(f"[工具结果: {content}]")
                elif role == "user":
                    summary_parts.append(f"[用户: {content}]")
                elif role == "assistant" and not m.get("tool_calls"):
                    summary_parts.append(f"[助手: {content}]")
            if summary_parts:
                result.append({
                    "role": "user",
                    "content": f"[上下文摘要]\n" + "\n".join(summary_parts[-6:]),
                })

        # Keep last 4 messages as-is
        result.extend(messages[-4:])
        return result

    def _get_default_tools(self) -> list[dict]:
        """Get tool schemas for function calling (built-in + user-configured)."""
        tools = list(AGENT_TOOLS)
        # v15 Phase 10: built-in real tools (weather/datetime/calculator)
        try:
            from hashmm.tools.builtin_tools import BUILTIN_TOOL_SCHEMAS
            tools.extend(BUILTIN_TOOL_SCHEMAS)
        except Exception as e:
            logger.debug(f"builtin tool schemas unavailable: {e}")
        # v14 Phase 3: merge in user-configured custom API tools
        try:
            from hashmm.tools.custom_tools import get_enabled_schemas
            tools.extend(get_enabled_schemas())
        except Exception as e:
            logger.debug(f"custom tool schemas unavailable: {e}")
        # v14 Phase 4: merge in tools from connected MCP servers
        try:
            from hashmm.tools.mcp_client import get_enabled_schemas as mcp_schemas
            tools.extend(mcp_schemas())
        except Exception as e:
            logger.debug(f"MCP tool schemas unavailable: {e}")
        return tools

    def _get_tool_executors(self) -> dict:
        """Get tool executor functions (built-in + user-configured)."""
        executors = {}
        try:
            from hashmm.api.tool_registry import get_executor_map
            executors = get_executor_map()
        except ImportError:
            pass
        # Add fetch_url
        try:
            from hashmm.tools.fetch_url import execute as _fetch_url
            executors["fetch_url"] = _fetch_url
        except ImportError:
            pass
        # v15 Phase 10: built-in real tools
        try:
            from hashmm.tools.builtin_tools import BUILTIN_EXECUTORS
            executors.update(BUILTIN_EXECUTORS)
        except Exception as e:
            logger.debug(f"builtin executors unavailable: {e}")
        # v14 Phase 3: register executors for enabled custom API tools
        try:
            from hashmm.tools.custom_tools import list_tools, make_executor
            for cfg in list_tools(only_enabled=True):
                executors[cfg["name"]] = make_executor(cfg)
        except Exception as e:
            logger.debug(f"custom tool executors unavailable: {e}")
        # v14 Phase 4: register executors for connected MCP server tools
        try:
            from hashmm.tools.mcp_client import get_executors as mcp_executors
            executors.update(mcp_executors())
        except Exception as e:
            logger.debug(f"MCP tool executors unavailable: {e}")
        return executors


# ═══════════════════════════════════════════════════════════════
# Tool Schemas for Function Calling
# ═══════════════════════════════════════════════════════════════

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "从知识库中检索信息。用于回答需要文档数据支撑的问题。返回匹配的文档片段和来源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词，要具体（如'腾讯2025年营业收入净利润'而非'腾讯数据'）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["mix", "naive", "kg", "global"],
                        "description": "检索模式：mix=混合(默认), naive=向量, kg=知识图谱, global=全局摘要",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": (
                "生成文档文件（PPT/Word/Excel）。当用户要求生成文件时调用此工具。"
                "你需要提供结构化的内容，工具会渲染为真实文件并返回下载链接。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_type": {
                        "type": "string",
                        "enum": ["pptx", "docx", "xlsx"],
                        "description": "文档类型",
                    },
                    "title": {
                        "type": "string",
                        "description": "文档标题",
                    },
                    "content": {
                        "type": "string",
                        "description": "文档的完整内容（Markdown 格式）。包含所有要放入文档的文字、数据、表格。",
                    },
                },
                "required": ["doc_type", "title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "执行 Python 代码。可用于数据分析、计算、生成图表等。已安装 pandas, matplotlib, numpy 等常用库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python"],
                        "description": "编程语言（目前仅支持 Python）",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "读取网页或 PDF 的文本内容。支持 arxiv 论文（自动提取摘要+全文）、PDF 链接、普通网页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要读取的 URL（支持 https://arxiv.org/abs/xxx、PDF 链接、普通网页）",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取最新信息。当知识库中没有相关信息，或需要最新数据时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "创建文本文件（.md, .py, .json, .csv, .txt 等）。直接写入内容并返回下载链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名（含扩展名）",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kg_query",
            "description": "查询知识图谱中的实体和关系。用于获取公司、人物、产品之间的关联信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "要查询的实体名称（如'腾讯'、'小米'）",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "关系类型（可选，如'子公司'、'竞争对手'）",
                    },
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_worker",
            "description": (
                "派遣一个【子任务专员】去独立完成一件需要多步检索的子任务，"
                "并返回简洁结论。适用于：①需要并行查多个相对独立的事情"
                "（如分别查两家公司的数据）；②某个子问题需要多次检索才能查清。"
                "专员有独立上下文，它的中间过程不会污染你的对话。"
                "不要滥用——能你自己一次检索查到的，就别派专员。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "交给专员的具体子任务，要明确（如'查找小米2025年智能电动汽车业务的收入和交付量'）",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["research", "analysis", "code", "writer"],
                        "description": ("专员类型：research=检索专员(默认), analysis=分析专员, "
                                        "code=代码执行专员, writer=写作专员(把材料写成文件交付)"),
                    },
                    "context": {
                        "type": "string",
                        "description": ("V80 流水线衔接：把上一阶段专员的结论/材料传给本专员"
                                        "（如把 research 专员查到的数据传给 writer 专员成文）"),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_preference",
            "description": (
                "V80: 记住用户的【长期偏好】（跨会话生效）。当用户表达稳定的偏好或"
                "背景信息时调用——如'以后代码都用中文注释'、'我的研究领域是跨模态哈希'、"
                "'回答尽量简短'。不要记一次性的任务细节。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "偏好主题（如 '代码注释语言'）"},
                    "value": {"type": "string", "description": "偏好内容（如 '中文注释'，≤200字）"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": (
                "维护本次任务的待办清单（用户全程可见，类似项目看板）。"
                "复杂任务（3 步以上）开始时先调用它列出完整计划；之后【每完成一项就再调用一次】"
                "更新状态。每次都传【完整清单】（全量覆盖，不是增量）。简单任务不要用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "完整任务清单（覆盖式）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "任务短句，如'检索腾讯财报数据'"},
                                "status": {"type": "string", "enum": ["pending", "doing", "done"],
                                           "description": "pending=待办 doing=进行中 done=已完成"},
                            },
                            "required": ["text", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
    # ── V50: 编辑闭环三件套（与 create_file 同一会话工作区，executor 在 tool_registry/workspace）──
    {
        "type": "function",
        "function": {
            "name": "file_tree",
            "description": (
                "列出本会话工作区的文件树。开始多文件任务或续作之前先调用它，"
                "查看之前已创建/上传了哪些文件，避免重复创建或失忆。无参数。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": (
                "读取本会话工作区中某文件的指定行范围，返回带行号的内容。"
                "修改任何已存在的文件之前【必须先调用它】查看实际内容。"
                "读整个文件可设 end_line=100000。注意参数名是 filepath（不是 filename）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径（相对工作区，如 main.py）"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始（默认 1）"},
                    "end_line": {"type": "integer", "description": "结束行号（默认 100；读全文件传 100000）"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "精确字符串替换：在文件中找到 old_str 并替换为 new_str，只改匹配处、不动其余内容。"
                "这是修改已有文件的【唯一正确方式】——禁止为了小改动用 create_file 重写整个文件。"
                "old_str 必须与文件内容完全一致且唯一出现（含缩进/空格）；不唯一时带上更多上下文行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径（相对工作区）"},
                    "old_str": {"type": "string", "description": "被替换的原文（精确匹配且唯一，含缩进）"},
                    "new_str": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["filepath", "old_str", "new_str"],
            },
        },
    },
]
