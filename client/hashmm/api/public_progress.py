"""Public progress projection for model-private reasoning.

Provider reasoning payloads are continuation state, not execution evidence.
This module is intentionally dependency-free so every API surface can project
the same stable status without copying private chain-of-thought into SSE,
database rows, logs or client state.
"""
from __future__ import annotations


PUBLIC_ANALYSIS_STATUS = "正在分析任务并选择下一步"


def public_analysis_status(_private_reasoning: object = None) -> str:
    """Return a stable public status and deliberately ignore private input."""

    return PUBLIC_ANALYSIS_STATUS
