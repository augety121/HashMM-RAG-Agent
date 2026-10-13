"""v17 Phase 76 — calibrated uncertainty / confidence controller (轴 B3).

"Know what you don't know." A single confidence signal threaded through the
pipeline — retrieval (top score, #sources, whether sources mention the query) →
generation (citation grounding ratio) → output (hedging) — drives one decision:

  - high   → answer as-is;
  - medium → answer but **explicitly flag the uncertainty** (hedge note);
  - low    → **escalate**: do more retrieval (Phase 73 multi-hop) / web fallback /
             ask the user, and/or mark the answer low-confidence.

This composes the signals the codebase already produces (rerank top score,
``_sources_miss_query_entity``, the Phase 74 verifier's grounding ratio) into a
calibrated score + policy. Deterministic, domain-agnostic, **never raises**, and
robust to any signal being missing (it averages whatever is available).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

from hashmm.utils import get_logger

logger = get_logger(__name__)

# Words that signal the answer itself is unsure → pull confidence down.
_HEDGE_MARKERS = (
    "可能", "也许", "大概", "或许", "不确定", "似乎", "应该是", "估计", "好像",
    "perhaps", "maybe", "might", "not sure", "i think", "possibly", "unclear",
)


def confidence_enabled() -> bool:
    """Whether the confidence policy is consulted in the live path (default OFF)."""
    return os.environ.get("HASHMM_CONFIDENCE", "0").strip().lower() in ("1", "true", "yes", "on")


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except Exception:
        return default


def high_threshold() -> float:
    return _f("HASHMM_CONFIDENCE_HIGH", 0.66)


def low_threshold() -> float:
    return _f("HASHMM_CONFIDENCE_LOW", 0.4)


def _score_midpoint() -> float:
    # Align with the existing rerank "grounded" cutoff so a score at the web-
    # fallback threshold maps to ~0.5 confidence.
    return _f("HASHMM_CONFIDENCE_SCORE_MIDPOINT", _f("HASHMM_WEB_FALLBACK_THRESHOLD", 2.5))


@dataclass
class ConfidenceSignals:
    top_score: Optional[float] = None      # rerank score of the best source
    n_sources: Optional[int] = None        # number of grounded sources
    entity_present: Optional[bool] = None  # do sources mention the query entity
    grounding_ratio: Optional[float] = None  # Phase 74 citation overlap ratio [0,1]
    answer_hedged: Optional[bool] = None   # does the answer hedge / express doubt


def detect_hedging(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a or m in (answer or "") for m in _HEDGE_MARKERS)


def _logistic(x: float, midpoint: float, scale: float = 1.5) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-(x - midpoint) / max(1e-6, scale)))
    except OverflowError:
        return 0.0 if x < midpoint else 1.0


# Relative weights for each sub-signal (grounding weighted highest — it's the
# strongest evidence the answer is actually supported).
_WEIGHTS = {"score": 1.0, "sources": 0.6, "entity": 1.0, "grounding": 1.4, "hedge": 0.8}


def score(signals: ConfidenceSignals) -> float:
    """Weighted average of whatever sub-signals are present → confidence in [0,1].
    Returns a neutral 0.5 when nothing is available. Never raises."""
    try:
        parts: list[tuple[float, float]] = []  # (value, weight)

        if signals.top_score is not None:
            parts.append((_logistic(signals.top_score, _score_midpoint()), _WEIGHTS["score"]))
        if signals.n_sources is not None:
            target = 3.0
            parts.append((min(max(signals.n_sources, 0) / target, 1.0), _WEIGHTS["sources"]))
        if signals.entity_present is not None:
            parts.append((1.0 if signals.entity_present else 0.0, _WEIGHTS["entity"]))
        if signals.grounding_ratio is not None:
            parts.append((min(max(signals.grounding_ratio, 0.0), 1.0), _WEIGHTS["grounding"]))
        if signals.answer_hedged is not None:
            parts.append((0.3 if signals.answer_hedged else 1.0, _WEIGHTS["hedge"]))

        if not parts:
            return 0.5
        wsum = sum(w for _, w in parts)
        return round(sum(v * w for v, w in parts) / wsum, 4) if wsum else 0.5
    except Exception:
        return 0.5


def decide(confidence: float, *, high: Optional[float] = None, low: Optional[float] = None) -> str:
    high = high if high is not None else high_threshold()
    low = low if low is not None else low_threshold()
    if confidence >= high:
        return "answer"
    if confidence >= low:
        return "hedge"
    return "escalate"


def _reasons(signals: ConfidenceSignals) -> list[str]:
    r: list[str] = []
    if signals.entity_present is False:
        r.append("检索来源未提及问题中的关键实体")
    if signals.n_sources is not None and signals.n_sources == 0:
        r.append("没有检索到来源")
    if signals.grounding_ratio is not None and signals.grounding_ratio < 0.5:
        r.append(f"答案与来源的接地率偏低（{signals.grounding_ratio:.2f}）")
    if signals.top_score is not None and signals.top_score < _score_midpoint():
        r.append("最佳来源相关性偏弱")
    if signals.answer_hedged:
        r.append("答案本身表达了不确定")
    return r


HEDGE_NOTE = "（注：以下回答基于的资料有限，置信度中等，请核对关键信息。）"


def assess(signals: ConfidenceSignals, *, high: Optional[float] = None,
           low: Optional[float] = None) -> dict:
    """Full assessment: {confidence, decision, reasons}. Never raises."""
    try:
        conf = score(signals)
        dec = decide(conf, high=high, low=low)
        return {"confidence": conf, "decision": dec, "reasons": _reasons(signals)}
    except Exception:
        return {"confidence": 0.5, "decision": "hedge", "reasons": []}


def assess_retrieval(query: str, rag_sources: list, *, miss_fn=None,
                     grounding_ratio: Optional[float] = None,
                     answer_hedged: Optional[bool] = None,
                     high: Optional[float] = None, low: Optional[float] = None) -> dict:
    """Bridge: build ConfidenceSignals straight from a retrieval result and assess.

    Used to decide, right after the initial (single-shot) retrieval, whether the
    evidence is strong enough or the query should be **escalated** (e.g. to
    multi-hop retrieval). ``miss_fn(query, sources) -> bool`` is the existing
    "sources don't mention the query entity" check (injected to avoid a hard dep).
    Never raises.
    """
    try:
        sources = rag_sources or []
        top = None
        try:
            top = float(sources[0].get("score")) if sources and sources[0].get("score") is not None else None
        except Exception:
            top = None
        entity_present = None
        if miss_fn is not None and sources:
            try:
                entity_present = not bool(miss_fn(query, sources))
            except Exception:
                entity_present = None
        sig = ConfidenceSignals(
            top_score=top,
            n_sources=len(sources),
            entity_present=entity_present,
            grounding_ratio=grounding_ratio,
            answer_hedged=answer_hedged,
        )
        return assess(sig, high=high, low=low)
    except Exception:
        return {"confidence": 0.5, "decision": "hedge", "reasons": []}
