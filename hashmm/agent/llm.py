"""Thin LLM client for the agent's refine + generate nodes.

DeepSeek's API is OpenAI-compatible — we use the openai SDK with a custom
base_url. Same pattern works for Qwen API, vLLM-served local model, etc.

We expose a single `make_llm_fn(cfg)` → Callable[[str], str] for simplicity.
The agent nodes accept any such callable.
"""

from __future__ import annotations

from typing import Callable

from hashmm.config import HashMMConfig
from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.llm")


def make_llm_fn(
    cfg: HashMMConfig,
    temperature: float = 0.1,
    max_tokens: int = 800,
) -> Callable[[str], str] | None:
    """Build a str→str LLM callable from config. Returns None if no API key.

    Args:
        cfg: HashMMConfig (uses llm_api_key, llm_base_url, llm_model).
        temperature: 0.1 by default — we want deterministic-ish retrieval
                     refinement and grounded answers, not creative text.
        max_tokens: cap output to avoid runaway costs.
    """
    if not cfg.llm_api_key or cfg.llm_api_key.startswith("sk-your-"):
        logger.warning("LLM_API_KEY not set; agent will use heuristic fallbacks")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; agent will use heuristic fallbacks")
        return None

    client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)
    model = cfg.llm_model

    def call(prompt: str) -> str:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLM call failed: %s", e)
            raise

    return call
