"""v17 Phase 28 (A4) — agent evaluation: tool-call accuracy + task completion.

The system has an agent loop (ReactAgent) with tool calls but no agent-specific
eval. Following τ-bench (goal / final-state matching) and BFCL (function-call
accuracy), this scores an agent TRACE on two axes:

  1) tool-call accuracy — did it call the right tools with the right args
     (precision / recall / F1, order-insensitive; optional exact-sequence check);
  2) task completion — did the final answer / produced artifacts meet the goal.

Pure functions; offline-testable with synthetic traces. A trace looks like:
  {"tool_calls": [{"name": "search", "args": {"q": "小米营收"}}, ...],
   "final_answer": "..."}
and the expectation:
  {"tool_calls": [{"name": "search", "args": {"q": "小米营收"}}],
   "goal": {"must_contain": ["营收"], "must_not_contain": ["编造"]}}
"""
from __future__ import annotations

from typing import Sequence


def _args_match(a: dict, b: dict) -> bool:
    """b (expected) is satisfied if every key it specifies matches a (actual)."""
    if not b:
        return True
    return all(str(a.get(k, "")).strip() == str(v).strip() for k, v in b.items())


def tool_call_accuracy(expected: Sequence[dict], actual: Sequence[dict],
                       match_args: bool = False) -> dict:
    """Greedy match expected vs actual tool calls → precision / recall / F1.

    Matching is order-insensitive by name (and, if match_args, by the args the
    expected call specifies). Also reports `ordered` = exact name sequence match.
    """
    exp = list(expected or [])
    act = list(actual or [])
    used = [False] * len(act)
    correct = 0
    for e in exp:
        for i, a in enumerate(act):
            if used[i]:
                continue
            if a.get("name") == e.get("name") and (not match_args or _args_match(a.get("args", {}), e.get("args", {}))):
                used[i] = True
                correct += 1
                break
    precision = correct / len(act) if act else (1.0 if not exp else 0.0)
    recall = correct / len(exp) if exp else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    ordered = [c.get("name") for c in act] == [c.get("name") for c in exp]
    return {
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(f1, 4), "correct": correct,
        "n_expected": len(exp), "n_actual": len(act), "ordered": ordered,
    }


def task_completion(final_answer: str, goal: dict | None) -> dict:
    """Did the final answer meet the goal (all must_contain present, none forbidden)."""
    goal = goal or {}
    ans = final_answer or ""
    missing = [k for k in goal.get("must_contain", []) if k not in ans]
    forbidden = [k for k in goal.get("must_not_contain", []) if k in ans]
    completed = not missing and not forbidden
    return {"completed": completed, "missing": missing, "forbidden": forbidden}


def evaluate_agent_case(trace: dict, expected: dict,
                        tool_weight: float = 0.5, threshold: float = 0.7,
                        match_args: bool = False) -> dict:
    """Combine tool-call F1 and task completion into a single agent score.

    score = tool_weight * tool_f1 + (1 - tool_weight) * completion(1/0).
    passed = completed AND tool_f1 >= 0.5 (a correct answer reached via wrong tools
    is still a process failure worth flagging).
    """
    tc = tool_call_accuracy(expected.get("tool_calls", []), trace.get("tool_calls", []),
                            match_args=match_args)
    comp = task_completion(trace.get("final_answer", ""), expected.get("goal"))
    score = round(tool_weight * tc["f1"] + (1 - tool_weight) * (1.0 if comp["completed"] else 0.0), 4)
    passed = comp["completed"] and tc["f1"] >= 0.5
    return {
        "score": score, "passed": passed,
        "tool_f1": tc["f1"], "tool_precision": tc["precision"], "tool_recall": tc["recall"],
        "completed": comp["completed"], "missing": comp["missing"], "forbidden": comp["forbidden"],
        "threshold": threshold,
    }


def evaluate_agent_suite(cases: Sequence[dict], **kw) -> dict:
    """Aggregate over many {trace, expected} cases → pass rate + mean score."""
    results = []
    for c in cases:
        r = evaluate_agent_case(c.get("trace", {}), c.get("expected", {}), **kw)
        results.append({"id": c.get("id", ""), **r})
    n = len(results) or 1
    return {
        "n": len(results),
        "pass_rate": round(sum(1 for r in results if r["passed"]) / n, 4),
        "avg_score": round(sum(r["score"] for r in results) / n, 4),
        "avg_tool_f1": round(sum(r["tool_f1"] for r in results) / n, 4),
        "results": results,
    }
