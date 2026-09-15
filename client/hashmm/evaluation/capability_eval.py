"""Capability-layer evaluation — corpus-independent RAG ability benchmark.

The existing golden set (golden_cases_100) tests *domain knowledge* against a
specific corpus (company financials). That's the "domain layer": it must be
regenerated per corpus and can't compare systems fairly.

This module builds the **capability layer**: tests of general RAG behaviour that
do NOT depend on what's in the corpus, so the same benchmark runs on any
deployment and scores are comparable across corpora:

  - **refusal** — subject absent from corpus → must answer "not covered" (no
    hallucination). Generated against the live corpus vocab so it's always valid.
  - **injection resistance** — prompt-injection / system-prompt-exfiltration
    attempts → must refuse.
  - **fabrication resistance** — false-premise / "just make it up" prompts →
    must not fabricate.
  - **routing correctness** — the adaptive router (Phase 48/50) must pick the
    expected mode/hops for canonical query shapes. Pure function, no corpus.

The first three reuse the curated adversarial set + the absent-subject refusal
generator. Routing is checked directly against query_router. The whole set is
LLM-free to *build* (judging answers still needs the system under test).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.capability")

_ADVERSARIAL_PATH = Path(__file__).parent / "adversarial_cases.json"

# Canonical routing expectations — corpus-independent. Each is (query, mode, hops).
# These pin the adaptive-retrieval behaviour as a measurable capability.
_ROUTING_CASES = [
    {"id": "route_rel_01", "query": "雷军和小米是什么关系", "expect_mode": "kg", "expect_hops": 1},
    {"id": "route_rel_02", "query": "这两家公司之间有合作吗", "expect_mode": "kg", "expect_hops": 1},
    {"id": "route_cmp_01", "query": "小米和华为对比哪个好", "expect_mode": "kg", "expect_hops": 1},
    {"id": "route_hop_01", "query": "华为的CTO之前在哪家公司工作", "expect_mode": "kg", "expect_hops": 2},
    {"id": "route_hop_02", "query": "张三的上一份工作是哪家企业", "expect_mode": "kg", "expect_hops": 2},
    {"id": "route_naive_01", "query": "小米营收", "expect_mode": "naive", "expect_hops": 1},
    {"id": "route_mix_01", "query": "请详细分析这家公司的整体财务状况和未来发展战略规划", "expect_mode": "mix", "expect_hops": 1},
]


def build_capability_set(corpus_vocab_terms: set[str] | None = None,
                         n_refusal: int = 12) -> dict:
    """Assemble the corpus-independent capability eval set.

    Returns {"cases": [...], "routing": [...], "meta": {...}}. ``cases`` are
    answer-judged (refusal/injection/fabrication); ``routing`` is checked by
    :func:`evaluate_routing` without invoking the LLM.
    """
    cases: list[dict] = []

    # 1) Curated adversarial cases (injection + fabrication resistance).
    try:
        adv = json.loads(_ADVERSARIAL_PATH.read_text(encoding="utf-8"))
        adv_cases = adv if isinstance(adv, list) else adv.get("cases", [])
        for c in adv_cases:
            c = dict(c)
            c.setdefault("capability", "adversarial")
            cases.append(c)
    except Exception as _e:
        logger.warning(f"could not load adversarial cases: {_e}")

    # 2) Refusal cases generated against the live corpus (always-valid: subject
    #    guaranteed absent), so this layer adapts to ANY corpus automatically.
    try:
        from hashmm.evaluation.eval_set_builder import build_refusal_cases
        for c in build_refusal_cases(corpus_vocab_terms, n=n_refusal):
            c = dict(c)
            c["capability"] = "refusal"
            cases.append(c)
    except Exception as _e:
        logger.warning(f"could not build refusal cases: {_e}")

    return {
        "cases": cases,
        "routing": list(_ROUTING_CASES),
        "meta": {
            "layer": "capability",
            "corpus_independent": True,
            "n_cases": len(cases),
            "n_routing": len(_ROUTING_CASES),
        },
    }


def evaluate_routing(cases: list[dict] | None = None) -> dict:
    """Evaluate routing capability directly (no LLM, no corpus).

    Returns {accuracy, mode_accuracy, hops_accuracy, n, failures}.
    """
    from hashmm.retrieval.query_router import route_query
    cases = cases if cases is not None else _ROUTING_CASES
    n = len(cases)
    mode_ok = hops_ok = both_ok = 0
    failures = []
    for c in cases:
        d = route_query(c["query"])
        m_ok = d.mode == c["expect_mode"]
        h_ok = d.hops == c["expect_hops"]
        mode_ok += m_ok
        hops_ok += h_ok
        if m_ok and h_ok:
            both_ok += 1
        else:
            failures.append({"id": c.get("id"), "query": c["query"],
                             "got": {"mode": d.mode, "hops": d.hops},
                             "expected": {"mode": c["expect_mode"], "hops": c["expect_hops"]}})
    return {
        "accuracy": round(both_ok / n, 4) if n else 0.0,
        "mode_accuracy": round(mode_ok / n, 4) if n else 0.0,
        "hops_accuracy": round(hops_ok / n, 4) if n else 0.0,
        "n": n,
        "failures": failures,
    }


def write_capability_set(result: dict,
                         path: Path | str = "data/eval/capability_cases.json") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Capability] wrote {result['meta']['n_cases']} cases + "
                f"{result['meta']['n_routing']} routing checks → {p}")
    return p
