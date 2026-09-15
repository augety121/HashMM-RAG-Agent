"""v17 Phase 25 (A1) — calibrate the LLM judge against human labels.

judge-primary (Phase 24b) makes the LLM judge the primary gate for content
correctness. That is the industry standard, but LLM judges carry well-documented
biases — especially *agreeableness* (over-acceptance: high TPR, low TNR) — which
can silently inflate the apparent pass rate. Strong judges reach ~80% agreement
with humans (about the human–human level), but only after you VERIFY it.

This module is the safety belt: sample judged cases for human labelling, then
measure how well the judge agrees with humans — Cohen's kappa (agreement beyond
chance), raw agreement, TPR/TNR — flag over-acceptance and length bias, and
re-select the judge threshold on the labelled calibration set (so judge_threshold
is data-driven, not guessed).

Pure analysis: no live judge needed; fully testable offline.
"""
from __future__ import annotations

import random
from typing import Sequence


# ── sampling for human labelling ──
def sample_for_labeling(report: dict, n: int = 20, seed: int = 0,
                        threshold: float = 0.6) -> list[dict]:
    """Pick up to `n` judged cases from an eval report for a human to label.

    Returns rows with the judge's score/verdict and an empty `human_label` slot
    (fill in 1=correct / 0=incorrect). Stratified lightly by judge verdict so the
    sample isn't all easy passes (we want some judge-fails to estimate TNR).
    """
    results = [r for r in (report.get("results") or [])
               if r.get("judge_score") is not None]
    rng = random.Random(seed)
    passes = [r for r in results if r["judge_score"] >= threshold]
    fails = [r for r in results if r["judge_score"] < threshold]
    rng.shuffle(passes)
    rng.shuffle(fails)
    # take at least a third from judge-fails when available (to estimate TNR)
    n_fail = min(len(fails), max(1, n // 3)) if fails else 0
    chosen = fails[:n_fail] + passes[: n - n_fail]
    rng.shuffle(chosen)
    rows = []
    for r in chosen[:n]:
        rows.append({
            "case_id": r.get("case_id", ""),
            "query": r.get("query", ""),
            "answer": r.get("answer", ""),
            "judge_score": r.get("judge_score"),
            "judge_pass": int(r["judge_score"] >= threshold),
            "human_label": None,  # ← fill in: 1 correct / 0 incorrect
        })
    return rows


# ── agreement statistics ──
def cohens_kappa(a: Sequence[int], b: Sequence[int]) -> float:
    """Cohen's kappa for two binary label sequences (agreement beyond chance)."""
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1, pb1 = sum(a) / n, sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return round((po - pe) / (1 - pe), 4)


def interpret_kappa(k: float) -> str:
    """Landis–Koch bands."""
    if k < 0.0:
        return "差(低于随机)"
    if k < 0.20:
        return "极弱"
    if k < 0.40:
        return "弱"
    if k < 0.60:
        return "中等"
    if k < 0.80:
        return "较强"
    return "几乎完全一致"


def agreement_stats(judge_pass: Sequence[int], human_pass: Sequence[int]) -> dict:
    """Treat human as ground truth; report agreement, kappa, TPR, TNR, FP/FN.

    TPR (sensitivity) = of truly-correct answers, how many the judge passed.
    TNR (specificity) = of truly-wrong answers, how many the judge failed.
    Low TNR with high TPR = agreeableness / over-acceptance (the dangerous bias).
    """
    n = len(judge_pass)
    if n == 0 or n != len(human_pass):
        return {"n": 0}
    tp = sum(1 for j, h in zip(judge_pass, human_pass) if j == 1 and h == 1)
    tn = sum(1 for j, h in zip(judge_pass, human_pass) if j == 0 and h == 0)
    fp = sum(1 for j, h in zip(judge_pass, human_pass) if j == 1 and h == 0)
    fn = sum(1 for j, h in zip(judge_pass, human_pass) if j == 0 and h == 1)
    tpr = tp / (tp + fn) if (tp + fn) else None
    tnr = tn / (tn + fp) if (tn + fp) else None
    return {
        "n": n,
        "agreement": round((tp + tn) / n, 4),
        "kappa": cohens_kappa(judge_pass, human_pass),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "tpr": None if tpr is None else round(tpr, 4),
        "tnr": None if tnr is None else round(tnr, 4),
    }


def agreeableness_flag(stats: dict, tpr_hi: float = 0.9, tnr_lo: float = 0.5) -> bool:
    """Over-acceptance: passes the correct ones (high TPR) but fails to reject the
    wrong ones (low TNR) → the judge is too agreeable and inflates pass rate."""
    tpr, tnr = stats.get("tpr"), stats.get("tnr")
    return tpr is not None and tnr is not None and tpr >= tpr_hi and tnr < tnr_lo


def length_bias(rows: Sequence[dict]) -> float | None:
    """Point-biserial-ish signal: mean answer length of judge-pass minus judge-fail,
    normalised by overall mean length. >0 means longer answers tend to pass (a sign
    of length/verbosity bias). None if not estimable."""
    lp = [len(str(r.get("answer", ""))) for r in rows if r.get("judge_pass") == 1]
    lf = [len(str(r.get("answer", ""))) for r in rows if r.get("judge_pass") == 0]
    if not lp or not lf:
        return None
    mean_all = (sum(lp) + sum(lf)) / (len(lp) + len(lf)) or 1.0
    return round((sum(lp) / len(lp) - sum(lf) / len(lf)) / mean_all, 4)


def select_threshold(scores: Sequence[float], human_pass: Sequence[int],
                     candidates: Sequence[float] | None = None) -> dict:
    """Pick the judge threshold that best agrees with human labels (max kappa,
    tie-broken by agreement). Makes judge_threshold data-driven, not guessed."""
    if not scores or len(scores) != len(human_pass):
        return {"threshold": None, "kappa": None}
    cands = list(candidates) if candidates else [i / 20 for i in range(1, 20)]
    best = {"threshold": None, "kappa": -2.0, "agreement": 0.0}
    for t in cands:
        jp = [int(s >= t) for s in scores]
        st = agreement_stats(jp, human_pass)
        k, ag = st.get("kappa", 0.0), st.get("agreement", 0.0)
        if (k, ag) > (best["kappa"], best["agreement"]):
            best = {"threshold": round(t, 3), "kappa": k, "agreement": ag}
    return best


def calibrate(labeled_rows: Sequence[dict], threshold: float = 0.6) -> dict:
    """Full judge-health report from human-labelled rows.

    Each row needs `judge_score` and `human_label` (1/0). Rows missing a human
    label are ignored. Returns agreement/kappa/TPR/TNR, bias flags, a data-driven
    recommended threshold, and a plain verdict.
    """
    rows = [r for r in labeled_rows if r.get("human_label") in (0, 1)
            and r.get("judge_score") is not None]
    if not rows:
        return {"n_labeled": 0, "verdict": "无人工标注，无法校准"}
    scores = [float(r["judge_score"]) for r in rows]
    human = [int(r["human_label"]) for r in rows]
    judge_pass = [int(s >= threshold) for s in scores]
    for r, jp in zip(rows, judge_pass):
        r.setdefault("judge_pass", jp)
    stats = agreement_stats(judge_pass, human)
    over_accept = agreeableness_flag(stats)
    rec = select_threshold(scores, human)
    k = stats.get("kappa", 0.0)
    verdict = (
        "裁判可信，可作主判官" if k >= 0.6 and not over_accept
        else "裁判过度认可(高TPR低TNR)，会高估通过率——需收紧或换专用裁判" if over_accept
        else "裁判与人工一致度不足，judge-primary 需谨慎/校准"
    )
    return {
        "n_labeled": len(rows),
        "threshold_used": threshold,
        **stats,
        "kappa_label": interpret_kappa(k),
        "agreeableness_over_accept": over_accept,
        "length_bias": length_bias([{**r, "judge_pass": jp} for r, jp in zip(rows, judge_pass)]),
        "recommended_threshold": rec.get("threshold"),
        "recommended_threshold_kappa": rec.get("kappa"),
        "verdict": verdict,
    }
