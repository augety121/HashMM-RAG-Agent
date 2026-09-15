"""v17 Phase 107 — agentic end-to-end smoke test (live verification tool).

Everything in Phases 100-106 is stub-tested; this is the tool to verify the whole
agent chain **live on your machine** against the real pipeline + LLM. It runs the
agentic flow for a probe query and prints a readable report: which agentic features
are enabled, the routed intent/effort, the step-by-step trace, and the answer.

The formatting + orchestration core is pure (unit-tested with stubs); the
``__main__`` does the live wiring (retrieval pipeline + app llm) for your box.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.agent.agentic_chat import run_agentic_chat

logger = get_logger("hashmm.agent.smoke")

_FLAGS = [
    ("HASHMM_KG_RETRIEVAL", "KG 局部检索(95)"),
    ("HASHMM_KG_GLOBAL", "KG 社区全局(96)"),
    ("HASHMM_KG_PPR", "KG PPR 多跳(97)"),
    ("HASHMM_KG_AUTO", "查询自动路由(100)"),
    ("HASHMM_CRAG", "CRAG 自纠错(101)"),
    ("HASHMM_SUBAGENTS", "子代理(104)"),
    ("HASHMM_EVAL_OPTIMIZE", "答案回炉+出处(103)"),
    ("HASHMM_AGENTIC_LOCAL_LOOPS", "循环走本地 Qwen(106)"),
]


def feature_flags() -> dict:
    """Current on/off state of every agentic feature flag."""
    from hashmm.feature_flags import flag_enabled
    return {name: flag_enabled(name) for name, _ in _FLAGS}


def format_report(query: str, result: dict, flags: dict | None = None) -> str:
    """Human-readable smoke report. Pure; never raises."""
    flags = flags if flags is not None else feature_flags()
    lines = ["== Agentic 冒烟报告 ==", f"问题：{query}"]
    lines.append("特性开关：" + "、".join(
        f"{desc}{'✓' if flags.get(name) else '✗'}" for name, desc in _FLAGS))
    lines.append(f"意图={result.get('intent')}  effort={result.get('effort')}  "
                 f"corrected={result.get('corrected')}")
    trace = result.get("trace", [])
    if trace:
        lines.append("trace:")
        for t in trace:
            if isinstance(t, dict):
                lines.append(f"  - {t.get('node')}: {t.get('detail', '')}")
            else:
                lines.append(f"  - {t}")
    srcs = result.get("sources", [])
    lines.append(f"来源数：{len(srcs)}")
    ans = result.get("answer", "") or ""
    lines.append("答案：" + (ans[:400] + ("…" if len(ans) > 400 else "")))
    return "\n".join(lines)


def run_smoke(query: str, search_fn: Callable, generate_fn: Callable,
              llm_fn: Callable | None = None, worker_fn: Callable | None = None,
              synth_fn: Callable | None = None) -> dict:
    """Run the agentic flow and return {result, report}. Never raises."""
    try:
        result = run_agentic_chat(query, search_fn, generate_fn, llm_fn=llm_fn,
                                  worker_fn=worker_fn, synth_fn=synth_fn)
    except Exception as e:
        log_suppressed(logger, e)
        result = {"answer": "", "sources": [], "trace": [], "intent": None, "effort": 0}
    return {"result": result, "report": format_report(query, result)}


def _live_wiring():
    """Build (search_fn, generate_fn, llm_fn, worker_fn, synth_fn) from the live
    system, applying Phase 106 local-loop routing. For __main__ on your machine."""
    from hashmm.retrieval_pipeline import RetrievalPipeline
    from hashmm.agent.local_routing import resolve_loop_routing
    try:
        from hashmm.api import app_state
        chat_llm = getattr(app_state, "llm_fn", None)
    except Exception:
        chat_llm = None

    pipe = RetrievalPipeline()
    pipe.load()

    def search_fn(q):
        try:
            return pipe.search(q, top_k=8).results
        except Exception:
            return []

    def _ctx(results):
        parts = []
        for i, r in enumerate((results or [])[:6], 1):
            t = (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or ""
            if t:
                parts.append(f"[{i}] {t[:400]}")
        return "\n".join(parts)

    def generate_fn(q, results, feedback=None):
        if not callable(chat_llm):
            return f"[no-llm] 命中 {len(results or [])} 条资料"
        prompt = (f"依据以下资料回答问题，引用用 [1][2] 标注。\n资料：\n{_ctx(results)}\n\n问题：{q}"
                  + (f"\n（修正：{feedback}）" if feedback else ""))
        try:
            return chat_llm(prompt)
        except Exception:
            return ""

    def synth_fn(q, reports):
        merged = []
        for rep in (reports or []):
            merged += rep.get("results", [])
        return generate_fn(q, merged)

    routing = resolve_loop_routing(chat_llm)
    return search_fn, generate_fn, routing["llm_fn"], routing["worker_fn"], synth_fn


if __name__ == "__main__":  # live smoke on your machine
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "对比小米和网易的主营业务"
    sf, gf, lf, wf, synf = _live_wiring()
    print(run_smoke(q, sf, gf, llm_fn=lf, worker_fn=wf, synth_fn=synf)["report"])
