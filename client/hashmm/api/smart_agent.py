"""SmartAgent — DEPRECATED in v7.0.

Replaced by ReactAgent (hashmm/react_agent.py) which uses pure-text tool
tags instead of function calling API, making it compatible with all LLMs
including DeepSeek (whose reasoning_content conflicts with function calling).

This stub exists only to prevent ImportError in case any code still
references SmartAgent.
"""
from __future__ import annotations
import warnings


class SmartAgent:
    """Deprecated — use ReactAgent instead."""

    def __init__(self, **kwargs):
        warnings.warn(
            "SmartAgent is deprecated since v7.0. Use ReactAgent instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def run(self, *args, **kwargs):
        raise NotImplementedError("SmartAgent is deprecated. Use ReactAgent.")

    def run_streaming(self, *args, **kwargs):
        raise NotImplementedError("SmartAgent is deprecated. Use ReactAgent.")


class MultiAgentExecutor:
    """Deprecated stub."""

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "MultiAgentExecutor is deprecated since v7.0.",
            DeprecationWarning,
            stacklevel=2,
        )
