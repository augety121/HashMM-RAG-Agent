"""Run repository tests with the bundled dependency runtime, without mutating it.

The embedded Windows Python intentionally ignores PYTHONPATH.  Pytest and its
dependencies therefore live in a separate tooling directory and are inserted
only for this process.  This keeps the shipped runtime identical to
runtime-info.json while still making release tests reproducible.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLING = ROOT / ".tmp" / "v1900-pytest-tooling"
if not (TOOLING / "pytest").is_dir():
    raise SystemExit(f"pytest tooling not found: {TOOLING}")
sys.path.insert(0, str(TOOLING))
sys.path.insert(0, str(ROOT))
runpy.run_module("pytest", run_name="__main__")
