#!/usr/bin/env python3
"""Agent golden eval runner (Phase 32 / A4).

Two modes:

  # Run the agent golden set against the LIVE agent and score it
  python scripts/agent_eval_run.py --golden hashmm/evaluation/agent_golden_seed.json

  # Turn real agent traces into CANDIDATE golden cases (human-gated)
  python scripts/agent_eval_run.py --propose-from traces.json --out agent_candidates.json

`traces.json` shape: [{"query": "...", "trace": {"tool_calls":[{"name","args"}], "final_answer":"..."}}]

To run against the live agent, wire `agent_run_fn(query) -> trace` to your ReactAgent
(collect ToolResults during run, then `extract_trace_from_tool_results(results, answer)`).
Without that wiring this prints how to enable it (it won't fabricate agent runs).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hashmm.evaluation import agent_golden as AG  # noqa: E402


def _live_agent_run_fn():
    """Return agent_run_fn(query)->trace if the live agent is wired, else None."""
    try:
        from hashmm.api.routes.admin import build_agent_run_fn  # optional hook
        return build_agent_run_fn()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Agent golden eval / candidate proposer")
    ap.add_argument("--golden", default="hashmm/evaluation/agent_golden_seed.json")
    ap.add_argument("--propose-from", dest="propose_from", default=None)
    ap.add_argument("--out", default="agent_candidates.json")
    ap.add_argument("--match-args", action="store_true")
    args = ap.parse_args()

    if args.propose_from:
        src = Path(args.propose_from)
        if not src.exists():
            print(f"未找到 trace 文件：{args.propose_from}")
            print('需要先准备 JSON，格式：[{"query": "...", '
                  '"trace": {"tool_calls": [{"name": "search"}], "final_answer": "..."}}]')
            print("（在线 agent 跑完后，用 agent_golden.extract_trace_from_tool_results 收集真实 trace 导出此文件）")
            return 2
        try:
            samples = json.loads(src.read_text(encoding="utf-8"))
        except Exception as ex:
            print(f"trace 文件解析失败：{ex}")
            return 2
        if not isinstance(samples, list) or not samples:
            print("trace 文件应是非空 JSON 数组。")
            return 2
        cands = AG.propose_agent_golden(samples)
        AG.save_candidates(cands, args.out)
        print(f"已从 {len(samples)} 条 trace 生成 {len(cands)} 个候选 agent golden → {args.out}")
        print("全部 needs_review=True：请人工确认期望工具序列 + 填写 goal 后再入库。")
        return 0

    golden = AG.load_agent_golden(args.golden)
    if not golden:
        print(f"未找到 golden：{args.golden}")
        return 2
    run_fn = _live_agent_run_fn()
    if run_fn is None:
        print("ℹ️ 未接入真实 agent。请在 admin 暴露 build_agent_run_fn()（用 ReactAgent 收集 "
              "ToolResult 后 extract_trace_from_tool_results 组装 trace），再跑本命令对真实 agent 评测。")
        print(f"当前 golden 共 {len(golden)} 条，期望工具/目标已就绪。")
        return 2
    rep = AG.run_agent_golden(golden, run_fn, match_args=args.match_args)
    print(f"Agent 评测：{rep['pass_rate']*100:.0f}% 通过（{rep['n']} 条），"
          f"均分 {rep['avg_score']}，平均工具F1 {rep['avg_tool_f1']}")
    for r in rep["results"]:
        flag = "✓" if r["passed"] else "✗"
        print(f"  {flag} {r['id']}: score={r['score']} toolF1={r['tool_f1']} "
              f"completed={r['completed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
