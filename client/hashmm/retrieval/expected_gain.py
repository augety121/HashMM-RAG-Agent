"""Deterministic expected-utility selector for retrieved evidence.

The selector complements relevance reranking.  It cannot widen retrieval
scope and has a fail-safe fallback to the highest-ranked candidate.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any


def enabled() -> bool:
    return os.environ.get("HASHMM_EXPECTED_GAIN", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise_relevance(value: Any, rank: int) -> float:
    score = _number(value)
    if 0 <= score <= 1:
        return score
    if score:
        return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, score))))
    return 1.0 / max(1, rank)


def select_expected_gain(query: str, candidates: list[dict[str, Any]], *,
                         top_k: int, minimum_gain: float = 0.02) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select evidence by predicted answer gain minus bounded costs/risks."""
    bounded = [dict(item) for item in candidates[:200] if isinstance(item, dict)]
    if not enabled() or not bounded:
        return bounded[:top_k], {
            "schema": "hashmm.expected-gain.v1", "enabled": enabled(),
            "candidates": len(bounded), "selected": min(len(bounded), top_k),
            "fallback": False, "formula": "disabled",
        }

    seen: set[str] = set()
    scored: list[tuple[float, int, dict[str, Any], dict[str, float]]] = []
    for rank, item in enumerate(bounded, 1):
        text = str(item.get("content") or item.get("text") or "")
        source = str(item.get("filename") or item.get("source") or "")
        digest = hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()[:24]
        relevance = _normalise_relevance(item.get("score"), rank)
        citation = 0.08 if text.strip() and source.strip() else -0.20
        trust = max(-0.20, min(0.20, _number(item.get("source_trust"), 0.0)))
        token_cost = min(0.18, len(text) / 20_000.0)
        latency_cost = min(0.10, max(0.0, _number(item.get("latency_ms"))) / 100_000.0)
        source_risk = min(0.35, max(0.0, _number(item.get("source_risk"))))
        redundancy = 0.22 if digest in seen else 0.0
        seen.add(digest)
        gain = 0.72 * relevance + citation + trust - token_cost - latency_cost - source_risk - redundancy
        parts = {
            "relevance": round(relevance, 6), "citation": round(citation, 6),
            "trust": round(trust, 6), "token_cost": round(token_cost, 6),
            "latency_cost": round(latency_cost, 6), "source_risk": round(source_risk, 6),
            "redundancy": round(redundancy, 6), "expected_gain": round(gain, 6),
        }
        item["expected_gain"] = round(gain, 6)
        scored.append((gain, rank, item, parts))

    scored.sort(key=lambda value: (-value[0], value[1]))
    selected_rows = [row for row in scored if row[0] >= minimum_gain][:max(1, top_k)]
    fallback = False
    if not selected_rows:
        selected_rows = scored[:1]
        fallback = bool(selected_rows)
    selected = [row[2] for row in selected_rows]
    trace = [{"rank": row[1], "source": str(row[2].get("filename") or row[2].get("source") or "")[:260],
              **row[3]} for row in scored[:40]]
    return selected, {
        "schema": "hashmm.expected-gain.v1", "enabled": True,
        "candidates": len(bounded), "selected": len(selected), "fallback": fallback,
        "minimum_gain": minimum_gain,
        "formula": "0.72*relevance+citation+trust-token_cost-latency_cost-source_risk-redundancy",
        "trace": trace,
    }


__all__ = ["enabled", "select_expected_gain"]
