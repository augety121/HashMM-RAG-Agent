"""Legacy tool wrapper functions.

These are used by the old generate() pipeline.
SmartAgent uses tool_registry directly instead.
"""
from __future__ import annotations
import os
import sys
import re                # V308 修 F821：re/json/time 在多个函数体内使用却从未 import
import json              # （server.py 仍 import 本模块的 agent_plan 等，调用即 NameError 崩）
import time
import subprocess
import tempfile
import zipfile
from pathlib import Path
from hashmm.api import app_state
from hashmm.api import database as db
from hashmm.utils import get_logger, log_suppressed   # V308: log_suppressed 亦被用到

logger = get_logger("hashmm.tools")


# V308 修 F821 真 bug：agent_plan 的 prompt 里 f-string 引用了 TOOLS_DESC，但本模块从未
# 定义它（同名常量在 evaluation/benchmarks/tool_calling.py，工具集完全不同，也没被 import）。
# 结果：server.py 一 import 本模块的 agent_plan，调用即 NameError 崩溃。
# 这里按 agent_plan 自己 Rules 里列出的工具补上正确定义。
TOOLS_DESC = (
    "可用工具：\n"
    "- kb_search(query)：检索知识库，回答涉及文献/概念/数据的问题。\n"
    "- analyze(...)：对检索到的内容做分析、对比、归纳。\n"
    "- code_generate(...)：生成代码。\n"
    "- file_create(...)：把内容写成文件。\n"
    "- direct_answer()：无需工具，直接回答。"
)


def _get_llm_fn():
    """V308 修 F821：_llm_fn 全局从未赋值。这些遗留函数应通过 app_state 动态取 LLM，
    未配置时返回 None（调用方已有 `if not _llm_fn` 分支处理）。"""
    return getattr(app_state, "llm_fn", None)

def agent_plan(query: str, has_file: bool = False) -> list[dict]:
    """LLM-based task planner: decides which tools to use and in what order."""
    if not _get_llm_fn():
        return [{"tool": "direct_answer", "reason": "LLM not configured"}]
    if has_file:
        return [{"tool": "analyze", "reason": "分析上传文件"}]
    try:
        r = app_state.llm_fn(f"""You are a task planning agent. Given the user's request, decide which tools to use.
{TOOLS_DESC}

Rules:
- For knowledge questions: use kb_search then analyze
- For "write code" / "generate .py file" requests: use code_generate, then file_create
- For comparisons: use kb_search then analyze
- For simple chat: use direct_answer
- You can chain multiple tools

Return ONLY a JSON array like: [{{"tool":"kb_search","reason":"..."}},{{"tool":"analyze","reason":"..."}}]

User request: {query[:300]}
Plan:""")
        # Parse JSON from response
        import re as _re
        match = _re.search(r'\[.*\]', r, _re.DOTALL)
        if match:
            plan = json.loads(match.group())
            if isinstance(plan, list) and len(plan) > 0:
                return plan[:5]  # Max 5 steps
    except Exception as _e:

        pass  # Silenced: see logs if needed
    code_signals = ["代码", "code", "写一个", "实现", ".py", "函数", "class", "脚本", "文件"]
    if any(s in query.lower() for s in code_signals):
        return [{"tool": "code_generate", "reason": "用户请求生成代码"},
                {"tool": "file_create", "reason": "创建可下载文件"}]
    return [{"tool": "kb_search", "reason": "检索知识库"},
            {"tool": "analyze", "reason": "综合分析"}]


def tool_code_generate(query: str, context: str = "") -> dict:
    """Generate code based on user request."""
    if not _get_llm_fn():
        return {"code": "", "language": "python", "error": "LLM not configured"}
    try:
        prompt = (f"你是一个高级编程助手。根据用户需求生成完整、可运行的代码。\n"
                  f"输出格式：先简要说明，然后用 ```language 包裹完整代码。\n"
                  f"{'参考资料：\n' + context[:2000] + chr(10) if context else ''}"
                  f"用户需求：{query}\n\n")
        result = app_state.llm_fn(prompt)
        return {"content": result, "language": "python"}
    except Exception as e:
        return {"content": f"代码生成失败：{e}", "language": "text"}


def tool_file_create(filename: str, content: str) -> dict:
    """Create a downloadable file and return its path."""
    files_dir = Path("data/files")
    files_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^\w\u4e00-\u9fff._-]', '_', filename)
    if not safe_name:
        safe_name = f"output_{int(time.time())}.txt"
    filepath = files_dir / safe_name
    filepath.write_text(content, encoding="utf-8")
    return {"filename": safe_name, "path": str(filepath), "size": len(content),
            "download_url": f"/api/files/{safe_name}"}

def tool_create_pptx(title: str, slides: list[dict]) -> dict:
    """Create a real .pptx file from slide data.
    Each slide: {"title": str, "content": str, "bullets": list[str]}
    """
    files_dir = Path("data/files")
    files_dir.mkdir(parents=True, exist_ok=True)
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.enum.text import PP_ALIGN

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        for i, slide_data in enumerate(slides):
            if i == 0:
                layout = prs.slide_layouts[0]  # Title slide
            else:
                layout = prs.slide_layouts[1]  # Title + content
            slide = prs.slides.add_slide(layout)

            # Title
            if slide.shapes.title:
                slide.shapes.title.text = slide_data.get("title", f"Slide {i+1}")

            # Content
            content = slide_data.get("content", "")
            bullets = slide_data.get("bullets", [])
            if len(slide.placeholders) > 1:
                tf = slide.placeholders[1].text_frame
                tf.text = content
                for b in bullets:
                    p = tf.add_paragraph()
                    p.text = b
                    p.level = 0

        fname = re.sub(r'[^\w\u4e00-\u9fff]', '_', title[:30]) + ".pptx"
        fpath = files_dir / fname
        prs.save(str(fpath))
        return {"filename": fname, "path": str(fpath), "size": fpath.stat().st_size,
                "download_url": f"/api/files/{fname}"}
    except ImportError:
        # Fallback: create markdown file
        md = f"# {title}\n\n"
        for s in slides:
            md += f"---\n## {s.get('title','')}\n{s.get('content','')}\n"
            for b in s.get("bullets", []): md += f"- {b}\n"
            md += "\n"
        return tool_file_create(re.sub(r'[^\w]', '_', title[:30]) + ".md", md)

def tool_create_zip(zip_name: str, files: list[dict]) -> dict:
    """Create a ZIP file containing multiple files."""
    import zipfile
    files_dir = Path("data/files")
    files_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^\w\u4e00-\u9fff._-]', '_', zip_name)
    if not safe_name.endswith(".zip"): safe_name += ".zip"
    fpath = files_dir / safe_name
    with zipfile.ZipFile(str(fpath), 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f["name"], f["content"])
    return {"filename": safe_name, "path": str(fpath), "size": fpath.stat().st_size,
            "download_url": f"/api/files/{safe_name}"}

def tool_execute_code(code: str, language: str = "python", timeout: int = 15) -> dict:
    """Execute code in a sandboxed subprocess and return the output.
    This is like Claude Code's code execution capability.
    """
    import subprocess, tempfile
    if language != "python":
        return {"success": False, "output": f"暂只支持 Python 执行，不支持 {language}", "error": "unsupported"}

    # Safety checks
    dangerous_patterns = ["os.system", "subprocess", "shutil.rmtree", "rm -rf",
                          "__import__('os')", "eval(", "exec(", "open('/etc",
                          "import socket", "import http"]
    for p in dangerous_patterns:
        if p in code:
            return {"success": False, "output": f"安全限制：代码包含不允许的操作 ({p})", "error": "blocked"}

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True, text=True, timeout=timeout,
            env={**dict(__import__('os').environ), "PYTHONDONTWRITEBYTECODE": "1"}
        )

        output = ""
        if result.stdout: output += result.stdout[:3000]
        if result.stderr: output += "\n[STDERR]\n" + result.stderr[:1500]

        Path(tmp_path).unlink(missing_ok=True)

        return {
            "success": result.returncode == 0,
            "output": output.strip() or "(无输出)",
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        Path(tmp_path).unlink(missing_ok=True)
        return {"success": False, "output": f"执行超时（{timeout}秒限制）", "error": "timeout"}
    except Exception as e:
        return {"success": False, "output": f"执行失败：{repr(e)}", "error": str(e)}

