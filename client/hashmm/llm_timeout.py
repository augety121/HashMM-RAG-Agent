"""Centralised LLM client timeout policy.

Why this exists
---------------
The OpenAI SDK's default timeout is 10 minutes. For an online service that is
effectively "hang forever": if the upstream LLM (e.g. a slow reasoning model
like DeepSeek) stops responding, the request blocks, requests pile up, the
thread pool starves, and the whole service can cascade. Mature agents always
bound LLM calls with an explicit, shorter timeout.

This module provides one place to configure that bound. Every OpenAI client in
the codebase is created via :func:`client_timeout`, so the policy is uniform and
operator-tunable via env vars:

  HASHMM_LLM_TIMEOUT          total read timeout, seconds (default 120)
  HASHMM_LLM_CONNECT_TIMEOUT  connect timeout, seconds   (default 10)

120s read is generous enough for slow reasoning models + long generations, while
still guaranteeing a stuck upstream can never hang a worker indefinitely.
"""
from __future__ import annotations

import os


def _f(env: str, default: float) -> float:
    try:
        v = float(os.environ.get(env, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def read_timeout() -> float:
    return _f("HASHMM_LLM_TIMEOUT", 120.0)


def connect_timeout() -> float:
    return _f("HASHMM_LLM_CONNECT_TIMEOUT", 10.0)


def client_timeout():
    """Return a timeout object suitable for ``OpenAI(timeout=...)``.

    Prefers ``httpx.Timeout`` (separate connect/read bounds) when httpx is
    available — which it always is under the OpenAI SDK — and falls back to a
    plain float total timeout otherwise. Never raises.
    """
    rt, ct = read_timeout(), connect_timeout()
    try:
        import httpx
        # connect: how long to establish the TCP/TLS connection;
        # read/write/pool: bounded by the overall read timeout.
        return httpx.Timeout(rt, connect=ct)
    except Exception:
        return rt
