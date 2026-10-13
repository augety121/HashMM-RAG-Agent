#!/usr/bin/env python3
"""run_comparable_benchmarks.py —— 一条命令跑够题数、产出可对比分数、出对比图（V320.1）。

用户困境：自测里 τ² 只跑了 10 题（覆盖 8.7%），落在"仅供纵向参考"，凑不出和大厂比的图。
本脚本直接解决：

  1. 探测本机【现在就能跑出可比分数】的基准（τ²/GAIA/HumanEval/BFCL/Kotlin，官方口径·免 Docker）
  2. 把它们按 standard(50 题) 跑一轮——**强制 50 题**，无视你环境里残留的 HASHMM_*_LIMIT
  3. 结果自动入库 → 顺手生成对比图 comparison_chart.svg + .csv

用法：
    python scripts/run_comparable_benchmarks.py                 # 跑所有就绪项，standard(50)
    python scripts/run_comparable_benchmarks.py --only tau2_bench
    python scripts/run_comparable_benchmarks.py --sample full   # 官方全集（很贵）
    python scripts/run_comparable_benchmarks.py --list          # 只看就绪清单，不跑

前置：后端已配置可用模型（api_key/model_name）；基准数据集已装
（未装的项会被如实跳过并给出 install 提示，不会假装跑过）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _get_llm_fn():
    """取后端当前激活的模型函数（跑基准要用它驱动 agent）。"""
    try:
        from hashmm.api import app_state
        fn = getattr(app_state, "llm_fn", None)
        if callable(fn):
            return fn
    except Exception:  # noqa: BLE001
        pass
    try:
        from hashmm.api.model_manager import get_active_llm_fn
        fn, _ = get_active_llm_fn()
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="跑够题数产出可对比分数并出对比图")
    ap.add_argument("--only", nargs="*", help="只跑这些 bench_id（默认全部就绪项）")
    ap.add_argument("--sample", default="standard", choices=["standard", "full"],
                    help="样本档位：standard=50题(默认) / full=官方全集")
    ap.add_argument("--list", action="store_true", help="只打印就绪清单，不实际跑")
    ap.add_argument("--out-dir", default=".", help="对比图输出目录")
    args = ap.parse_args()

    from hashmm.evaluation.benchmarks import (
        chart_export, comparable_candidates, run_for_comparison, trend, vs_frontier)

    cands = comparable_candidates()
    print("── 本机可对比基准就绪清单 ──")
    for c in cands:
        mark = "✅ 可跑" if c["runnable"] else "⬜ 未就绪"
        tail = (f"standard {c['standard_n']} 题 / 全集 {c['full_n'] or '—'} 题"
                if c["runnable"] else c["hint"])
        print(f"  {mark}  {c['name']:22} {tail}")
    runnable = [c["id"] for c in cands if c["runnable"]]

    if args.list:
        print(f"\n就绪 {len(runnable)}/{len(cands)}。加 --only 或直接运行即可跑够题数。")
        return 0

    if not runnable:
        print("\n⚠️ 当前没有就绪的可比基准。按上面每项提示装好数据集"
              "（多为 install.sh xxx，纯 Python 免 Docker），并确认后端已配模型，再来跑。")
        return 1

    if _get_llm_fn() is None:
        print("\n⚠️ 后端没有可用模型（api_key/model_name），无法驱动基准。先在管理后台配好模型。")
        return 1

    ids = args.only or runnable
    ids = [i for i in ids if i in runnable]
    print(f"\n▶ 开始跑 {len(ids)} 个基准 · 档位 {args.sample}（强制题数，无视 env 残留）")
    print("  注意：standard 每个基准 50 题，会比较久（τ² 单题数十秒）。")

    def _progress(i, total, bid):
        print(f"  [{i + 1}/{total}] 跑 {bid} …", flush=True)

    t0 = time.time()
    results = run_for_comparison(ids, sample=args.sample, llm_fn=_get_llm_fn(),
                                 progress=_progress)
    dt = int(time.time() - t0)

    print(f"\n── 结果（用时 {dt}s）──")
    for r in results:
        if r.get("skip"):
            print(f"  ⏭️  {r.get('name', r['id'])}: {r.get('detail', '跳过')[:80]}")
        else:
            n = r.get("total", 0)
            cmp_ok = "可对比✅" if r.get("comparable") and n >= 50 else "样本不足⚠️"
            print(f"  📊 {r.get('name', r['id'])}: {r.get('score_pct')}% "
                  f"（{r.get('passed', 0)}/{n}）· {cmp_ok}")

    # 出图
    latest = trend.latest_runs()
    cmp = vs_frontier.build_comparison(latest)
    meta = chart_export.chart_meta(cmp)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison_chart.svg").write_text(chart_export.render_svg(cmp), encoding="utf-8")
    (out / "comparison_chart.csv").write_text(chart_export.render_csv(cmp), encoding="utf-8")
    print(f"\n✅ 对比图已生成：{out / 'comparison_chart.svg'}（可比 {meta['comparable_count']} 项）")
    print(f"✅ 数据表：{out / 'comparison_chart.csv'}")
    if meta["comparable_count"] == 0:
        print("   （还没有进正式对比区的基准——检查上面是否有项因样本/环境被跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
