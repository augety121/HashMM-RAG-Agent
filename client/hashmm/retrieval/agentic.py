"""v17 Phase 73 — Agentic (multi-hop) retrieval (轴 A1).

The 2026 standard for hard / multi-hop questions is **agentic retrieval**: instead
of a single fixed retrieval pass, an LLM *participates in retrieval* — it issues a
sub-query, looks at what came back, decides whether that's enough, and if not
issues another sub-query, until it has the evidence to answer (A-RAG / Agentic RAG
survey). This is a ReAct loop (action → observation → reasoning) over a search
tool, with autonomous stopping and **test-time scaling** (more hops = more compute
= better recall on hard questions).

This controller is **fully injectable** so it's testable without any models:
- ``search_fn(subquery) -> list[source dict]`` — your real retrieval (BM25+vector+
  rerank+KG). In production this wraps the existing single-shot retrieval; in tests
  it's a stub.
- ``llm_fn(prompt) -> str`` — decides the next sub-query or to finish.

Robustness: it **always does at least the initial search**, dedups across hops,
stops on finish / max_hops / source cap / diminishing returns, and **never raises**
— any LLM/parse error ends the loop with whatever was gathered (≥ the initial
single-shot result), i.e. it degrades to today's behavior.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)


def agentic_enabled() -> bool:
    """Whether agentic multi-hop retrieval is active (default OFF — opt-in)."""
    return os.environ.get("HASHMM_AGENTIC_RETRIEVAL", "0").strip().lower() in ("1", "true", "yes", "on")


def max_hops() -> int:
    """Test-time scaling knob: max retrieval rounds (default 3)."""
    try:
        return max(1, int(os.environ.get("HASHMM_AGENTIC_MAX_HOPS", "3")))
    except Exception:
        return 3


def uncertainty_gate_enabled() -> bool:
    """V103.53 (P0 · 免训练版 Search-R1 + UncertaintyRAG 思想)：是否启用「不确定性闸」。

    开启后，多跳回路的停止判断不再只看 LLM 文本，而是**融合一个量化的置信度信号**
    （来自 confidence.py 的证据强度评估）：
      · 证据已足够强（confidence >= 高阈值）→ 即使还能搜也提前收手，省 hop、降延迟、减干扰；
      · 证据仍不足（confidence < 低阈值）→ 即使 LLM 想 finish 也再补一轮（到 max_hops 为止）。
    这正是 Search-R1「模型驱动、按需多轮」与 UncertaintyRAG「用不确定性决定检索」的免训练融合。
    默认开（这是 P0 的核心增益）；可用 HASHMM_UNCERTAINTY_GATE=0 关掉退回纯 LLM 判停。
    """
    return os.environ.get("HASHMM_UNCERTAINTY_GATE", "1").strip().lower() in ("1", "true", "yes", "on")


def _gate_thresholds() -> tuple[float, float]:
    """(high, low) 置信度阈值。high 以上提前收手，low 以下强制再检索。可配。"""
    def _f(env: str, dv: float) -> float:
        try:
            return float(os.environ.get(env, str(dv)))
        except Exception:
            return dv
    high = _f("HASHMM_UNCERTAINTY_GATE_HIGH", 0.75)
    low = _f("HASHMM_UNCERTAINTY_GATE_LOW", 0.45)
    # 守护：high 必须 >= low，否则回退默认，避免配置写反导致逻辑紊乱。
    if high < low:
        high, low = 0.75, 0.45
    return high, low


_DECISION_PROMPT = (
    "你在做多跳检索。原始问题：\n{query}\n\n"
    "已检索到的证据摘要（共 {n} 条）：\n{evidence}\n\n"
    "已经检索过的子查询：{tried}\n\n"
    "判断现有证据是否足以完整回答原始问题。\n"
    "- 若足够：输出 {{\"action\":\"finish\"}}。\n"
    "- 若不够：给出一个**新的、与已检索过的不同**的子查询去补充缺失的信息，"
    "输出 {{\"action\":\"search\",\"query\":\"<子查询>\"}}。\n"
    "只输出一行 JSON，不要解释。"
)


def _source_key(s: dict) -> str:
    """Stable identity for dedup: prefer id, else filename+page+text-prefix."""
    if not isinstance(s, dict):
        return repr(s)
    if s.get("id") not in (None, ""):
        return f"id:{s['id']}"
    return f"{s.get('filename','')}|{s.get('page','')}|{(s.get('text','') or '')[:60]}"


def _parse_decision(text: str) -> dict:
    """Robustly parse the LLM decision. Unparseable → finish (safe default)."""
    if not text:
        return {"action": "finish"}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            action = str(obj.get("action", "")).lower()
            if action == "search":
                q = str(obj.get("query", "")).strip()
                return {"action": "search", "query": q} if q else {"action": "finish"}
            if action == "finish":
                return {"action": "finish"}
        except Exception:
            pass
    # Heuristic fallback on free text.
    low = text.lower()
    if "finish" in low or "足够" in text or "无需" in text:
        return {"action": "finish"}
    return {"action": "finish"}


def _evidence_summary(sources: list, limit: int = 8) -> str:
    lines = []
    for s in sources[:limit]:
        fn = s.get("filename", "") if isinstance(s, dict) else ""
        snippet = (s.get("text", "") if isinstance(s, dict) else "")[:80].replace("\n", " ")
        lines.append(f"- {fn}: {snippet}")
    if len(sources) > limit:
        lines.append(f"… 另有 {len(sources) - limit} 条")
    return "\n".join(lines) if lines else "（暂无）"


def _entity_terms(text: str) -> list[str]:
    """从文本里抽轻量实体/关键词（0ms、无 LLM），用于给多跳生成「实体导向」的补充子查询。

    先试 kg.keyword_extractor 的正则抽取；它对通用中文常返回空，故**为空时退到**一个
    最简中英分词（连续中文≥2 字 / 英文单词≥3 字母）。两条都不可用时返回空列表。
    """
    def _simple(t: str) -> list[str]:
        import re as _re
        # 过滤明显的文件名/格式噪声（证据摘要里带 "- filename: ..." 前缀）
        _NOISE = {"doc", "docx", "pdf", "txt", "md", "html", "csv", "xlsx", "ppt", "pptx",
                  "the", "and", "for", "with", "from", "http", "https", "www"}
        toks = _re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", t or "")
        seen, out = set(), []
        for x in toks:
            if x.lower() in _NOISE:
                continue
            if x not in seen:
                seen.add(x); out.append(x)
        return out[:20]

    try:
        from hashmm.kg.keyword_extractor import extract_keywords_regex
        kw = extract_keywords_regex(text or "") or {}
        terms: list[str] = []
        for k in ("low_level", "ll", "entities", "high_level", "hl", "keywords"):
            v = kw.get(k)
            if isinstance(v, (list, tuple)):
                terms.extend(str(x).strip() for x in v if str(x).strip())
        seen, out = set(), []
        for t in terms:
            if t not in seen:
                seen.add(t); out.append(t)
        # 正则抽取为空（通用中文常见）→ 退到简易分词，保证实体扩展真的能拿到候选
        return out[:20] if out else _simple(text)
    except Exception:
        return _simple(text)


def _fallback_subquery(query: str, tried: list, evidence: str = "") -> str:
    """低置信但 LLM 想收手时，确定性地造一个**与已试过不同**的补充子查询。

    V103.54（吸收 SAG 精华，轻量版）：优先做「实体导向扩展」—— 这正是 SAG「沿事件/实体
    关系继续多跳召回」的思想，但不需要事件层与重建索引。做法：
      1. 从已检索证据(evidence)里抽实体/关键词，挑一个**原问题里没出现过**的实体，
         用「原问题 + 该实体」作下一跳子查询（把检索往证据牵出的新实体方向扩展）；
      2. 实在没有新实体可用，再退回机械的角度词后缀（原行为）。
    不调 LLM、可复现、跳过已试过的；全用尽返回空串。纯函数、不抛错。
    """
    try:
        tried_set = set(tried or [])
        q = (query or "").strip()
        # 1) SAG 式：用证据里、但问题里没有的实体来扩展
        if evidence:
            ql = q.lower()
            for ent in _entity_terms(evidence):
                if ent.lower() in ql:
                    continue  # 问题里已有，不算新方向
                cand = f"{q} {ent}".strip()
                if cand and cand not in tried_set:
                    return cand
        # 2) 退回机械角度词
        for suf in ["背景 细节", "相关 数据", "原因 影响", "时间 经过", "对比 其他"]:
            cand = f"{q} {suf}".strip()
            if cand not in tried_set:
                return cand
        return ""
    except Exception:
        return ""


class AgenticRetriever:
    def __init__(self, search_fn: Callable[[str], list], llm_fn: Callable[[str], str] | None = None,
                 *, max_hops: int = 3, max_sources: int = 20, min_new: int = 1,
                 confidence_fn: Callable[[str, list], float] | None = None,
                 gate_high: float | None = None, gate_low: float | None = None):
        self.search_fn = search_fn
        self.llm_fn = llm_fn
        self.max_hops = max(1, max_hops)
        self.max_sources = max(1, max_sources)
        self.min_new = max(0, min_new)
        # V103.53 不确定性闸：confidence_fn(query, sources) -> [0,1] 越高越确定。
        # 不注入则闸自动关闭（纯 LLM 判停，行为同旧版）——保证零风险增量。
        self.confidence_fn = confidence_fn
        _h, _l = _gate_thresholds()
        self.gate_high = gate_high if gate_high is not None else _h
        self.gate_low = gate_low if gate_low is not None else _l

    def _confidence(self, query: str, sources: list) -> float | None:
        """算当前证据的置信度 [0,1]。无 confidence_fn 或异常 → None（闸不参与）。"""
        if not callable(self.confidence_fn):
            return None
        try:
            c = float(self.confidence_fn(query, sources))
            if c != c:  # NaN guard
                return None
            return max(0.0, min(1.0, c))
        except Exception as e:
            log_suppressed(logger, e)
            return None

    def _search(self, q: str) -> list:
        try:
            res = self.search_fn(q)
            return list(res) if res else []
        except Exception as e:
            log_suppressed(logger, e)
            return []

    def retrieve(self, query: str) -> dict:
        """Run the multi-hop loop. Returns
        {sources, n_hops, subqueries, stopped_reason, confidence, trace}. Never raises.

        V103.53: 若注入了 confidence_fn，则在每跳后用量化置信度参与停止判断：
          · confidence >= gate_high → 证据已足够强，提前收手（stopped="confident"）；
          · LLM 说 finish 但 confidence < gate_low → 不轻信，自己造一个补充子查询再搜一轮；
          · 介于两者之间 → 听 LLM 的（与旧版一致）。
        闸只会让「该停的早停、该补的多补」，绝不会超过 max_hops / max_sources 这些硬上限。

        V103.54 (P3 过程监督的数据采集点)：额外返回 ``trace`` —— 每一跳的
        {hop, subquery, n_sources_after, confidence, action} 列表。这是 RAG-Gym 式过程奖励
        要喂的「逐步决策」数据；纯记录，不影响检索行为。
        """
        seen: dict[str, dict] = {}
        subqueries: list[str] = []
        stopped = "max_hops"
        last_conf: float | None = None
        trace: list[dict] = []  # P3：逐跳过程轨迹

        def _absorb(results: list) -> int:
            added = 0
            for s in results:
                k = _source_key(s)
                if k not in seen:
                    seen[k] = s
                    added += 1
            return added

        def _log_hop(hop_i: int, subq: str, action: str) -> None:
            trace.append({"hop": hop_i, "subquery": subq, "n_sources_after": len(seen),
                          "confidence": last_conf, "action": action})

        # Hop 0: always the original query (this is today's single-shot result).
        subqueries.append(query)
        _absorb(self._search(query))
        last_conf = self._confidence(query, list(seen.values()))
        _log_hop(0, query, "seed")
        # 闸：开局证据就已足够强 → 直接收手（最省的情况，简单问题不必多跳）。
        if last_conf is not None and last_conf >= self.gate_high:
            return {"sources": list(seen.values())[: self.max_sources], "n_hops": 1,
                    "subqueries": subqueries, "stopped_reason": "confident",
                    "confidence": last_conf, "trace": trace}

        hop = 1
        while hop < self.max_hops:
            if len(seen) >= self.max_sources:
                stopped = "max_sources"
                break
            if not callable(self.llm_fn):
                stopped = "no_llm"
                break
            try:
                prompt = _DECISION_PROMPT.format(
                    query=query, n=len(seen),
                    evidence=_evidence_summary(list(seen.values())),
                    tried="；".join(subqueries),
                )
                decision = _parse_decision(self.llm_fn(prompt) or "")
            except Exception as e:
                log_suppressed(logger, e)
                stopped = "llm_error"
                break

            sub = ""
            if decision.get("action") != "search":
                # LLM 想收手。开了闸且证据仍不足 → 不轻信，自己补一个子查询继续。
                if last_conf is not None and last_conf < self.gate_low:
                    sub = _fallback_subquery(query, subqueries, _evidence_summary(list(seen.values())))
                    if not sub:
                        stopped = "finish"
                        break
                    stopped = "low_confidence_continue"
                else:
                    stopped = "finish"
                    break
            else:
                sub = decision.get("query", "").strip()

            if not sub or sub in subqueries:
                stopped = "repeat_or_empty"
                break
            subqueries.append(sub)
            added = _absorb(self._search(sub))
            hop += 1
            last_conf = self._confidence(query, list(seen.values()))
            _log_hop(hop - 1, sub, stopped if stopped == "low_confidence_continue" else "search")
            # 闸：补检索后证据已足够强 → 提前收手。
            if last_conf is not None and last_conf >= self.gate_high:
                stopped = "confident"
                break
            if added < self.min_new:  # diminishing returns
                stopped = "no_new"
                break

        sources = list(seen.values())[: self.max_sources]
        return {"sources": sources, "n_hops": len(subqueries),
                "subqueries": subqueries, "stopped_reason": stopped,
                "confidence": last_conf, "trace": trace}
