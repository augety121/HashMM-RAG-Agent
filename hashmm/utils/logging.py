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
