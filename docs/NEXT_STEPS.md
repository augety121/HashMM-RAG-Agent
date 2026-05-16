# Phase 1 → Phase 2 handoff

## What's in this drop (Phase 1 = M1-M3 + scripts + tests + docs)

| Item | Path | Status |
|---|---|---|
| RAG-Anything ingestion adapter | `hashmm/ingestion/adapter.py` | ✓ |
| Chunk + cross-modal pair extractor | `hashmm/ingestion/chunk_extractor.py` | ✓ tested (7/7) |
| Frozen text/image encoders (BGE-M3, SigLIP-2) | `hashmm/hashing/encoders.py` | ✓ |
| Cross-modal hash net (DCMH-family) | `hashmm/hashing/hash_net.py` | ✓ bit-packing tested |
| Losses (pairwise + quant + balance) | `hashmm/hashing/losses.py` | ✓ |
| Training loop | `hashmm/hashing/train.py` | ✓ |
| Faiss binary index wrapper | `hashmm/hashing/index.py` | ✓ |
| Retriever protocol | `hashmm/retrieval/base.py` | ✓ |
| Hash retriever | `hashmm/retrieval/hash_retriever.py` | ✓ |
| Vector retriever (LightRAG wrapper) | `hashmm/retrieval/vector_retriever.py` | ✓ |
| Hybrid router (static / hash_first / adaptive) | `hashmm/retrieval/hybrid_router.py` | ✓ RRF tested (5/5) |
| Hash dedup post-processor | `hashmm/retrieval/post_process.py` | ✓ |
| CLI scripts 00-05 | `scripts/` | ✓ AST-validated |
| Jupyter quickstart | `quickstart.ipynb` | ✓ |
| AutoDL setup guide | `docs/setup.md` | ✓ |
| Tests (no-torch path) | `tests/` | ✓ 19/19 passing |

## What you should do after you receive this

1. **Unpack on AutoDL** under `/root/autodl-tmp/hashmm-rag/`
2. **Install deps**: `pip install -e ".[hash,ingest,eval]"`
3. **Set up `.env`** with your DeepSeek key
4. **Run the smoke pipeline** following `docs/setup.md` or `quickstart.ipynb`
5. **Collect numbers** — at minimum: val mAP@10 (t2i and i2t) at end of training, and a feel for retrieval quality on 5-10 hand-picked queries

## What we deliberately did NOT build yet (Phase 2 — after you run Phase 1)

| Module | Why deferred |
|---|---|
| **M4 — LangGraph Agent layer** | Needs real chunks + working retrieval to design state schema around. Building it before we know retrieval shape would mean rewriting twice. |
| **M5 — Three-layer memory (Hermes-inspired)** | Same as M4 — sits on top of the agent. |
| **M6 — MCP Server wrapper** | One-day job once M2/M3 are stable. Trivial. |
| **M7 — ViDoRe / MS MARCO benchmark scripts** | Should be run after we know our pipeline is producing sane numbers on the dev set. |
| **Demo Gradio app** | Same reason — wait for working pipeline. |

## What to send back to me to start Phase 2

After your smoke run, paste me:

1. **Training log** — the last 5 epochs' lines showing `val_map_t2i` / `val_map_i2t`. If those numbers look sad (< 0.3) we tune before going further.
2. **Output of**:
   ```bash
   ls -la /root/autodl-tmp/hashmm/data/
   wc -l /root/autodl-tmp/hashmm/data/chunks.jsonl /root/autodl-tmp/hashmm/data/pairs.jsonl
   ```
3. **One sample query** result from `scripts/05_query.py` — paste the rich table.
4. **Any error tracebacks**.

With those four things I can:
- Tune the hash net if it underperforms
- Start building the LangGraph agent around the actual retrieval behavior
- Wire up the ViDoRe benchmark for the paper-quality numbers

## Known risks / things to watch

1. **MinerU first-run download is slow on AutoDL even with academic acceleration** (~3 GB of OCR/layout models). Budget 30 min for this. After that it's cached.
2. **First epoch of training is slow** because encoder caches warm up. Epochs 2+ should be ~3-4x faster.
3. **With only 6 arXiv papers from `00_download_arxiv.py` you'll have ~30-60 pairs total** — barely enough to train. For a serious run, drop 50+ papers into `data/pdfs`.
4. **Watch GPU memory during training**: with `HASH_BATCH_SIZE=64` and both encoders loaded, expect ~14-18 GB used. Drop to 32 if you hit OOM.
5. **DeepSeek vision (`deepseek-chat` doesn't have strong VLM)**: image-content understanding will fall back to caption-based reasoning. This is fine for retrieval (we use SigLIP for that) but limits multimodal *generation* quality. If you want strong VLM, swap to a Qwen-VL or GLM-4V API in `.env`.

## Bug-report priority

If something breaks, in this order:
1. ImportError → dependency mismatch → tell me the exact error
2. CUDA OOM → tell me your batch size + what step
3. MinerU parse failure → I can fall back to a simpler docling parser
4. Training loss NaN → typically the tanh temperature is too high too fast → I tune the annealing schedule
