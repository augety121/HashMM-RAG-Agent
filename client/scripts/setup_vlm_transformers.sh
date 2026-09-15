#!/usr/bin/env bash
# Setup MinerU vlm-transformers backend — the LOW-RISK fast option.
#
# This runs MinerU's 1.2B vision-language model via plain HuggingFace
# transformers. No vllm/sglang. Slower than vllm but:
#   - no torch upgrade risk
#   - 5-10× faster than the current pipeline backend
#   - one model loaded once instead of YOLO + OCR + table + formula
#
# Expected: ~30-60s per arXiv paper on a 4090 (vs. ~45min current).

set -e

echo "========================================="
echo "  HashMM-RAG: setup vlm-transformers"
echo "========================================="
echo

# ── 1. Verify torch is the right version ──────────────────────────────
echo "[1/4] Checking torch + GPU..."
python - <<'PY'
import torch
ver = torch.__version__
assert ver.startswith("2.5"), f"torch must be 2.5.x (got {ver})"
assert torch.cuda.is_available(), "CUDA not available"
print(f"  torch {ver}, GPU: {torch.cuda.get_device_name(0)}")
print("  ✓ ok")
PY

# ── 2. Free VRAM check ────────────────────────────────────────────────
echo
echo "[2/4] Checking VRAM (need ~5 GiB free for MinerU2.5-VL-1.2B in fp16)..."
FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
echo "  free VRAM: ${FREE_MB} MiB"
if [ "$FREE_MB" -lt 5000 ]; then
    echo "  ✗ less than 5 GiB free — kill running python processes first:"
    echo "      nvidia-smi"
    echo "      kill <pids>"
    exit 1
fi
echo "  ✓ ok"

# ── 3. Install mineru-vl-utils (transformers extra) ───────────────────
echo
echo "[3/4] Installing mineru-vl-utils[transformers]..."
echo "  --no-deps protects torch from being upgraded"
pip install --no-deps "mineru-vl-utils"
# Its transformers-backend deps (most likely already installed)
pip install --no-deps "qwen-vl-utils" || echo "  qwen-vl-utils optional"

# ── 4. Validate torch wasn't touched + try a tiny import ──────────────
echo
echo "[4/4] Validating install..."
python - <<'PY'
import torch
ver = torch.__version__
assert ver.startswith("2.5"), f"DANGER: torch was upgraded to {ver}!"
print(f"  torch still {ver}")

from mineru_vl_utils import MinerUClient
print(f"  ✓ mineru_vl_utils imports cleanly")

# We don't actually load the model here — just verify the import path
# is reachable. The first real call will download the 1.2B model
# (~2.5 GB) automatically.
print()
print("  Model will be auto-downloaded on first parse call:")
print("    opendatalab/MinerU2.5-2509-1.2B (~2.5 GiB)")
PY

echo
echo "========================================="
echo "  ✓ Setup complete!"
echo
echo "  Next: edit .env and set:"
echo "      MINERU_BACKEND=vlm-transformers"
echo
echo "  Or just export it for this session:"
echo "      export MINERU_BACKEND=vlm-transformers"
echo
echo "  Then re-run parsing:"
echo "      python scripts/01_parse_documents.py \\"
echo "          --input  /root/autodl-tmp/data/pdfs \\"
echo "          --output /root/autodl-tmp/data/parsed"
echo "========================================="
