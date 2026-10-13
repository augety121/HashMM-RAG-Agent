"""hashmm/tools/loop_insights.py — Loop 工程·经验回灌一期（V72）。

读 RunRecord 遥测（HASHMM_AGENT_TRACE=1 落盘的 logs/agent_runs/*.jsonl），
聚合出"经验报告"：哪类工具常失败、停止理由分布、迭代/耗时分位数、超预算任务。

这是 Loop 工程第 5 步（经验回灌）的可操作起点：先把证据变成可读的洞察，
人看报告 → 调规则/提示词/守卫阈值 → 系统越用越聪明。后续二期才考虑
自动把高频失败沉淀为负面规则。

用法：
    HASHMM_AGENT_TRACE=1 跑一段时间后：
    python -m hashmm.tools.loop_insights                 # 全部遥测
    python -m hashmm.tools.loop_insights --days 7        # 近 7 天
    python -m hashmm.tools.loop_insights --json out.json # 机器可读

纯标准库零依赖；坏行跳过不挡整体。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path


def _trace_dir() -> Path:
    return Path(os.environ.get("HASHMM_TRACE_DIR", "logs/agent_runs"))


def load_runs(days: int = 0) -> list[dict]:
    """读遥测目录下全部（或近 N 天）的 run 记录。坏行跳过。"""
    d = _trace_dir()
    if not d.exists():
        return []
    cutoff = time.time() - days * 86400 if days > 0 else 0
    runs: list[dict] = []
    for fp in sorted(d.glob("*.jsonl")):
        try:
            if cutoff and fp.stat().st_mtime < cutoff:
                continue
            for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    runs.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    return runs


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    idx = min(len(vs) - 1, max(0, int(round((p / 100) * (len(vs) - 1)))))
    return vs[idx]


def analyze(runs: list[dict]) -> dict:
    """聚合经验指标（图1 的'关键指标'：通过率/迭代次数/失败热点/停止理由）。"""
    if not runs:
        return {"runs": 0}
    tool_total: Counter = Counter()
    tool_fail: Counter = Counter()
    stop_reasons: Counter = Counter()
    iters: list[float] = []
    elapsed: list[float] = []
    over_budget: list[dict] = []
    for r in runs:
        stop_reasons[str(r.get("stop_reason", r.get("status", "unknown")))] += 1
        if isinstance(r.get("iterations"), (int, float)):
            iters.append(float(r["iterations"]))
        if isinstance(r.get("elapsed_s"), (int, float)):
            elapsed.append(float(r["elapsed_s"]))
        n_tools = 0
        for ev in r.get("events", []) or []:
            if ev.get("k") != "tool":
                continue
            name = str(ev.get("name", "?"))
            tool_total[name] += 1
            n_tools += 1
            if str(ev.get("status", "")).lower() in ("error", "failed", "fail"):
                tool_fail[name] += 1
        if n_tools >= 20 or (r.get("iterations") or 0) >= 9:
            over_budget.append({"conv_id": r.get("conv_id", ""),
                                "query": str(r.get("query", ""))[:60],
                                "iterations": r.get("iterations"),
                                "tools": n_tools})
    fail_rate = {
        name: {"calls": tool_total[name], "fails": tool_fail.get(name, 0),
               "rate": round(tool_fail.get(name, 0) / tool_total[name], 3)}
        for name in tool_total
    }
    hot = sorted(((n, v) for n, v in fail_rate.items() if v["fails"] > 0),
                 key=lambda x: (-x[1]["rate"], -x[1]["fails"]))
    return {
        "runs": len(runs),
        "stop_reasons": dict(stop_reasons.most_common()),
        "iterations": {"p50": _pct(iters, 50), "p90": _pct(iters, 90), "max": max(iters) if iters else 0},
        "elapsed_s": {"p50": _pct(elapsed, 50), "p90": _pct(elapsed, 90), "max": max(elapsed) if elapsed else 0},
        "tool_fail_hotspots": [{"tool": n, **v} for n, v in hot[:10]],
        "tool_usage": dict(tool_total.most_common(15)),
        "over_budget_runs": over_budget[:10],
    }


def render(report: dict) -> str:
    """人类可读报告。"""
    if not report.get("runs"):
        return ("没有遥测数据。开启方式：启动后端时加 HASHMM_AGENT_TRACE=1，"
                "跑几轮对话后再来看。")
    L: list[str] = []
    L.append(f"═══ Loop 经验报告 · {report['runs']} 次运行 ═══")
    L.append("")
    L.append("【停止理由分布】（Loop 工程：停止条件是重点）")
    for k, v in report["stop_reasons"].items():
        L.append(f"  {k:<16} {v}")
    it, el = report["iterations"], report["elapsed_s"]
    L.append("")
    L.append(f"【迭代轮数】 p50={it['p50']:.0f}  p90={it['p90']:.0f}  max={it['max']:.0f}")
    L.append(f"【耗时(s)】  p50={el['p50']:.1f}  p90={el['p90']:.1f}  max={el['max']:.1f}")
    L.append("")
    if report["tool_fail_hotspots"]:
        L.append("【工具失败热点】（经验回灌候选：高频失败 → 调规则/提示/守卫）")
        for h in report["tool_fail_hotspots"]:
            L.append(f"  {h['tool']:<20} 失败 {h['fails']}/{h['calls']}  ({h['rate']*100:.0f}%)")
    else:
        L.append("【工具失败热点】 无失败记录 ✓")
    if report["over_budget_runs"]:
        L.append("")
        L.append("【接近预算上限的运行】（候选：拆任务/调 MAX_*）")
        for r in report["over_budget_runs"]:
            L.append(f"  iter={r['iterations']} tools={r['tools']}  {r['query']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop 经验回灌：遥测 → 经验报告")
    ap.add_argument("--days", type=int, default=0, help="只看近 N 天（默认全部）")
    ap.add_argument("--json", default="", help="另存机器可读 JSON 到指定路径")
    args = ap.parse_args()
    report = analyze(load_runs(days=args.days))
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已存: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
