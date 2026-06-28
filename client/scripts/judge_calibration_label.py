#!/usr/bin/env python3
"""Judge calibration labelling tool (Phase 25 / A1).

judge-primary makes the LLM judge the content gate. Before trusting it, VERIFY it
against humans. Two steps:

  1) sample  — pull a labelling sheet from a saved eval run (run eval with a tag so
               it persists), each row has the judge's verdict + an empty slot:
                 python scripts/judge_calibration_label.py sample --n 20
               → writes data/eval/judge_labels.json  (fill `human_label`: 1 / 0)

  2) report  — after you fill human_label (1=correct, 0=incorrect), compute the
               judge-health report (Cohen's kappa vs humans, TPR/TNR, over-
               acceptance flag, length bias, data-driven recommended threshold):
                 python scripts/judge_calibration_label.py report --threshold 0.6

Only humans can produce the labels — that is the whole point of calibration. This
tool does the sampling and the math; you do the judging.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hashmm.evaluation import judge_calibration as JC  # noqa: E402
from hashmm.evaluation import metrics as M  # noqa: E402

DEFAULT_SHEET = Path("data/eval/judge_labels.json")


def _latest_run(run_id: str | None) -> dict:
    if run_id:
        run = M.load_run(run_id)
        if not run:
            raise SystemExit(f"run {run_id} not found in data/eval_runs/")
        return run
    runs = M.list_runs(limit=1)
    if not runs:
        raise SystemExit(
            "没有已保存的评测运行。请在评测页填一个标签(如 'cal') 再点运行评测，"
            "这样结果才会持久化到 data/eval_runs/。")
    rid = runs[0].get("run_id") or runs[0].get("id")
    return M.load_run(rid)


def cmd_sample(args):
    run = _latest_run(args.run_id)
    rows = JC.sample_for_labeling(run, n=args.n, seed=args.seed, threshold=args.threshold)
    if not rows:
        raise SystemExit("该运行没有带 judge_score 的用例，无法抽样校准。")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[sample] 写出 {len(rows)} 条待标注 → {out}")
    print(f"[sample] 来源 run_id={run.get('run_id')}  judge_pass 阈值={args.threshold}")
    print("请逐条阅读 query+answer，把每行的 human_label 填成 1(正确) 或 0(错误)，再跑 report。")


def cmd_report(args):
    sheet = Path(args.infile)
    if not sheet.exists():
        raise SystemExit(f"找不到标注文件 {sheet}，请先跑 sample 并完成人工标注。")
    rows = json.loads(sheet.read_text(encoding="utf-8"))
    rep = JC.calibrate(rows, threshold=args.threshold)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep.get("n_labeled", 0) == 0:
        return
    print("\n── 解读 ──")
    print(f"  与人工一致度 kappa={rep.get('kappa')} ({rep.get('kappa_label')})；"
          f"一致率={rep.get('agreement')}；TPR={rep.get('tpr')} TNR={rep.get('tnr')}")
    if rep.get("agreeableness_over_accept"):
        print("  ⚠️ 裁判过度认可(高TPR低TNR)：会高估通过率，judge-primary 有水分风险。")
    if rep.get("recommended_threshold") is not None:
        print(f"  建议阈值 judge_threshold≈{rep.get('recommended_threshold')} "
              f"(kappa={rep.get('recommended_threshold_kappa')})；现用 {args.threshold}。")
    print(f"  结论：{rep.get('verdict')}")


def main():
    ap = argparse.ArgumentParser(description="LLM judge calibration labelling tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="抽样生成待标注表")
    s.add_argument("--run-id", default=None, help="指定 run_id，默认最近一次")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--threshold", type=float, default=0.6)
    s.add_argument("--out", default=str(DEFAULT_SHEET))
    s.set_defaults(func=cmd_sample)

    r = sub.add_parser("report", help="读人工标注表，输出裁判校准报告")
    r.add_argument("--in", dest="infile", default=str(DEFAULT_SHEET))
    r.add_argument("--threshold", type=float, default=0.6)
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
