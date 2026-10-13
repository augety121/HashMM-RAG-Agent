"""ReAct-lite Agent v6.0 — universal tool-use agent via text tags.

Replaces SmartAgent (which depends on function calling API and breaks on DeepSeek).
Uses pure text tags that any LLM can generate:

  <<SEARCH: 查询词>>          → search knowledge base
  <<CODE: python代码>>        → execute Python code  
  <<FILE: filename.py>>       → create a file
  content
  <</FILE>>

The agent streams normally until it encounters a tag, then:
1. Pauses streaming
2. Executes the action
3. Injects the result as a system message
4. Continues LLM generation with the result

Max 3 tool calls per turn to prevent infinite loops.

Usage:
    agent = ReactAgent(llm_fn=llm, tool_exec_fn=execute_tool)
    for event in agent.run(query, history, system_prompt):
        yield event  # SSEEvent
"""
from __future__ import annotations
import re
import json
import time
from dataclasses import dataclass
from hashmm.utils import get_logger

logger = get_logger("hashmm.react_agent")

MAX_TOOL_CALLS = 3

# Tag patterns
_SEARCH_TAG = re.compile(r'<<SEARCH:\s*(.+?)>>', re.DOTALL)
_CODE_TAG = re.compile(r'<<CODE:\s*(.+?)>>', re.DOTALL)
_FILE_TAG = re.compile(r'<<FILE:\s*(\S+?)>>\s*\n(.*?)<</FILE>>', re.DOTALL)

REACT_TOOL_PROMPT = """
你有以下工具可以使用（通过在回答中插入特殊标签来调用）：

1. **搜索知识库**：在回答中写 <<SEARCH: 查询词>> ，系统会返回搜索结果
2. **执行代码**：在回答中写 <<CODE: python代码>> ，系统会执行并返回结果
3. **创建文件**：在回答中写 <<FILE: 文件名>> 文件内容 <</FILE>> ，系统会创建文件

使用规则：
- 需要查找文档信息时用 <<SEARCH>>
- 需要计算、画图、数据分析时用 <<CODE>>
- 需要保存代码或文档时用 <<FILE>>
- 每次只用一个工具，等结果回来再决定下一步
- 简单问题直接回答，不要用工具
- 最多使用 3 次工具
""".strip()


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool: str
    input_text: str
    output: str
    success: bool
    tool_name: str = ""
    call_id: str = ""
    receipt: dict | None = None


class ReactAgent:
    """ReAct-lite agent using text tags for tool invocation.
    
    Compatible with any LLM — no function calling API needed.
    """

    def __init__(self, *, llm_fn, tool_exec_fn=None, kb_search_fn=None,
                 exec_context: dict | None = None):
        self.llm_fn = llm_fn
        self.tool_exec_fn = tool_exec_fn
        self.kb_search_fn = kb_search_fn
        # The context is authored by the server, not the model.  ReAct-lite is
        # still used for providers without native function calling, but it
        # must cross the same owner/scope/permission boundary as AgentLoop.
        self.exec_context = dict(exec_context or {})
        self.tool_calls = 0
        self._receipt_seq = 0
        self.t0 = time.time()

    def run(self, query: str, messages: list[dict],
            system_prompt: str = "") -> str:
        """Run the agent and return the final answer.

        For streaming, use run_streaming() instead.

        Args:
            query: User query
            messages: Pre-built messages (from ContextBuilder)
            system_prompt: Additional system prompt

        Returns:
            Final answer text
        """
        if system_prompt:
            # Inject tool prompt into system message
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + REACT_TOOL_PROMPT
            else:
                messages.insert(0, {"role": "system",
                                    "content": system_prompt + "\n\n" + REACT_TOOL_PROMPT})

        self.tool_calls = 0
        self._receipt_seq = 0
        full_answer = ""

        for iteration in range(MAX_TOOL_CALLS + 1):
            # Generate response
            if hasattr(self.llm_fn, 'chat'):
                response = self.llm_fn.chat(messages)
            else:
                response = self.llm_fn(messages[-1]["content"])

            # Check for tool tags
            tool_result = self._extract_and_execute(response)

            if tool_result:
                # Tool was called — inject result and continue
                self.tool_calls += 1
                logger.info(f"ReAct tool call #{self.tool_calls}: "
                            f"{tool_result.tool}({tool_result.input_text[:50]})")

                # Add LLM response + tool result to messages
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "system",
                    "content": f"[工具结果] {tool_result.tool}:\n{tool_result.output[:3000]}"
                })

                if self.tool_calls >= MAX_TOOL_CALLS:
                    # Max tools reached — force final answer
                    messages.append({
                        "role": "system",
                        "content": "已达到工具使用上限，请直接给出最终回答。"
                    })
            else:
                # No tool tag — this is the final answer
                # Strip any remaining thinking tags
                response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
                full_answer = response
                break

        return full_answer

    def run_streaming(self, query: str, messages: list[dict]):
        """Generator that yields (event_type, data) tuples for SSE streaming.

        Yields:
            ("token", {"content": "text"})
            ("tool", {"tool": "SEARCH", "input": "query", "output": "result"})
            ("thinking", {"content": "thinking text"})
        """
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += "\n\n" + REACT_TOOL_PROMPT

        self.tool_calls = 0
        self._receipt_seq = 0

        for iteration in range(MAX_TOOL_CALLS + 1):
            # Stream response and collect full text
            full_response = ""
            buffer = ""
            in_tag = False
            tag_buffer = ""

            if hasattr(self.llm_fn, 'stream'):
                for token in self.llm_fn.stream(messages):
                    buffer += token
                    full_response += token

                    # Check if we're entering a tag
                    if "<<" in buffer and not in_tag:
                        # Yield text before the tag
                        before_tag = buffer.split("<<")[0]
                        if before_tag:
                            yield ("token", {"content": before_tag})
                        in_tag = True
                        tag_buffer = "<<" + buffer.split("<<", 1)[1]
                        buffer = ""
                    elif in_tag:
                        tag_buffer += token
                        # Check if tag is complete
                        if ">>" in tag_buffer and not tag_buffer.strip().endswith("<<"):
                            in_tag = False
                            # Don't yield the tag content — it will be processed below
                            buffer = ""
                    elif not in_tag:
                        # Normal streaming
                        yield ("token", {"content": token})
                        buffer = ""

                # Flush remaining buffer
                if buffer and not in_tag:
                    yield ("token", {"content": buffer})
            else:
                # Non-streaming fallback
                if hasattr(self.llm_fn, 'chat'):
                    full_response = self.llm_fn.chat(messages)
                else:
                    full_response = self.llm_fn(messages[-1]["content"])
                yield ("token", {"content": full_response})

            # Check for tool tags in the full response
            tool_result = self._extract_and_execute(full_response)

            if tool_result:
                self.tool_calls += 1
                yield ("tool", {
                    "tool": tool_result.tool,
                    "tool_name": tool_result.tool_name or tool_result.tool,
                    "call_id": tool_result.call_id,
                    "input": tool_result.input_text[:100],
                    "output": tool_result.output[:500],
                    "success": tool_result.success,
                    "status": "done" if tool_result.success else "failed",
                    "receipt": dict(tool_result.receipt or {}),
                })

                messages.append({"role": "assistant", "content": full_response})
                messages.append({
                    "role": "system",
                    "content": f"[工具结果] {tool_result.tool}:\n{tool_result.output[:3000]}"
                })

                if self.tool_calls >= MAX_TOOL_CALLS:
                    messages.append({
                        "role": "system",
                        "content": "已达到工具使用上限，请基于已有结果给出最终回答。"
                    })
            else:
                break  # Final answer, done

    def _extract_and_execute(self, text: str) -> ToolResult | None:
        """Extract the first tool tag from text and execute it."""
        # Search tag
        m = _SEARCH_TAG.search(text)
        if m:
            query = m.group(1).strip()
            return self._exec_search(query)

        # Code tag
        m = _CODE_TAG.search(text)
        if m:
            code = m.group(1).strip()
            return self._exec_code(code)

        # File tag
        m = _FILE_TAG.search(text)
        if m:
            filename = m.group(1).strip()
            content = m.group(2)
            return self._exec_file(filename, content)

        return None

    def _exec_search(self, query: str) -> ToolResult:
        """Execute knowledge base search."""
        args = {"query": query}
        try:
            # The governed executor carries scope, hooks and security policy.
            # ``kb_search_fn`` is retained as a constructor compatibility
            # field, but calling it directly would bypass that boundary.
            if self.tool_exec_fn:
                result = self.tool_exec_fn("kb_search", args, self.exec_context)
                from hashmm.api.tool_result import parse_tool_result
                normalized = parse_tool_result(result)
                item = ToolResult("SEARCH", query, normalized.content[:2000], normalized.success)
                return self._with_receipt(item, "kb_search", args, result)
            item = ToolResult(
                "SEARCH", query,
                "搜索功能未接入统一权限执行器，已安全阻止。",
                False,
            )
            return self._with_receipt(item, "kb_search", args, {"status": "failed"})
        except Exception as e:
            item = ToolResult("SEARCH", query, f"搜索出错: {e}", False)
            return self._with_receipt(
                item, "kb_search", args,
                {"status": "failed", "error": type(e).__name__},
            )

    def _exec_code(self, code: str) -> ToolResult:
        """Execute Python code."""
        args = {"code": code}
        # v17 Phase 31 ⑤: high-risk (code execution) → human-in-the-loop when enabled.
        _hitl = self._hitl_block("execute_code", args)
        if _hitl is not None:
            return self._with_receipt(_hitl, "execute_code", args, {"status": "denied"})
        try:
            if self.tool_exec_fn:
                result = self.tool_exec_fn("execute_code", args, self.exec_context)
                from hashmm.api.tool_result import parse_tool_result
                normalized = parse_tool_result(result)
                content = normalized.content if normalized.success else (normalized.error or normalized.content)
                item = ToolResult("CODE", code[:100], content[:2000], normalized.success)
                return self._with_receipt(item, "execute_code", args, result)
            item = ToolResult("CODE", code[:100], "代码执行功能不可用", False)
            return self._with_receipt(item, "execute_code", args, {"status": "failed"})
        except Exception as e:
            item = ToolResult("CODE", code[:100], f"执行出错: {e}", False)
            return self._with_receipt(
                item, "execute_code", args,
                {"status": "failed", "error": type(e).__name__},
            )

    def _hitl_block(self, tool_name: str, args: dict):
        """v17 Phase 31 ⑤: return a 'needs approval' ToolResult for high-risk tools
        when HITL is enabled (HASHMM_AGENT_HITL=1); else None (auto-run, default)."""
        try:
            import os
            if os.environ.get("HASHMM_AGENT_HITL") != "1":
                return None
            from hashmm.agent_safety import requires_approval
            if requires_approval(tool_name, args):
                return ToolResult(tool_name.upper(), str(args)[:80],
                                  f"高风险操作 '{tool_name}' 需人工审批，已暂停（设 HASHMM_AGENT_HITL=0 可关闭门禁）。",
                                  False)
        except Exception as exc:
            # A broken approval classifier is a security failure.  It must not
            # silently turn a high-risk compatibility action into auto-run.
            return ToolResult(
                tool_name.upper(), str(args)[:80],
                f"安全审批检查失败，操作已阻止：{type(exc).__name__}",
                False,
            )
        return None

    def _exec_file(self, filename: str, content: str) -> ToolResult:
        """Create a file."""
        args = {"filename": filename, "content": content}
        _hitl = self._hitl_block("create_file", {"filename": filename})
        if _hitl is not None:
            return self._with_receipt(_hitl, "create_file", args, {"status": "denied"})
        try:
            if self.tool_exec_fn:
                result = self.tool_exec_fn(
                    "create_file", args,
                    self.exec_context,
                )
                from hashmm.api.tool_result import parse_tool_result
                normalized = parse_tool_result(result)
                output = normalized.content if normalized.success else (normalized.error or normalized.content)
                item = ToolResult("FILE", filename, output[:500], normalized.success)
                return self._with_receipt(item, "create_file", args, result)
            item = ToolResult("FILE", filename, "文件创建功能不可用", False)
            return self._with_receipt(item, "create_file", args, {"status": "failed"})
        except Exception as e:
            item = ToolResult("FILE", filename, f"创建出错: {e}", False)
            return self._with_receipt(
                item, "create_file", args,
                {"status": "failed", "error": type(e).__name__},
            )

    def _with_receipt(
        self,
        item: ToolResult,
        tool_name: str,
        arguments: dict,
        raw_result,
    ) -> ToolResult:
        """Attach one bounded receipt to every attempted compatibility action."""
        self._receipt_seq += 1
        scope = self.exec_context.get("execution_scope")
        scope = scope if isinstance(scope, dict) else {}
        run_id = str(scope.get("run_id") or self.exec_context.get("run_id") or "react")[:160]
        call_id = f"react-call-{self._receipt_seq}"
        try:
            from hashmm.agent.execution_receipt import (
                build_execution_receipt,
                infer_side_effect,
            )
            artifacts = []
            if isinstance(raw_result, dict) and isinstance(raw_result.get("file"), dict):
                artifacts.append(raw_result["file"])
            denied = isinstance(raw_result, dict) and raw_result.get("status") == "denied"
            receipt = build_execution_receipt(
                run_id=run_id,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                result=raw_result,
                status="completed" if item.success else ("denied" if denied else "failed"),
                execution_scope=scope,
                executor={"kind": "react_compat", "name": tool_name},
                permission={
                    "decision": "denied" if denied else "allowed",
                    "authority": "execution_scope",
                },
                side_effect=infer_side_effect(tool_name, arguments),
                artifacts=artifacts,
                idempotency_key=f"{run_id}:{call_id}",
            )
        except Exception:
            # The manifest gate treats missing receipts as a hard failure.  Do
            # not manufacture a partial or unverifiable substitute.
            receipt = None
        item.tool_name = tool_name
        item.call_id = call_id
        item.receipt = receipt
        return item
