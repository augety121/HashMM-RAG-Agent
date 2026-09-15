"""v17 Phase 103 — evaluator-optimizer answer refinement + citations.

Anthropic's "evaluator-optimizer" loop: one model generates, another evaluates
against criteria, and the generator refines with feedback until it passes (or a
bound). Here the evaluator scores **grounding** (answer ↔ retrieved sources),
coverage, and presence of citations; weak answers are regenerated *with feedback*.
A citation pass guarantees the answer carries source references.

Conventions: **default OFF** (env ``HASHMM_EVAL_OPTIMIZE``); pure + injectable
(``generate_fn`` / ``llm_fn``) → unit-testable without a GPU or LLM; never raises.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.evaluator_optimizer")


def eval_optimize_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_EVAL_OPTIMIZE")


def _text_of(r: Any) -> str:
    return (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or ""


def _meta(r: Any, key: str, default=""):
    return r.get(key, default) if isinstance(r, dict) else getattr(r, key, default)


def _grounding(answer: str, sources: list) -> float:
    """Faithfulness proxy: fraction of the answer's 2-grams found in source texts."""
    a = (answer or "").strip()
    if len(a) < 2 or not sources:
        return 0.0
    grams = {a[i:i + 2] for i in range(len(a) - 1)}
    if not grams:
        return 0.0
    blob = "\n".join(_text_of(s) for s in sources)
    return sum(1 for g in grams if g in blob) / len(grams)


def _has_citation(answer: str) -> bool:
    a = answer or ""
    return ("来源" in a) or ("[1]" in a) or ("【1】" in a) or ("[1" in a)


def evaluate_answer(query: str, answer: str, sources: list,
                    llm_fn: Callable | None = None,
                    min_grounding: float = 0.30, min_len: int = 10) -> dict:
    """Score an answer. Heuristic by default; if ``llm_fn`` given, also blends the
    project's LLM RAG-component metrics (faithfulness …) when available. Never raises."""
    issues = []
    a = (answer or "").strip()
    if len(a) < min_len:
        issues.append("答案过短或为空")
    g = _grounding(a, sources)
    if g < min_grounding:
        issues.append("与检索来源的支撑不足（可能脱离资料/编造）")
    if not _has_citation(a):
        issues.append("缺少出处标注")
    score = round(g, 4)
    if llm_fn is not None:
        try:
            from hashmm.evaluation.metrics import rag_component_metrics
            m = rag_component_metrics(query, a, sources, llm_fn)
            faith = m.get("faithfulness")
            if isinstance(faith, (int, float)):
                score = round(0.5 * score + 0.5 * float(faith), 4)
                if faith < 0.5 and "与检索来源的支撑不足（可能脱离资料/编造）" not in issues:
                    issues.append("LLM 评审：忠实度偏低")
        except Exception as e:
            log_suppressed(logger, e)
    ok = (len(a) >= min_len) and (g >= min_grounding)
    return {"ok": ok, "score": score, "grounding": round(g, 4), "issues": issues}


def ensure_citations(answer: str, sources: list, max_sources: int = 5) -> str:
    """Append a source footer if the answer lacks citations. Pure; never raises."""
    try:
        a = (answer or "").strip()
        if not a or _has_citation(a) or not sources:
            return a
        lines = []
        for i, s in enumerate(sources[:max_sources]):
            fn = _meta(s, "filename", "") or _meta(s, "doc_id", "")
            pg = _meta(s, "page", -1)
            tag = f"[{i + 1}] {fn}" + (f" p.{pg}" if isinstance(pg, int) and pg >= 0 else "")
            lines.append(tag.strip())
        return a + "\n\n来源：" + "；".join(x for x in lines if x)
    except Exception:
        return answer or ""


def optimize(query: str, sources: list, generate_fn: Callable,
             llm_fn: Callable | None = None, max_iters: int = 2,
             min_grounding: float = 0.30) -> dict:
    """Generate → evaluate → (refine with feedback) until it passes or hits the
    iteration bound. ``generate_fn(query, sources[, feedback])``. Never raises."""
    history = []
    answer, srcs = "", sources or []
    try:
        feedback = None
        for i in range(max(1, max_iters)):
            try:
                out = generate_fn(query, srcs, feedback) if feedback is not None else generate_fn(query, srcs)
            except TypeError:
                # generate_fn doesn't accept feedback → can't refine further
                if feedback is not None:
                    break
                out = generate_fn(query, srcs)
            except Exception as e:
                log_suppressed(logger, e)
                break
            if isinstance(out, dict):
                answer = out.get("answer", "")
                srcs = out.get("sources", srcs)
            else:
                answer = str(out)
            ev = evaluate_answer(query, answer, srcs, llm_fn, min_grounding=min_grounding)
            history.append({"iter": i, "score": ev["score"], "ok": ev["ok"], "issues": ev["issues"]})
            if ev["ok"]:
                break
            feedback = "请修正以下问题并仅依据给定资料作答：" + "；".join(ev["issues"])
        answer = ensure_citations(answer, srcs)
    except Exception as e:
        log_suppressed(logger, e)
    return {"answer": answer, "sources": srcs, "iters": len(history),
            "score": history[-1]["score"] if history else 0.0, "history": history}
