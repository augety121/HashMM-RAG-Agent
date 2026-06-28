"""v17 Phase 99 — KG-retrieval A/B evaluation harness ("answer-quality 尺子").

Phase 93 gave a ruler for the *graph*; this gives one for *retrieval/answers*: run
the same gold queries with KG retrieval OFF vs ON and measure the delta in
document-level recall@k / MRR / nDCG (using the golden ``relevant_docs`` labels)
and answer-keyword hit-rate (using ``must_contain_any``). This is how we *prove*
Phase 95/96/97 actually help — not by vibes.

Reuses the project's existing ``hashmm.evaluation.metrics`` (recall_at_k / mrr /
ndcg_at_k); adds no new metric and no new dependency. The pure scoring/aggregation
functions are unit-tested; the live-pipeline runner is a thin CLI for your machine.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.metrics import retrieval_metrics

logger = get_logger("hashmm.eval.kg_retrieval_eval")

_DEFAULT_KS = (3, 5, 10)

# Categories where dense+BM25 baseline is NOT already saturated, so KG retrieval
# has room to help. Simple "factual" lookups are usually already recall@5≈1.0.
_HARD_CATEGORIES = ("multihop", "comparison", "analytical", "temporal")


def select_cases(cases: list[dict], n: int = 40,
                 categories: tuple | None = None, hard_first: bool = True) -> list[dict]:
    """Pick cases for an A/B. With ``categories`` → keep only those; else with
    ``hard_first`` → multi-hop/comparison/analytical/temporal first (they have
    headroom), then the rest. Caps at ``n``. Never raises."""
    try:
        cs = list(cases or [])
        if categories:
            cs = [c for c in cs if c.get("category") in categories]
        elif hard_first:
            hard = [c for c in cs if c.get("category") in _HARD_CATEGORIES]
            easy = [c for c in cs if c.get("category") not in _HARD_CATEGORIES]
            cs = hard + easy
        return cs[:max(1, n)]
    except Exception:
        return list(cases or [])[:max(1, n)]


def doc_ids_from_results(results: list) -> list[str]:
    """Ordered, de-duplicated document identifiers from pipeline results.
    Accepts SearchResult-like objects or dicts; prefers filename, falls back to doc_id."""
    out: list[str] = []
    seen = set()
    for r in results or []:
        if isinstance(r, dict):
            did = r.get("filename") or r.get("doc_id") or ""
        else:
            did = getattr(r, "filename", "") or getattr(r, "doc_id", "")
        did = str(did).strip()
        if did and did not in seen:
            seen.add(did)
            out.append(did)
    return out


def texts_from_results(results: list) -> list[str]:
    out = []
    for r in results or []:
        t = r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")
        if t:
            out.append(str(t))
    return out


def keyword_hit(texts: list[str], keywords: list[str]) -> bool:
    """True if ANY required keyword appears in ANY retrieved text (context-recall proxy)."""
    if not keywords:
        return True  # nothing required → trivially satisfied
    blob = "\n".join(texts or [])
    return any(str(kw) in blob for kw in keywords)


def evaluate_cases(cases: list[dict], search_fn: Callable[[str], list],
                   ks: tuple[int, ...] = _DEFAULT_KS) -> dict:
    """Run each case's query through ``search_fn`` and aggregate metrics.
    ``search_fn(query) -> list[result]`` (SearchResult-like or dicts). Never raises."""
    per_case = []
    by_cat: dict[str, list] = defaultdict(list)
    try:
        for c in cases or []:
            q = c.get("query", "")
            if not q:
                continue
            try:
                results = search_fn(q) or []
            except Exception as e:
                log_suppressed(logger, e)
                results = []
            retrieved = doc_ids_from_results(results)
            relevant = [str(x) for x in (c.get("relevant_docs", []) or [])]
            m = retrieval_metrics(retrieved, relevant, ks=ks) if relevant else {}
            m["keyword_hit"] = 1.0 if keyword_hit(texts_from_results(results), c.get("must_contain_any", [])) else 0.0
            m["_n_retrieved"] = len(retrieved)
            per_case.append(m)
            by_cat[str(c.get("category", "?"))].append(m)
    except Exception as e:
        log_suppressed(logger, e)
    agg = _aggregate(per_case)
    agg["by_category"] = {cat: _aggregate(ms) for cat, ms in by_cat.items()}
    agg["n_cases"] = len(per_case)
    return agg


def _aggregate(per_case: list[dict]) -> dict:
    if not per_case:
        return {}
    keys = set()
    for m in per_case:
        keys |= {k for k in m if not k.startswith("_")}
    out = {}
    for k in keys:
        vals = [m[k] for m in per_case if k in m]
        if vals:
            out[k] = round(sum(vals) / len(vals), 4)
    return out


def compare_runs(before: dict, after: dict) -> dict:
    """Delta (after − before) for the headline metrics."""
    out = {}
    for k in sorted(set(before) | set(after)):
        if k in ("by_category", "n_cases"):
            continue
        a, b = before.get(k), after.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(b - a, 4)
    return out


# ── gold loading + live runner (used on your machine; not in unit tests) ─────
def load_golden(path: str | Path | None = None) -> list[dict]:
    """Load golden cases. Tries the eval_kit large set by default. Never raises."""
    candidates = [path] if path else [
        "eval_kit/golden_cases_large.json",
        "hashmm/eval_kit/golden_cases_large.json",
        "hashmm/evaluation/golden_cases_100.json",
    ]
    for p in candidates:
        if not p:
            continue
        fp = Path(p)
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                return d if isinstance(d, list) else d.get("cases", d.get("data", []))
            except Exception as e:
                log_suppressed(logger, e)
    return []


def run_ab(cases: list[dict] | None = None, flags: dict | None = None,
           top_k: int = 10) -> dict:
    """A/B on the LIVE pipeline: run cases with the given KG flags OFF then ON.
    ``flags`` defaults to {HASHMM_KG_RETRIEVAL:1}. Toggles os.environ between passes
    (the pipeline reads flags per-query). For use on your machine. Never raises."""
    try:
        from hashmm.retrieval_pipeline import RetrievalPipeline
        cases = cases or load_golden()
        flags = flags or {"HASHMM_KG_RETRIEVAL": "1"}
        pipe = RetrievalPipeline()
        pipe.load()

        def _search(q):
            return pipe.search(q, top_k=top_k).results

        saved = {k: os.environ.get(k) for k in flags}
        for k in flags:
            os.environ.pop(k, None)
        before = evaluate_cases(cases, _search)
        for k, v in flags.items():
            os.environ[k] = str(v)
        after = evaluate_cases(cases, _search)
        for k, v in saved.items():  # restore
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return {"kg_off": before, "kg_on": after,
                "delta": compare_runs(before, after), "flags": flags}
    except Exception as e:
        log_suppressed(logger, e)
        return {"error": str(e)}


if __name__ == "__main__":  # CLI: A/B the default KG-retrieval flag on golden cases
    import sys
    flag = sys.argv[1] if len(sys.argv) > 1 else "HASHMM_KG_RETRIEVAL"
    res = run_ab(flags={flag: "1"})
    print(json.dumps(res, ensure_ascii=False, indent=2))
