"""v17 Phase 87 — Qwen2.5-backed LLM provider for knowledge-graph extraction.

Why: the lightweight regex KG builder produces sentence-fragment "entities" on
real Chinese long-form docs (annual reports, web pages) — e.g. "下表载列本集团",
"术咨询及相关服务". The fix is to extract **semantic triples** with a real LLM.
The building blocks already exist (``kg.local_hf_llm.build_hf_llm_fn`` loads a
local Qwen2.5-Instruct; ``kg.llm_extractor.LLMTripleExtractor`` turns chunks into
clean head/relation/tail triples). This module wires them to the
``build-from-chunks`` path.

Design:
  * **Default OFF.** Unless ``HASHMM_KG_LLM_EXTRACT`` is truthy, ``get_kg_llm_fn``
    returns ``None`` and the KG build path is byte-for-byte unchanged (regex).
  * **Resolution order** when enabled:
      1. local HF model at ``HASHMM_KG_LLM_HF_PATH`` (a Qwen2.5-Instruct directory;
         ``HASHMM_KG_LLM_MODEL`` is also accepted when it *looks like a path*),
         loaded once and cached on a CUDA stream;
      2. a caller-provided chat ``llm_fn`` fallback — ONLY when no local path is
         configured (a configured-but-broken local path does NOT fall back, to
         avoid the paid-API 402 storm);
      3. ``None`` (→ regex path still runs; nothing breaks).
  * **Never raises.** Any load/import failure logs a warning and falls back.
  * **Injectable.** ``get_kg_llm_fn`` takes the chat fn as an argument and the
    cache can be reset, so tests need neither a GPU nor transformers installed.

Enable on the live box (Qwen2.5 on the 4090) — consistent with KG_LOCAL_LLM.md:
    # download once (huggingface unreachable here → ModelScope):
    #   modelscope download --model Qwen/Qwen2.5-7B-Instruct \
    #     --local_dir /root/autodl-tmp/models/Qwen2.5-7B-Instruct   (or 3B for speed)
    export HASHMM_KG_LLM_EXTRACT=1
    export HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct
then click 知识图谱 → 重新构建. (Slower than regex — it calls the model per chunk —
but a built-in pre-filter skips boilerplate chunks, and you can cap with
HASHMM_KG_LLM_MAX_CHUNKS.)
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("hashmm.kg.llm_provider")

_TRUTHY = {"1", "true", "yes", "on"}
# path-keyed cache so a model is loaded at most once per process
_cache: dict = {"fn": None, "tried": False, "path": None}


def kg_llm_enabled() -> bool:
    """True iff KG LLM extraction is opted in via env. Default OFF."""
    return os.environ.get("HASHMM_KG_LLM_EXTRACT", "").strip().lower() in _TRUTHY


def kg_llm_max_chunks() -> Optional[int]:
    """Optional cap on chunks fed to the LLM (cost/time control). None = no cap."""
    raw = os.environ.get("HASHMM_KG_LLM_MAX_CHUNKS", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _resolve_model_path() -> str:
    """Resolve the local HF model directory for KG extraction.

    Consistent with KG_LOCAL_LLM.md and scripts/rebuild_kg_llm.py, the canonical
    env for a *local* model directory is ``HASHMM_KG_LLM_HF_PATH``. We also accept
    ``HASHMM_KG_LLM_MODEL`` — but ONLY when it looks like a filesystem path —
    because the rebuild script uses ``HASHMM_KG_LLM_MODEL`` for an API *model name*
    (e.g. ``qwen2.5:7b-instruct``), which must never be loaded as a local dir.
    """
    p = os.environ.get("HASHMM_KG_LLM_HF_PATH", "").strip()
    if p:
        return p
    m = os.environ.get("HASHMM_KG_LLM_MODEL", "").strip()
    if m and (m.startswith(("/", "./", "../", "~")) or (os.sep in m)):
        return m
    return ""


def _load_local(model_path: str) -> Optional[Callable]:
    """Load (and cache) a local HF chat model as fn(prompt)->str. Never raises."""
    if _cache["tried"] and _cache["path"] == model_path:
        return _cache["fn"]
    _cache["tried"] = True
    _cache["path"] = model_path
    try:
        from hashmm.kg.local_hf_llm import build_hf_llm_fn
        _cache["fn"] = build_hf_llm_fn(model_path)
        logger.info(f"[KG-LLM] local model ready: {model_path}")
    except Exception as e:  # noqa: BLE001 — transformers/torch missing, bad path, OOM…
        logger.warning(f"[KG-LLM] failed to load local model {model_path}: {e}; will fall back")
        _cache["fn"] = None
    return _cache["fn"]


def get_kg_llm_fn(chat_llm_fn: Optional[Callable] = None) -> Optional[Callable]:
    """Return an ``fn(prompt)->str`` for KG triple extraction, or ``None``.

    ``None`` means "use the regex path" — callers stay safe. Never raises.
    Pass the app's chat llm_fn as a fallback (used when no local model path is
    configured or the local model fails to load).
    """
    try:
        if not kg_llm_enabled():
            return None
        model_path = _resolve_model_path()
        if model_path:
            fn = _load_local(model_path)
            if fn is not None:
                return fn
            # A local model was explicitly requested but failed to load. Do NOT
            # silently fall back to the paid chat API — that previously caused a
            # 402 "Insufficient Balance" storm and contradicts the intent of
            # "use local Qwen, not the paid endpoint". Use the free regex path and
            # tell the user how to fix the model path.
            logger.error(
                "[KG-LLM] 已配置本地模型路径但加载失败；"
                "为避免误用付费 API（402 余额不足），本次不回退到聊天模型，"
                "KG 改用免费的正则抽取。请确认已下载 Qwen2.5 且 HASHMM_KG_LLM_HF_PATH "
                "指向真实存在的目录后重试。"
            )
            return None
        # No local model path configured → the chat llm_fn is the intended provider.
        if callable(chat_llm_fn):
            logger.info("[KG-LLM] using chat llm_fn for KG extraction")
            return chat_llm_fn
        logger.warning("[KG-LLM] enabled but no usable model/llm_fn; KG falls back to regex")
        return None
    except Exception as e:  # noqa: BLE001 — provider must never break the build path
        logger.warning(f"[KG-LLM] get_kg_llm_fn error: {e}")
        return None


def reset_cache() -> None:
    """Drop the cached local model (tests / model switch)."""
    _cache.update(fn=None, tried=False, path=None)
