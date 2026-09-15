"""Intent classification + skill matching.

Extracted from server.py v29.
"""
from __future__ import annotations
import re
import json
from pathlib import Path
from hashmm.api import app_state
from hashmm.utils import get_logger

logger = get_logger("hashmm.intent")

_skills: list[dict] = []

def classify_intent(query):
    """Fast heuristic classification (saves 1 LLM call per request)."""
    q = query.lower()
    # Chitchat detection
    chitchat = ["你好", "hello", "hi", "谢谢", "thanks", "再见", "bye", "你是谁", "who are you"]
    if any(c in q for c in chitchat) and len(query) < 30:
        return "chitchat"
    # Compare detection
    compare = ["对比", "比较", "区别", "不同", "差异", "compare", "vs", "versus", "difference", "相比"]
    if any(c in q for c in compare):
        return "compare"
    # Academic open (general knowledge, no specific paper/method)
    open_signals = ["为什么", "原理", "概念", "定义", "background", "overview", "历史"]
    kb_signals = ["论文", "paper", "方法", "模型", "算法", "实验", "结果", "dataset", "benchmark",
                  "DCMH", "ColPali", "ColBERT", "hash", "哈希", "检索", "retrieval"]
    has_kb = any(s.lower() in q for s in kb_signals)
    has_open = any(s in q for s in open_signals) and not has_kb
    if has_open:
        return "academic_open"
    return "academic_kb"

def extract_topics(query):
    topics = []
    for p in [r'\b(DCMH|DJSRH|ColPali|ColBERT|Hash-RAG|DPSH|HashNet)\b',
              r'\b(cross-modal|hashing|retrieval|quantization|RAG|embedding)\b',
              r'(跨模态|哈希|检索|量化|向量|编码)']:
        topics.extend(re.findall(p, query, re.IGNORECASE))
    return list(set(t.lower() if isinstance(t,str) else t for t in topics))


# ═══════════════════════════════════════════════════════════════════
# TOOL SYSTEM + AGENT PLANNER (Claude Code-style)
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# SKILL SYSTEM (importable, extensible)
# ═══════════════════════════════════════════════════════════════════
_skills: list[dict] = []

def load_skills():
    """Load all skills from skills/ directory."""
    global _skills
    _skills = []
    import os as _os
    cwd = Path(_os.getcwd())
    pkg_root = Path(__file__).resolve().parent.parent.parent  # intent_engine.py -> api -> hashmm -> project
    search_roots = list(dict.fromkeys([cwd, pkg_root, Path("/root/autodl-tmp")]))
    for root in search_roots:
        for sub in ["skills", "data/skills"]:
            d = root / sub
            if not d.exists():
                continue
            for f in d.glob("**/*.json"):
                try:
                    skill = json.loads(f.read_text(encoding="utf-8"))
                    skill["_path"] = str(f)
                    if skill.get("name") and skill["name"] not in [s["name"] for s in _skills]:
                        _skills.append(skill)
                except Exception as e:
                    logger.warning(f"[Skill] Failed to load {f}: {e}")
    logger.info(f"[Skills] Loaded {len(_skills)} skills")
    return _skills

def _skill_score(query_lower: str, triggers: list) -> float:
    """打分：空格分隔的触发词按「关键词组」处理（全部命中=强信号，部分命中=弱信号）；
    单关键词按子串命中，越长越具体分越高。这样「合同 风险」这类词组也能在「这份合同有什么风险」里命中。"""
    score = 0.0
    for t in triggers:
        t = (t or "").lower().strip()
        if not t:
            continue
        if " " in t:
            kws = [k for k in t.split() if k]
            if kws and all(k in query_lower for k in kws):
                score += 2.0            # 词组全部命中：强信号（不给部分命中，避免「写」这类泛词误触）
        else:
            if t in query_lower:
                score += 1.0 + min(len(t), 6) * 0.15   # 越长越具体
    return score

def match_skills(query: str) -> list[dict]:
    """Match query against skill triggers, return relevant skills (multi-keyword aware)."""
    if not _skills:
        load_skills()
    matched = []
    q_lower = (query or "").lower()
    for skill in _skills:
        sc = _skill_score(q_lower, skill.get("triggers", []))
        if sc > 0:
            matched.append({**skill, "_score": sc})
    matched.sort(key=lambda x: -x["_score"])
    return matched[:3]  # Top 3 matching skills

def get_skill_prompt(query: str) -> str:
    """Get combined skill prompts for a query."""
    matched = match_skills(query)
    if not matched:
        return ""
    parts = []
    for s in matched:
        parts.append(f"[技能: {s['name']}] {s.get('prompt', '')}")
    return "\n".join(parts)

TOOLS_DESC = """Available tools:
- kb_search: Search the knowledge base for academic papers and technical content
- code_generate: Generate complete, runnable code (Python, etc.) for the user's request
- file_create: Create a downloadable file (.py, .md, .txt) and return a download link
- analyze: Deep analysis combining KB results with LLM knowledge
- direct_answer: Answer directly from LLM knowledge without KB search"""

