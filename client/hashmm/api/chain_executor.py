"""ToolChainExecutor v19 — predefined tool chains for common tasks.

Instead of letting the LLM figure out tool order every time (12+ steps),
detect common patterns and execute pre-built chains (2-3 steps).

Falls back to full Agent Loop for novel/complex tasks.

Chains:
  A: write_python — create_file → execute (import-based)
  B: write_cpp — create_file → compile+run wrapper
  C: edit_file — read → str_replace → verify
  D: make_pptx — [kb_search] → create_pptx_from_plan
  E: make_docx — [kb_search] → create_document
  F: answer — [kb_search] → text response (no tools needed)
"""
from __future__ import annotations
import ast
import json, re, time
from typing import Generator, Any
try:
    from .handlers.base import SSEEvent
except ImportError:
    from handlers.base import SSEEvent
from hashmm.api.tool_result import parse_tool_result


class ToolChainExecutor:
    """Execute predefined tool chains for common tasks."""

    def __init__(self, tool_exec_fn: Any, conv_id: str):
        self.tool_exec = tool_exec_fn
        self.conv_id = conv_id
        self.created_files: list[dict] = []
        self.steps: list[dict] = []

    def detect_chain(self, task_type: str, output_format: str) -> str | None:
        """Detect if a predefined chain fits. Returns chain name or None."""
        # v28: Only code tasks use chains. Doc tasks go through Agent Loop
        # (Agent Loop shows thinking + tool cards + can self-correct)
        mapping = {
            ("code_task", "python_file"): "write_python",
            ("code_task", "cpp_file"): "write_cpp",
            ("code_task", "java_file"): "write_java",
            ("code_task", "js_file"): "write_js",
            ("code_task", "javascript_file"): "write_js",
            # PPT/Word/Excel → Agent Loop (not chain) for better UX:
            # - Shows thinking process
            # - Shows tool step cards (搜索知识库 → 创建PPT)
            # - LLM can self-correct if creation fails
        }
        return mapping.get((task_type, output_format))

    def execute(self, chain: str, llm_output: str, query: str,
                kb_search_fn: Any = None) -> Generator[SSEEvent, None, None]:
        """Execute a predefined chain. Yields SSE events."""
        if chain == "write_python":
            yield from self._chain_write_python(llm_output)
        elif chain == "write_cpp":
            yield from self._chain_write_compiled(llm_output, "cpp", "g++")
        elif chain == "write_java":
            yield from self._chain_write_compiled(llm_output, "java", "javac")
        elif chain == "make_pptx":
            yield from self._chain_make_pptx(llm_output)
        elif chain == "make_docx":
            yield from self._chain_make_docx(llm_output, query)
        elif chain == "write_js":
            yield from self._chain_write_js(llm_output)
        elif chain == "make_xlsx":
            yield from self._chain_make_xlsx(llm_output, query)

    # ═══════════════════════════════════════════════════════════════
    # Chain A: Write Python → create_file → execute (import)
    # ═══════════════════════════════════════════════════════════════

    def _chain_write_python(self, llm_output: str) -> Generator[SSEEvent, None, None]:
        # Step 1: Extract code and filename
        blocks = self._extract_code_blocks(llm_output, "python")
        if not blocks:
            return

        for filename, code in blocks[:3]:
            # Step 2: Create file
            yield self._step_start("create_file", f"创建: {filename}")
            t0 = time.time()
            result = self._exec("create_file", {"filename": filename, "content": code})
            yield self._step_done("create_file", result[:150], time.time() - t0)

            if "OK" in result:
                self._track_file(filename)
                yield SSEEvent("file", {"filename": filename,
                    "download_url": f"/api/conversations/{self.conv_id}/download/{filename}"})

        # Step 3: Execute the first Python file
        main_file = blocks[0][0]
        module = main_file.replace(".py", "")

        # Build test code that imports the module
        test_code = self._build_test_code(blocks[0][1], module, main_file)

        yield self._step_start("execute_code", f"运行: {main_file}")
        t0 = time.time()
        result = self._exec("execute_code", {"code": test_code, "timeout": 15})
        dur = time.time() - t0
        normalized = parse_tool_result(result)
        status = "done" if normalized.success else "error"
        yield self._step_done("execute_code", result[:300], dur, status)

        # Legacy reviewer output is intentionally disabled; no review claim is
        # emitted here. The deterministic verification below is the source of
        # truth for this chain.
        try:
            main_code = blocks[0][1]
            if False:  # legacy reviewer output removed; deterministic verification is authoritative
                yield SSEEvent("token", {"content": "\n\n---\n**🔍 代码审查：**\n"})
                # Note: actual review requires LLM, deferred to SmartAgent integration
        except Exception as _e:

            pass  # Silenced: see logs if needed
        # Independent verification is deterministic and therefore auditable.
        # It does not claim an LLM review; it only checks Python syntax.
        try:
            ast.parse(main_code, filename=main_file)
        except SyntaxError as exc:
            yield SSEEvent("trace", {
                "node": "independent_verify",
                "status": "failed",
                "tool": "python_ast",
                "artifact": main_file,
                "checks": [{
                    "name": "syntax",
                    "status": "failed",
                    "detail": f"{exc.msg} (line {exc.lineno})",
                }],
            })
        else:
            yield SSEEvent("trace", {
                "node": "independent_verify",
                "status": "passed",
                "tool": "python_ast",
                "artifact": main_file,
                "checks": [{
                    "name": "syntax",
                    "status": "passed",
                    "detail": "AST parsed without executing the artifact",
                }],
            })
        if normalized.success:
            output = normalized.content[:500]
            yield SSEEvent("token", {"content": f"\n\n**▶ 执行结果：**\n```\n{output.strip()[:500]}\n```\n"})
        else:
            err = normalized.error or normalized.content or result[:500]
            yield SSEEvent("token", {"content": f"\n\n**执行出错：**\n```\n{err.strip()[:300]}\n```\n"})

    def _build_test_code(self, code: str, module: str, filename: str) -> str:
        """Build execution test code that imports the created file."""
        # If code has __main__ block, just run the file directly
        if "if __name__" in code:
            return f"exec(open('{filename}').read())"

        # Otherwise, import and run basic tests
        return f"""
import sys
sys.path.insert(0, '.')
from {module} import *
# print("✅ 导入成功")
"""

    # ═══════════════════════════════════════════════════════════════
    # Chain B: Write C++/Java → create_file → compile → run
    # ═══════════════════════════════════════════════════════════════

    def _chain_write_js(self, llm_output: str) -> Generator[SSEEvent, None, None]:
        """JavaScript execution via Node.js."""
        blocks = self._extract_code_blocks(llm_output, "javascript")
        if not blocks:
            blocks = self._extract_code_blocks(llm_output, "js")
        if not blocks:
            return

        filename, code_content = blocks[0]
        if not filename.endswith('.js'):
            filename = filename.rsplit('.', 1)[0] + '.js' if '.' in filename else filename + '.js'

        yield self._step_start("create_file", f"创建: {filename}")
        result = self._exec("create_file", {"filename": filename, "content": code_content})
        yield self._step_done("create_file", result[:150], 0)
        if "OK" in result:
            self._track_file(filename)
            yield SSEEvent("file", {"filename": filename,
                "download_url": f"/api/conversations/{self.conv_id}/download/{filename}"})

        # Run with Node.js
        run_code = f"""
import subprocess
r = subprocess.run(['node', '{filename}'], capture_output=True, text=True, timeout=15)
if r.returncode == 0:
    # print("✅", r.stdout[:1000])
else:
    # print("❌", r.stderr[:500])
"""
        yield self._step_start("execute_code", f"运行: node {filename}")
        t0 = __import__('time').time()
        result = self._exec("execute_code", {"code": run_code, "timeout": 20})
        yield self._step_done("execute_code", result[:300], __import__('time').time() - t0)
        normalized = parse_tool_result(result)
        if normalized.success:
            output = normalized.content[:500]
            yield SSEEvent("token", {"content": f"\n\n**▶ 执行结果：**\n```\n{output.strip()[:500]}\n```\n"})

    def _chain_write_compiled(self, llm_output: str, lang: str, compiler: str) -> Generator[SSEEvent, None, None]:
        ext = {"cpp": "cpp", "java": "java"}.get(lang, lang)
        blocks = self._extract_code_blocks(llm_output, lang)
        if not blocks:
            return

        filename, code = blocks[0]
        yield self._step_start("create_file", f"创建: {filename}")
        result = self._exec("create_file", {"filename": filename, "content": code})
        yield self._step_done("create_file", result[:150], 0)
        if "OK" in result:
            self._track_file(filename)
            yield SSEEvent("file", {"filename": filename,
                "download_url": f"/api/conversations/{self.conv_id}/download/{filename}"})

        # Compile and run via execute_code
        if lang == "cpp":
            run_code = f"""
import subprocess, os
r = subprocess.run(['{compiler}', '{filename}', '-o', 'a.out', '-lm'], capture_output=True, text=True, timeout=15)
if r.returncode != 0:
    # print("❌ 编译错误:", r.stderr[:500])
else:
    r2 = subprocess.run(['./a.out'], capture_output=True, text=True, timeout=15)
    # print("✅", r2.stdout[:1000])
    if r2.stderr: print("stderr:", r2.stderr[:300])
"""
        elif lang == "java":
            class_name = re.search(r'public\s+class\s+(\w+)', code)
            cn = class_name.group(1) if class_name else "Main"
            run_code = f"""
import subprocess
r = subprocess.run(['javac', '{filename}'], capture_output=True, text=True, timeout=15)
if r.returncode != 0:
    # print("❌ 编译错误:", r.stderr[:500])
else:
    r2 = subprocess.run(['java', '{cn}'], capture_output=True, text=True, timeout=15)
    # print("✅", r2.stdout[:1000])
"""
        else:
            return

        yield self._step_start("execute_code", f"编译运行: {filename}")
        t0 = time.time()
        result = self._exec("execute_code", {"code": run_code, "timeout": 30})
        yield self._step_done("execute_code", result[:300], time.time() - t0)
        normalized = parse_tool_result(result)
        if normalized.success:
            output = normalized.content[:500]
            yield SSEEvent("token", {"content": f"\n\n**▶ 执行结果：**\n```\n{output.strip()[:500]}\n```\n"})

    # ═══════════════════════════════════════════════════════════════
    # Chain D/E/F: Document creation
    # ═══════════════════════════════════════════════════════════════

    def _chain_make_pptx(self, llm_output: str) -> Generator[SSEEvent, None, None]:
        plan = self._extract_json(llm_output, "slides")
        if not plan:
            yield SSEEvent("token", {"content": "\n⚠️ PPT 计划解析失败，请重试。\n"})
            return

        yield self._step_start("create_pptx_from_plan", "创建 PPT")
        t0 = time.time()
        result = self._exec("create_pptx_from_plan", {"plan": plan, "conv_id": self.conv_id})
        yield self._step_done("create_pptx_from_plan", result[:200], time.time() - t0)

        fname = self._extract_fname_from_result(result)
        if fname:
            self._track_file(fname)
            yield SSEEvent("file", {"filename": fname,
                "download_url": f"/api/conversations/{self.conv_id}/download/{fname}"})

        # Generate summary
        if plan and "slides" in plan:
            n = len(plan["slides"])
            secs = [s.get("title", "") for s in plan["slides"] if s.get("role") == "section"]
            tables = sum(1 for s in plan["slides"] if s.get("role") == "table")
            summary = f"\n\n已创建 **{n} 页** PPT"
            if secs:
                summary += f"，包含 {len(secs)} 个章节：{'、'.join(secs[:5])}"
            if tables:
                summary += f"，含 {tables} 个数据表格"
            summary += "。"
            yield SSEEvent("token", {"content": summary})

    def _chain_make_docx(self, llm_output: str, query: str) -> Generator[SSEEvent, None, None]:
        title = re.sub(r'^(帮我|给我|请|写|做|制作|创建|生成)(一个|一份)?', '', query).strip()[:50] or "文档"
        title = re.sub(r'(的)?(Word|word|文档|报告|方案)', '', title).strip() or "文档"
        fname = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', title)[:30] + ".docx"

        yield self._step_start("create_document", f"创建 Word: {fname}")
        t0 = time.time()
        result = self._exec("create_document", {
            "filename": fname, "content": llm_output, "title": title
        })
        yield self._step_done("create_document", result[:200], time.time() - t0)

        if "OK" in result:
            self._track_file(fname)
            yield SSEEvent("file", {"filename": fname,
                "download_url": f"/api/conversations/{self.conv_id}/download/{fname}"})
            sections = re.findall(r'^##?\s+(.+)', llm_output, re.MULTILINE)
            summary = f"\n\n已创建 Word 文档 **{title}**，约 {len(llm_output)} 字"
            if sections:
                summary += f"，{len(sections)} 个章节"
            summary += "。"
            yield SSEEvent("token", {"content": summary})

    def _chain_make_xlsx(self, llm_output: str, query: str) -> Generator[SSEEvent, None, None]:
        title = re.sub(r'^(帮我|给我)?(做|生成|创建)?', '', query).strip()[:30] or "表格"
        yield self._step_start("create_xlsx", f"创建 Excel")
        t0 = time.time()
        result = self._exec("create_xlsx", {"data": llm_output, "title": title})
        yield self._step_done("create_xlsx", result[:200], time.time() - t0)
        fname = self._extract_fname_from_result(result)
        if fname:
            self._track_file(fname)
            yield SSEEvent("file", {"filename": fname,
                "download_url": f"/api/conversations/{self.conv_id}/download/{fname}"})

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _exec(self, tool: str, args: dict) -> str:
        if not self.tool_exec:
            return "Error: tool executor not available"
        return self.tool_exec(tool, args, {"session_id": self.conv_id, "conv_id": self.conv_id})

    def _step_start(self, tool: str, detail: str) -> SSEEvent:
        return SSEEvent("step_start", {"tool": tool, "detail": detail, "status": "running"})

    def _step_done(self, tool: str, detail: str, dur: float, status: str = "done") -> SSEEvent:
        dur_ms = round(dur * 1000)
        step = {"tool": tool, "status": status, "detail": detail, "duration_ms": dur_ms}
        self.steps.append(step)
        return SSEEvent("step_done", step)

    def _track_file(self, fname: str):
        if fname not in [f.get("filename") for f in self.created_files]:
            self.created_files.append({"filename": fname,
                "download_url": f"/api/conversations/{self.conv_id}/download/{fname}"})

    def _extract_code_blocks(self, text: str, lang: str) -> list[tuple[str, str]]:
        blocks = []
        for m in re.finditer(r'```(?:' + lang + r'|' + lang[:2] + r')\n(.*?)```', text, re.DOTALL):
            code = m.group(1).strip()
            if len(code.splitlines()) < 3:
                continue
            fname = self._detect_filename(code, lang)
            blocks.append((fname, code))
        return blocks

    def _detect_filename(self, code: str, lang: str) -> str:
        first = code.split('\n')[0]
        m = re.match(r'[#/]+\s*filename:\s*(\S+)', first, re.IGNORECASE)
        if m:
            return m.group(1)
        ext = {"python": ".py", "cpp": ".cpp", "java": ".java", "c": ".c"}.get(lang, f".{lang}")
        # Try class name
        cm = re.search(r'class\s+(\w+)', code)
        if cm:
            name = re.sub(r'([A-Z])', r'_\1', cm.group(1)).strip('_').lower()
            return name + ext
        return f"main{ext}"

    def _extract_json(self, text: str, key: str) -> dict | None:
        m = re.search(r'```json\s*(.*?)```', text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1).strip())
                if key in d: return d
            except Exception: pass
        for i in range(len(text)):
            if text[i] == '{':
                depth = 0
                for j in range(i, min(i + 60000, len(text))):
                    if text[j] == '{': depth += 1
                    elif text[j] == '}': depth -= 1
                    if depth == 0:
                        try:
                            d = json.loads(text[i:j+1])
                            if key in d: return d
                        except Exception: pass
                        break
        return None

    def _extract_fname_from_result(self, result: str) -> str | None:
        m = re.search(r'(?:文件|文档|PPT|Word|Excel)\s+(\S+)\s+已创建', result)
        return m.group(1) if m else None


# ═══════════════════════════════════════════════════════════════════
# v27: Tool Pipeline — chain tools with auto data passing
# ═══════════════════════════════════════════════════════════════════

class ToolPipeline:
    """Chain tools: output of tool A auto-feeds into tool B.
    Reduces LLM calls by pre-defining data flow.
    """

    PIPELINES = {
        "analyze_and_report": {
            "description": "读取数据 → 分析 → 生成报告",
            "steps": ["read_file", "execute_code", "create_document"],
        },
        "code_and_test": {
            "description": "创建代码 → 运行 → 生成测试",
            "steps": ["create_file", "execute_code", "create_file"],
        },
        "search_and_present": {
            "description": "搜索知识库 → 生成 PPT",
            "steps": ["kb_search", "create_pptx_from_plan"],
        },
    }

    def __init__(self, tool_exec_fn, conv_id: str):
        self.tool_exec = tool_exec_fn
        self.conv_id = conv_id

    def detect_pipeline(self, query: str) -> str | None:
        """Detect if a pipeline fits the query."""
        q = query.lower()
        if ("分析" in q or "analyze" in q) and ("报告" in q or "report" in q):
            return "analyze_and_report"
        if ("代码" in q or "写" in q) and ("测试" in q or "test" in q):
            return "code_and_test"
        if ("搜索" in q or "检索" in q) and ("ppt" in q or "演示" in q):
            return "search_and_present"
        return None

    def get_pipeline_info(self, name: str) -> dict | None:
        return self.PIPELINES.get(name)
