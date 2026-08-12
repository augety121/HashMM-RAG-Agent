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
import json
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


def _dr_json(text):
    """从模型输出抠 JSON（容忍 ```json 围栏与前后噪声）。失败返回 None。"""
    if not text:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.S)
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _closest_title(t: str, titles: list) -> str:
    """把评审里的小节标题对回最接近的真实小节（模型可能改了字）。按字符重叠。"""
    if not t or not titles:
        return titles[0] if titles else ""
    ts = set(t)
    best, bestov = titles[0], -1
    for o in titles:
        ov = len(ts & set(o))
        if ov > bestov:
            best, bestov = o, ov
    return best


# SAGE 适配：对抗式质量门——按 5 条标准审每个小节，宁拒不放。
_DR_REVIEW_SYS = (
    "你是深度研究里的【质量门】，立场是【对抗式】的：主动挑弱点、空泛措辞、缺证据、覆盖不全。"
    "放过一份弱研究会不可逆地损害最终报告，而重试总能补救——所以宁可打回。\n"
    "按 5 条标准审查每个小节：①相关性：是否答到该子主题；②深度：有没有【具体数据/数字/名称/日期】，"
    "还是“广泛使用/效果显著”这类空话；③引用：关键论断是否都有 [n] 支撑；④来源：是否单一/重复；"
    "⑤完整性：是否覆盖该子主题的关键面。\n"
    "裁决 verdict ∈ approved/retry：证据充分=approved；有具体短板（缺数据/缺引用/太空泛）=retry。\n"
    "只输出 JSON：{\"reviews\":[{\"title\":\"小节标题\",\"verdict\":\"approved|retry\","
    "\"feedback\":\"打回时写明缺什么、下一轮该重点补查什么\"}],"
    "\"missing\":\"研究问题里仍未被任何小节覆盖的维度，或空串\"}"
)


def review_sections(query: str, sections, llm_fn: Callable | None) -> tuple[dict, str]:
    """SAGE 风格评审：对每个小节给 {verdict, feedback}，并指出仍缺的维度。
    返回 ({title: {verdict, feedback}}, missing_dimensions)。无 llm_fn/解析失败 → 全 approved（不卡流程）。"""
    titles = [s.title for s in sections]
    if not llm_fn or not sections:
        return {t: {"verdict": "approved", "feedback": ""} for t in titles}, ""
    packed = "\n\n".join(f"【小节】{s.title}\n{(s.body or '')[:1500]}" for s in sections)
    try:
        raw = llm_fn(f"{_DR_REVIEW_SYS}\n\n研究问题：{query}\n\n待评审小节：\n{packed}\n\nJSON：")
    except Exception:
        return {t: {"verdict": "approved", "feedback": ""} for t in titles}, ""
    data = _dr_json(raw)
    verdicts: dict = {}
    missing = ""
    if isinstance(data, dict):
        missing = str(data.get("missing", "") or "").strip()
        for it in (data.get("reviews") or []):
            if not isinstance(it, dict):
                continue
            t = str(it.get("title", "")).strip()
            v = str(it.get("verdict", "")).strip().lower()
            if v not in ("approved", "retry"):
                v = "approved"
            match = t if t in titles else _closest_title(t, titles)
            if match:
                verdicts[match] = {"verdict": v, "feedback": str(it.get("feedback", "")).strip()}
    for t in titles:
        verdicts.setdefault(t, {"verdict": "approved", "feedback": ""})
    return verdicts, missing


def run_deep_research(query: str, *, search_fn: Callable, llm_fn: Callable | None = None,
                      summarize_fn: Optional[Callable[[str, list], str]] = None,
                      max_subtopics: int | None = None,
                      audit: bool = True,
                      max_rounds: int = 2,
                      on_event: Optional[Callable[[str, dict], None]] = None) -> ResearchReport:
    """端到端深度研究：规划子主题 → 各自检索取来源 → 摘要成带引用正文 → 合并成全局引用长报告。

    依赖注入（便于沙箱用 mock、真机用真实件）：
      - search_fn(subtopic) -> list[source_dict]    每个子主题的检索来源
      - summarize_fn(subtopic, sources) -> str       把来源写成带本地 [k] 引用的正文（真机用 LLM）；
                                                      不给则用 _default_summary（无 LLM 兜底）
    SAGE 适配（Supervisor 评审+重试）：max_rounds>1 且有 llm_fn 时，每轮研究后做【对抗式质量门】评审，
      把被打回(retry)的小节带反馈重研究、把评审指出的缺失维度追加为新小节，最多 max_rounds 轮。
      on_event(stage, payload) 可把进度（plan/research/review/retry/write/done）推给前端。
    audit=True 时用方案1 忠实度合约给最终报告打接地率。永不抛异常。
    """
    def _emit(stage, payload=None):
        if on_event:
            try:
                on_event(stage, payload or {})
            except Exception:
                pass

    def _gather(st):
        # V174：MQE 多查询扩展（把子主题拆成多角度查询分别检索后合并去重）+ 可选 rerank 重排。
        # 默认关（HASHMM_MQE / HASHMM_RERANK）；全程 best-effort，任何异常退回单查询原行为。
        queries = [st]
        try:
            from hashmm.retrieval import mqe as _mqe
            if _mqe.mqe_enabled() and llm_fn:
                queries = _mqe.expand_queries(st, llm_fn=llm_fn) or [st]
        except Exception:
            queries = [st]
        merged: list = []
        seen: set = set()
        for q in queries:
            try:
                for s in (search_fn(q) or []):
                    k = _source_key(s)
                    if k in seen:
                        continue
                    seen.add(k)
                    merged.append(s)
            except Exception:
                pass
        try:
            from hashmm.retrieval import rerank as _rr
            if _rr.rerank_enabled() and len(merged) > 1:
                merged = _rr.rerank(st, merged, top_k=max(8, len(merged)))   # 仅重排，不强裁
        except Exception:
            pass
        return merged

    def _research_one(st, fb=""):
        srcs = _gather(st)
        body = ""
        if summarize_fn is not None:
            try:
                # 重研究时把评审反馈并进子主题，让综述针对性补强（不改 summarize_fn 签名）
                topic = st if not fb else f"{st}（补强要求：{fb}）"
                body = summarize_fn(topic, srcs) or ""
            except Exception:
                body = ""
        if not body:
            body = _default_summary(st, srcs)
        return Section(title=st, body=body, sources=srcs)

    try:
        subs = plan_subtopics(query, llm_fn, max_subtopics)
        _emit("plan", {"subtopics": list(subs)})
        by_title: dict = {}             # title -> Section
        feedback: dict = {}             # title -> 重研究反馈
        rounds_done = 0

        for rnd in range(max(1, max_rounds)):
            rounds_done = rnd + 1
            todo = [st for st in subs if st not in by_title or feedback.get(st)]
            if not todo:
                break
            _emit("research", {"round": rounds_done, "subtopics": list(todo)})
            for st in todo:
                by_title[st] = _research_one(st, feedback.get(st, ""))

            # 单轮模式（无 llm 或 max_rounds<=1）→ 不评审，保持原行为
            if not llm_fn or max_rounds <= 1:
                break

            _emit("review", {"round": rounds_done})
            verdicts, missing = review_sections(query, list(by_title.values()), llm_fn)
            feedback = {t: v["feedback"] for t, v in verdicts.items()
                        if v.get("verdict") == "retry" and v.get("feedback")}
            # 评审发现缺维度 → 追加新子主题（replan），下一轮研究
            if missing and rounds_done < max_rounds:
                try:
                    for e in plan_subtopics("仅补足这些尚未覆盖的维度：" + missing, llm_fn, 2):
                        if e not in subs:
                            subs.append(e)
                except Exception:
                    pass
            if feedback:
                _emit("retry", {"round": rounds_done, "count": len(feedback)})
            else:
                if not (missing and rounds_done < max_rounds):
                    break               # 全部通过且无缺口 → 收工

        _emit("write", {"sections": len(by_title)})
        report = build_report(query, list(by_title.values()))

        # 方案1 集成：用忠实度合约审计最终报告（每段是否真有证据支撑）
        if audit and report.sources:
            try:
                from hashmm.evaluation import faithfulness as _fa
                _rep = _fa.audit_faithfulness(report.markdown, report.sources)
                if _rep.checked:
                    report.grounding = _rep.ratio
            except Exception:
                pass
        _emit("done", {"rounds": rounds_done, "sections": len(by_title)})
        return report
    except Exception:
        return ResearchReport(markdown=f"# 深度研究报告：{query}", sources=[])
