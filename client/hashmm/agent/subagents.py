"""v17 Phase 104 — orchestrator-worker subagents (Anthropic multi-agent pattern).

A lead orchestrator decomposes a complex/comparison query into sub-queries, runs
**subagents in parallel** (each retrieves on its sub-query and returns a *distilled*
finding), then synthesizes the findings into one answer. Effort scales with query
complexity (Anthropic's rule: 1 for simple, 2-4 for comparisons, more for complex).

The project's edge lands here: pass a **local-Qwen** ``worker_fn`` (free) for the
many parallel subagent calls and reserve the paid model for the single ``synth_fn``
— multi-agent quality at roughly single-call cost.

Conventions: **default OFF** (env ``HASHMM_SUBAGENTS``); pure + injectable
(``search_fn`` / ``worker_fn`` / ``synth_fn``) → unit-testable without a GPU/LLM;
bounded fan-out; never raises.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.kg.kg_router import classify_query

logger = get_logger("hashmm.agent.subagents")

_MAX_SUBAGENTS = 5


def subagents_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_SUBAGENTS")


def _max_subagents() -> int:
    try:
        return max(1, min(10, int(os.environ.get("HASHMM_SUBAGENTS_MAX", str(_MAX_SUBAGENTS)))))
    except ValueError:
        return _MAX_SUBAGENTS


def effort_level(query: str) -> int:
    """Anthropic effort-scaling: 1 simple, 2-4 comparison/multi-part, more if complex."""
    q = query or ""
    try:
        kind = classify_query(q)
        # count distinct comparison/list separators as a complexity proxy
        seps = sum(q.count(s) for s in ("和", "与", "、", "vs", "VS"))
        if kind == "global" or seps >= 1:
            return min(_MAX_SUBAGENTS, max(2, seps + 1))
        if kind == "multihop":
            return 2
        return 1
    except Exception:
        return 1


_SPLIT_SEPS = ("、", "和", "与", " vs ", " VS ", "VS")
_PUNCT = "？?。.!！,，;；:：\"'《》()（）【】 "


def decompose(query: str, llm_fn: Callable | None = None, max_parts: int | None = None) -> list[str]:
    """Split a complex/comparison query into independent sub-queries. Heuristic
    (split on 和/与/、/vs) + optional LLM decomposition. Returns ≥1 (the original
    if indivisible)."""
    q = (query or "").strip()
    if not q:
        return []
    cap = max_parts if max_parts is not None else _max_subagents()
    parts: list[str] = []
    try:
        # heuristic: split on the first comparison/list separator that appears
        for sep in _SPLIT_SEPS:
            if sep in q:
                for p in q.split(sep):
                    p = p.strip(_PUNCT)
                    if p and len(p) >= 2:
                        parts.append(p)
                break
        # optional LLM decomposition when the query didn't split heuristically
        if llm_fn is not None and len(parts) < 2:
            try:
                prompt = f"把下面的复杂问题拆成2-4个可独立检索的子问题，每行一个，不要编号：\n{q}"
                resp = llm_fn(prompt)
                for line in str(resp).splitlines():
                    line = line.strip().lstrip("-•0123456789. 、）)").strip()
                    if line and line != q and len(line) >= 2:
                        parts.append(line)
            except Exception as e:
                log_suppressed(logger, e)
    except Exception as e:
        log_suppressed(logger, e)
    # dedup; fall back to the original query if nothing split out
    seen, uniq = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        uniq = [q]
    return uniq[:cap]


def _run_one(subquery: str, search_fn: Callable, worker_fn: Callable | None) -> dict:
    """One subagent: retrieve, then distill (worker_fn) into a short finding."""
    try:
        results = search_fn(subquery) or []
    except Exception as e:
        log_suppressed(logger, e)
        results = []
    finding = ""
    if worker_fn is not None:
        try:
            finding = str(worker_fn(subquery, results) or "")
        except Exception as e:
            log_suppressed(logger, e)
            finding = ""
    return {"subquery": subquery, "results": results, "finding": finding}


def orchestrate(query: str, search_fn: Callable, synth_fn: Callable,
                worker_fn: Callable | None = None, llm_fn: Callable | None = None,
                max_subagents: int | None = None) -> dict:
    """Decompose → run subagents in parallel → synthesize. ``search_fn(sub)->results``,
    optional ``worker_fn(sub, results)->finding`` (run these on local Qwen),
    ``synth_fn(query, subreports)->answer`` (the single paid call). Never raises."""
    cap = max_subagents if max_subagents is not None else _max_subagents()
    subqueries = decompose(query, llm_fn, max_parts=cap)
    reports: list[dict] = []
    try:
        if len(subqueries) == 1:
            reports = [_run_one(subqueries[0], search_fn, worker_fn)]
        else:
            with ThreadPoolExecutor(max_workers=min(cap, len(subqueries))) as ex:
                futs = {ex.submit(_run_one, sq, search_fn, worker_fn): sq for sq in subqueries}
                for fut in as_completed(futs):
                    try:
                        reports.append(fut.result())
                    except Exception as e:
                        log_suppressed(logger, e)
    except Exception as e:
        log_suppressed(logger, e)
        reports = reports or [_run_one(subqueries[0], search_fn, worker_fn)]

    # keep subreport order stable (by original subquery order)
    order = {sq: i for i, sq in enumerate(subqueries)}
    reports.sort(key=lambda r: order.get(r.get("subquery", ""), 0))

    answer = ""
    try:
        answer = str(synth_fn(query, reports) or "")
    except Exception as e:
        log_suppressed(logger, e)
    merged_sources = []
    for r in reports:
        merged_sources.extend(r.get("results", []))
    return {"answer": answer, "subqueries": subqueries, "reports": reports,
            "sources": merged_sources, "n_subagents": len(reports),
            "effort": effort_level(query)}
