"""v17 Phase 101 — Corrective RAG (CRAG): self-correcting retrieval.

A lightweight retrieval evaluator + corrective loop on top of the existing
pipeline (Yan et al. 2024). When retrieval looks weak (empty / too few hits), it
rewrites the query and retrieves again, keeping the better result. Conservative by
design — only fires on clearly-weak retrieval, so it never degrades good answers.

Conventions: **default OFF** (env ``HASHMM_CRAG``); pure + injectable (``search_fn``
/ ``llm_fn`` passed in → unit-testable without a GPU or live index); never raises.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.crag")

STRONG, AMBIGUOUS, WEAK = "STRONG", "AMBIGUOUS", "WEAK"

# Question particles / fillers to strip when forming a keyword-style rewrite.
_QUESTION_BITS = (
    "请问", "想知道", "麻烦", "帮我", "告诉我",
    "是什么", "是谁", "有哪些", "有什么", "怎么样", "怎么", "如何", "为什么", "多少",
    "吗", "呢", "啊", "的吗", "了吗",
)
_PUNCT = "？?。.!！,，、:：;；\"'《》()（）【】"


def crag_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_CRAG")


def _text_of(r: Any) -> str:
    return (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or ""


def _coverage(results: list, query: str) -> float:
    """Fraction of the query's 2-grams that appear in the retrieved texts."""
    q = (query or "").strip()
    if len(q) < 2 or not results:
        return 0.0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    if not grams:
        return 0.0
    blob = "\n".join(_text_of(r) for r in results)
    return sum(1 for g in grams if g in blob) / len(grams)


def evaluate_retrieval(results: list, query: str, min_results: int = 2,
                       min_coverage: float = 0.15) -> dict:
    """Conservative retrieval-quality grade. Only labels clearly-bad retrieval
    WEAK/AMBIGUOUS so the corrective loop never disturbs good answers."""
    n = len(results or [])
    cov = _coverage(results, query)
    if n == 0:
        label = WEAK
    elif n < min_results or cov < min_coverage:
        label = AMBIGUOUS
    else:
        label = STRONG
    conf = round(min(1.0, 0.25 * n) * (0.5 + 0.5 * cov), 4)
    return {"label": label, "confidence": conf, "n": n, "coverage": round(cov, 4)}


def rewrite_query(query: str, llm_fn: Callable | None = None) -> list[str]:
    """Produce alternative query phrasings. Heuristic rewrites always; an optional
    LLM rewrite is appended when ``llm_fn`` is provided. Never raises."""
    out: list[str] = []
    q = (query or "").strip()
    if not q:
        return out
    try:
        # 1) keyword form: strip question particles + punctuation
        kw = q
        for bit in _QUESTION_BITS:
            kw = kw.replace(bit, " ")
        kw = "".join(ch if ch not in _PUNCT else " " for ch in kw)
        kw = re.sub(r"\s+", " ", kw).strip()
        if kw and kw != q:
            out.append(kw)
        # 2) split comparison/list queries on 和/与/、/vs into separate sub-queries
        for sep in ("和", "与", "、", " vs ", "VS"):
            if sep in q:
                parts = [p.strip(_PUNCT + " ") for p in q.split(sep)]
                out.extend(p for p in parts if p and len(p) >= 2)
                break
        # 3) optional LLM rewrite (injected; e.g. local Qwen)
        if llm_fn is not None:
            try:
                prompt = f"把下面的问题改写成更利于检索的一句话（只输出改写后的问题，不要解释）：\n{q}"
                resp = llm_fn(prompt)
                resp = (resp if isinstance(resp, str) else str(resp)).strip().strip(_PUNCT)
                if resp and resp != q:
                    out.append(resp)
            except Exception as e:
                log_suppressed(logger, e)
    except Exception as e:
        log_suppressed(logger, e)
    # dedup, drop anything equal to the original
    seen, uniq = set(), []
    for r in out:
        if r and r != q and r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def correct(query: str, results: list, search_fn: Callable[[str], list],
            llm_fn: Callable | None = None, max_rewrites: int = 2) -> tuple[list, dict]:
    """If retrieval is weak, rewrite + re-retrieve (bounded), keep the better set.
    ``search_fn(q) -> list[result]``. Returns (results, info). Never raises."""
    info = {"corrected": False, "label": STRONG, "tried": []}
    try:
        ev = evaluate_retrieval(results, query)
        info["label"] = ev["label"]
        if ev["label"] == STRONG:
            return results, info
        best = results or []
        best_cov = ev["coverage"]
        for rq in rewrite_query(query, llm_fn)[:max_rewrites]:
            info["tried"].append(rq)
            try:
                cand = search_fn(rq) or []
            except Exception as e:
                log_suppressed(logger, e)
                continue
            cev = evaluate_retrieval(cand, query)
            # accept only a strictly better candidate (more coverage, or more hits
            # when we had none) — conservative, never replaces good with worse.
            better = (cev["coverage"] > best_cov) or (len(best) == 0 and len(cand) > 0)
            if better:
                best, best_cov = cand, cev["coverage"]
                info["corrected"] = True
                info["accepted"] = rq
                if cev["label"] == STRONG:
                    break
        return best, info
    except Exception as e:
        log_suppressed(logger, e)
        return results, info
