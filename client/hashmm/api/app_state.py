"""Shared application state — accessed by routes and server.py.

This module holds mutable singletons that are populated during startup
by server._load(). Route files import from here to avoid circular
imports with server.py.
"""
from __future__ import annotations
from typing import Any


# ── Core state dict (encoder, index, metadata, etc.) ──
state: dict[str, Any] = {}

# ── LLM function ──
llm_fn: Any = None

# ── Metrics instance ──
metrics: Any = None

# ── Session memory ──
memory: Any = None

# ── Semantic cache ──
sem_cache: Any = None

# ── Functions that routes can call ──
_load_fn = None       # -> server._load
_reload_llm_fn = None  # -> server._reload_llm
load_skills_fn = None  # -> server.load_skills


def ensure_loaded():
    """Call _load() if state hasn't been initialized yet."""
    if _load_fn and not state.get("loaded"):
        _load_fn()


def reload_llm():
    """Reload LLM configuration."""
    if _reload_llm_fn:
        _reload_llm_fn()


def load_skills():
    """Reload skills from disk."""
    if load_skills_fn:
        return load_skills_fn()
    return []
