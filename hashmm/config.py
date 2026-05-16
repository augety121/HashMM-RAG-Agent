"""Central configuration for HashMM-RAG.

Mirrors the design pattern of RAG-Anything's config: dataclass + env-var
overrides + .env file loading. One source of truth for every module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=".env", override=False)
except ImportError:
    # dotenv is optional; env vars from the shell still work
    pass

# Apply HF mirror early so subsequent transformers imports see it. AutoDL /
# Mainland-China users without this will hit hangs on model download.
if os.environ.get("HF_ENDPOINT"):
    # Already set by the user; respect it.
    pass
elif os.environ.get("USE_HF_MIRROR", "").lower() in ("1", "true", "yes"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


def _env(name: str, default: Any, cast: type = str) -> Any:
    """Read an env var with type casting. Falls back to default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class HashMMConfig:
    """All configurable knobs in one place. Read once at startup."""

    # ── Paths ─────────────────────────────────────────────────────────
    working_dir: str = field(default=_env("WORKING_DIR", "./rag_storage"))
    parser_output_dir: str = field(default=_env("PARSER_OUTPUT_DIR", "./output"))
    hash_index_dir: str = field(default=_env("HASH_INDEX_DIR", "./indexes"))
    checkpoint_dir: str = field(default=_env("CHECKPOINT_DIR", "./checkpoints"))
    data_dir: str = field(default=_env("DATA_DIR", "./data"))

    # ── LLM (OpenAI-compatible) ───────────────────────────────────────
    llm_api_key: str = field(default=_env("LLM_API_KEY", ""))
    llm_base_url: str = field(default=_env("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default=_env("LLM_MODEL", "gpt-4o-mini"))
    vision_model: str = field(default=_env("VISION_MODEL", "gpt-4o-mini"))
    embedding_model: str = field(default=_env("EMBEDDING_MODEL", "BAAI/bge-m3"))
    embedding_dim: int = field(default=_env("EMBEDDING_DIM", 1024, int))
    use_local_embedding: bool = field(
        default=_env("USE_LOCAL_EMBEDDING", True, bool)
    )
    """If True, use a local HuggingFace embedding model (BGE-M3 default).
    If False, call an OpenAI-compatible embedding API instead."""

    # ── Hash net architecture ─────────────────────────────────────────
    hash_text_encoder: str = field(default=_env("HASH_TEXT_ENCODER", "BAAI/bge-m3"))
    hash_image_encoder: str = field(
        default=_env("HASH_IMAGE_ENCODER", "openai/clip-vit-base-patch32")
    )
    """Image encoder backbone. Default CLIP-ViT-base-patch32 (605 MB, well-mirrored
    in China). To use SigLIP-2 instead (slightly stronger, 1.5 GB), set
    HASH_IMAGE_ENCODER=google/siglip2-base-patch16-256 in .env."""
    hash_bits: int = field(default=_env("HASH_BITS", 128, int))
    hash_proj_hidden: int = field(default=_env("HASH_PROJ_HIDDEN", 2048, int))
    hash_device: str = field(default=_env("HASH_DEVICE", "cuda:0"))

    # ── Training ──────────────────────────────────────────────────────
    hash_batch_size: int = field(default=_env("HASH_BATCH_SIZE", 64, int))
    hash_lr: float = field(default=_env("HASH_LR", 1e-4, float))
    hash_epochs: int = field(default=_env("HASH_EPOCHS", 20, int))
    hash_loss_w_quant: float = field(default=_env("HASH_LOSS_W_QUANT", 0.1, float))
    hash_loss_w_balance: float = field(default=_env("HASH_LOSS_W_BALANCE", 0.05, float))
    hash_tanh_temp_start: float = field(
        default=_env("HASH_TANH_TEMPERATURE_START", 1.0, float)
    )
    hash_tanh_temp_end: float = field(
        default=_env("HASH_TANH_TEMPERATURE_END", 10.0, float)
    )

    # ── Retrieval ─────────────────────────────────────────────────────
    retrieval_top_k: int = field(default=_env("RETRIEVAL_TOP_K", 20, int))
    hybrid_mode: str = field(default=_env("HYBRID_MODE", "adaptive"))
    rrf_k: int = field(default=_env("RRF_K", 60, int))
    dedup_hamming_threshold: int = field(
        default=_env("DEDUP_HAMMING_THRESHOLD", 8, int)
    )

    # ── MinerU parser backend ─────────────────────────────────────────
    # One of: pipeline | vlm-transformers | vlm-vllm-engine | vlm-sglang-engine
    # - pipeline (default): YOLO + OCR + table/formula models. Slow on first
    #   doc (~30-60s) but no extra deps.
    # - vlm-transformers: single MinerU2.5-VL model via plain transformers.
    #   Moderate speed, no extra deps beyond what's already installed.
    # - vlm-vllm-engine: fastest (~10-15s/doc on 4090) but needs `vllm`
    #   installed AND a careful pin to avoid upgrading torch.
    # - vlm-sglang-engine: alternative fast backend, needs sglang.
    mineru_backend: str = field(default=_env("MINERU_BACKEND", "pipeline"))
    mineru_device: str = field(default=_env("MINERU_DEVICE", "cuda:0"))
    mineru_lang: str = field(default=_env("MINERU_LANG", "en"))

    # ── Logging ───────────────────────────────────────────────────────
    log_level: str = field(default=_env("LOG_LEVEL", "INFO"))

    # ── Memory (M5): episodic + semantic cache ────────────────────────
    memory_dir: str = field(default=_env("MEMORY_DIR", "./memory"))
    """Where episodic SQLite + semantic cache files live. Created on first use."""

    # Semantic cache toggles
    semcache_enabled: bool = field(default=_env("SEMCACHE_ENABLED", True, bool))
    """Master switch. Per-query --no-cache overrides this."""

    semcache_hamming_threshold: int = field(
        default=_env("SEMCACHE_HAMMING_THRESHOLD", 12, int)
    )
    """Stage 1: Hamming distance below this → candidate. 128-bit codes; 12 ≈ 91% bit agreement."""

    semcache_cosine_threshold: float = field(
        default=_env("SEMCACHE_COSINE_THRESHOLD", 0.88, float)
    )
    """Stage 2: float cosine above this → real hit. Empirical default; tune via metrics CLI."""

    semcache_ttl_seconds: float = field(
        default=_env("SEMCACHE_TTL_SECONDS", 7 * 24 * 3600, float)
    )
    """Cache entry TTL. Default 7 days. 0 = never expire."""

    semcache_index_version: int = field(
        default=_env("SEMCACHE_INDEX_VERSION", 1, int)
    )
    """Bump this when retraining hash net or rebuilding hash index. Older
    entries become invalid automatically (their codes are from a different
    embedding space)."""

    semcache_max_entries: int = field(
        default=_env("SEMCACHE_MAX_ENTRIES", 10000, int)
    )
    """Cap on cache size. When exceeded, oldest (by last_hit_at) are evicted."""

    semcache_stage1_topk: int = field(
        default=_env("SEMCACHE_STAGE1_TOPK", 20, int)
    )
    """How many Hamming candidates to bring forward to stage 2."""

    # Working memory
    working_mem_max_turns: int = field(
        default=_env("WORKING_MEM_MAX_TURNS", 10, int)
    )

    def __post_init__(self) -> None:
        # Ensure dirs exist (cheap, idempotent)
        for d in (
            self.working_dir,
            self.parser_output_dir,
            self.hash_index_dir,
            self.checkpoint_dir,
            self.data_dir,
            self.memory_dir,
        ):
            Path(d).mkdir(parents=True, exist_ok=True)

        # Validate enum-ish fields early
        if self.hybrid_mode not in ("static", "hash_first", "adaptive"):
            raise ValueError(
                f"hybrid_mode must be one of static/hash_first/adaptive, got {self.hybrid_mode!r}"
            )
        if self.hash_bits not in (32, 64, 128, 256, 512, 1024):
            raise ValueError(
                f"hash_bits must be a power of two between 32 and 1024, got {self.hash_bits}"
            )
        _valid_backends = (
            "pipeline",
            "vlm-transformers",
            "vlm-vllm-engine",
            "vlm-sglang-engine",
            "vlm-http-client",
        )
        if self.mineru_backend not in _valid_backends:
            raise ValueError(
                f"mineru_backend must be one of {_valid_backends}, got {self.mineru_backend!r}"
            )

    # ── Convenience derived paths ─────────────────────────────────────
    @property
    def hash_net_ckpt(self) -> Path:
        return Path(self.checkpoint_dir) / "hash_net.pt"

    @property
    def hash_index_path(self) -> Path:
        return Path(self.hash_index_dir) / f"hash_{self.hash_bits}bit.faiss"

    @property
    def hash_metadata_path(self) -> Path:
        return Path(self.hash_index_dir) / "metadata.jsonl"

    # ── Memory paths ──────────────────────────────────────────────────
    @property
    def episodic_db_path(self) -> Path:
        return Path(self.memory_dir) / "episodic.sqlite"

    @property
    def semcache_dir(self) -> Path:
        d = Path(self.memory_dir) / "semcache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def semcache_db_path(self) -> Path:
        return self.semcache_dir / "meta.sqlite"

    @property
    def semcache_faiss_path(self) -> Path:
        return self.semcache_dir / f"codes_{self.hash_bits}bit.faiss"

    @property
    def semcache_embeddings_path(self) -> Path:
        return self.semcache_dir / "embeddings.npy"

    @property
    def semcache_ids_path(self) -> Path:
        """JSONL keeping faiss row → entry_id mapping (faiss has no metadata)."""
        return self.semcache_dir / "row_to_entry.jsonl"

    def to_dict(self) -> dict:
        """Serialise (excludes secrets)."""
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        # Don't leak the API key
        if d.get("llm_api_key"):
            d["llm_api_key"] = "***redacted***"
        return d
