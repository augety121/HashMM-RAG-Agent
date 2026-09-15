#!/usr/bin/env bash
# HashMM canonical launcher. Configuration belongs in .env, never here.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${HASHMM_PROJECT_DIR:-$SCRIPT_DIR}"
# A copied launcher may live outside the source tree (for example Downloads).
# Preserve the user's AutoDL convention as a discovery fallback, but never run
# an old/partial tree silently.
if [[ ! -f "$PROJECT_DIR/scripts/hashmm_launcher.py" && -f "/root/autodl-tmp/scripts/hashmm_launcher.py" ]]; then
  PROJECT_DIR=/root/autodl-tmp
fi
if [[ ! -f "$PROJECT_DIR/scripts/hashmm_launcher.py" ]]; then
  echo "[start] FAIL: project helper not found: $PROJECT_DIR/scripts/hashmm_launcher.py" >&2
  echo "[start] Upload the complete HashMM project, or set HASHMM_PROJECT_DIR to its root." >&2
  exit 2
fi
cd "$PROJECT_DIR"

PYTHON_BIN="${HASHMM_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3; else PYTHON_BIN=python; fi
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[start] FAIL: Python >=3.10 not found. Set HASHMM_PYTHON to its executable." >&2
  exit 127
fi

exec "$PYTHON_BIN" scripts/hashmm_launcher.py "$@"
