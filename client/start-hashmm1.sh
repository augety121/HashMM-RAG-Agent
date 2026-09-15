#!/usr/bin/env bash
# HashMM server profile used on the AutoDL host.
#
# This file contains operational defaults only. Secrets, account identifiers,
# deployment URLs and service credentials belong in the project .env (mode
# 0600) or the inherited process environment. The canonical launcher loads
# that configuration, performs security diagnostics, and then starts HashMM.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export HASHMM_PROJECT_DIR="${HASHMM_PROJECT_DIR:-$SCRIPT_DIR}"
export HASHMM_DEPLOYMENT_PROFILE="${HASHMM_DEPLOYMENT_PROFILE:-autodl}"
export HASHMM_ENV="${HASHMM_ENV:-production}"
export HASHMM_IDENTITY_PROVIDER_REQUIRED="${HASHMM_IDENTITY_PROVIDER_REQUIRED:-supabase}"
export HASHMM_REQUIRE_AUTH="${HASHMM_REQUIRE_AUTH:-1}"
# Do not export HASHMM_HOST/HASHMM_PORT here. The canonical Python launcher
# loads .env first, then supplies the AutoDL defaults (0.0.0.0:6006) only when
# the operator did not configure them. Exporting defaults in this wrapper would
# incorrectly outrank a secure Tunnel deployment's HASHMM_HOST=127.0.0.1.

# Model and index locations. Existing environment values always win.
export HASHMM_SRC="${HASHMM_SRC:-/root/autodl-tmp}"
export HASH_INDEX_DIR="${HASH_INDEX_DIR:-/root/autodl-tmp/data/vector_index}"
export HASHMM_BASE_MODEL="${HASHMM_BASE_MODEL:-/root/autodl-tmp/models/Qwen2.5-7B-Instruct}"
export HASHMM_SEARCHR1_LORA="${HASHMM_SEARCHR1_LORA:-/root/autodl-tmp/models/qwen2.5-7b-hashmm-final-v2}"

# Full product profile: auditable long tasks, scheduler and retrieval features.
export HASHMM_PRESET="${HASHMM_PRESET:-max}"
export HASHMM_ACCESS_TTL="${HASHMM_ACCESS_TTL:-2592000}"
export HASHMM_AUDIT_TOOLS="${HASHMM_AUDIT_TOOLS:-1}"
export HASHMM_AGENT_TRACE="${HASHMM_AGENT_TRACE:-1}"
export HASHMM_SCHEDULER="${HASHMM_SCHEDULER:-1}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

# Optional local speech-to-text. Dependency installation is intentionally not
# performed on every startup; use `./hashmm-start.sh install-optional` once.
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HASHMM_STT_ENABLED="${HASHMM_STT_ENABLED:-1}"
export HASHMM_STT_MODEL="${HASHMM_STT_MODEL:-small}"
export HASHMM_STT_DEVICE="${HASHMM_STT_DEVICE:-cuda}"
export HASHMM_STT_COMPUTE="${HASHMM_STT_COMPUTE:-float16}"

# Benchmark defaults. Limits below the official suites are deliberately not
# set, so reports cannot accidentally present a quick sample as a comparable
# benchmark result.
export HASHMM_BENCH_MODE="${HASHMM_BENCH_MODE:-full}"
export HASHMM_BENCH_HOME="${HASHMM_BENCH_HOME:-/root/autodl-tmp/hashmm-benchmarks}"
export HASHMM_SEARCH_BACKEND="${HASHMM_SEARCH_BACKEND:-serper}"
export HASHMM_AGENTBENCH_ALLOW_LOCAL="${HASHMM_AGENTBENCH_ALLOW_LOCAL:-1}"
export HASHMM_BENCH_CONCURRENCY="${HASHMM_BENCH_CONCURRENCY:-6}"

if [[ ! -f "$SCRIPT_DIR/hashmm-start.sh" ]]; then
  echo "[start] FAIL: canonical launcher is missing: $SCRIPT_DIR/hashmm-start.sh" >&2
  exit 2
fi

# Compatibility recovery for the user-supplied pre-V344 launcher.  We pass the
# path to the Python launcher, which treats it as untrusted text and imports
# only allowlisted static assignments into .env.  The old script is never
# sourced or executed, existing non-placeholder values always win, and no
# credential value is printed.  Once .env is complete this becomes a no-op.
if [[ -z "${HASHMM_LEGACY_ENV_SCRIPT:-}" ]]; then
  for candidate in \
    "$SCRIPT_DIR/start-hashmm1 (1).sh" \
    "$SCRIPT_DIR/start-hashmm1.old.sh" \
    "$SCRIPT_DIR/start-hashmm1-legacy.sh"; do
    if [[ -f "$candidate" ]]; then
      export HASHMM_LEGACY_ENV_SCRIPT="$candidate"
      export HASHMM_AUTO_MIGRATE_LEGACY_ENV="${HASHMM_AUTO_MIGRATE_LEGACY_ENV:-1}"
      break
    fi
  done
fi

exec bash "$SCRIPT_DIR/hashmm-start.sh" "$@"
