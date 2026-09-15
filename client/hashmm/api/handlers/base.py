"""BaseHandler — foundation for all task handlers.

Provides:
  - SSEEvent data class
  - HeartbeatThread for keepalive during tool execution
  - Context building (system prompt + history)
  - Common emit helpers (token, thinking, progress, step, file, done)
"""
from __future__ import annotations
import json, re, time, threading, queue
from dataclasses import dataclass, field
from typing import Generator, Any

from hashmm.api.public_progress import public_analysis_status


@dataclass
class SSEEvent:
    """One SSE event to yield."""
    event: str      # trace, token, thinking, step_start, step_done, file, progress, keepalive, done
    data: dict = field(default_factory=dict)

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class HeartbeatThread:
    """Sends keepalive events every `interval` seconds during tool execution."""

    def __init__(self, interval: int = 15):
        self.interval = interval
        self._stop = threading.Event()
        self._queue: queue.Queue[SSEEvent] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self):
        # One handler reuses the heartbeat around every provider and tool call.
        # ``threading.Event`` remains set after stop(), so reusing the old
        # instance silently disabled every heartbeat after the first call.
        # Create a fresh event for each run and never start overlapping workers.
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = threading.Event()

        def _run():
            while not self._stop.wait(self.interval):
                self._queue.put(SSEEvent("keepalive", {"ts": time.time()}))
        thread = threading.Thread(target=_run, daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.2)

    def drain(self) -> list[SSEEvent]:
        """Collect all pending heartbeat events."""
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events


# ── System prompts ──

BASE_INST = """你是 HashMM-RAG Agent，一个懂学术、会写码、能建文件的 AI 助手。

**语言规则（最高优先级）：默认用中文回答。只有当用户明确要求英文时才用英文。**

回答风格：
- 说人话，像同事讨论而非教科书。先给结论，再自然展开。
- 不许用 # 标题（只能 **加粗** 分节）。不用编号清单开场。
- 代码完整可运行，禁止 pass/TODO 占位。
- 引用知识库标 [1][2]，自己知道的不标。不确定就说不确定。
- 公式用 $行内$ 或 $$块级$$，对比用表格，代码用 ```lang。
- 短问短答，长问深答。信息密度高，不水字数。
"""

THINK_INST = "在回答前先在 <think>...</think> 中思考，但用户看不到这部分。"

SYS_PROMPTS = {
    "direct": BASE_INST + "本次是闲聊或简单问答，友好简洁。涉及技术内容自动切换专业模式。",

    "knowledge": BASE_INST + """
本次任务：基于知识库回答。
- 优先使用检索内容，用 [1][2] 标注来源
- 检索不够时用你的知识自然补充（不标注）
- 技术概念给出核心定义、关键公式、实际应用
""",

    "code": BASE_INST + """
本次任务：代码生成。
- 完整可运行，有 `if __name__ == '__main__'` 入口
- docstring + 类型注解 + 关键行注释
- 禁止 pass / ... / TODO 占位
- 超过 150 行拆成多个代码块，标注文件名
- 每个代码块开头注释文件名：# filename: xxx.py
""",

    "document": BASE_INST + """
本次任务：创建文档/PPT。请用中文生成内容（除非用户明确要求英文）。

用纯 JSON 格式输出幻灯片计划（不要 Markdown），格式如下：
```json
{
  "title": "标题",
  "slides": [
    {"role": "cover", "title": "...", "subtitle": "..."},
    {"role": "section", "title": "章节标题"},
    {"role": "bullets", "title": "结论式标题", "bullets": ["要点1", "要点2"]},
    {"role": "table", "title": "...", "headers": ["列1","列2"], "rows": [["a","b"]]},
    {"role": "two_column", "title": "...", "left": {"heading":"左标题","bullets":["..."]}, "right": {"heading":"右标题","bullets":["..."]}},
    {"role": "highlight", "title": "核心发现", "text": "一句话重点"},
    {"role": "end"}
  ]
}
```

要求：
- 所有标题和内容用中文（技术术语可保留英文原文）
- cover + 4-6 个 section（每个 2-4 页内容）+ end
- 标题表达**结论**而非主题（"深度方法提升10-20%mAP" 而非 "性能分析"）
- 每页 3-6 个要点，关键术语用 **加粗**
- 数据对比必须用 table role
- 总页数 15-30 页
""",

    "analysis": BASE_INST + """
本次任务：深度分析。多维度分析，给出具体结论和建议。
如果分析代码，指出问题并给完整修复代码。
""",
}


class BaseHandler:
    """Base class for all task handlers.

    Subclasses implement `run()` which yields SSEEvent objects.
    """

    def __init__(self, *,
                 query: str,
                 conv_id: str,
                 history: list[dict],
                 file_context: str = "",
                 profile_ctx: str = "",
                 custom_prompt: str = "",
                 llm_fn: Any = None,
                 kb_search_fn: Any = None,
                 tool_exec_fn: Any = None):
        self.query = query
        self.conv_id = conv_id
        self.history = history
        self.file_context = file_context
        self.profile_ctx = profile_ctx
        self.custom_prompt = custom_prompt
        self.llm_fn = llm_fn
        self.kb_search_fn = kb_search_fn
        self.tool_exec_fn = tool_exec_fn
        self.heartbeat = HeartbeatThread(interval=15)
        self.t0 = time.time()
        self.steps: list[dict] = []
        self.created_files: list[dict] = []
        self.thinking_text = ""
        self.sources: list[dict] = []

    def run(self) -> Generator[SSEEvent, None, None]:
        raise NotImplementedError

    # ── Emit helpers ──

    def emit_progress(self, stage: str, pct: int, msg: str) -> SSEEvent:
        return SSEEvent("progress", {"stage": stage, "pct": pct, "msg": msg})

    def emit_token(self, content: str) -> SSEEvent:
        return SSEEvent("token", {"content": content})

    def emit_thinking(self, content: str) -> SSEEvent:
        # ``content`` may be a provider-private reasoning payload or a legacy
        # <think> block.  It is required for some provider continuation
        # protocols, but it is not execution evidence and must never be copied
        # into the user-visible timeline or persisted message metadata.
        public_status = public_analysis_status(content)
        self.thinking_text = public_status
        return SSEEvent("thinking", {"content": public_status})

    def emit_step_start(self, tool: str, detail: str, step_id: str = "") -> SSEEvent:
        return SSEEvent("step_start", {"tool": tool, "detail": detail, "id": step_id, "status": "running"})

    def emit_step_done(self, tool: str, detail: str, duration_ms: int = 0, status: str = "done") -> SSEEvent:
        step = {"tool": tool, "status": status, "detail": detail[:300], "duration_ms": duration_ms}
        self.steps.append(step)
        return SSEEvent("step_done", step)

    def emit_file(self, filename: str, download_url: str, size: int = 0) -> SSEEvent:
        f = {"filename": filename, "download_url": download_url, "size": size}
        self.created_files.append(f)
        return SSEEvent("file", f)

    def emit_done(self, **extra) -> SSEEvent:
        elapsed = round((time.time() - self.t0) * 1000)
        data = {
            "sources": self.sources,
            "trace": [],
            "steps": self.steps,
            "files": self.created_files,
            "elapsed_ms": elapsed,
            "session_id": self.conv_id,
            "thinking": self.thinking_text if self.thinking_text else None,
            **extra
        }
        return SSEEvent("done", data)

    # ── Suggestion generation ──

    def generate_suggestions(self, query: str, response: str, task_type: str) -> list[str]:
        """Generate follow-up suggestions based on task type and response."""
        suggestions = []

        if task_type == "code_task":
            suggestions = [
                "帮我写单元测试",
                "优化一下这段代码的性能",
                "添加错误处理和日志",
            ]
            if "class " in response:
                suggestions.append("加上类型注解和文档字符串")
            if "def " in response and "test" not in response.lower():
                suggestions.append("写一个使用示例")

        elif task_type == "doc_task":
            suggestions = [
                "帮我把这个转成其他格式",
                "内容再丰富一些",
                "添加更多数据对比",
            ]

        elif task_type == "knowledge_task":
            suggestions = [
                "能详细解释一下吗",
                "有没有代码示例",
                "和其他方法对比一下",
            ]

        elif task_type == "direct_task":
            suggestions = [
                "帮我写一段代码",
                "做一个 PPT",
                "解释一个技术概念",
            ]

        return suggestions[:3]

    # ── Context building ──

    def build_messages(self, sys_key: str = "text", max_history: int = 8) -> list[dict]:
        """Build LLM messages array: system + history + user query."""
        from hashmm.api.prompts import get_system_prompt
        sys_content = get_system_prompt(sys_key, self.custom_prompt, self.profile_ctx)

        messages = [{"role": "system", "content": sys_content}]

        # History (recent N turns only, truncated)
        for h in self.history[-max_history:]:
            role = "user" if h["role"] == "user" else "assistant"
            content = h.get("content", "")[:600]
            if content.strip():
                messages.append({"role": role, "content": content})

        # Current user message
        parts = []
        if self.file_context:
            parts.append("用户上传文件内容：\n" + self.file_context[:5000])
        parts.append(self.query)
        messages.append({"role": "user", "content": "\n\n".join(parts)})

        return messages

    # ── Streaming LLM call with think-tag filtering ──

    def stream_llm(self, messages: list[dict]) -> Generator[SSEEvent, None, str]:
        """Stream LLM response, filter <think> tags, yield token events.
        Returns the full text content (without thinking).
        """
        if not self.llm_fn or not hasattr(self.llm_fn, 'stream'):
            # Fallback: non-streaming
            if self.llm_fn:
                text = self.llm_fn(messages[-1]["content"])
                yield self.emit_token(text)
                return text
            yield self.emit_token("LLM 未配置")
            return "LLM 未配置"

        buf = ""
        in_think = False
        done_think = False
        parts: list[str] = []

        for token in self.llm_fn.stream(messages):
            if not done_think:
                buf += token
                if "<think>" in buf and not in_think:
                    in_think = True
                if "</think>" in buf and in_think:
                    # Extract thinking content
                    think_match = re.search(r'<think>(.*?)</think>', buf, re.DOTALL)
                    if think_match:
                        yield self.emit_thinking(think_match.group(1).strip())
                    remaining = buf.split("</think>", 1)[1].lstrip("\n")
                    done_think = True
                    if remaining:
                        parts.append(remaining)
                        yield self.emit_token(remaining)
                    buf = ""
                elif len(buf) > 400 and not in_think:
                    # No think tags, start emitting
                    done_think = True
                    parts.append(buf)
                    yield self.emit_token(buf)
                    buf = ""
            else:
                parts.append(token)
                yield self.emit_token(token)

        # Flush remaining buffer
        if buf and not done_think:
            if "<think>" in buf:
                think_match = re.search(r'<think>(.*?)(?:</think>|$)', buf, re.DOTALL)
                if think_match:
                    yield self.emit_thinking(think_match.group(1).strip())
                remaining = re.sub(r'<think>.*?(?:</think>|$)', '', buf, flags=re.DOTALL).strip()
                if remaining:
                    parts.append(remaining)
                    yield self.emit_token(remaining)
            else:
                parts.append(buf)
                yield self.emit_token(buf)

        return "".join(parts)
