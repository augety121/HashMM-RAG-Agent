#!/usr/bin/env bash
# Setup MinerU vlm-vllm-engine backend safely.
#
# Why a shell script and not Python: pip install vllm has a real risk of
# upgrading torch underneath you (vllm hard-pins torch versions). We do
# this step-by-step with --no-deps so torch stays at 2.5.1+cu124, then
# verify torch is still that version, then test vllm imports.

set -e

echo "========================================="
echo "  HashMM-RAG: setup vlm-vllm-engine"
echo "========================================="
echo

# ── 1. Sanity check: torch + GPU ──────────────────────────────────────
echo "[1/6] Checking current torch and GPU..."
python - <<'PY'
import sys
import torch
ver = torch.__version__
print(f"  torch version: {ver}")
assert ver.startswith("2.5"), f"torch must be 2.5.x (got {ver}); re-run the torch-fix steps first"
print(f"  cuda available: {torch.cuda.is_available()}")
assert torch.cuda.is_available(), "CUDA not available — fix that first"
props = torch.cuda.get_device_properties(0)
print(f"  GPU: {props.name}, {props.total_memory / 1024**3:.1f} GiB")
assert props.total_memory > 20 * 1024**3, "need >20 GiB VRAM"
print("  ✓ ok")
PY

# ── 2. Free VRAM check ────────────────────────────────────────────────
echo
echo "[2/6] Checking VRAM headroom (need ~6 GiB free for MinerU2.5-VL)..."
FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
echo "  free VRAM: ${FREE_MB} MiB"
if [ "$FREE_MB" -lt 6000 ]; then
    echo "  ✗ less than 6 GiB free — kill any python processes holding VRAM first:"
    echo "      nvidia-smi"
    echo "      kill <pids>"
    exit 1
fi
echo "  ✓ ok"

# ── 3. Disk check ─────────────────────────────────────────────────────
echo
echo "[3/6] Checking disk (vllm + MinerU2.5-VL ~5 GiB)..."
FREE_GB=$(df -BG /root/autodl-tmp | tail -1 | awk '{print $4}' | tr -d 'G')
echo "  free on /root/autodl-tmp: ${FREE_GB} GiB"
if [ "$FREE_GB" -lt 10 ]; then
    echo "  ✗ less than 10 GiB free on /root/autodl-tmp"
    exit 1
fi
echo "  ✓ ok"

# ── 4. Install vllm WITHOUT upgrading torch ───────────────────────────
echo
echo "[4/6] Installing vllm (with --no-deps to protect torch)..."
echo "  pinning vllm to 0.6.x which still supports torch 2.5.1"
# vllm 0.6.x is the last family that supports torch 2.5.x cleanly.
# Anything 0.7+ wants torch 2.6+, 0.20+ wants torch 2.11. Avoid both.
pip install --no-deps "vllm>=0.6.0,<0.7.0"

# Now install vllm's own deps minus torch
echo "  installing vllm's runtime deps (excluding torch/triton)..."
pip install --no-deps "xformers>=0.0.28" || echo "  (xformers optional — skipping if it fails)"
pip install "msgspec>=0.18" "outlines>=0.0.43" "lm-format-enforcer>=0.10" \
            "interegular" "tiktoken" "blake3" "py-cpuinfo" \
            "prometheus-fastapi-instrumentator>=7.0.0" \
            "partial-json-parser" "pyzmq" 2>&1 | tail -5

# ── 5. Install mineru-vl-utils ────────────────────────────────────────
echo
echo "[5/6] Installing mineru-vl-utils[vllm]..."
pip install --no-deps "mineru-vl-utils"

# ── 6. Validate torch wasn't touched ──────────────────────────────────
echo
echo "[6/6] Validating torch is still 2.5.1+cu124..."
python - <<'PY'
import torch
ver = torch.__version__
print(f"  torch version after install: {ver}")
assert ver.startswith("2.5"), f"DANGER: torch got upgraded to {ver}! Re-run torch-fix steps."
print(f"  cuda still works: {torch.cuda.is_available()}")
print(f"  GPU: {torch.cuda.get_device_name(0)}")

print()
print("Testing vllm import...")
try:
    import vllm
    print(f"  ✓ vllm {vllm.__version__} imports cleanly")
except Exception as e:
    print(f"  ✗ vllm import failed: {e}")
    raise

print()
print("Testing mineru-vl-utils import...")
try:
    from mineru_vl_utils import MinerUClient
    print(f"  ✓ mineru_vl_utils imports cleanly")
except Exception as e:
    print(f"  ✗ mineru_vl_utils import failed: {e}")
    raise
PY

echo
echo "========================================="
echo "  ✓ Setup complete!"
echo "  Now edit .env and set:"
echo "      MINERU_BACKEND=vlm-vllm-engine"
echo "  Then re-run scripts/01_parse_documents.py"
echo "========================================="
