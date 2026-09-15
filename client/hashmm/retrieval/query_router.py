"""Query-adaptive retrieval routing.

The chat pipeline supports three retrieval modes — ``naive`` (BM25 + vector +
rerank), ``kg`` (knowledge-graph entity/relation traversal), and ``mix`` (both
fused). Today the mode is fixed to ``mix`` for every query. That's wasteful and
sometimes wrong: a relationship question ("who founded X and where did they work
before") is best served by graph traversal, while a short keyword lookup barely
needs the KG at all.

This module decides the mode *from the query* — the small but real "smart
routing" layer that distinguishes a mature RAG system from a fixed pipeline. It
is intentionally:

  - **Rule-based and transparent.** Every decision comes with a human-readable
    reason, surfaced in traces and the observability endpoint. No opaque model.
  - **Opt-in and behaviour-preserving.** It only activates when the caller asks
    for mode ``auto``. Explicit ``naive``/``kg``/``mix`` are passed through
    unchanged, so nothing about current behaviour changes unless auto is chosen.
  - **Cheap.** Pure regex/heuristics on the query string; no extra LLM call.

The classifier reuses the corpus-vocab / entity signals the pipeline already
computes elsewhere, plus a few relationship/multi-hop cues that the KG handles
best. When signals are weak it falls back to ``mix`` — the safe superset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Relationship / multi-hop cues → the knowledge graph is the right tool.
# (Chinese + English; kept deliberately conservative to avoid over-routing.)
_RELATION_CUES = re.compile(
    r"(关系|联系|关联|之间|谁是谁|谁的|属于|隶属|下属|母公司|子公司|创始|创办|"
    r"任职|曾在|前.*东家|合作|竞争对手|对手|收购|投资了|持股|供应商|客户是|"
    r"对比|比较|差异|区别|哪个好|vs\.?|"
    r"relationship|related to|connected|who founded|works? (?:at|for)|"
    r"subsidiary|parent company|acquired|partnership|compare|versus)",
    re.IGNORECASE,
)

# Multi-hop phrasing ("X 的 Y 的 Z", "之前/后来", career chains) favours
# 2-hop graph traversal: the answer needs an intermediate entity (A→role→B).
_MULTIHOP_CUES = re.compile(
    r"([^，。、\s]{2,6}的[^，。、的\s]{1,6}的[^，。、\s]{1,8}|先后|之前.*之后|后来又|then.*again|chain|path between|两步|多跳|"
    r"之前.{0,6}(在|任|就职|工作|待过)|曾经.{0,6}(在|任)|前.{0,4}(东家|公司|雇主)|"
    r"上一(份|家|个).{0,4}(工作|公司|企业|东家)|(履历|经历|职业生涯).{0,4}(中|里)|"
    r"的(CTO|CEO|创始人|高管|负责人|董事长|总裁).{0,8}(之前|曾|原来|以前)|"
    r"previously|used to work|before joining|former employer)",
    re.IGNORECASE,
)

# Short, keyword-dense lookups (few function words) → naive lexical+vector is
# enough; the KG adds latency without much value. Heuristic: very short query
# with no relationship cue.
_SHORT_LOOKUP_MAXLEN = 8


@dataclass
class RouteDecision:
    mode: str            # "naive" | "kg" | "mix"
    reason: str          # human-readable, surfaced in traces/observability
    matched: str = ""    # the signal that fired (for debugging)
    hops: int = 1        # suggested KG traversal depth (2 for multi-hop queries)


def route_query(query: str, default: str = "mix") -> RouteDecision:
    """Pick a retrieval mode for ``query``.

    Returns a RouteDecision; ``mode`` is one of naive/kg/mix. Falls back to
    ``default`` (mix) when no strong signal fires — mix is the safe superset, so
    an uncertain route never loses recall.
    """
    q = (query or "").strip()
    if not q:
        return RouteDecision(default, "empty query → default", "")

    # 1) Relationship / multi-hop → KG traversal is the differentiator.
    m = _RELATION_CUES.search(q)
    if m:
        return RouteDecision("kg" if default != "naive" else "mix",
                             "关系型查询 → 知识图谱遍历", m.group(0), hops=1)
    m = _MULTIHOP_CUES.search(q)
    if m:
        # multi-hop phrasing ("X 的 Y 的 Z", "之前/后来") → go 2 hops so chains
        # like A→CTO→previous-company can be traversed.
        return RouteDecision("kg", "多跳查询 → 图谱两跳遍历", m.group(0), hops=2)

    # 2) Very short keyword lookup with no relational intent → naive is enough.
    #    (Count CJK chars + ascii words as a rough token proxy.)
    approx_tokens = len(re.findall(r"[\u4e00-\u9fff]", q)) + len(re.findall(r"[A-Za-z]+", q))
    if approx_tokens <= _SHORT_LOOKUP_MAXLEN:
        return RouteDecision("naive", "短关键词查询 → 词法+向量召回足够", f"~{approx_tokens}tok")

    # 3) Default: fused retrieval (safe superset).
    return RouteDecision(default, "通用查询 → 融合检索", "")


def resolve_mode(requested_mode: str, query: str, default: str = "mix") -> tuple[str, RouteDecision | None]:
    """Resolve the effective retrieval mode.

    - If ``requested_mode`` is an explicit naive/kg/mix → pass through unchanged
      (returns that mode and ``None`` decision: no routing happened).
    - If it is ``auto`` (or empty) → run :func:`route_query` and return its mode
      plus the decision (so the trace can explain the choice).

    This is the single integration point: callers translate "auto" into a
    concrete mode here, and everything downstream is unchanged.
    """
    rm = (requested_mode or "").strip().lower()
    if rm in ("naive", "kg", "mix"):
        return rm, None
    if rm in ("", "auto"):
        decision = route_query(query, default=default)
        return decision.mode, decision
    # Unknown value → safe default, no routing claim.
    return default, None
