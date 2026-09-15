"""KnowledgeHandler — knowledge-based Q&A.

Pipeline: kb_search → single LLM streaming call.
Token cost: ~10,000 (was ~20,000 in Agent Loop)
"""
from __future__ import annotations
import time
from typing import Generator
from .base import BaseHandler, SSEEvent


class KnowledgeHandler(BaseHandler):

    def run(self) -> Generator[SSEEvent, None, None]:
        # ── Step 1: Search knowledge base (no LLM needed) ──
        yield self.emit_progress("search", 20, "搜索知识库...")
        yield self.emit_step_start("kb_search", f"搜索: {self.query[:60]}")

        self.heartbeat.start()
        t0 = time.time()
        search_result = ""
        if self.kb_search_fn:
            try:
                search_result = self.kb_search_fn({"query": self.query, "top_k": 5}, {})
            except Exception as e:
                search_result = f"检索出错: {repr(e)}"
        dur = round((time.time() - t0) * 1000)
        self.heartbeat.stop()
        for ev in self.heartbeat.drain():
            yield ev

        has_results = search_result and "未找到" not in search_result and "Error" not in search_result
        status = "done" if has_results else "error"
        yield self.emit_step_done("kb_search", search_result[:200], dur, status)

        # ── Step 2: Build context and stream answer ──
        yield self.emit_progress("generate", 50, "生成回答中...")

        messages = self.build_messages(sys_key="text", max_history=6)

        # Inject search results before user query
        if has_results:
            # Insert search context before the last user message
            search_msg = {"role": "system", "content": f"检索结果：\n{search_result[:6000]}"}
            messages.insert(-1, search_msg)

        full_text = ""
        for event in self.stream_llm(messages):
            yield event
            if event.event == "token":
                full_text += event.data.get("content", "")

        yield self.emit_done(
            intent="knowledge",
            tokens={"input": int(len(self.query)/1.8) + int(len(search_result)/1.8),
                    "output": int(len(full_text)/1.8)}
        )
