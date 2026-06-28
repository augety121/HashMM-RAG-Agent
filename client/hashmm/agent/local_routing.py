"""v17 Phase 106 — route the agentic *loops* to the local Qwen (the cost edge).

Anthropic's multi-agent quality comes at ~15× tokens. This project's structural
advantage: run the *many cheap loop calls* — query decomposition, CRAG rewrites,
per-subagent distillation — on the **free local Qwen**, and spend the paid model
(DeepSeek) only on the single final synthesis. Result: multi-agent quality at
roughly single-call cost.

Reuses the existing local-model provider (``get_kg_llm_fn``) — no new model
loading. **Default OFF** (env ``HASHMM_AGENTIC_LOCAL_LOOPS``); when off (or when no
local model is configured) the loops simply use whatever ``llm_fn`` the caller
passes (unchanged behaviour). Never raises.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.local_routing")


def local_loops_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_AGENTIC_LOCAL_LOOPS")


def get_local_loop_llm() -> Callable | None:
    """Return a local-Qwen ``fn(prompt)->str`` for cheap loop work, or None if no
    local model is configured (caller then keeps using the paid llm_fn). Never raises."""
    try:
        from hashmm.kg.kg_llm_provider import get_kg_llm_fn
        fn = get_kg_llm_fn(None)  # local model only; returns None if not configured
        return fn if callable(fn) else None
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _text_of(r: Any) -> str:
    return (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or ""


def make_worker_fn(local_llm: Callable | None, max_ctx: int = 1500) -> Callable | None:
    """Build a subagent ``worker_fn(subquery, results) -> finding`` that distills
    retrieved context with the local Qwen. Returns None if no local model (then the
    orchestrator just retrieves + synthesizes). Never raises."""
    if local_llm is None:
        return None

    def worker(subquery: str, results: list) -> str:
        try:
            ctx = "\n".join(_text_of(r) for r in (results or [])[:4])[:max_ctx]
            if not ctx.strip():
                return ""
            prompt = ("只依据下面的资料，用一两句话回答子问题；资料没提到就说“资料未提及”。\n"
                      f"子问题：{subquery}\n资料：\n{ctx}\n答：")
            out = local_llm(prompt)
            return (out if isinstance(out, str) else str(out)).strip()
        except Exception as e:
            log_suppressed(logger, e)
            return ""

    return worker


def resolve_loop_routing(default_llm: Callable | None) -> dict:
    """Decide which fns the agentic flow should use for loops vs synthesis.
    Returns {'llm_fn': ..., 'worker_fn': ..., 'local': bool}. Never raises.

    * local loops ON + local model available → loops use local Qwen, worker distills locally
    * otherwise → loops use ``default_llm`` (e.g. DeepSeek), no special worker
    Final generation/synthesis always stays on the caller's paid generate_fn/synth_fn.
    """
    routing = {"llm_fn": default_llm, "worker_fn": None, "local": False}
    try:
        if local_loops_enabled():
            local = get_local_loop_llm()
            if local is not None:
                routing["llm_fn"] = local
                routing["worker_fn"] = make_worker_fn(local)
                routing["local"] = True
                logger.info("[agentic] loop calls routed to local Qwen (free); synthesis stays on chat model")
    except Exception as e:
        log_suppressed(logger, e)
    return routing
