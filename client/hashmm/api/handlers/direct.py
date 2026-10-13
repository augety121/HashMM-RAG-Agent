"""DirectHandler — simple questions and chitchat.

Single LLM streaming call, no tools. Fastest path.
Token cost: ~3000
"""
from __future__ import annotations
import re
from typing import Generator
from .base import BaseHandler, SSEEvent


class DirectHandler(BaseHandler):

    def run(self) -> Generator[SSEEvent, None, None]:
        # Greetings: don't include history (prevents "你好" → repeat last task)
        q = self.query.strip()
        is_greeting = len(q) < 8 and any(
            g in q for g in ["你好", "hello", "hi", "谢谢", "嗨", "hey", "早", "晚"]
        )

        messages = self.build_messages(sys_key="text", max_history=0 if is_greeting else 4)

        yield self.emit_progress("generate", 30, "生成回答中...")

        full_text = ""
        for event in self.stream_llm(messages):
            yield event
            if event.event == "token":
                full_text += event.data.get("content", "")

        yield self.emit_done(
            intent="direct",
            tokens={"input": int(len(self.query)/1.8), "output": int(len(full_text)/1.8)}
        )
