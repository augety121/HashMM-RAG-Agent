"""HashMM-RAG utilities — logging, helpers."""
from __future__ import annotations
import logging
import sys

_configured = False

# ── Fix: server.py uses logger.info("...", flush=True) ──
# flush is a print() param, not logging. Patch _log to ignore unexpected kwargs.
_original_log = logging.Logger._log

def _patched_log(self, level, msg, args, exc_info=None, extra=None,
                 stack_info=False, stacklevel=1, **kwargs):
    _original_log(self, level, msg, args, exc_info=exc_info, extra=extra,
                  stack_info=stack_info, stacklevel=stacklevel)

logging.Logger._log = _patched_log


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    global _configured
    if not _configured:
        root = logging.getLogger("hashmm")
        root.setLevel(logging.INFO)
        if not root.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            ))
            root.addHandler(handler)
        _configured = True
    return logging.getLogger(name)
