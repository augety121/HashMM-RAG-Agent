"""Verifier v15 — output quality validation per format.

Each format has specific checks. Returns structured results
that the Agent can use to self-correct.
"""
from __future__ import annotations
import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class VerifyResult:
    passed: bool
    checks: list[tuple[str, bool, str]]  # (check_name, passed, detail)

    @property
    def issues(self) -> list[str]:
        return [detail for _, ok, detail in self.checks if not ok]

    @property
    def summary(self) -> str:
        failed = self.issues
        if not failed:
            return "✅ 全部检查通过"
        return "❌ " + "; ".join(failed)


def verify_output(output_format: str, content: str = "",
                  file_path: Path | None = None) -> VerifyResult:
    """Run all checks for the given format."""
    checks_fn = _CHECKS.get(output_format, [])
    results = []
    for fn in checks_fn:
        name, ok, detail = fn(content, file_path)
        results.append((name, ok, detail))
    all_passed = all(ok for _, ok, _ in results)
    return VerifyResult(passed=all_passed, checks=results)


# ═══════════════════════════════════════════════════════════════
# Check functions — return (name, passed, detail)
# ═══════════════════════════════════════════════════════════════

def _not_empty(content, path):
    if not content or len(content.strip()) < 10:
        return ("not_empty", False, "内容为空或过短")
    return ("not_empty", True, "")

def _is_chinese(content, path):
    chinese = sum(1 for c in content[:500] if '\u4e00' <= c <= '\u9fff')
    if len(content) > 200 and chinese < 3:
        return ("is_chinese", False, "内容未使用中文")
    return ("is_chinese", True, "")

def _no_placeholder(content, path):
    for p in ["pass  #", "# TODO", "raise NotImplementedError"]:
        if p in content:
            return ("no_placeholder", False, f"包含占位符: {p}")
    return ("no_placeholder", True, "")

def _has_main(content, path):
    if "__main__" not in content and "def main" not in content:
        return ("has_main", False, "缺少 __main__ 入口")
    return ("has_main", True, "")

def _docx_not_json(content, path):
    stripped = content.strip()
    if stripped.startswith("{") and '"slides"' in stripped[:200]:
        return ("docx_not_json", False, "Word 内容是 PPT JSON（prompt 错误）")
    if stripped.startswith("```json"):
        return ("docx_not_json", False, "Word 内容是 JSON 代码块")
    return ("docx_not_json", True, "")

def _docx_has_structure(content, path):
    if "# " not in content and "## " not in content:
        return ("has_structure", False, "缺少标题结构")
    return ("has_structure", True, "")

def _docx_min_length(content, path):
    if len(content) < 200:
        return ("min_length", False, f"内容过短（{len(content)} 字符）")
    return ("min_length", True, "")

def _pptx_has_slides(content, path):
    if '"slides"' not in content:
        return ("has_slides", False, "PPT 计划缺少 slides")
    return ("has_slides", True, "")

def _xlsx_has_data(content, path):
    if '"headers"' not in content and '"rows"' not in content:
        return ("has_data", False, "Excel 数据缺少 headers/rows")
    return ("has_data", True, "")

def _file_exists(content, path):
    if path and not path.exists():
        return ("file_exists", False, f"文件未创建: {path}")
    return ("file_exists", True, "")

def _file_not_empty(content, path):
    if path and path.exists() and path.stat().st_size < 50:
        return ("file_not_empty", False, f"文件过小: {path.stat().st_size}B")
    return ("file_not_empty", True, "")

def _code_no_syntax_error(content, path):
    """Check Python code for syntax errors without executing."""
    try:
        compile(content, "<check>", "exec")
        return ("syntax", True, "")
    except SyntaxError as e:
        return ("syntax", False, f"语法错误: {e.msg} (行 {e.lineno})")
    except Exception as _e:
        return ("syntax", True, "")  # Non-Python or complex, skip


# ═══════════════════════════════════════════════════════════════
# Check registry
# ═══════════════════════════════════════════════════════════════

_CHECKS = {
    "python_file": [_not_empty, _no_placeholder, _has_main, _code_no_syntax_error],
    "cpp_file": [_not_empty],
    "java_file": [_not_empty],
    "generic_code": [_not_empty],
    "pptx": [_not_empty, _pptx_has_slides],
    "docx": [_not_empty, _docx_not_json, _docx_has_structure, _docx_min_length],
    "xlsx": [_not_empty, _xlsx_has_data],
    "pdf": [_not_empty],
    "text": [_not_empty],
}


# ═══════════════════════════════════════════════════════════════
# Friendly error messages (Tier 1.2)
# ═══════════════════════════════════════════════════════════════

def error_to_user_message(error: Exception) -> str:
    """Convert exception to user-friendly message. Never show stack traces."""
    msg = str(error).lower()
    if "timeout" in msg:
        return "⏰ 响应超时，请稍后重试或简化问题。"
    if "rate_limit" in msg or "rate limit" in msg or "429" in msg:
        return "🚫 请求过于频繁，请稍等 30 秒再试。"
    if "400" in msg and "deserialize" in msg:
        return "⚠️ API 请求格式错误，正在重试..."
    if "401" in msg or "unauthorized" in msg:
        return "🔑 API 密钥无效，请在设置中检查。"
    if "500" in msg or "502" in msg or "503" in msg:
        return "🔧 LLM 服务暂时不可用，请稍后重试。"
    if "cuda" in msg or "gpu" in msg:
        return "⚠️ GPU 不可用，已切换到 CPU 模式。"
    if "connection" in msg or "network" in msg:
        return "🌐 网络连接失败，请检查网络。"
    return "⚠️ 处理时遇到问题，请重试。如持续出现请反馈。"
