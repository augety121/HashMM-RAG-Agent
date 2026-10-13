"""CodeHandler v12 — multi-file projects + chart rendering + multi-language.

Features:
  - Single file: generate → create → execute → verify
  - Multi-file project: generate all files → create each → execute main → verify
  - Chart injection: plt.show() → plt.savefig() for inline rendering
  - Multi-language: Python direct, C/C++/Java compile+run
  - Auto-retry on execution failure (1 attempt)
"""
from __future__ import annotations
import re, time, json
from typing import Generator
from .base import BaseHandler, SSEEvent
from hashmm.api.tool_result import parse_tool_result


_PROJECT_SIGNALS = {"项目", "project", "完整", "框架", "scaffold", "系统", "full",
                    "多文件", "multi-file", "package", "模块化"}


class CodeHandler(BaseHandler):

    def run(self) -> Generator[SSEEvent, None, None]:
        q = self.query.strip()

        # Detect if multi-file project is needed
        is_project = any(s in q.lower() for s in _PROJECT_SIGNALS)

        # ── Optional KB search (academic topics) ──
        search_result = ""
        academic_signals = ["论文", "paper", "方法", "算法", "模型", "哈希", "hash",
                           "transformer", "attention", "损失函数", "loss"]
        if any(s in q.lower() for s in academic_signals) and self.kb_search_fn:
            yield self.emit_progress("search", 10, "检索相关知识...")
            yield self.emit_step_start("kb_search", f"搜索: {q[:50]}")
            self.heartbeat.start()
            t0 = time.time()
            try:
                search_result = self.kb_search_fn({"query": q, "top_k": 3}, {})
            except Exception:
                search_result = ""
            dur = round((time.time() - t0) * 1000)
            self.heartbeat.stop()
            for ev in self.heartbeat.drain():
                yield ev
            yield self.emit_step_done("kb_search", search_result[:150], dur)

        # ── Generate code ──
        sys_key = "project" if is_project else "python_file"
        # Detect language from intent or query
        for lang, kws in [("cpp_file", ["c++", "cpp"]), ("java_file", ["java"]),
                          ("generic_code", ["matlab", "rust", "golang", "go ", "javascript", "typescript"])]:
            if any(k in q.lower() for k in kws):
                sys_key = lang
                break

        yield self.emit_progress("generate", 30, "生成代码中...")

        from hashmm.api.prompts import get_system_prompt
        sys_prompt = get_system_prompt(sys_key, self.custom_prompt, self.profile_ctx)
        messages = [{"role": "system", "content": sys_prompt}]

        for h in self.history[-6:]:
            role = "user" if h["role"] == "user" else "assistant"
            c = h.get("content", "")[:500]
            if c.strip():
                messages.append({"role": role, "content": c})

        if search_result and "未找到" not in search_result:
            messages.append({"role": "system", "content": f"参考资料：\n{search_result[:4000]}"})

        parts = []
        if self.file_context:
            parts.append("用户上传文件内容：\n" + self.file_context[:5000])
        parts.append(q)
        messages.append({"role": "user", "content": "\n\n".join(parts)})

        # Stream LLM output to user (code IS the response)
        full_text = ""
        for event in self.stream_llm(messages):
            yield event
            if event.event == "token":
                full_text += event.data.get("content", "")

        # ── Extract and create files ──
        yield self.emit_progress("extract", 70, "创建文件...")

        if is_project:
            files = self._parse_project_files(full_text)
        else:
            files = self._extract_code_blocks(full_text)

        created = []
        for fname, lang, code in files:
            if len(code.strip()) < 15:
                continue
            if self.tool_exec_fn:
                yield self.emit_step_start("create_file", f"创建: {fname}")
                t0 = time.time()
                result = self.tool_exec_fn("create_file", {
                    "filename": fname, "content": code
                }, {"session_id": self.conv_id})
                dur = round((time.time() - t0) * 1000)
                yield self.emit_step_done("create_file", result[:100], dur)
                if "OK" in result:
                    dl = f"/api/conversations/{self.conv_id}/files/{fname}"
                    self.created_files.append({"filename": fname, "download_url": dl})
                    yield SSEEvent("file", {"filename": fname, "download_url": dl})
                    created.append((fname, lang, code))

        # ── Execute ──
        for fname, lang, code in created:
            can_exec, exec_code = self._prepare_execution(fname, lang, code)
            if not can_exec:
                continue

            yield self.emit_progress("execute", 85, f"执行 {fname}...")
            yield self.emit_step_start("execute_code", f"运行: {fname}")
            self.heartbeat.start()
            t0 = time.time()
            result = self.tool_exec_fn("execute_code", {"code": exec_code, "timeout": 15}, {})
            dur = round((time.time() - t0) * 1000)
            self.heartbeat.stop()
            for ev in self.heartbeat.drain():
                yield ev

            normalized = parse_tool_result(result)
            status = "done" if normalized.success else "error"
            yield self.emit_step_done("execute_code", result[:300], dur, status)

            # Show result inline
            if normalized.success:
                output = normalized.content[:500]
                yield self.emit_token(f"\n\n**▶ 执行结果：**\n```\n{output.strip()[:500]}\n```\n")
                # Check for saved charts
                yield from self._check_charts(output)
            else:
                err = normalized.error or normalized.content or result[:500]
                yield self.emit_token(f"\n\n**执行出错：**\n```\n{err.strip()[:500]}\n```\n")
                # Auto-retry (Python only)
                if lang == "python":
                    yield from self._auto_fix(code, fname, result)

            break  # Execute first runnable file only

        # ── v12: Auto-generate tests for Python classes/functions ──
        python_files = [(f, c) for f, l, c in created if l == "python" and ("class " in c or "def " in c)]
        if python_files and self.llm_fn and hasattr(self.llm_fn, 'stream') and not is_project:
            main_file, main_code = python_files[0]
            if "class " in main_code and len(main_code.splitlines()) > 20:
                yield from self._generate_tests(main_file, main_code)

        yield self.emit_progress("done", 100, "完成")
        yield self.emit_done(intent="code_task")

    # ═══════════════════════════════════════════════════════════════
    # Multi-file project parsing
    # ═══════════════════════════════════════════════════════════════

    def _parse_project_files(self, text: str) -> list[tuple[str, str, str]]:
        """Parse === FILE: xxx === format for multi-file projects."""
        files = []
        parts = re.split(r'===\s*FILE:\s*(\S+)\s*===', text)
        for i in range(1, len(parts), 2):
            fname = parts[i].strip()
            content = parts[i + 1] if i + 1 < len(parts) else ""
            # Extract code from markdown block
            m = re.search(r'```\w*\n(.*?)```', content, re.DOTALL)
            code = m.group(1).strip() if m else content.strip()
            if code:
                lang = self._detect_lang(fname)
                files.append((fname, lang, code))

        # If no === FILE: === markers found, fall back to code block extraction
        if not files:
            files = self._extract_code_blocks(text)

        return files[:15]  # Max 15 files

    # ═══════════════════════════════════════════════════════════════
    # Single-file code block extraction
    # ═══════════════════════════════════════════════════════════════

    def _extract_code_blocks(self, text: str) -> list[tuple[str, str, str]]:
        """Extract (filename, language, code) from markdown code blocks."""
        blocks = []
        for m in re.finditer(r'```(\w+)\n(.*?)```', text, re.DOTALL):
            lang = m.group(1).lower()
            code = m.group(2).strip()
            if lang in ("text", "txt", "log", "output", "json", "markdown", "md"):
                continue
            if len(code.splitlines()) < 3:
                continue
            fname = self._detect_filename(code, lang, text, m.start())
            blocks.append((fname, lang, code))
        return blocks[:5]

    def _detect_filename(self, code: str, lang: str, full_text: str, pos: int) -> str:
        first_line = code.split("\n")[0] if code else ""
        m = re.match(r'[#/]+\s*(?:filename|file):\s*(\S+)', first_line, re.IGNORECASE)
        if m:
            return m.group(1)
        ext = self._lang_ext(lang)
        class_m = re.search(r'class\s+(\w+)', code)
        if class_m:
            name = re.sub(r'([A-Z])', r'_\1', class_m.group(1)).strip('_').lower()
            return name + ext
        func_m = re.search(r'(?:def|function|func)\s+(\w+)', code)
        if func_m and func_m.group(1) not in ("main", "__init__", "test"):
            return func_m.group(1) + ext
        context = full_text[max(0, pos - 200):pos]
        file_m = re.search(r'[`*](\w[\w.]+\.\w+)[`*]', context)
        if file_m:
            return file_m.group(1)
        return f"code{ext}"

    # ═══════════════════════════════════════════════════════════════
    # Execution preparation (multi-language + chart injection)
    # ═══════════════════════════════════════════════════════════════

    def _prepare_execution(self, fname: str, lang: str, code: str) -> tuple[bool, str]:
        """Prepare code for execution. Returns (can_execute, execution_code)."""
        if lang == "python":
            # Inject chart saving for matplotlib
            code = self._inject_chart_save(code)
            return True, code
        elif lang in ("cpp", "c"):
            compiler = "g++" if lang == "cpp" else "gcc"
            ext = "cpp" if lang == "cpp" else "c"
            wrapper = f'''import subprocess, tempfile, os
src = """{code.replace(chr(92), chr(92)*2).replace('"""', "'''") }"""
with tempfile.NamedTemporaryFile(suffix='.{ext}', mode='w', delete=False) as f:
    f.write(src); f.flush()
    out = f.name.replace('.{ext}', '')
    r = subprocess.run(['{compiler}', f.name, '-o', out, '-lm'], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        print("编译错误:", r.stderr[:500])
    else:
        r2 = subprocess.run([out], capture_output=True, text=True, timeout=15)
        print(r2.stdout[:1000])
        if r2.stderr: print("stderr:", r2.stderr[:300])
    os.unlink(f.name)
    if os.path.exists(out): os.unlink(out)
'''
            return True, wrapper
        elif lang == "java":
            class_m = re.search(r'public\s+class\s+(\w+)', code)
            cn = class_m.group(1) if class_m else "Main"
            wrapper = f'''import subprocess, tempfile, os, shutil
src = """{code.replace(chr(92), chr(92)*2).replace('"""', "'''") }"""
td = tempfile.mkdtemp()
fp = os.path.join(td, '{cn}.java')
with open(fp, 'w') as f: f.write(src)
r = subprocess.run(['javac', fp], capture_output=True, text=True, timeout=15)
if r.returncode != 0:
    print("编译错误:", r.stderr[:500])
else:
    r2 = subprocess.run(['java', '-cp', td, '{cn}'], capture_output=True, text=True, timeout=15)
    print(r2.stdout[:1000])
    if r2.stderr: print("stderr:", r2.stderr[:300])
shutil.rmtree(td, ignore_errors=True)
'''
            return True, wrapper
        return False, code

    def _inject_chart_save(self, code: str) -> str:
        """Replace plt.show() with plt.savefig() for inline chart rendering."""
        if "plt.show()" not in code:
            return code
        save_dir = f"data/conversations/{self.conv_id}"
        chart_name = f"chart_{int(time.time())}.png"
        save_path = f"{save_dir}/{chart_name}"
        code = code.replace(
            "plt.show()",
            f"import os; os.makedirs('{save_dir}', exist_ok=True)\n"
            f"plt.savefig('{save_path}', dpi=150, bbox_inches='tight', facecolor='white')\n"
            f"print('CHART_SAVED:{save_path}')"
        )
        return code

    def _check_charts(self, output: str) -> Generator[SSEEvent, None, None]:
        """Check if execution produced chart files."""
        for m in re.finditer(r'CHART_SAVED:(\S+)', output):
            chart_path = m.group(1)
            fname = chart_path.split("/")[-1]
            dl = f"/api/conversations/{self.conv_id}/files/{fname}"
            self.created_files.append({"filename": fname, "download_url": dl})
            yield SSEEvent("file", {"filename": fname, "download_url": dl, "type": "image"})

    # ═══════════════════════════════════════════════════════════════
    # Auto-fix on failure
    # ═══════════════════════════════════════════════════════════════

    def _auto_fix(self, code: str, fname: str, error: str) -> Generator[SSEEvent, None, None]:
        """Try to fix Python code that failed execution."""
        if not self.llm_fn or not hasattr(self.llm_fn, 'stream'):
            return
        yield self.emit_progress("fix", 92, "自动修复中...")
        fix_msgs = [
            {"role": "system", "content": "修复以下 Python 代码的错误。只输出修复后的完整代码（用 ```python 包裹）。不要解释。"},
            {"role": "user", "content": f"代码:\n```python\n{code}\n```\n\n错误:\n{error[:1000]}"}
        ]
        fix_text = ""
        for event in self.stream_llm(fix_msgs):
            if event.event == "token":
                fix_text += event.data.get("content", "")

        # Extract fixed code
        fm = re.search(r'```python\n(.*?)```', fix_text, re.DOTALL)
        if fm:
            fixed = fm.group(1).strip()
            fixed = self._inject_chart_save(fixed)
            self.tool_exec_fn("create_file", {"filename": fname, "content": fixed}, {"session_id": self.conv_id})
            result2 = self.tool_exec_fn("execute_code", {"code": fixed, "timeout": 15}, {})
            fixed_result = parse_tool_result(result2)
            if fixed_result.success:
                out = fixed_result.content[:300]
                yield self.emit_token(f"\n**修复成功：**\n```\n{out.strip()[:300]}\n```\n")
                yield from self._check_charts(result2)
            else:
                yield self.emit_step_done("auto_fix", "修复后仍然失败", 0, "error")

    # ═══════════════════════════════════════════════════════════════
    # v12: Auto-test generation
    # ═══════════════════════════════════════════════════════════════

    def _generate_tests(self, source_file: str, source_code: str) -> Generator[SSEEvent, None, None]:
        """Auto-generate pytest tests for a Python module."""
        yield self.emit_progress("test", 92, "生成测试代码...")

        test_prompt = f"""为以下 Python 代码生成 pytest 测试。
要求：
- 文件名: test_{source_file}
- 覆盖所有公共方法
- 包含正常输入和边界值测试
- 用 assert 断言
- 只输出代码（```python 包裹）

代码:
```python
{source_code[:5000]}
```"""

        test_text = ""
        test_msgs = [
            {"role": "system", "content": "你是测试工程师。只输出 pytest 测试代码，不要解释。"},
            {"role": "user", "content": test_prompt}
        ]
        for event in self.stream_llm(test_msgs):
            if event.event == "token":
                test_text += event.data.get("content", "")

        # Extract test code
        m = re.search(r'```python\n(.*?)```', test_text, re.DOTALL)
        if m and self.tool_exec_fn:
            test_code = m.group(1).strip()
            test_fname = f"test_{source_file}"
            self.tool_exec_fn("create_file", {
                "filename": test_fname, "content": test_code
            }, {"session_id": self.conv_id})

            dl = f"/api/conversations/{self.conv_id}/files/{test_fname}"
            self.created_files.append({"filename": test_fname, "download_url": dl})
            yield SSEEvent("file", {"filename": test_fname, "download_url": dl})
            yield self.emit_token(f"\n\n**🧪 已自动生成测试：** `{test_fname}`\n")

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _detect_lang(self, fname: str) -> str:
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        return {"py": "python", "js": "javascript", "ts": "typescript",
                "java": "java", "cpp": "cpp", "c": "c", "go": "go",
                "rs": "rust", "m": "matlab", "r": "r", "rb": "ruby",
                "php": "php", "sh": "bash", "kt": "kotlin"}.get(ext, ext)

    def _lang_ext(self, lang: str) -> str:
        return {"python": ".py", "javascript": ".js", "typescript": ".ts",
                "java": ".java", "cpp": ".cpp", "c": ".c", "go": ".go",
                "rust": ".rs", "matlab": ".m", "r": ".R", "ruby": ".rb",
                "php": ".php", "bash": ".sh", "kotlin": ".kt",
                "csharp": ".cs", "swift": ".swift", "scala": ".scala",
                "perl": ".pl", "lua": ".lua", "sql": ".sql"}.get(lang, f".{lang}" if lang else ".py")
