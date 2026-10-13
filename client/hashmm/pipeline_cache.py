"""v17 Phase 71 — pipeline-aware (reasoning-level) cache.

Caches expensive, query-deterministic *intermediate* artifacts of the RAG/agent
pipeline — HyDE passages, multi-query expansions, (and future idempotent steps) —
so repeated or multi-hop work doesn't re-pay the LLM / retrieval cost. This is the
2026 "cache the reasoning, not just the response" pattern (SemanticALLI),
complementing the existing response-level semantic cache.

Properties
- Thread-safe, dependency-light, in-memory (TTL + LRU eviction).
- Stable fingerprint key over (namespace, payload) via canonical JSON + sha256.
- Version-aware: an optional ``version`` (e.g. corpus/index version) is folded
  into the key, so changing the corpus auto-invalidates stale entries.
- Env-gated: ``HASHMM_PIPELINE_CACHE`` (default OFF → pure passthrough, **zero
  behavior change**); ``HASHMM_PIPELINE_CACHE_TTL`` (seconds, default 3600);
  ``HASHMM_PIPELINE_CACHE_MAX`` (entries, default 2048).
- Never raises: any cache error degrades to recompute. Exceptions from the
  compute fn are propagated and **not** cached.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)

_DEFAULT_TTL = int(os.environ.get("HASHMM_PIPELINE_CACHE_TTL", "3600"))
_DEFAULT_MAX = int(os.environ.get("HASHMM_PIPELINE_CACHE_MAX", "2048"))


def enabled() -> bool:
    """Whether the pipeline cache is active (default OFF — opt-in)."""
    return os.environ.get("HASHMM_PIPELINE_CACHE", "0").strip().lower() in ("1", "true", "yes", "on")


def make_key(namespace: str, payload: Any, version: str = "") -> str:
    """Stable sha256 key over (namespace, version, payload). Canonical JSON so
    dict ordering / whitespace don't matter; falls back to repr for odd types."""
    try:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        body = repr(payload)
    raw = f"{namespace}\x1f{version}\x1f{body}"
    return f"{namespace}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _copy(v: Any) -> Any:
    """Defensive copy for mutable container results so cached entries can't be
    mutated by callers (strings/numbers are immutable → returned as-is)."""
    if isinstance(v, list):
        return list(v)
    if isinstance(v, dict):
        return dict(v)
    return v


class _Cache:
    def __init__(self, max_entries: int = _DEFAULT_MAX):
        self._d: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max(1, max_entries)
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        """Return (hit: bool, value). Expired/missing → (False, None)."""
        now = time.time()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                self.misses += 1
                return False, None
            value, expiry = item
            if expiry and expiry < now:
                self._d.pop(key, None)
                self.misses += 1
                return False, None
            self._d.move_to_end(key)  # LRU touch
            self.hits += 1
            return True, value

    def set(self, key: str, value: Any, ttl: int | None = None):
        expiry = time.time() + (ttl if ttl is not None else _DEFAULT_TTL)
        with self._lock:
            self._d[key] = (value, expiry)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)  # evict LRU

    def invalidate(self, prefix: str = ""):
        with self._lock:
            if not prefix:
                self._d.clear()
            else:
                for k in [k for k in self._d if k.startswith(prefix)]:
                    self._d.pop(k, None)

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._d),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
                "max_entries": self._max,
            }

    def reset(self):
        with self._lock:
            self._d.clear()
            self.hits = 0
            self.misses = 0


_CACHE = _Cache()


def get_or_compute(namespace: str, payload: Any, compute_fn: Callable[[], Any], *,
                   version: str = "", ttl: int | None = None,
                   should_cache: Callable[[Any], bool] | None = None) -> Any:
    """Return a cached value for (namespace, payload, version) or compute + store it.

    - When the cache is disabled this is a pure passthrough: ``compute_fn()``.
    - ``should_cache`` lets the caller skip caching degraded results (e.g. an
      empty HyDE from a transient LLM failure) so they're retried next time.
    - Exceptions from ``compute_fn`` propagate and are never cached.
    """
    if not enabled():
        return compute_fn()
    try:
        key = make_key(namespace, payload, version)
        hit, value = _CACHE.get(key)
        if hit:
            return _copy(value)
    except Exception as e:
        log_suppressed(logger, e)
        return compute_fn()

    value = compute_fn()  # may raise → propagate, not cached
    try:
        if should_cache is None or should_cache(value):
            _CACHE.set(key, _copy(value), ttl=ttl)
    except Exception as e:
        log_suppressed(logger, e)
    return value


def stats() -> dict:
    return _CACHE.stats()


def invalidate(prefix: str = ""):
    _CACHE.invalidate(prefix)


def reset():
    """Clear cache + counters (tests / fresh windows)."""
    _CACHE.reset()
