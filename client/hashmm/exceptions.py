"""HashMM-RAG Exception Hierarchy — unified error handling across all modules.

Usage:
    from hashmm.exceptions import RetrievalError, LLMError

    try:
        results = pipeline.search(query)
    except RetrievalError as e:
        logger.warning(f"Retrieval failed: {e.message}")
        # graceful degradation
"""
from __future__ import annotations


class HashMMError(Exception):
    """Base exception for all HashMM-RAG errors."""

    def __init__(self, message: str, code: str = "internal_error",
                 details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class RetrievalError(HashMMError):
    """Failed to retrieve from knowledge base (FAISS, BM25, or pipeline)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="retrieval_error", details=details)


class LLMError(HashMMError):
    """LLM call failed (API error, timeout, rate limit, etc.)."""

    def __init__(self, message: str, model: str = "",
                 details: dict | None = None):
        d = details or {}
        if model:
            d["model"] = model
        super().__init__(message, code="llm_error", details=d)


class IngestError(HashMMError):
    """Document ingestion failed (parse, chunk, encode, or index)."""

    def __init__(self, message: str, filename: str = "",
                 stage: str = "", details: dict | None = None):
        d = details or {}
        if filename:
            d["filename"] = filename
        if stage:
            d["stage"] = stage
        super().__init__(message, code="ingest_error", details=d)


class ConfigError(HashMMError):
    """Configuration error (missing API key, invalid model, etc.)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="config_error", details=details)


class SafetyError(HashMMError):
    """Content safety violation detected."""

    def __init__(self, message: str = "检测到不安全内容",
                 details: dict | None = None):
        super().__init__(message, code="safety_error", details=details)


class AuthError(HashMMError):
    """Authentication or authorization failure."""

    def __init__(self, message: str = "认证失败",
                 details: dict | None = None):
        super().__init__(message, code="auth_error", details=details)
