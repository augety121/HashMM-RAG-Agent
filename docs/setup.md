# Setup on AutoDL (Ubuntu 22.04 + CUDA 12.4 + Python 3.12 + PyTorch 2.5.1)

This guide is tuned for the AutoDL image `cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1` on an RTX 4090 (24 GB).

---

## 0. AutoDL persistent storage

AutoDL gives you `/root/autodl-tmp/` as persistent disk (~50 GB by default). **Everything large goes here** — models, indexes, parsed data — so you don't redownload on every instance restart.

```bash
mkdir -p /root/autodl-tmp/hashmm
cd /root/autodl-tmp
```

## 1. Get the code

Either upload the zip via Jupyter's upload button, or:

```bash
cd /root/autodl-tmp
# unzip the file you got from Claude:
unzip ~/hashmm-rag.zip
cd hashmm-rag
```

## 2. AutoDL academic acceleration (optional but recommended)

AutoDL provides a free, fast mirror for major academic sites (HuggingFace, GitHub, arXiv). Activate it in **every Jupyter terminal** before running anything network-heavy:

```bash
source /etc/network_turbo
# or, manually:
export HF_ENDPOINT=https://hf-mirror.com
```

## 3. Python deps

```bash
# Recommended: use the system python; torch 2.5.1 is already installed.
pip install -e ".[hash,ingest,eval]"

# If you hit a slow PyPI, point to a Chinese mirror:
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

What this installs:
- `transformers`, `accelerate`, `Pillow`, `faiss-cpu` (for the hash core)
- `raganything`, `lightrag-hku` (for ingestion)
- `mineru[core]` (pulled in by raganything — heavy, ~3 GB of models the first run)

## 4. Configure

```bash
cp .env.example .env
nano .env   # edit the API key and any paths
```

Required edits:
- `LLM_API_KEY` — your DeepSeek key
- `HF_HOME` — point to `/root/autodl-tmp/hashmm/.hf_cache` (already default in the example)

## 5. First-run model downloads

These happen automatically the first time the respective script runs, but you can pre-fetch to avoid blocking later:

```bash
# BGE-M3 (text encoder — also used as embedding for RAG-Anything)
python -c "from transformers import AutoModel, AutoTokenizer; \
           AutoTokenizer.from_pretrained('BAAI/bge-m3'); \
           AutoModel.from_pretrained('BAAI/bge-m3')"

# SigLIP-2 (image encoder)
python -c "from transformers import AutoModel, AutoProcessor; \
           AutoProcessor.from_pretrained('google/siglip2-base-patch16-256'); \
           AutoModel.from_pretrained('google/siglip2-base-patch16-256')"

# MinerU PDF parser models (do this once, ~3 GB)
mineru-models-download --source modelscope
```

Models cache to `$HF_HOME` (i.e. `/root/autodl-tmp/hashmm/.hf_cache`) — survives instance restarts.

## 6. End-to-end smoke run

```bash
# 0. (optional) grab a few arXiv PDFs to play with
python scripts/00_download_arxiv.py --output /root/autodl-tmp/hashmm/data/pdfs

# 1. parse the PDFs (slow on first run — MinerU models bootstrap)
python scripts/01_parse_documents.py \
    --input  /root/autodl-tmp/hashmm/data/pdfs \
    --output /root/autodl-tmp/hashmm/data/parsed

# 2. extract chunks + cross-modal pairs (fast)
python scripts/02_extract_pairs.py \
    --parsed /root/autodl-tmp/hashmm/data/parsed \
    --chunks /root/autodl-tmp/hashmm/data/chunks.jsonl \
    --pairs  /root/autodl-tmp/hashmm/data/pairs.jsonl

# 3. train the hash net (20 epochs, ~30 min on a 4090 with ~6 papers)
python scripts/03_train_hash_net.py \
    --pairs /root/autodl-tmp/hashmm/data/pairs.jsonl

# 4. encode everything and build the binary index
python scripts/04_build_index.py \
    --chunks /root/autodl-tmp/hashmm/data/chunks.jsonl

# 5. query!
python scripts/05_query.py --query "attention mechanism in transformer"
python scripts/05_query.py --query "figure showing model architecture" --modality image
```

---

## Troubleshooting

**`HfHubHTTPError: 401` or `OSError: We couldn't connect to huggingface.co`**
> You forgot `HF_ENDPOINT`. Run `source /etc/network_turbo` or `export HF_ENDPOINT=https://hf-mirror.com`.

**`CUDA out of memory` during training**
> Drop `HASH_BATCH_SIZE` from 64 to 32 (or 16). The frozen encoders dominate memory; halving the batch frees about 6 GB.

**`mineru-models-download` fails or hangs**
> Try `--source modelscope` (China-friendly) instead of the default HuggingFace source. If you don't need OCR for scanned PDFs, set `PARSER=docling` in `.env` to skip MinerU entirely.

**Training runs but val mAP stays near zero**
> Look at the loss components in the training log. If `loss_pair` plateaus but `loss_balance` is near zero, the network has collapsed — increase `HASH_LOSS_W_BALANCE` to 0.1. If `loss_quant` is dominating, decrease `HASH_LOSS_W_QUANT` to 0.05.

**RAG-Anything import error**
> `pip install raganything` doesn't always pull a working version. Verify with `python -c "from raganything import RAGAnything; print('ok')"`. If broken, try `pip install raganything==1.3.0` or pin to whatever the latest stable on PyPI is.

**Jupyter kernel dies when calling the image encoder**
> Usually means the SigLIP model didn't finish downloading. Check `du -sh /root/autodl-tmp/hashmm/.hf_cache/models--google--siglip2*`. If it's smaller than ~400 MB, redownload.

**Faiss "AttributeError: module 'faiss' has no attribute 'IndexBinaryFlat'"**
> You installed `faiss-cpu==1.7.x` which lacks newer features. Force the latest: `pip install -U faiss-cpu>=1.8`.

---

## Disk-space planning

| Item | Size |
|---|---|
| HuggingFace model cache (BGE-M3 + SigLIP-2) | ~2.5 GB |
| MinerU model cache | ~3 GB |
| Parsed JSONs for ~100 PDFs | ~200 MB |
| chunks.jsonl + pairs.jsonl for 100 PDFs | ~50 MB |
| LightRAG storage (rag_storage/) for 100 PDFs | ~500 MB |
| Hash index (10k chunks × 128 bits) | ~160 KB |
| Hash net checkpoint | ~80 MB |
| Vector index (10k chunks × 1024d float32) | ~40 MB |

50 GB persistent disk handles ~1000 PDFs comfortably.
