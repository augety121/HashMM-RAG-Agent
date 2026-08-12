"""v17 Phase 105 — agentic-RAG composition for a live endpoint.

One call that composes everything built in Phases 100-104 into an observable flow:

    plan (effort + intent)
      → if complex/comparison & subagents on → orchestrate subagents (104)
        else → run_agentic_rag: retrieve → CRAG correct (101) → generate (102)
      → if eval-optimize on → evaluator-optimizer refine + citations (103)

Everything is via **injected functions** (search_fn / generate_fn / worker_fn /
synth_fn / llm_fn) so the composition is fully unit-testable without a server, GPU,
index, or LLM. The endpoint (server.py) wires the real pipeline + LLM into it.
Sub-features keep their own default-OFF flags; never raises.
"""
from __future__ import annotations

from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.kg.kg_router import classify_query
from hashmm.agent.subagents import effort_level, subagents_enabled, orchestrate
from hashmm.agent.agentic_rag import run_agentic_rag
from hashmm.agent.evaluator_optimizer import eval_optimize_enabled, optimize

logger = get_logger("hashmm.agent.agentic_chat")


def run_agentic_chat(query: str,
                     search_fn: Callable[[str], list],
                     generate_fn: Callable,
                     llm_fn: Callable | None = None,
                     worker_fn: Callable | None = None,
                     synth_fn: Callable | None = None,
                     execution_scope: dict | None = None) -> dict:
    """Compose the agentic-RAG flow. Returns {answer, sources, trace, intent,
    effort, corrected}. Never raises — always returns a best-effort dict."""
    intent = classify_query(query or "")
    eff = effort_level(query or "")
    trace: list = [{"node": "plan", "detail": f"intent={intent} effort={eff}"}]
    answer, sources, corrected = "", [], False
    errors: list[dict] = []
    try:
        use_sub = subagents_enabled() and eff >= 2 and synth_fn is not None
        if use_sub:
            o = orchestrate(
                query, search_fn, synth_fn,
                worker_fn=worker_fn,
                llm_fn=llm_fn,
                execution_scope=execution_scope,
            )
            answer, sources = o.get("answer", ""), o.get("sources", [])
            errors.extend(item for item in (o.get("errors") or []) if isinstance(item, dict))
            admission = o.get("mesh_admission") or {}
            trace.append({"node": "subagents",
                          "detail": f"{o.get('n_subagents', 0)} 个子代理：" + " / ".join(o.get("subqueries", [])),
                          "mesh_admission": admission})
        else:
            r = run_agentic_rag(query, search_fn, generate_fn, llm_fn)
            answer, sources = r.get("answer", ""), r.get("sources", [])
            corrected = bool(r.get("corrected"))
            errors.extend(item for item in (r.get("errors") or []) if isinstance(item, dict))
            for step in r.get("trace", []):
                trace.append({"node": "agentic", "detail": str(step)})

        if eval_optimize_enabled():
            try:
                opt = optimize(query, sources, generate_fn, llm_fn)
                if opt.get("answer"):
                    answer, sources = opt["answer"], opt.get("sources", sources)
                trace.append({"node": "evaluator-optimizer",
                              "detail": f"iters={opt.get('iters')} score={opt.get('score')}"})
            except Exception as e:
                log_suppressed(logger, e)
                errors.append({
                    "stage": "evaluator_optimizer",
                    "type": type(e).__name__,
                    "message": str(e)[:240],
                })
    except Exception as e:
        log_suppressed(logger, e)
        errors.append({
            "stage": "orchestrator",
            "type": type(e).__name__,
            "message": str(e)[:240],
        })
    return {"answer": answer, "sources": sources, "trace": trace,
            "intent": intent, "effort": eff, "corrected": corrected,
            "errors": errors}
