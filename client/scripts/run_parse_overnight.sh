#!/bin/bash
#
# run_parse_overnight.sh — full overnight pipeline for ~100 papers.
#
# Stages (each resumable; partial completion is safe):
#   1. download all PDFs in paper_list.py        (~30 min, network bound)
#   2. parse with MinerU (resumable per-file)    (~13 min × N papers)
#   3. extract chunks + pairs (cheap, idempotent) (~1 min)
#
# Re-runs skip already-done work. Hash net retrain + index rebuild are
# kept MANUAL (run them after morning sanity check on parse results).
#
# Usage (run in screen / nohup so SSH disconnects don't kill it):
#   screen -S parse                            # or: nohup
#   bash scripts/run_parse_overnight.sh
#   # Ctrl+a d to detach; reattach with: screen -r parse
#
# Watch live progress:
#   tail -f logs/overnight/parse_*.log
#
# Check status next morning:
#   ls -lh data/parsed/                # how many JSONs now
#   wc -l data/parsed/.processed_files.txt
#   tail -50 logs/overnight/parse_*.log
#   cat logs/overnight/SUMMARY.txt     # written at end

set -e   # exit on any unhandled error within stages
set -u   # error on undefined variables (catches typos)

# ── Configuration ───────────────────────────────────────────────────
WORK_DIR="${WORK_DIR:-/root/autodl-tmp}"
DATA_DIR="${WORK_DIR}/data"
PDF_DIR="${DATA_DIR}/pdfs"
PARSED_DIR="${DATA_DIR}/parsed"
LOG_DIR="${WORK_DIR}/logs/overnight"
TS=$(date +%Y%m%d_%H%M%S)
SUMMARY="${LOG_DIR}/SUMMARY_${TS}.txt"

mkdir -p "${PDF_DIR}" "${PARSED_DIR}" "${LOG_DIR}"

cd "${WORK_DIR}"

# Required environment (verify before starting)
export PYTHONUNBUFFERED=1
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"

echo "════════════════════════════════════════════════════════════"
echo "HashMM-RAG overnight pipeline"
echo "Started: $(date)"
echo "Work dir: ${WORK_DIR}"
echo "PDF dir : ${PDF_DIR}  (current: $(ls ${PDF_DIR} 2>/dev/null | wc -l) files)"
echo "Parsed  : ${PARSED_DIR}  (current: $(ls ${PARSED_DIR}/*.json 2>/dev/null | wc -l) files)"
echo "Logs    : ${LOG_DIR}"
echo "HF mirror: ${HF_ENDPOINT}"
echo "MinerU  : ${MINERU_MODEL_SOURCE}"
echo "════════════════════════════════════════════════════════════"
echo ""

# ── Stage 1: download PDFs ──────────────────────────────────────────
DOWNLOAD_LOG="${LOG_DIR}/download_${TS}.log"
echo "[Stage 1/3] Downloading PDFs from paper_list.py (100 papers)..."
echo "  log: ${DOWNLOAD_LOG}"
T0=$(date +%s)

# Read paper IDs; download_arxiv.py skips already-existing files.
PAPER_IDS=$(python scripts/paper_list.py)
python scripts/00_download_arxiv.py --output "${PDF_DIR}" --ids ${PAPER_IDS} \
    2>&1 | tee "${DOWNLOAD_LOG}" || echo "[WARN] download step had failures; continuing"

T1=$(date +%s)
N_PDFS=$(ls ${PDF_DIR}/*.pdf 2>/dev/null | wc -l)
echo "[Stage 1/3] DONE in $((T1-T0))s. ${N_PDFS} PDFs on disk."
echo ""

# ── Stage 2: parse with MinerU ──────────────────────────────────────
# This is the long pole. Resumable: if interrupted, restart this script
# and it will skip already-parsed PDFs.
PARSE_LOG="${LOG_DIR}/parse_${TS}.log"
echo "[Stage 2/3] Parsing PDFs through MinerU (this is the long pole)..."
echo "  log: ${PARSE_LOG}"
echo "  resume: tracked in ${PARSED_DIR}/.processed_files.txt"

T2=$(date +%s)
# `set +e` here so a single bad PDF doesn't kill the whole job.
set +e
python scripts/01_parse_documents.py \
    --input  "${PDF_DIR}" \
    --output "${PARSED_DIR}" \
    2>&1 | tee "${PARSE_LOG}"
PARSE_RC=$?
set -e
T3=$(date +%s)
N_PARSED=$(ls ${PARSED_DIR}/*.json 2>/dev/null | wc -l)
echo "[Stage 2/3] DONE in $((T3-T2))s. ${N_PARSED} parsed JSONs (exit=${PARSE_RC})."
echo ""

# ── Stage 3: extract chunks + pairs ─────────────────────────────────
CHUNK_LOG="${LOG_DIR}/chunks_${TS}.log"
echo "[Stage 3/3] Extracting chunks + cross-modal pairs..."
echo "  log: ${CHUNK_LOG}"
T4=$(date +%s)

python scripts/02_extract_pairs.py 2>&1 | tee "${CHUNK_LOG}" \
    || echo "[WARN] chunk/pair extraction had failures; check the log"

T5=$(date +%s)
N_CHUNKS=$(wc -l < "${DATA_DIR}/chunks.jsonl" 2>/dev/null || echo 0)
N_PAIRS=$(wc -l  < "${DATA_DIR}/pairs.jsonl" 2>/dev/null || echo 0)
echo "[Stage 3/3] DONE in $((T5-T4))s.  chunks=${N_CHUNKS}  pairs=${N_PAIRS}"
echo ""

# ── Summary ────────────────────────────────────────────────────────
TOTAL=$((T5-T0))
{
    echo "HashMM-RAG overnight pipeline summary"
    echo "Started:   $(date -d "@${T0}" 2>/dev/null || date)"
    echo "Finished:  $(date)"
    echo "Wall time: ${TOTAL}s ($((TOTAL/60)) min)"
    echo ""
    echo "Stages:"
    echo "  download : $((T1-T0))s  (${N_PDFS} PDFs)"
    echo "  parse    : $((T3-T2))s  (${N_PARSED} JSONs)"
    echo "  chunks   : $((T5-T4))s  (${N_CHUNKS} chunks, ${N_PAIRS} pairs)"
    echo ""
    echo "Next steps (manual; not auto-run for safety):"
    echo "  # 1. Retrain hash net on the bigger corpus (~3-10 min)"
    echo "  python scripts/03_train_hash_net.py"
    echo ""
    echo "  # 2. Rebuild the hash index (~1-2 min)"
    echo "  python scripts/04_build_index.py"
    echo ""
    echo "  # 3. Bump semcache index_version so old entries become invalid"
    echo "  # (or set SEMCACHE_INDEX_VERSION=2 in .env)"
} | tee "${SUMMARY}"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "Done at $(date). Summary: ${SUMMARY}"
echo "════════════════════════════════════════════════════════════"
