"""Answer groundedness checking (V16 Phase 14).

The existing citation_validator only removes out-of-range markers ([5] when there
are 3 sources). It does NOT check whether the claim next to [2] is actually
supported by source #2. That's the real "看着有引用其实编的" problem.

This module adds:
  1. citation_overlap_check — cheap lexical check that each cited sentence shares
     meaningful tokens with the source it cites. Flags suspicious citations
     without an LLM call.
  2. self_check (optional, LLM) — ask the model whether the answer is supported by
     the retrieved context; returns a groundedness verdict + unsupported claims.

Both are non-destructive: they REPORT, they don't silently edit the answer. The
caller decides whether to append a caveat, retry, or downgrade confidence.
"""
from __future__ import annotations

import re
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.generation.groundedness")

_CITE_RE = re.compile(r"\[(\d+)\]")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])")


def _tokens(text: str) -> set[str]:
    """Cheap bilingual tokenization: CJK bigrams + ascii words."""
    text = text.lower()
    toks = set(re.findall(r"[a-z0-9]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk) - 1):
        toks.add(cjk[i] + cjk[i + 1])  # bigrams
    return toks


def citation_overlap_check(answer: str, sources: list[dict],
                           min_overlap: int = 2) -> dict:
    """Check each cited sentence shares tokens with the source it cites.

    Returns {checked, supported, suspicious: [{sentence, cite, overlap}], ratio}.
    A citation is 'suspicious' if its sentence has < min_overlap shared tokens
    with the cited source text — i.e. the source probably doesn't back the claim.
    """
    src_tokens = []
    for s in sources:
        txt = str(s.get("text") or s.get("snippet") or "")
        src_tokens.append(_tokens(txt))

    sentences = [s for s in _SENT_SPLIT.split(answer) if s.strip()]
    checked = 0
    supported = 0
    suspicious = []
    for sent in sentences:
        cites = [int(m.group(1)) for m in _CITE_RE.finditer(sent)]
        if not cites:
            continue
        sent_tok = _tokens(_CITE_RE.sub("", sent))
        for c in cites:
            idx = c - 1
            if idx < 0 or idx >= len(src_tokens):
                continue
            checked += 1
            overlap = len(sent_tok & src_tokens[idx])
            if overlap >= min_overlap:
                supported += 1
            else:
                suspicious.append({
                    "sentence": sent.strip()[:120],
                    "cite": c,
                    "overlap": overlap,
                })
    ratio = round(supported / checked, 3) if checked else 1.0
    return {"checked": checked, "supported": supported,
            "suspicious": suspicious, "ratio": ratio}


_SELFCHECK_SYSTEM = (
    "你是事实核查员。判断【回答】中的关键事实是否被【检索内容】支持。"
    "只输出 JSON：{\"grounded\": true/false, \"unsupported\": [\"未被支持的具体说法\"], "
    "\"confidence\": 0-1 的小数}。如果回答完全有依据，unsupported 为空数组。"
)


def self_check(answer: str, context: str, llm_fn: Any) -> dict:
    """LLM self-check: is the answer supported by the retrieved context?

    Returns {grounded: bool, unsupported: [...], confidence: float} or
    {grounded: None} if no LLM. Best-effort, never raises.
    """
    if not llm_fn or not answer.strip() or not context.strip():
        return {"grounded": None, "unsupported": [], "confidence": None}
    import json
    prompt = f"检索内容：\n{context[:3000]}\n\n回答：\n{answer[:1500]}\n\n核查结果（JSON）："
    try:
        if hasattr(llm_fn, "quick_call"):
            raw = llm_fn.quick_call(_SELFCHECK_SYSTEM, prompt, 300)
        elif callable(llm_fn):
            raw = llm_fn(f"{_SELFCHECK_SYSTEM}\n\n{prompt}")
        else:
            return {"grounded": None, "unsupported": [], "confidence": None}
        raw = (raw or "").strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < 0:
            return {"grounded": None, "unsupported": [], "confidence": None}
        data = json.loads(raw[s:e + 1])
        return {
            "grounded": bool(data.get("grounded", True)),
            "unsupported": list(data.get("unsupported", []))[:5],
            "confidence": data.get("confidence"),
        }
    except Exception as ex:
        logger.debug(f"self_check failed: {ex}")
        return {"grounded": None, "unsupported": [], "confidence": None}


def groundedness_caveat(overlap_result: dict, self_check_result: dict | None = None) -> str:
    """Produce a short user-facing caveat if the answer looks weakly grounded.

    Returns "" if the answer is well-grounded (most cases).
    """
    ratio = overlap_result.get("ratio", 1.0)
    sc = self_check_result or {}
    unsupported = sc.get("unsupported") or []
    if sc.get("grounded") is False and unsupported:
        items = "；".join(str(u) for u in unsupported[:2])
        return f"\n\n> ⚠️ 提示：以下内容可能缺乏充分依据，请核实：{items}"
    if ratio < 0.5 and overlap_result.get("checked", 0) >= 2:
        return "\n\n> ⚠️ 提示：部分引用与来源的关联较弱，建议核对原文。"
    return ""
