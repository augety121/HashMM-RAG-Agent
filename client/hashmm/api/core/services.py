"""ServiceRegistry — single source of truth for all shared services.

Replaces the scattered global variables (_state, _llm_fn, memory,
sem_cache, metrics) that were defined in server.py and accessed via
app_state wiring.

Lifecycle:
    1. FastAPI lifespan calls ServiceRegistry.init()
    2. Route handlers access services via ServiceRegistry.xxx
    3. FastAPI shutdown calls ServiceRegistry.shutdown()

All lazy-loading happens inside init(). After init() returns, all
services are either ready or logged as unavailable.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed
from hashmm.api.core.state import Metrics, SemanticCache, PersistentMemory
from hashmm.api.core.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = get_logger("hashmm.services")


def _sanitize_model_info(info: dict) -> dict:
    """Redact sensitive fields from model info before logging."""
    safe = {}
    for k, v in info.items():
        if k in ("api_key", "api_keys"):
            if isinstance(v, str) and len(v) > 8:
                safe[k] = f"{v[:3]}***{v[-3:]}"
            else:
                safe[k] = "***"
        elif k in ("password", "secret", "token"):
            safe[k] = "***"
        else:
            safe[k] = v
    return safe


class ServiceRegistry:
    """Process-global service registry.

    v12: Two-phase initialization for fast startup:
      Phase 1 (init_fast): DB + config + memory — <0.5s, server immediately available
      Phase 2 (init_heavy): Encoder + FAISS + BM25 + LLM — runs in background thread
    """

    # ── Core services ──
    metrics: Metrics | None = None
    memory: PersistentMemory | None = None
    sem_cache: SemanticCache | None = None

    # ── LLM ──
    llm_fn: Any = None
    llm_info: dict | None = None
    llm_breaker: CircuitBreaker = CircuitBreaker(
        name="llm", failure_threshold=5, recovery_timeout=30
    )

    # ── State dict (legacy — holds metadata, cfg, text_enc) ──
    state: dict[str, Any] = {}

    # ── Status tracking (v12) ──
    status: str = "starting"  # starting → loading_models → loading_index → loading_llm → ready
    status_detail: str = ""
    init_start_time: float = 0
    _initialized: bool = False
    _heavy_initialized: bool = False

    @classmethod
    def init_fast(cls) -> None:
        """Phase 1: Fast init (<0.5s) — DB, config, memory. Server can accept requests."""
        if cls._initialized:
            return
        cls.init_start_time = time.time()
        cls.status = "starting"
        logger.info("[Services] Phase 1: Fast init...")

        cls.metrics = Metrics()
        cls.memory = PersistentMemory()
        cls.sem_cache = SemanticCache()

        # GPU diagnostic
        try:
            import torch
            cuda_env = os.environ.get("CUDA_VISIBLE_DEVICES", "not set")
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                logger.info(f"[Services] GPU: {gpu_name} (CUDA_VISIBLE_DEVICES={cuda_env})")
            else:
                logger.warning(f"[Services] ⚠️ GPU not available (CUDA_VISIBLE_DEVICES={cuda_env})")
        except ImportError:
            pass

        # Load config
        from hashmm.config import HashMMConfig
        cfg = HashMMConfig()
        cls.state["cfg"] = cfg

        # Load metadata (fast, just reading a file)
        meta: list[dict] = []
        try:
            with open(Path(cfg.hash_index_dir) / "metadata.jsonl") as f:
                for line in f:
                    meta.append(json.loads(line))
        except FileNotFoundError:
            logger.info("[Services] metadata.jsonl not found (normal if using new pipeline)")
        cls.state["metadata"] = meta

        cls._initialized = True
        cls.status = "loading_models"
        cls.status_detail = "加载 BGE-M3 模型..."
        elapsed = round((time.time() - cls.init_start_time) * 1000)
        logger.info(f"[Services] Phase 1 done ({elapsed}ms). Server accepting requests.")

    @classmethod
    def init_heavy(cls) -> None:
        """Phase 2: Heavy init (5-10s) — Encoder, FAISS, BM25, LLM. Runs in background."""
        if cls._heavy_initialized:
            return

        # Step 1: Encoder (BGE-M3) — ~3s
        cls.status = "loading_models"
        cls.status_detail = "加载 BGE-M3 嵌入模型..."
        try:
            from hashmm.encoder_pool import EncoderPool
            encoder = EncoderPool.get_encoder()
            import torch

            def _text_enc_wrapper(texts: list[str]):
                with torch.no_grad():
                    return encoder(texts)

            cls.state["text_enc"] = _text_enc_wrapper
            logger.info(f"[Services] Encoder: BGE-M3, dim={EncoderPool.dim()}, device={EncoderPool._device}")
        except Exception as e:
            logger.warning(f"[Services] Encoder init failed: {e}")
            cls.state["text_enc"] = None

        # Step 2: Retrieval pipeline (FAISS + BM25) — ~3s
        cls.status = "loading_index"
        cls.status_detail = "加载检索索引..."
        try:
            from hashmm.retriever_bridge import init_retriever
            init_retriever()
            logger.info("[Services] Retrieval pipeline ready")
        except Exception as e:
            logger.warning(f"[Services] Retrieval pipeline init failed: {e}")

        # Step 3: LLM — ~1s
        cls.status = "loading_llm"
        cls.status_detail = "连接 LLM 服务..."
        cls.reload_llm()

        # Step 4: Tool registry
        try:
            from hashmm.tools.registry import ToolRegistry
            ToolRegistry.sync_from_legacy()
        except Exception as e:
            logger.warning(f"[Services] ToolRegistry sync failed: {e}")

        # Sync with legacy app_state
        cls._sync_app_state()

        cls.state["loaded"] = True
        cls._heavy_initialized = True
        cls.status = "ready"
        cls.status_detail = ""
        elapsed = round((time.time() - cls.init_start_time) * 1000)
        logger.info(f"[Services] All services initialized ({elapsed}ms)")

    @classmethod
    def init(cls) -> None:
        """Legacy sync init — calls both phases sequentially."""
        cls.init_fast()
        cls.init_heavy()

    @classmethod
    def reload_llm(cls) -> None:
        """Reload LLM function. V103.48: 若配置了 LLM 网关配置档（profile），
        优先用带多 provider 故障转移的 FailoverLLM；否则走原单模型逻辑（零变化）。"""
        try:
            fn = None
            info = None
            # V103.48: 优先尝试网关配置档（CC Switch + 9Router 基座）。
            try:
                from hashmm.llm_gateway import get_active_llm
                _gw = get_active_llm()
                if _gw is not None:
                    fn = _gw
                    info = {"model_name": _gw.model_name, "via": "gateway-failover"}
                    logger.info("[Services] LLM 网关已启用（多 provider 故障转移）")
            except Exception as _e:
                logger.warning(f"[Services] LLM 网关加载失败，回退单模型: {_e}")
            # 无配置档 → 原逻辑，行为完全不变
            if fn is None:
                from hashmm.api import model_manager
                fn, info = model_manager.get_active_llm_fn()
            cls.llm_fn = fn
            cls.llm_info = info
            cls.llm_breaker.reset()
            if fn:
                safe_info = _sanitize_model_info(info) if info else {}
                logger.info(f"[Services] LLM ready: {safe_info}")
            cls._sync_app_state()
        except Exception as e:
            logger.warning(f"[Services] LLM reload failed: {e}")

    @classmethod
    def call_llm(cls, prompt: str) -> str:
        """Call LLM with circuit breaker protection.

        Raises:
            RuntimeError if LLM not configured
            CircuitOpenError if breaker is OPEN
        """
        if not cls.llm_fn:
            from hashmm.api import database as db
            model = db.get_default_model()
            if model:
                raise RuntimeError(
                    f"LLM 已配置（{model.get('model_name')}）但连接失败，"
                    f"请在管理后台检查 API Key 和 Base URL"
                )
            raise RuntimeError("LLM 未配置，请在管理后台添加模型")

        if cls.metrics:
            cls.metrics.total_llm_calls += 1

        return cls.llm_breaker.call(cls.llm_fn, prompt)

    @classmethod
    def ensure_loaded(cls) -> None:
        """Lazy-init if not yet initialized."""
        if not cls._initialized:
            cls.init()

    @classmethod
    def shutdown(cls) -> None:
        """Clean shutdown — persist memory to disk."""
        if cls.memory:
            cls.memory.save_to_disk()
            logger.info("[Services] Memory saved to disk")

    @classmethod
    def _sync_app_state(cls) -> None:
        """Keep legacy app_state module in sync for route modules that use it."""
        try:
            from hashmm.api import app_state as _as
            _as.state = cls.state
            _as.llm_fn = cls.llm_fn
            _as.metrics = cls.metrics
            _as.memory = cls.memory
            _as.sem_cache = cls.sem_cache
        except Exception as _e:
            log_suppressed(logger, _e)

    @classmethod
    def health_check(cls) -> dict:
        """Return dependency health status for readiness probe."""
        checks: dict[str, dict] = {}

        # Database
        try:
            from hashmm.api import database as db
            with db._conn() as c:
                c.execute("SELECT 1")
            checks["database"] = {"ok": True}
        except Exception as e:
            checks["database"] = {"ok": False, "error": str(e)[:100]}

        # LLM
        checks["llm"] = {
            "ok": cls.llm_fn is not None,
            "model": cls.llm_info.get("model_name", "") if cls.llm_info else "",
            "breaker": cls.llm_breaker.to_dict(),
        }

        # Retrieval pipeline
        try:
            from hashmm.retriever_bridge import get_pipeline
            pipe = get_pipeline()
            if pipe and pipe.vector_index:
                checks["retrieval"] = {
                    "ok": True,
                    "vectors": pipe.vector_index.total_vectors,
                    "bm25_docs": pipe.bm25_index.size,
                }
            else:
                checks["retrieval"] = {"ok": False, "error": "Pipeline not loaded"}
        except Exception as e:
            checks["retrieval"] = {"ok": False, "error": str(e)[:100]}

        # GPU
        try:
            import torch
            checks["gpu"] = {
                "ok": torch.cuda.is_available(),
                "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                "memory_allocated": f"{torch.cuda.memory_allocated() / 1024**3:.1f}GB" if torch.cuda.is_available() else "0",
            }
        except Exception:
            checks["gpu"] = {"ok": False, "error": "torch not available"}

        # Embedding cache
        try:
            from hashmm.encoder_pool import EncoderPool
            checks["embedding_cache"] = EncoderPool.cache_stats()
        except Exception as _e:
            log_suppressed(logger, _e)

        return checks
