"""方案7 — 深度研究模式（对标 DeerFlow / R2R Deep Research / SkyworkAI DeepResearchAgent）。

deep_search 是单链多跳；本模块把它升级为"规划 → 并行多个子检索 → 综合成跨多文档、每段带
[N] 引用的长报告"。复用已有件：`subagents.decompose`（拆子主题）、`orchestrator`/`worker`
（子代理）、方案1 的 `evaluation.faithfulness`（给最终报告做接地审计）。

本模块的**新核心**（也是现有编排缺的、且可沙箱全测的部分）：
  跨子代理的**引用合并与全局重编号**——每个子检索各自带本地 [1][2]（指向它自己的来源），
  合成一份报告时若不重编号，A 的 [1] 和 B 的 [1] 会撞车。这里把所有子来源去重成一张全局
  来源表、把每段正文里的本地 [k] 改写成全局 [N]，最后组装成结构化长报告 + 全局来源清单。

约定（与项目其他能力一致）：默认 OFF（env ``HASHMM_DEEP_RESEARCH``）、纯函数 + 依赖注入、
不抛异常、信号缺失安全降级。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

_CITE_RE = re.compile(r"\[(\d{1,3})\]")


def deep_research_enabled() -> bool:
    return os.environ.get("HASHMM_DEEP_RESEARCH", "0").strip().lower() in ("1", "true", "yes", "on")


def _max_subtopics() -> int:
    try:
        return max(1, min(8, int(os.environ.get("HASHMM_DEEP_RESEARCH_MAX", "4"))))
    except Exception:
        return 4


# ── 数据结构 ──

@dataclass
class Section:
    """一个子主题的检索结果：标题 + 带本地 [k] 引用的正文 + 本地来源表（下标+1=本地编号）。"""
    title: str
    body: str
    sources: list = field(default_factory=list)


@dataclass
class ResearchReport:
    markdown: str = ""
    sources: list = field(default_factory=list)   # 全局去重后的来源
    n_sections: int = 0
    n_sources: int = 0
    grounding: Optional[float] = None             # 方案1 忠实度接地率（可选）

    def to_dict(self) -> dict:
        return {"markdown": self.markdown, "sources": self.sources,
                "n_sections": self.n_sections, "n_sources": self.n_sources,
                "grounding": self.grounding}


# ── 纯核心：引用合并 + 全局重编号 ──

def _source_key(s) -> str:
    """来源去重指纹：文件名 + 页码 + 正文前缀（同一 chunk 多处被引视为同一全局来源）。"""
    if not isinstance(s, dict):
        return str(s)[:120]
    fn = str(s.get("filename") or s.get("file") or s.get("id") or "")
    pg = str(s.get("page", ""))
    tx = str(s.get("text") or s.get("snippet") or "")[:80]
    return f"{fn}|{pg}|{tx}".strip()


def renumber_body(body: str, local_to_global: dict) -> str:
    """把正文里的本地 [k] 改写成全局 [N]（按 local_to_global 映射）。无映射的原样保留。"""
    def _sub(m):
        k = int(m.group(1))
        g = local_to_global.get(k)
        return f"[{g}]" if g else m.group(0)
    return _CITE_RE.sub(_sub, body or "")


def merge_sections(sections):
    """把多个子主题 Section 合并：构建全局去重来源表 + 各段正文重编号。

    返回 (rewritten_sections, global_sources)：
      - rewritten_sections: [{"title", "body"(已全局编号)}]
      - global_sources: 去重后的来源列表（顺序即全局编号 1..N）
    纯函数、可单测。
    """
    global_sources = []
    key_to_global = {}    # source_key -> 全局编号(1-based)

    rewritten = []
    for sec in (sections or []):
        srcs = getattr(sec, "sources", None) or []
        local_to_global = {}
        for i, s in enumerate(srcs):
            local_no = i + 1
            key = _source_key(s)
            if key not in key_to_global:
                global_sources.append(s)
                key_to_global[key] = len(global_sources)   # 新全局编号
            local_to_global[local_no] = key_to_global[key]
        rewritten.append({
            "title": getattr(sec, "title", "") or "",
            "body": renumber_body(getattr(sec, "body", "") or "", local_to_global),
        })
    return rewritten, global_sources


def render_sources_block(global_sources) -> str:
    """把全局来源表渲染成"## 参考来源\n[1] 文件 p.X ..."文本块。"""
    if not global_sources:
        return ""
    lines = ["## 参考来源"]
    for i, s in enumerate(global_sources):
        no = i + 1
        if isinstance(s, dict):
            fn = s.get("filename") or s.get("file") or s.get("id") or "未知来源"
            pg = s.get("page")
            tag = f"{fn}" + (f" p.{pg}" if pg not in (None, "", -1) else "")
            snippet = str(s.get("text") or s.get("snippet") or "").strip().replace("\n", " ")[:80]
            lines.append(f"[{no}] {tag}" + (f" — {snippet}" if snippet else ""))
        else:
            lines.append(f"[{no}] {str(s)[:120]}")
    return "\n".join(lines)


def build_report(query: str, sections, *, intro: str = "") -> ResearchReport:
    """把子主题 Section 列表组装成跨多文档、每段带全局 [N] 引用的长报告（Markdown）。永不抛异常。"""
    try:
        rewritten, global_sources = merge_sections(sections)
        parts = [f"# 深度研究报告：{query}".rstrip()]
        if intro:
            parts.append(intro.strip())
        for sec in rewritten:
            title = sec["title"].strip()
            body = sec["body"].strip()
            if title:
                parts.append(f"## {title}")
            if body:
                parts.append(body)
        src_block = render_sources_block(global_sources)
        if src_block:
            parts.append(src_block)
        md = "\n\n".join(p for p in parts if p)
        return ResearchReport(markdown=md, sources=global_sources,
                              n_sections=len(rewritten), n_sources=len(global_sources))
    except Exception:
        return ResearchReport(markdown=f"# 深度研究报告：{query}", sources=[],
                              n_sections=0, n_sources=0)


# ── 编排（可注入依赖；真机用 LLM/检索，沙箱用 mock）──

def plan_subtopics(query: str, llm_fn: Callable | None = None,
                   max_parts: int | None = None) -> list[str]:
    """把研究问题拆成若干子主题。优先复用 subagents.decompose；失败则按分隔符兜底切分。"""
    cap = max_parts if max_parts is not None else _max_subtopics()
    try:
        from hashmm.agent.subagents import decompose
        subs = decompose(query, llm_fn=llm_fn, max_parts=cap)
        subs = [s.strip() for s in (subs or []) if s and s.strip()]
        if subs:
            return subs[:cap]
    except Exception:
        pass
    # 兜底：按常见分隔符切；再不行就单主题
    raw = re.split(r"[、,，;；和与]|vs|VS", query or "")
    subs = [s.strip() for s in raw if s and s.strip()]
    return (subs or [query.strip()])[:cap]


def _default_summary(subtopic: str, sources: list) -> str:
    """无 LLM 时的兜底"摘要"：把来源要点列成带本地 [k] 引用的段落（保证报告仍可读、可溯源）。"""
    if not sources:
        return f"未检索到与「{subtopic}」直接相关的资料。"
    bits = []
    for i, s in enumerate(sources[:6]):
        txt = ""
        if isinstance(s, dict):
            txt = str(s.get("text") or s.get("snippet") or "").strip().replace("\n", " ")[:100]
        bits.append(f"{txt}[{i + 1}]" if txt else f"见来源[{i + 1}]")
    return "；".join(bits) + "。"


def run_deep_research(query: str, *, search_fn: Callable, llm_fn: Callable | None = None,
                      summarize_fn: Optional[Callable[[str, list], str]] = None,
                      max_subtopics: int | None = None,
                      audit: bool = True) -> ResearchReport:
    """端到端深度研究：规划子主题 → 各自检索取来源 → 摘要成带引用正文 → 合并成全局引用长报告。

    依赖注入（便于沙箱用 mock、真机用真实件）：
      - search_fn(subtopic) -> list[source_dict]    每个子主题的检索来源
      - summarize_fn(subtopic, sources) -> str       把来源写成带本地 [k] 引用的正文（真机用 LLM）；
                                                      不给则用 _default_summary（无 LLM 兜底）
    audit=True 时用方案1 忠实度合约给最终报告打接地率。永不抛异常。
    """
    try:
        subs = plan_subtopics(query, llm_fn, max_subtopics)
        sections = []
        for st in subs:
            try:
                srcs = list(search_fn(st) or [])
            except Exception:
                srcs = []
            body = ""
            if summarize_fn is not None:
                try:
                    body = summarize_fn(st, srcs) or ""
                except Exception:
                    body = ""
            if not body:
                body = _default_summary(st, srcs)
            sections.append(Section(title=st, body=body, sources=srcs))

        report = build_report(query, sections)

        # 方案1 集成：用忠实度合约审计最终报告（每段是否真有证据支撑）
        if audit and report.sources:
            try:
                from hashmm.evaluation import faithfulness as _fa
                _rep = _fa.audit_faithfulness(report.markdown, report.sources)
                if _rep.checked:
                    report.grounding = _rep.ratio
            except Exception:
                pass
        return report
    except Exception:
        return ResearchReport(markdown=f"# 深度研究报告：{query}", sources=[])
