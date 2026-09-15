"""v17 Phase 28 (A3) — online feedback → golden flywheel.

Closes the loop: thumbs-down messages and low-quality online samples become
CANDIDATE golden cases, so production weaknesses turn into regression tests. Strict
rule: candidates are **never auto-added** — they are flagged needs_review for a
human to curate; approved ones enter golden (some into held-out). This keeps the
golden set trustworthy (no machine-fabricated cases sneaking in).

Pure functions; offline-testable. Wire-up: feed `quality_monitor` samples +
message feedback in; write the candidates out for review.
"""
from __future__ import annotations

import re
from typing import Sequence


REASON_TO_CATEGORY = {
    "wrong_tool": "agent_tool",
    "retrieval_miss": "retrieval",
    "unsupported": "grounding",
    "unsafe": "security",
    "too_slow": "efficiency",
    "instruction_miss": "instruction_following",
    "incomplete": "task_completion",
    "incorrect": "factual",
    "other": "analytical",
}


def collect_negative_signals(messages: Sequence[dict] | None = None,
                             samples: Sequence[dict] | None = None,
                             quality_threshold: float = 0.5) -> list[dict]:
    """Gather weak interactions worth turning into tests.

    - messages: chat messages with optional `feedback` == 'down' (+ query/answer).
    - samples: quality_monitor samples with a `quality`/`score` field below threshold.
    Returns de-duplicated candidate dicts {query, answer, signal}.
    """
    cands: list[dict] = []
    seen = set()

    def _add(query, answer, signal):
        q = (query or "").strip()
        if not q or q in seen:
            return
        seen.add(q)
        cands.append({"query": q, "answer": (answer or "").strip(), "signal": signal})

    for m in (messages or []):
        if m.get("feedback") == "down":
            _add(m.get("query") or m.get("user_query"), m.get("answer") or m.get("content"), "thumbs_down")
    for s in (samples or []):
        q = s.get("quality", s.get("score"))
        if q is not None and q < quality_threshold:
            _add(s.get("query"), s.get("answer"), f"low_quality({q})")
    return cands


_REFUSAL_HINT = re.compile(r"(无法|不能|抱歉|没有相关|不予|未提及)")
_NUMBER_HINT = re.compile(r"\d")
_CITE_HINT = re.compile(r"\[\d+\]")


def _guess_category(answer: str) -> str:
    a = answer or ""
    if _REFUSAL_HINT.search(a) and not _CITE_HINT.search(a):
        return "refusal"
    if _CITE_HINT.search(a) or _NUMBER_HINT.search(a):
        return "factual"
    return "analytical"


def propose_golden_from_feedback(candidates: Sequence[dict]) -> list[dict]:
    """Draft CANDIDATE golden stubs from negative signals — flagged for human review,
    NEVER auto-added. The human fills/fixes reference_answer + checks before入库."""
    out = []
    for i, c in enumerate(candidates):
        cat = _guess_category(c.get("answer", ""))
        out.append({
            "id": f"fb_{i+1:03d}",
            "query": c["query"],
            "category": cat,
            # a thumbs-down answer is NOT a trusted reference — leave for the human.
            "reference_answer": None,
            "candidate_answer": c.get("answer", "")[:500],
            "source": "feedback",
            "signal": c.get("signal", ""),
            "needs_review": True,
            "status": "candidate",
        })
    return out


def build_reviewed_eval_case(candidate: dict, reference_answer: str) -> dict:
    """Convert one human-reviewed production failure into a held-out case.

    The failed answer is evidence only and is deliberately never copied into
    ``reference_answer``. A human must provide the trusted target first.
    """
    reference = str(reference_answer or "").strip()
    if len(reference) < 5:
        raise ValueError("a trusted reference_answer is required")
    cid = str(candidate.get("id") or "").strip()
    query = str(candidate.get("query") or "").strip()
    if not cid or not query:
        raise ValueError("candidate id and query are required")
    reason = str(candidate.get("reason_code") or "other")
    label = str(candidate.get("reason_label") or reason)
    rubric = f"来自真实用户失败回灌。按人工参考答案判断核心事实和任务要求是否满足；重点防止再次出现：{label}。"
    if candidate.get("comment"):
        rubric += "用户补充：" + str(candidate["comment"])[:500]
    return {
        "id": f"feedback_{cid}",
        "query": query[:4000],
        "category": REASON_TO_CATEGORY.get(reason, "analytical"),
        "reference_answer": reference[:12000],
        "rubric": rubric,
        "must_cite": reason in ("unsupported", "retrieval_miss"),
        "min_sources": 1 if reason in ("unsupported", "retrieval_miss") else 0,
        "retrieval_mode": "mix",
        # Production failures stay held out from tuning to avoid teaching to the test.
        "split": "heldout",
    }


def summarize(candidates: Sequence[dict], proposals: Sequence[dict]) -> dict:
    by_signal: dict = {}
    for c in candidates:
        key = (c.get("signal") or "").split("(")[0]
        by_signal[key] = by_signal.get(key, 0) + 1
    by_cat: dict = {}
    for p in proposals:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    return {"n_candidates": len(candidates), "n_proposals": len(proposals),
            "by_signal": by_signal, "by_category": by_cat,
            "note": "全部 needs_review=True，人工筛选后方可入库（部分进 held-out）。"}
