#!/usr/bin/env python3
"""Eval regression gate (Phase 26 / A2).

Turns "防止改退步" from human vigilance into an enforceable gate. Compares a
candidate eval run against a baseline and **exits non-zero on a real regression**,
so a bad change can't merge / ship.

Crucially it uses a TOLERANCE band. We observed run-to-run judge variance (a
borderline case flips pass↔fail between runs even at temperature 0.1, e.g.
111/112 → 110/112). A naive gate would fire on that noise. The band lets normal
jitter through and only fails on a drop beyond tolerance — and ALWAYS fails on a
structural/safety per-case regression regardless of band.

    python scripts/eval_regression_gate.py                 # latest vs previous
    python scripts/eval_regression_gate.py --baseline ID --candidate ID --tol 0.03

Run eval with a tag (so it persists to data/eval_runs/) before gating.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hashmm.evaluation import metrics as M  # noqa: E402


def _pick_runs(baseline_id, candidate_id):
    runs = M.list_runs(limit=20)
    if not runs:
        raise SystemExit("没有已保存的评测运行。评测页填标签再运行，结果才会持久化到 data/eval_runs/。")
    ids = [r.get("run_id") or r.get("id") for r in runs]
    cand = candidate_id or ids[0]
    if not baseline_id:
        # prefer a run tagged 'baseline', else the run right before the candidate
        tagged = [r for r in runs if "baseline" in str(r.get("tag", "")).lower()]
        if tagged:
            baseline_id = tagged[0].get("run_id") or tagged[0].get("id")
        else:
            rest = [i for i in ids if i != cand]
            baseline_id = rest[0] if rest else cand
    return baseline_id, cand


def evaluate_gate(cmp: dict, base_sum: dict, cand_sum: dict,
                  tol: float, score_tol: float) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Fails on: pass-rate / avg-score drop beyond band,
    overfit_gap widening beyond band, or ANY structural/safety per-case regression."""
    reasons = []
    if cmp.get("pass_rate_delta", 0) < -tol:
        reasons.append(f"通过率下降 {cmp['pass_rate_delta']:+.3f}（容差 -{tol}）")
    if cmp.get("avg_score_delta", 0) < -score_tol:
        reasons.append(f"平均分下降 {cmp['avg_score_delta']:+.3f}（容差 -{score_tol}）")

    # overfit_gap widening (train >> heldout) is a real regression even if pass rate holds
    bg = (base_sum or {}).get("overfit_gap")
    cg = (cand_sum or {}).get("overfit_gap")
    if bg is not None and cg is not None and (cg - bg) > tol:
        reasons.append(f"过拟合缺口扩大 {bg:.3f}→{cg:.3f}（容差 +{tol}）")

    # structural/safety per-case regressions ALWAYS fail (no tolerance): a case that
    # lost its citations / leaked something / went unsafe is not acceptable noise.
    hard = [r for r in cmp.get("regressed", [])
            if (r.get("candidate", 1) == 0) or (r.get("delta", 0) <= -0.5)]
    if hard:
        ids = ", ".join(r["case_id"] for r in hard[:6])
        reasons.append(f"{len(hard)} 个用例硬退步(掉到0或跌≥0.5)：{ids}")

    return (len(reasons) == 0), reasons


def main():
    ap = argparse.ArgumentParser(description="hashmm eval regression gate")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--candidate", default=None)
    ap.add_argument("--tol", type=float, default=0.03,
                    help="通过率/过拟合缺口容差(默认0.03，吸收裁判抖动)")
    ap.add_argument("--score-tol", type=float, default=0.02, help="平均分容差(默认0.02)")
    args = ap.parse_args()

    base_id, cand_id = _pick_runs(args.baseline, args.candidate)
    cmp = M.compare_runs(base_id, cand_id)
    if cmp.get("error"):
        raise SystemExit(f"比较失败：{cmp['error']}")
    base_sum = (M.load_run(base_id) or {}).get("summary", {})
    cand_sum = (M.load_run(cand_id) or {}).get("summary", {})

    print(f"baseline={base_id}  candidate={cand_id}")
    print(f"  通过率Δ={cmp.get('pass_rate_delta'):+.3f}  平均分Δ={cmp.get('avg_score_delta'):+.3f}"
          f"  退步用例={len(cmp.get('regressed', []))}  进步用例={len(cmp.get('improved', []))}")
    for r in cmp.get("regressed", [])[:6]:
        print(f"    ↓ {r['case_id']}: {r['baseline']}→{r['candidate']} ({r['delta']:+.3f})")

    passed, reasons = evaluate_gate(cmp, base_sum, cand_sum, args.tol, args.score_tol)
    if passed:
        print("\n✓ 回归门禁通过（无超容差退步；硬性安全/结构用例无退步）。")
        return 0
    print("\n✗ 回归门禁未通过：")
    for r in reasons:
        print(f"  - {r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
