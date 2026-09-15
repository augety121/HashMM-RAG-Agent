"""AgentLoopHandler — LLM-driven multi-step tool execution.

For complex tasks that need the LLM to decide which tools to call:
  - File analysis (read → analyze → respond)
  - Code modification (read → edit → execute → verify)
  - Multi-step research (search → search → synthesize)

Uses DeepSeek function calling API (tools parameter).
"""
from __future__ import annotations
import json, re, time
from typing import Generator, Any
from .base import BaseHandler, SSEEvent, SYS_PROMPTS, BASE_INST, THINK_INST


MAX_ITERATIONS = 10
MAX_TOOL_RESULT_CHARS = 10000


class AgentLoopHandler(BaseHandler):

    def __init__(self, *, tool_definitions: list[dict] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.tool_definitions = tool_definitions or []

    def run(self) -> Generator[SSEEvent, None, None]:
        if not self.llm_fn or not hasattr(self.llm_fn, 'call_with_tools'):
            # Fallback: just do a streaming answer
            yield self.emit_progress("generate", 30, "生成回答中...")
            msgs = self.build_messages(sys_key="analysis")
            full = ""
            for ev in self.stream_llm(msgs):
                yield ev
                if ev.event == "token":
                    full += ev.data.get("content", "")
            yield self.emit_done(intent="complex_task")
            return

        # Build system prompt with tool descriptions
        sys_p = self._build_system_prompt()
        messages = [{"role": "system", "content": sys_p}]

        # Add history
        for h in self.history[-8:]:
            role = "user" if h["role"] == "user" else "assistant"
            messages.append({"role": role, "content": h.get("content", "")[:600]})

        # Current query
        parts = []
        if self.file_context:
            parts.append("用户上传文件内容：\n" + self.file_context[:5000])
        parts.append(self.query)
        messages.append({"role": "user", "content": "\n\n".join(parts)})

        tools = self.tool_definitions
        full_content = ""

        for iteration in range(1, MAX_ITERATIONS + 1):
            yield SSEEvent("iteration", {"current": iteration, "max": MAX_ITERATIONS})

            # Force text on last iterations
            tc = "none" if iteration >= MAX_ITERATIONS - 1 else "auto"

            # Heartbeat during LLM call
            self.heartbeat.start()
            try:
                choice = self.llm_fn.call_with_tools(messages, tools, tool_choice=tc)
                message = choice.message
            except Exception as e:
                self.heartbeat.stop()
                for ev in self.heartbeat.drain():
                    yield ev
                err = f"LLM 调用失败：{repr(e)[:150]}"
                yield self.emit_token(err)
                full_content += err
                break
            self.heartbeat.stop()
            for ev in self.heartbeat.drain():
                yield ev

            msg_content = message.content or ""

            # Provider reasoning stays private.  It is retained below on the
            # assistant tool-call message because DeepSeek thinking mode
            # requires exact replay, but it must never enter the public SSE
            # timeline.
            if "<think>" in msg_content:
                think_m = re.search(r'<think>(.*?)</think>', msg_content, re.DOTALL)
                msg_content = re.sub(r'<think>.*?</think>', '', msg_content, flags=re.DOTALL).strip()

            # Case A: Tool calls
            if message.tool_calls:
                if msg_content and len(msg_content) > 5:
                    yield self.emit_token(msg_content + "\n\n")
                    full_content += msg_content + "\n\n"

                # Build assistant message for history
                asst_msg = {"role": "assistant", "content": message.content or ""}
                if hasattr(message, 'reasoning_content') and message.reasoning_content is not None:
                    asst_msg["reasoning_content"] = message.reasoning_content
                tc_list = []
                for tc_item in message.tool_calls:
                    tc_list.append({
                        "id": tc_item.id, "type": "function",
                        "function": {"name": tc_item.function.name, "arguments": tc_item.function.arguments},
                    })
                asst_msg["tool_calls"] = tc_list
                messages.append(asst_msg)

                # Execute tools
                for tc_item in message.tool_calls:
                    tool_name = tc_item.function.name
                    try:
                        tool_args = json.loads(tc_item.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    yield self.emit_step_start(tool_name, self._tool_desc(tool_name, tool_args))

                    self.heartbeat.start()
                    t0 = time.time()
                    result = ""
                    if self.tool_exec_fn:
                        try:
                            result = self.tool_exec_fn(tool_name, tool_args, {"session_id": self.conv_id})
                        except Exception as e:
                            result = f"Error: {repr(e)}"
                    dur = round((time.time() - t0) * 1000)
                    self.heartbeat.stop()
                    for ev in self.heartbeat.drain():
                        yield ev

                    if len(result) > MAX_TOOL_RESULT_CHARS:
                        result = result[:MAX_TOOL_RESULT_CHARS] + "\n... (截断)"

                    messages.append({"role": "tool", "tool_call_id": tc_item.id, "content": result})
                    from hashmm.api.tool_result import parse_tool_result
                    normalized = parse_tool_result(result)
                    status = "done" if normalized.success else "error"
                    yield self.emit_step_done(tool_name, result[:300], dur, status)

                    # Emit file events for created files
                    if tool_name in ("create_file", "create_document") and "OK" in result:
                        fname = tool_args.get("filename", "")
                        fn_match = re.search(r'(?:文件|文档|PPT|Word)\s+(\S+)\s+已创建', result)
                        if fn_match:
                            fname = fn_match.group(1)
                        if fname:
                            yield self.emit_file(fname, f"/api/conversations/{self.conv_id}/files/{fname}")

                continue  # Loop back — LLM sees tool results

            # Case B: Text response (final answer)
            if msg_content:
                yield self.emit_token(msg_content)
                full_content += msg_content
                break

            # Case C: Stop
            if choice.finish_reason == "stop":
                break

        yield self.emit_done(
            intent="complex_task",
            tokens={"input": 0, "output": int(len(full_content) / 1.8)}
        )

    def _build_system_prompt(self) -> str:
        sp = SYS_PROMPTS.get("analysis", BASE_INST)
        sp += "\n你有以下工具可用：\n"
        for td in self.tool_definitions:
            fn = td.get("function", {})
            sp += f"  {fn.get('name', '?')} — {fn.get('description', '')[:80]}\n"
        sp += "\n铁律：工具调完后必须有文字回答。如果 3 次执行仍有错，停止调试。\n"
        sp += "\n" + THINK_INST
        if self.custom_prompt:
            sp += "\n用户自定义指令：" + self.custom_prompt
        if self.profile_ctx:
            sp += "\n" + self.profile_ctx
        return sp

    def _tool_desc(self, name: str, args: dict) -> str:
        descs = {
            "kb_search": f"搜索: {args.get('query', '')[:60]}",
            "web_search": f"搜索: {args.get('query', '')[:60]}",
            "execute_code": f"运行 Python ({len(args.get('code', '').splitlines())} 行)",
            "create_file": f"创建: {args.get('filename', '')}",
            "create_document": f"创建: {args.get('filename', '')}",
            "read_file": f"读取: {args.get('filepath', '')}",
            "edit_file": f"编辑: {args.get('filepath', '')}",
            "list_files": "列出文件",
        }
        return descs.get(name, f"{name}(...)")
