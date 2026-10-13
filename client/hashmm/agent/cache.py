"""Cache Layer — 双层缓存（内存 LRU + 可选 Redis）。

用途：
  1. 查询缓存 — 同一问题 5 分钟内不重复检索
  2. 嵌入缓存 — 同一文本不重复编码
  3. 会话状态缓存 — 跨请求共享

设计：Redis 不可用时自动退化为纯内存缓存。
"""
from __future__ import annotations

import hashlib
import time
import threading
from typing import Any
from collections import OrderedDict

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.cache")


class LRUCache:
    """线程安全的内存 LRU 缓存。"""

    def __init__(self, max_size: int = 500, default_ttl: int = 300):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            value, expire_at = self._cache[key]
            if time.time() > expire_at:
                del self._cache[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int | None = None):
        ttl = ttl or self._default_ttl
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time() + ttl)
            # Evict oldest if over capacity
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str):
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1) * 100, 1),
        }


class CacheLayer:
    """统一缓存接口 — 内存 + 可选 Redis。

    Usage:
        cache = get_cache()
        result = cache.get_retrieval("query_hash")
        if result is None:
            result = do_retrieval(query)
            cache.set_retrieval("query_hash", result)
    """

    def __init__(self):
        # L1: 内存缓存（始终可用）
        self._retrieval_cache = LRUCache(max_size=200, default_ttl=300)    # 5分钟
        self._embedding_cache = LRUCache(max_size=1000, default_ttl=3600)  # 1小时
        self._session_cache = LRUCache(max_size=100, default_ttl=1800)     # 30分钟

        # L2: Redis（可选）
        self._redis = None
        self._try_redis()

    def _try_redis(self):
        """尝试连接 Redis。失败则静默退化为纯内存。

        配置（按优先级）：
          - HASHMM_REDIS_URL  例如 redis://:password@host:6379/0
          - HASHMM_REDIS_HOST / HASHMM_REDIS_PORT / HASHMM_REDIS_DB
          - 默认 localhost:6379 db=0
        """
        import os
        url = os.environ.get("HASHMM_REDIS_URL", "").strip()
        try:
            import redis
        except ImportError:
            logger.warning(
                "[Cache] Redis URL/host configured but the 'redis' Python "
                "package is not installed. Run: pip install redis. "
                "Falling back to memory-only cache."
                if (url or os.environ.get("HASHMM_REDIS_HOST"))
                else "[Cache] Redis not available, using memory-only cache"
            )
            return
        try:
            if url:
                r = redis.Redis.from_url(
                    url, decode_responses=True,
                    socket_timeout=2, socket_connect_timeout=2,
                )
                where = url.split("@")[-1]
            else:
                host = os.environ.get("HASHMM_REDIS_HOST", "localhost")
                port = int(os.environ.get("HASHMM_REDIS_PORT", "6379"))
                dbn = int(os.environ.get("HASHMM_REDIS_DB", "0"))
                r = redis.Redis(
                    host=host, port=port, db=dbn, decode_responses=True,
                    socket_timeout=2, socket_connect_timeout=2,
                )
                where = f"{host}:{port}"
            r.ping()
            self._redis = r
            logger.info(f"[Cache] Redis connected ({where})")
        except Exception as e:
            # Show the reason — silent fallback made this impossible to debug.
            if url or os.environ.get("HASHMM_REDIS_HOST"):
                logger.warning(
                    f"[Cache] Redis configured but connection failed "
                    f"({type(e).__name__}: {str(e)[:120]}). "
                    f"Is redis-server running? Falling back to memory-only cache."
                )
            else:
                logger.info("[Cache] Redis not available, using memory-only cache")

    # ── 检索缓存 ──

    def get_retrieval(self, query: str) -> dict | list | None:
        """获取缓存的检索结果（dict 或 list，JSON 可序列化即可）。"""
        key = f"ret:{self._hash(query)}"
        # L1
        result = self._retrieval_cache.get(key)
        if result is not None:
            return result
        # L2 (Redis)
        if self._redis:
            try:
                data = self._redis.get(key)
                if data:
                    import json
                    result = json.loads(data)
                    self._retrieval_cache.set(key, result)  # Promote to L1
                    return result
            except Exception as _e:
                log_suppressed(logger, _e)
        return None

    def set_retrieval(self, query: str, results: dict | list, ttl: int = 300):
        """缓存检索结果（dict 或 list）。"""
        key = f"ret:{self._hash(query)}"
        self._retrieval_cache.set(key, results, ttl)
        if self._redis:
            try:
                import json
                self._redis.setex(key, ttl, json.dumps(results, ensure_ascii=False))
            except Exception as _e:
                log_suppressed(logger, _e)

    # ── 嵌入缓存 ──

    def get_embedding(self, text: str) -> list | None:
        """获取缓存的嵌入向量。"""
        key = f"emb:{self._hash(text)}"
        return self._embedding_cache.get(key)

    def set_embedding(self, text: str, vector: list, ttl: int = 3600):
        """缓存嵌入向量。"""
        key = f"emb:{self._hash(text)}"
        self._embedding_cache.set(key, vector, ttl)

    # ── 会话缓存 ──

    def get_session(self, session_id: str) -> dict | None:
        """获取会话状态。"""
        return self._session_cache.get(f"sess:{session_id}")

    def set_session(self, session_id: str, state: dict, ttl: int = 1800):
        """缓存会话状态。"""
        self._session_cache.set(f"sess:{session_id}", state, ttl)

    # ── 统计 ──

    @property
    def stats(self) -> dict:
        return {
            "retrieval": self._retrieval_cache.stats,
            "embedding": self._embedding_cache.stats,
            "session": self._session_cache.stats,
            "redis": self._redis is not None,
        }

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()[:16]


# ── 单例 ──

_instance: CacheLayer | None = None


def get_cache() -> CacheLayer:
    global _instance
    if _instance is None:
        _instance = CacheLayer()
    return _instance
