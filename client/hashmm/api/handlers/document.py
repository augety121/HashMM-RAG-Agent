"""DocumentHandler v5 — per-format pipelines.

Each document format has its own:
  - System prompt (from prompts.py)
  - Post-processing (builder)
  - Summary template

PPT → JSON plan → pptx_builder
Word → Markdown → docx_builder
Excel → JSON data → xlsx_builder
PDF → Markdown → pdf_builder (or docx fallback)
"""
from __future__ import annotations
import json, re, time
from typing import Generator
from .base import BaseHandler, SSEEvent


class DocumentHandler(BaseHandler):

    def __init__(self, *, output_format: str = "docx", **kwargs):
        super().__init__(**kwargs)
        # Detect format from query if not explicitly set
        q = self.query.lower()
        if output_format in ("pptx", "docx", "xlsx", "pdf", "md"):
            self.fmt = output_format
        elif any(k in q for k in ["ppt", "幻灯片", "slides", "演示"]):
            self.fmt = "pptx"
        elif any(k in q for k in ["excel", "xlsx", "表格文件"]):
            self.fmt = "xlsx"
        elif any(k in q for k in ["pdf"]):
            self.fmt = "pdf"
        else:
            self.fmt = "docx"  # Default: Word document

    def run(self) -> Generator[SSEEvent, None, None]:
        # ── Phase 1: Search for source material ──
        yield self.emit_progress("search", 15, "搜索资料...")
        yield self.emit_step_start("kb_search", f"搜索: {self.query[:50]}")

        self.heartbeat.start()
        t0 = time.time()
        search_result = ""
        if self.kb_search_fn:
            try:
                search_result = self.kb_search_fn({"query": self.query, "top_k": 8}, {})
            except Exception as e:
                search_result = f"检索出错: {repr(e)[:100]}"
        dur = round((time.time() - t0) * 1000)
        self.heartbeat.stop()
        for ev in self.heartbeat.drain():
            yield ev

        has_results = search_result and "未找到" not in search_result
        yield self.emit_step_done("kb_search", search_result[:200], dur,
                                  "done" if has_results else "error")

        # ── Phase 2: Generate content with format-specific prompt ──
        yield self.emit_progress("generate", 40, f"生成{self._fmt_label()}内容...")

        from hashmm.api.prompts import get_system_prompt
        sys_prompt = get_system_prompt(self.fmt, self.custom_prompt, self.profile_ctx)

        messages = [{"role": "system", "content": sys_prompt}]

        # Add history (limited)
        for h in self.history[-4:]:
            role = "user" if h["role"] == "user" else "assistant"
            content = h.get("content", "")[:400]
            if content.strip():
                messages.append({"role": role, "content": content})

        # Inject search results
        if has_results:
            messages.append({
                "role": "system",
                "content": f"参考资料（来自知识库检索）：\n{search_result[:8000]}"
            })

        # User query
        messages.append({"role": "user", "content": self.query})

        # Collect LLM output silently — don't stream raw content to user
        full_text = ""
        for event in self.stream_llm(messages):
            if event.event == "thinking":
                yield event  # Show thinking
            elif event.event == "token":
                full_text += event.data.get("content", "")
                # Don't yield tokens — content goes to file, not chat

        # ── Phase 3: Validate content before creating file ──
        from hashmm.api.validator import validate_output
        ok, issues = validate_output(self.fmt, full_text)
        if not ok:
            # Log issues but continue — partial content is better than nothing
            for issue in issues:
                yield self.emit_step_done("validate", issue, 0, "error")

        # ── Phase 4: Create file ──
        yield self.emit_progress("create", 75, f"创建{self._fmt_label()}文件...")

        if self.fmt == "pptx":
            yield from self._build_pptx(full_text)
        elif self.fmt == "xlsx":
            yield from self._build_xlsx(full_text)
        elif self.fmt == "docx":
            yield from self._build_docx(full_text)
        elif self.fmt == "pdf":
            yield from self._build_pdf(full_text)
        else:
            yield from self._build_md(full_text)

        # ── Phase 5: Deliver summary ──
        yield self.emit_progress("deliver", 95, "完成")
        yield from self._deliver_summary(full_text)

        yield self.emit_done(intent="doc_task")

    # ═══════════════════════════════════════════════════════════════
    # Per-format builders
    # ═══════════════════════════════════════════════════════════════

    def _build_pptx(self, content: str) -> Generator[SSEEvent, None, None]:
        yield self.emit_step_start("create_pptx", "创建 PPT...")
        t0 = time.time()

        # Try JSON plan first
        plan = self._extract_json(content, key="slides")
        result = ""
        if plan and self.tool_exec_fn:
            result = self.tool_exec_fn("create_pptx_from_plan", {
                "plan": plan, "conv_id": self.conv_id
            }, {"session_id": self.conv_id})
        elif self.tool_exec_fn:
            # Fallback: markdown → pptx
            title = self._extract_title()
            fname = self._safe_filename(title) + ".pptx"
            result = self.tool_exec_fn("create_document", {
                "filename": fname, "content": content, "title": title
            }, {"session_id": self.conv_id})

        dur = round((time.time() - t0) * 1000)
        yield self.emit_step_done("create_pptx", result[:200], dur,
                                  "done" if "OK" in result else "error")
        self._emit_file_from_result(result)

    def _build_docx(self, content: str) -> Generator[SSEEvent, None, None]:
        yield self.emit_step_start("create_docx", "创建 Word 文档...")
        t0 = time.time()

        title = self._extract_title()
        fname = self._safe_filename(title) + ".docx"
        result = ""
        if self.tool_exec_fn:
            result = self.tool_exec_fn("create_document", {
                "filename": fname, "content": content, "title": title
            }, {"session_id": self.conv_id})

        dur = round((time.time() - t0) * 1000)
        yield self.emit_step_done("create_docx", result[:200], dur,
                                  "done" if "OK" in result else "error")
        self._emit_file_from_result(result)

    def _build_xlsx(self, content: str) -> Generator[SSEEvent, None, None]:
        yield self.emit_step_start("create_xlsx", "创建 Excel...")
        t0 = time.time()

        title = self._extract_title()
        data = self._extract_json(content, key="sheets") or content
        result = ""
        if self.tool_exec_fn:
            result = self.tool_exec_fn("create_xlsx", {
                "data": json.dumps(data) if isinstance(data, dict) else data,
                "title": title
            }, {"session_id": self.conv_id})

        dur = round((time.time() - t0) * 1000)
        yield self.emit_step_done("create_xlsx", result[:200], dur,
                                  "done" if "OK" in result else "error")
        self._emit_file_from_result(result)

    def _build_pdf(self, content: str) -> Generator[SSEEvent, None, None]:
        yield self.emit_step_start("create_pdf", "创建 PDF...")
        t0 = time.time()

        title = self._extract_title()
        result = ""
        if self.tool_exec_fn:
            result = self.tool_exec_fn("create_pdf", {
                "content": content, "title": title
            }, {"session_id": self.conv_id})

        dur = round((time.time() - t0) * 1000)
        yield self.emit_step_done("create_pdf", result[:200], dur,
                                  "done" if "OK" in result else "error")
        self._emit_file_from_result(result)

    def _build_md(self, content: str) -> Generator[SSEEvent, None, None]:
        yield self.emit_step_start("create_md", "创建文档...")
        t0 = time.time()

        title = self._extract_title()
        fname = self._safe_filename(title) + ".md"
        result = ""
        if self.tool_exec_fn:
            result = self.tool_exec_fn("create_file", {
                "filename": fname, "content": content
            }, {"session_id": self.conv_id})

        dur = round((time.time() - t0) * 1000)
        yield self.emit_step_done("create_md", result[:200], dur,
                                  "done" if "OK" in result else "error")
        self._emit_file_from_result(result)

    # ═══════════════════════════════════════════════════════════════
    # Format-specific summaries
    # ═══════════════════════════════════════════════════════════════

    def _deliver_summary(self, content: str) -> Generator[SSEEvent, None, None]:
        """Generate format-appropriate summary for the user."""
        if self.fmt == "pptx":
            plan = self._extract_json(content, key="slides")
            if plan and "slides" in plan:
                slides = plan["slides"]
                n = len(slides)
                secs = [s["title"] for s in slides if s.get("role") == "section"]
                tables = sum(1 for s in slides if s.get("role") == "table")
                summary = f"已创建 **{n} 页** PPT"
                if secs:
                    summary += f"，包含 {len(secs)} 个章节：{'、'.join(secs[:5])}"
                if tables:
                    summary += f"，含 {tables} 个数据表格"
                summary += "。\n\n**主要内容：**\n"
                for s in slides:
                    if s.get("role") in ("cover", "section", "end"):
                        continue
                    icon = {"bullets": "📝", "table": "📊", "two_column": "⚖️", "highlight": "💡"}.get(s.get("role", ""), "📄")
                    summary += f"- {icon} {s.get('title', '')}\n"
                yield self.emit_token(summary)
            else:
                yield self.emit_token("PPT 文件已创建，可点击下载。\n")

        elif self.fmt == "docx":
            # Count sections and approximate word count
            sections = re.findall(r'^##?\s+(.+)', content, re.MULTILINE)
            word_count = len(content)
            table_count = content.count("|---") + content.count("| ---")
            summary = f"已创建 Word 文档"
            if sections:
                summary += f"，包含 {len(sections)} 个章节"
            summary += f"，约 {word_count} 字"
            if table_count:
                summary += f"，{table_count} 个表格"
            summary += "。"
            yield self.emit_token(summary + "\n")

        elif self.fmt == "xlsx":
            data = self._extract_json(content, key="sheets")
            if data and "sheets" in data:
                sheets = data["sheets"]
                total_rows = sum(len(s.get("rows", [])) for s in sheets)
                yield self.emit_token(f"已创建 Excel 表格，{len(sheets)} 个工作表，{total_rows} 行数据。\n")
            else:
                yield self.emit_token("Excel 文件已创建。\n")

        elif self.fmt == "pdf":
            yield self.emit_token("PDF 文档已创建，可点击下载。\n")

        else:
            yield self.emit_token("文档已创建。\n")

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _fmt_label(self) -> str:
        return {"pptx": "PPT", "docx": "Word", "xlsx": "Excel", "pdf": "PDF", "md": "文档"}.get(self.fmt, "文档")

    def _extract_title(self) -> str:
        q = re.sub(r'^(帮我|给我|请|写|做|制作|创建|生成)(一个|一份|个)?', '', self.query).strip()
        q = re.sub(r'(的)?(PPT|ppt|Word|word|Excel|excel|PDF|pdf|文档|报告|方案|综述|slides|presentation)', '', q).strip()
        return q[:50] if q else "文档"

    def _safe_filename(self, title: str) -> str:
        return re.sub(r'[^\w\u4e00-\u9fff\-]', '_', title)[:40]

    def _extract_json(self, text: str, key: str = "slides") -> dict | None:
        """Extract JSON object containing the specified key."""
        # Try ```json block
        m = re.search(r'```json\s*(.*?)```', text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1).strip())
                if key in d:
                    return d
            except json.JSONDecodeError:
                pass
        # Try raw JSON
        for start in range(len(text)):
            if text[start] == '{':
                depth = 0
                for end in range(start, min(start + 60000, len(text))):
                    if text[end] == '{': depth += 1
                    elif text[end] == '}': depth -= 1
                    if depth == 0:
                        try:
                            d = json.loads(text[start:end+1])
                            if key in d:
                                return d
                        except json.JSONDecodeError:
                            pass
                        break
        return None

    def _emit_file_from_result(self, result: str):
        """Extract filename from tool result and emit file event."""
        if "OK" not in result:
            return
        # Try to extract filename
        m = re.search(r'(?:文件|文档|PPT|Word|Excel|PDF)\s+(\S+)\s+已创建', result)
        if m:
            fname = m.group(1)
        else:
            m2 = re.search(r'/files/(\S+)', result)
            fname = m2.group(1) if m2 else None
        if fname:
            dl = f"/api/conversations/{self.conv_id}/files/{fname}" if self.conv_id else f"/api/files/{fname}"
            f_info = {"filename": fname, "download_url": dl, "size": 0}
            self.created_files.append(f_info)
