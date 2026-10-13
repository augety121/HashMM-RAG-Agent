"""Encoder Pool — process-level singleton for BGE-M3 text encoder.

Solves: BGE-M3 takes ~2 minutes to load. Without a singleton, every ingest
call reloads the model. With EncoderPool, it loads once and stays in memory.

v7.0: Added query embedding LRU cache — avoids re-encoding identical queries.
      Cache holds up to 512 entries (~2MB at dim=1024, float32).

Usage:
    from hashmm.encoder_pool import EncoderPool

    # First call loads the model (slow), subsequent calls reuse it
    embeddings = EncoderPool.encode_texts(["Hello", "World"])
    # → np.ndarray shape (2, 1024)

    # Cached for repeated queries (e.g. same user retrying)
    q_emb = EncoderPool.encode_query("小米2024年营收")
"""
from __future__ import annotations
import os
import threading
import time
from collections import OrderedDict
import numpy as np
import torch
from hashmm.utils import get_logger

logger = get_logger("hashmm.encoder_pool")

# Search paths for the BGE-M3 model (tried in order)
_MODEL_SEARCH_PATHS = [
    lambda: os.environ.get("BGE_MODEL_PATH", ""),
    lambda: os.path.join(os.getcwd(), ".local_models", "bge-m3"),
    lambda: "/root/autodl-tmp/.local_models/bge-m3",
    lambda: "BAAI/bge-m3",  # HuggingFace download fallback
]

# ── Query Embedding Cache ──

_QUERY_CACHE_MAX = 1024   # v10.0: doubled from 512 (~4MB at dim=1024)
_CACHE_TTL = 3600         # v10.0: 1 hour TTL — stale entries auto-expire


class _EmbeddingCache:
    """Thread-safe LRU cache for query embeddings with TTL expiration.

    Keys: query text (str)
    Values: (embedding ndarray, timestamp)
    Eviction: LRU when size > max_size, or TTL expired
    """

    def __init__(self, max_size: int = _QUERY_CACHE_MAX, ttl: float = _CACHE_TTL):
        self._cache: OrderedDict[str, tuple[np.ndarray, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> np.ndarray | None:
        with self._lock:
            if key in self._cache:
                emb, ts = self._cache[key]
                # v10.0: TTL check — expire stale entries
                if self._ttl > 0 and (time.time() - ts) > self._ttl:
                    del self._cache[key]
                    self._misses += 1
                    return None
                self._cache.move_to_end(key)
                self._hits += 1
                return emb.copy()
            self._misses += 1
            return None

    def put(self, key: str, embedding: np.ndarray) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = (embedding.copy(), time.time())
            else:
                self._cache[key] = (embedding.copy(), time.time())
                if len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def evict_expired(self) -> int:
        """Remove all expired entries. Returns count of evicted entries."""
        if self._ttl <= 0:
            return 0
        now = time.time()
        evicted = 0
        with self._lock:
            expired_keys = [k for k, (_, ts) in self._cache.items() if (now - ts) > self._ttl]
            for k in expired_keys:
                del self._cache[k]
                evicted += 1
        return evicted

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / max(total, 1) * 100:.1f}%",
            }


class EncoderPool:
    """Process-level singleton for text encoding.

    Thread-safe: PyTorch inference with torch.no_grad() is safe
    across threads when the model is frozen (no parameter updates).
    """

    _encoder = None       # TextEncoder instance
    _device: str = "cpu"
    _dim: int = 1024
    _query_cache = _EmbeddingCache()

    @classmethod
    def get_encoder(cls):
        """Get or initialize the BGE-M3 encoder. Returns the TextEncoder instance."""
        if cls._encoder is not None:
            return cls._encoder

        from hashmm.hashing.encoders import TextEncoder

        # Find model path
        model_path = None
        for path_fn in _MODEL_SEARCH_PATHS:
            p = path_fn()
            if p and (os.path.isdir(p) or "/" not in p):  # Directory or HF ID
                model_path = p
                if os.path.isdir(p):
                    break  # Prefer local path

        if not model_path:
            model_path = "BAAI/bge-m3"

        # Select device
        device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(f"Loading BGE-M3 from {model_path} on {device}...")
        cls._encoder = TextEncoder(model_name=model_path, device=device)
        cls._device = device
        cls._dim = cls._encoder.out_dim
        logger.info(f"BGE-M3 ready: dim={cls._dim}, device={device}")

        return cls._encoder

    @classmethod
    def encode_texts(cls, texts: list[str], batch_size: int = 64,
                     show_progress: bool = False) -> np.ndarray:
        """Encode texts to L2-normalized embeddings.

        Args:
            texts: List of strings to encode
            batch_size: GPU batch size (64 fits comfortably on 4090)
            show_progress: Log progress every batch

        Returns:
            np.ndarray of shape (len(texts), 1024), dtype float32
        """
        if not texts:
            return np.empty((0, cls._dim), dtype=np.float32)

        encoder = cls.get_encoder()
        all_embeddings = []
        total = len(texts)

        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            with torch.no_grad():
                emb = encoder(batch)  # (B, dim) on GPU
                all_embeddings.append(emb.cpu().numpy())

            if show_progress and (i + batch_size) % (batch_size * 5) == 0:
                logger.info(f"Encoded {min(i + batch_size, total)}/{total} chunks")

        result = np.vstack(all_embeddings).astype(np.float32)
        return result

    @classmethod
    def encode_query(cls, query: str) -> np.ndarray:
        """Encode a single query string with LRU caching.

        Returns shape (1, 1024). Cached results avoid GPU round-trip.
        """
        cached = cls._query_cache.get(query)
        if cached is not None:
            return cached

        result = cls.encode_texts([query])
        cls._query_cache.put(query, result)
        return result

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._encoder is not None

    @classmethod
    def dim(cls) -> int:
        if cls._encoder:
            return cls._encoder.out_dim
        return 1024  # Default BGE-M3 dim

    @classmethod
    def cache_stats(cls) -> dict:
        """Get query embedding cache statistics."""
        return cls._query_cache.stats

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the query embedding cache (e.g. after model reload)."""
        cls._query_cache.clear()
        logger.info("Query embedding cache cleared")
