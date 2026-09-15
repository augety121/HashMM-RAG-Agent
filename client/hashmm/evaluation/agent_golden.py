"""v17 Phase 32 (A4) — agent golden pipeline: real traces → eval cases.

Phase 28 added agent scoring (tool-call accuracy + task completion). This connects
it to PRODUCTION: turn real agent traces into CANDIDATE agent golden cases (so the
tools an agent actually used become the expectation a human reviews/curates), and
run an agent golden set end-to-end against the live agent.

Strict (same as the feedback flywheel): trace-derived cases are needs_review=True
and never auto-added — a human confirms the expected tool sequence + goal first.

Pure + offline-testable (mock agent_run_fn). The trace format matches agent_eval:
  {"tool_calls": [{"name", "args"}], "final_answer": "..."}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Sequence

from hashmm.evaluation.agent_eval import evaluate_agent_case, evaluate_agent_suite


def extract_trace_from_tool_results(tool_results: Sequence, final_answer: str) -> dict:
    """Map a list of ReactAgent ToolResult objects (.tool/.input_text) + final answer
    into the universal trace dict used by agent_eval."""
    calls = []
    for tr in (tool_results or []):
        name = getattr(tr, "tool", None) or (tr.get("tool") if isinstance(tr, dict) else "")
        inp = getattr(tr, "input_text", None) or (tr.get("input_text", "") if isinstance(tr, dict) else "")
        calls.append({"name": (name or "").lower(), "args": {"input": inp}})
    return {"tool_calls": calls, "final_answer": final_answer or ""}


def trace_to_golden_candidate(query: str, trace: dict, idx: int = 0) -> dict:
    """Draft a CANDIDATE agent golden case from a real trace. The expected tool
    sequence is what the agent did (for a human to confirm); the goal is left for
    the human to fill (we can't trust the trace's own answer as ground truth)."""
    return {
        "id": f"agent_fb_{idx + 1:03d}",
        "query": query,
        "expected": {
            "tool_calls": [{"name": c.get("name", "")} for c in trace.get("tool_calls", [])],
            "goal": {"must_contain": [], "must_not_contain": []},  # human fills
        },
        "observed_final_answer": (trace.get("final_answer", "") or "")[:500],
        "source": "trace",
        "needs_review": True,
        "status": "candidate",
    }


def propose_agent_golden(samples: Sequence[dict]) -> list[dict]:
    """samples: [{query, trace}] → candidate agent golden cases (human-gated)."""
    return [trace_to_golden_candidate(s.get("query", ""), s.get("trace", {}), i)
            for i, s in enumerate(samples)]


def run_agent_golden(golden_cases: Sequence[dict], agent_run_fn: Callable[[str], dict],
                     **eval_kw) -> dict:
    """Run the live agent on each golden query, capture its trace, and score it.

    agent_run_fn(query) -> trace dict ({"tool_calls", "final_answer"}). Returns the
    aggregate suite report (pass_rate / avg_score / per-case) from agent_eval.
    """
    cases = []
    for c in golden_cases:
        try:
            trace = agent_run_fn(c["query"])
        except Exception as ex:
            trace = {"tool_calls": [], "final_answer": f"[agent error: {ex}]"}
        cases.append({"id": c.get("id", ""), "trace": trace, "expected": c.get("expected", {})})
    return evaluate_agent_suite(cases, **eval_kw)


def load_agent_golden(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_candidates(candidates: Sequence[dict], path: str) -> str:
    Path(path).write_text(json.dumps(list(candidates), ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return path
