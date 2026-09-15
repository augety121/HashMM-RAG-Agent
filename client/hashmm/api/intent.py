"""Intent Classifier v11.1 — LLM-based with rule fallback.

Two-layer classification:
  Layer 1: Rule fast-path for obvious cases (0 tokens, 0ms)
  Layer 2: LLM semantic understanding (~600 tokens, ~1s)
  Layer 3: Rule fallback if LLM fails

Also includes ProjectContext for workspace awareness.
"""
from __future__ import annotations
import json, re, time
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Intent Classification
# ═══════════════════════════════════════════════════════════════════

INTENT_SYSTEM = """你是意图分类器。分析用户消息，只返回 JSON（不要其他任何内容）：
{"task":"code|document|chat|data|modify","output":"code_file|pptx|docx|xlsx|pdf|md|text|chart","language":"python|java|cpp|c|matlab|javascript|typescript|go|rust|shell|r|none","needs_search":true,"needs_clarification":false,"summary":"一句话描述","needs_clarification":false}

分类规则：
- 写代码/实现算法/脚本 → task=code, output=code_file
- 做PPT/幻灯片/slides/演示 → task=document, output=pptx
- 做Word/写报告/写方案/写文档 → task=document, output=docx
- 做Excel/做表格 → task=document, output=xlsx
- 转PDF/做PDF → task=document, output=pdf
- 知识问答/解释/什么是/对比/compare → task=chat, output=text
- 分析数据/画图/可视化 → task=data, output=chart
- 修改代码/改一下/优化/fix → task=modify, output=code_file
- 闲聊/你好/谢谢 → task=chat, output=text

关键区分（需要追问的情况）：
- "帮我写一个管理系统" → needs_clarification=true（不知道语言/框架/功能）
- "写个爬虫" → needs_clarification=false（默认Python就好）
- "帮我做个项目" → needs_clarification=true（太模糊）

关键区分：
- "用Python写一个生成PPT的脚本" → task=code（要的是代码，不是PPT）
- "做一个关于Transformer的PPT" → task=document（要的是PPT文件）
- "Excel公式教程" → task=chat（要的是知识，不是Excel文件）
- "写一个文档管理系统" → task=code（"系统"暗示代码）

示例：
"帮我写一个Transformer Encoder的PyTorch实现" → {"task":"code","output":"code_file","language":"python","needs_search":false,"summary":"Transformer Encoder PyTorch实现"}
"做个PPT讲跨模态哈希" → {"task":"document","output":"pptx","language":"none","needs_search":true,"summary":"跨模态哈希PPT"}
"Compare ColPali and ColBERT" → {"task":"chat","output":"text","language":"none","needs_search":true,"summary":"ColPali vs ColBERT对比"}
"帮我写一个C++的快速排序" → {"task":"code","output":"code_file","language":"cpp","needs_search":false,"summary":"C++快速排序"}
"把这个数据做成Excel" → {"task":"document","output":"xlsx","language":"none","needs_search":false,"summary":"数据转Excel"}
"写一个爬虫抓取豆瓣电影数据" → {"task":"code","output":"code_file","language":"python","needs_search":false,"summary":"豆瓣电影爬虫"}"""


_GREETING = {"你好", "hello", "hi", "谢谢", "嗨", "hey", "早上好", "晚上好", "在吗", "你是谁"}

_FORMAT_KW = {"ppt", "幻灯片", "slides", "presentation", "演示",
              "word", "docx", "excel", "xlsx", "pdf"}

_CODE_SIGNALS = {
    "代码", "code", "实现", "implement", ".py", ".js", ".java", ".cpp",
    "函数", "class", "脚本", "script", "编程", "算法",
    "python", "javascript", "typescript", "java", "c++", "cpp",
    "pytorch", "tensorflow", "numpy", "pandas", "matplotlib",
    "golang", "rust", "matlab", "排序", "sort", "api", "爬虫",
}


def classify_rules(query: str, file_context: str = "") -> dict | None:
    """Layer 1: Fast rule-based classification for obvious cases.
    Returns None if ambiguous → needs LLM.
    """
    q = query.strip()
    ql = q.lower()

    # Obvious greetings
    if len(q) < 10 and any(g in q for g in _GREETING):
        return {"task": "chat", "output": "text", "language": "none",
                "needs_search": False, "summary": "闲聊"}

    # Very short
    if len(q) < 6:
        return {"task": "chat", "output": "text", "language": "none",
                "needs_search": False, "summary": "简短问题"}

    # Explicit format + no code signal → clearly a document task
    fmt_match = [f for f in _FORMAT_KW if f in ql]
    has_code = any(s in ql for s in _CODE_SIGNALS)

    if fmt_match and not has_code:
        fmt = fmt_match[0]
        output = "pptx" if fmt in ("ppt", "幻灯片", "slides", "presentation", "演示") else \
                 "xlsx" if fmt in ("excel", "xlsx") else \
                 "pdf" if fmt == "pdf" else "docx"
        return {"task": "document", "output": output, "language": "none",
                "needs_search": True, "summary": f"创建{output}文档"}

    # Pure code signal + no format keyword → clearly code
    if has_code and not fmt_match:
        # Detect language
        lang = "python"  # default
        for l, kws in [("cpp", ["c++", "cpp"]), ("java", ["java"]),
                        ("javascript", ["javascript", "js", "node"]),
                        ("typescript", ["typescript", "ts"]),
                        ("matlab", ["matlab"]), ("rust", ["rust"]),
                        ("golang", ["go ", "golang"])]:
            if any(k in ql for k in kws):
                lang = l; break
        return {"task": "code", "output": "code_file", "language": lang,
                "needs_search": False, "summary": "代码生成"}

    # Ambiguous → return None, needs LLM
    return None


def classify_fallback(query: str, file_context: str = "") -> dict:
    """Layer 3: Rule fallback when LLM fails."""
    q = query.strip()
    ql = q.lower()

    if any(f in ql for f in _FORMAT_KW):
        return {"task": "document", "output": "docx", "language": "none",
                "needs_search": True, "summary": "文档创建"}
    if any(s in ql for s in _CODE_SIGNALS):
        return {"task": "code", "output": "code_file", "language": "python",
                "needs_search": False, "summary": "代码生成"}
    if file_context:
        return {"task": "modify", "output": "code_file", "language": "python",
                "needs_search": False, "summary": "文件分析/修改"}

    return {"task": "chat", "output": "text", "language": "none",
            "needs_search": True, "summary": "知识问答"}


def parse_llm_intent(raw: str) -> dict | None:
    """Parse LLM response into intent dict."""
    # Strip markdown fences
    text = raw.strip().strip('`').strip()
    if text.startswith("json"):
        text = text[4:].strip()
    # Try direct parse
    try:
        d = json.loads(text)
        if "task" in d:
            return d
    except json.JSONDecodeError:
        pass
    # Try to find JSON in text
    m = re.search(r'\{[^{}]+\}', text)
    if m:
        try:
            d = json.loads(m.group())
            if "task" in d:
                return d
        except json.JSONDecodeError:
            pass
    return None


def intent_to_task_type(intent: dict) -> str:
    """Convert intent dict to legacy task_type string for handler dispatch."""
    task = intent.get("task", "chat")
    if task == "code":
        return "code_task"
    elif task == "document":
        return "doc_task"
    elif task in ("modify", "data", "analysis"):
        return "complex_task"
    elif task == "chat":
        output = intent.get("output", "text")
        if output == "text":
            ns = intent.get("needs_search", True)
            return "knowledge_task" if ns else "direct_task"
        return "knowledge_task"
    return "knowledge_task"


# ═══════════════════════════════════════════════════════════════════
# Project Context Manager
# ═══════════════════════════════════════════════════════════════════

class ProjectContext:
    """Workspace-aware context for multi-turn code iteration.

    Scans the conversation's file directory and builds a context prompt
    so the LLM knows what files exist and their contents.
    """

    def __init__(self, conv_id: str, files_root: Path):
        self.conv_id = conv_id
        self.files_root = files_root
        self.files: dict[str, dict] = {}  # filename → {size, content_preview}

    def scan(self) -> None:
        """Scan conversation directory for files."""
        fdir = self.files_root / self.conv_id
        if not fdir.exists():
            return
        self.files.clear()
        for f in sorted(fdir.iterdir()):
            if not f.is_file():
                continue
            sz = f.stat().st_size
            preview = ""
            if sz < 50000:
                try:
                    preview = f.read_text(encoding="utf-8", errors="replace")[:3000]
                except Exception as _e:
                    preview = f"[binary, {sz} bytes]"
            else:
                preview = f"[large file, {sz} bytes]"
            self.files[f.name] = {"size": sz, "preview": preview}

    def get_context_prompt(self, max_chars: int = 6000) -> str:
        """Build context injection for LLM system prompt."""
        if not self.files:
            return ""

        parts = [f"\n**当前工作区（{len(self.files)} 个文件）：**"]
        total = 0
        for fname, info in self.files.items():
            preview = info["preview"]
            if total + len(preview) > max_chars:
                preview = preview[:500] + "\n... (截断)"
            ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
            if ext in ("py", "js", "ts", "java", "cpp", "c", "go", "rs", "m", "r"):
                parts.append(f"\n**{fname}** ({info['size']}B):\n```{ext}\n{preview}\n```")
            else:
                parts.append(f"\n**{fname}** ({info['size']}B)")
            total += len(preview)

        return "\n".join(parts)

    def has_file(self, name: str) -> bool:
        return name in self.files

    def get_file_content(self, name: str) -> str:
        return self.files.get(name, {}).get("preview", "")


# ═══════════════════════════════════════════════════════════════════
# v28: Fast rule-based classification (0ms, no LLM needed for 80%)
# ═══════════════════════════════════════════════════════════════════

def fast_classify(query: str) -> dict | None:
    """Rule-based intent classification. Returns None if unsure (need LLM).
    
    Handles 80% of queries in 0ms without LLM call.
    """
    q = query.strip()
    ql = q.lower()
    
    # Direct chat (greetings, short messages)
    if len(q) < 6 or ql in ("你好", "hi", "hello", "hey", "谢谢", "好的", "ok"):
        return {"task": "chat", "output": "text", "confidence": 0.99}
    
    # PPT
    if any(k in ql for k in ["ppt", "幻灯片", "演示文稿", "slides"]):
        return {"task": "document", "output": "pptx", "confidence": 0.95}
    
    # Word
    if any(k in ql for k in ["word文档", "docx", "写报告", "写方案", "写文档"]):
        return {"task": "document", "output": "docx", "confidence": 0.95}
    
    # Excel
    if any(k in ql for k in ["excel", "xlsx", "表格", "spreadsheet"]):
        return {"task": "document", "output": "xlsx", "confidence": 0.90}
    
    # Code (strong signals)
    code_signals = ["写代码", "实现", "编程", "代码", "函数", "算法", "脚本",
                    ".py", ".cpp", ".java", ".js", ".ts", "python", "javascript",
                    "pytorch", "tensorflow", "import ", "class ", "def "]
    if any(k in ql for k in code_signals):
        # Detect language
        if any(k in ql for k in [".cpp", "c++", "c语言"]):
            return {"task": "code", "output": "cpp_file", "confidence": 0.90}
        if any(k in ql for k in [".java", "java"]):
            return {"task": "code", "output": "java_file", "confidence": 0.90}
        if any(k in ql for k in [".js", "javascript", "node"]):
            return {"task": "code", "output": "js_file", "confidence": 0.90}
        return {"task": "code", "output": "python_file", "confidence": 0.90}
    
    # Knowledge (question patterns)
    if q.endswith("?") or q.endswith("？") or any(k in ql for k in ["什么", "怎么", "为什么", "如何", "是否", "有哪些", "区别", "对比", "解释"]):
        return {"task": "knowledge", "output": "text", "confidence": 0.85}
    
    # Modification
    if any(k in ql for k in ["改", "修改", "修复", "fix", "优化", "重构", "更新", "调整"]):
        return {"task": "modify", "output": "text", "confidence": 0.85}
    
    return None  # Unsure — need LLM classification
