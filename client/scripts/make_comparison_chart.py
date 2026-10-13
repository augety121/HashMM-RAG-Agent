#!/usr/bin/env python3
"""make_comparison_chart.py —— 一条命令生成「HashMM vs 大厂」对比图（V320）。

Usage:
    python scripts/make_comparison_chart.py                    # 输出到 ./comparison_chart.{svg,csv}
    python scripts/make_comparison_chart.py --out-dir reports  # 指定目录

数据来源：本机 bench_runs 历史库（每次跑外部基准都会入库）；只取每个基准最近一次
真实分数。可比性门禁（≥50 题 + Wilson 95%CI）与 /api/selftest/bench/vs-frontier
完全同源——图和报告永远一致。

产出：
    comparison_chart.svg —— 浏览器直接打开 / 插 PPT / 转 PNG 的对比图
    comparison_chart.csv —— Excel 双击即开（UTF-8 BOM），可自行二次绘图
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 HashMM vs 大厂 对比图（SVG+CSV）")
    ap.add_argument("--out-dir", default=".", help="输出目录（默认当前目录）")
    args = ap.parse_args()

    from hashmm.evaluation.benchmarks import chart_export, trend, vs_frontier

    latest = trend.latest_runs()
    cmp = vs_frontier.build_comparison(latest)
    meta = chart_export.chart_meta(cmp)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    svg_path = out / "comparison_chart.svg"
    csv_path = out / "comparison_chart.csv"
    svg_path.write_text(chart_export.render_svg(cmp), encoding="utf-8")
    csv_path.write_text(chart_export.render_csv(cmp), encoding="utf-8")

    print(f"✅ 对比图已生成：{svg_path}")
    print(f"✅ 数据表已生成：{csv_path}")
    print(f"   可比 {meta['comparable_count']} 项 · 趋势 {meta['trend_count']} 项 · "
          f"未跑 {meta['missing_count']} 项（模型 {meta['model']}）")
    if meta["comparable_count"] == 0:
        print("   ⚠️ 还没有达标基准（≥50 题）。用 HASHMM_BENCH_SAMPLE=standard 重跑"
              "想对比的基准，分数就会进正式对比区。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
