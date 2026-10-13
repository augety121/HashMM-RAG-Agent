"""Centralised logging setup."""

import logging
import os
import sys
from logging import Logger


_CONFIGURED = False


def get_logger(name: str = "hashmm") -> Logger:
    """Return a logger configured once per process. Safe to call repeatedly."""
    global _CONFIGURED
    if not _CONFIGURED:
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

        handler = logging.StreamHandler(sys.stderr)
        fmt = "%(asctime)s %(levelname)s %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

        root = logging.getLogger("hashmm")
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)
        root.propagate = False
        _CONFIGURED = True

    return logging.getLogger(name)

def log_suppressed(logger: Logger, exc: BaseException, note: str = "") -> None:
    """Record an intentionally-suppressed exception at DEBUG level.

    Replaces silent ``except ...: pass`` blocks: behaviour is unchanged (the
    error is still swallowed and the path degrades gracefully), but the failure
    is no longer invisible — it leaves a debug trace with accurate source
    location (``stacklevel=2`` points at the call site). Mature agents hold this
    bar: errors may be non-fatal, but never soundless. Never raises.
    """
    try:
        msg = f"suppressed: {type(exc).__name__}: {exc}"
        if note:
            msg = f"{note} — {msg}"
        logger.debug(msg, stacklevel=2)
    except Exception:
        pass  # logging must never itself break the caller
