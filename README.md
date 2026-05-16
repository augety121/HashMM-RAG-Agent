# HashMM-RAG

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests: 86 passed](https://img.shields.io/badge/tests-86%20passed-brightgreen)]()
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Cross-modal hash-augmented multimodal RAG agent.**

HashMM-RAG replaces the dense-vector retrieval layer in multimodal RAG with
learned cross-modal hash codes. On the ViDoRe v2 biomedical benchmark,
**256-bit HashMM-RAG matches 99.7% of BGE-M3's nDCG@5 (0.385 vs 0.386) while
using a 124× smaller index** (32 KB vs 3.97 MB). The system combines a
DCMH-style cross-modal hash network, a LangGraph agent with rule-based
routing, a three-layer memory system with hash-indexed semantic cache, and an
MCP server for integration with Claude Desktop / Cursor.

> **v0.5.0** — M1–M7 closed. M6 MCP server shipped. 86 tests. PaddleOCR.

---

## Benchmark Results

ViDoRe v2 `biomedical_lectures_eng_v2` (1,016 documents, 160 queries).
OCR: PaddleOCR v2.x. Evaluation: pytrec_eval.

| Model | nDCG@5 | nDCG@10 | R@5 | R@10 | MAP | Index | Compression |
|---|---|---|---|---|---|---|---|
| BGE-M3 dense | **0.3861** | 0.4193 | 0.4341 | 0.5322 | 0.3570 | 3.97 MB | 1× |
| HashMM-RAG 256-bit | 0.3851 | 0.4203 | 0.4325 | 0.5333 | 0.3516 | 0.03 MB | **124×** |
| HashMM-RAG 128-bit | 0.3567 | 0.3887 | 0.3946 | 0.4890 | 0.3204 | 0.02 MB | **199×** |

**256-bit HashMM-RAG matches BGE-M3 within 0.1 pts nDCG@5 — functionally
equivalent retrieval quality at 1/124th the storage.**

### PaddleOCR Impact

Switching OCR from Tesseract to PaddleOCR improved both models significantly:

| Configuration | BGE-M3 nDCG@5 | HashMM-RAG 128-bit nDCG@5 |
|---|---|---|
| Tesseract (v0.4.0) | 0.3250 | 0.3052 |
| PaddleOCR (v0.5.0) | 0.3861 (+6.1 pts) | 0.3567 (+5.2 pts) |

OCR quality is the single largest contributor to retrieval performance on
slide-heavy datasets.

---

## Architecture

```
                       PDFs / images / tables
                        │
       ┌────────────────▼───────────────┐
       │ M1  Ingestion                  │  MinerU adapter → typed chunks
       │     + cross-modal pair extract │  (text / image / table / chart)
       └────────────────┬───────────────┘
                        │
       ┌────────────────▼───────────────┐
       │ M2  Cross-Modal Hash Net       │  DCMH-style: BGE-M3 + SigLIP-2
       │     128/256-bit binary codes   │  → shared code space via MLP heads
       └────────────────┬───────────────┘
                        │
       ┌────────────────▼───────────────┐
       │ M3  Retrieval Layer            │  vector / hash / hybrid (RRF)
       │     + Hamming dedup            │
       └────────────────┬───────────────┘
                        │
       ┌────────────────▼───────────────────────────────┐
       │ M4  LangGraph Agent (9 nodes)                  │
       │   semcache → classify → plan → retrieve →      │
       │   check → {generate | refine} → write → END    │
       └────────────────┬───────────────────────────────┘
                        │
       ┌────────────────▼───────────────┐
       │ M5  Three-Layer Memory         │  working / episodic / semcache
       │     hash-indexed cache:        │  O(N) Hamming + O(K·d) cosine
       └────────────────────────────────┘

       ┌────────────────────────────────┐
       │ M6  MCP Server                 │  cross_modal_search tool
       │     Claude Desktop / Cursor    │  stdio transport
       └────────────────────────────────┘

       ┌────────────────────────────────┐
       │ M7  Benchmark                  │  ViDoRe v2, pytrec_eval
       │     PaddleOCR + fine-tuning    │
       └────────────────────────────────┘
```

---

## Quick Start

```bash
# ── Environment ────────────────────────────────────────────────────
export HF_ENDPOINT=https://hf-mirror.com
export MINERU_MODEL_SOURCE=modelscope
pip install -e ".[hash,ingest,agent,eval,mcp]"
pip install "paddleocr<3" --break-system-packages

# ── 1. Bootstrap corpus ───────────────────────────────────────────
bash scripts/run_parse_overnight.sh

# ── 2. Train hash net + build index ──────────────────────────────
python scripts/03_train_hash_net.py --pairs data/pairs.jsonl
python scripts/04_build_index.py --chunks data/chunks.jsonl

# ── 3. Query ──────────────────────────────────────────────────────
python scripts/05_query.py --query "cross-modal hashing"
python scripts/06_agent_query.py --query "compare ColPali and ColBERT"

# ── 4. Benchmark (PaddleOCR) ─────────────────────────────────────
HASH_BITS=256 python scripts/09_run_vidore.py \
    --dataset biomedical_lectures_eng_v2 --retriever both --ocr paddleocr

# ── 5. Launch demo ────────────────────────────────────────────────
pip install gradio --break-system-packages
python scripts/demo_gradio.py

# ── 6. Start MCP server (for Claude Desktop / Cursor) ────────────
python -m hashmm.mcp_server.server
```

### Claude Desktop Integration

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hashmm-rag": {
      "command": "python",
      "args": ["-m", "hashmm.mcp_server.server"],
      "cwd": "/root/autodl-tmp"
    }
  }
}
```

Then ask Claude: *"Search my papers for cross-modal hashing methods"* — it
calls `cross_modal_search` automatically.

---

## Project Structure

```
hashmm/
  ingestion/       M1 — MinerU adapter, chunk + pair extraction
  hashing/         M2 — CrossModalHashNet, losses, FAISS bridge, training
  retrieval/       M3 — vector / hash / hybrid, RRF, Hamming dedup
  agent/           M4 — LangGraph state machine (9 nodes, 2 conditional edges)
  memory/          M5 — working / episodic / hash-indexed semantic cache
  benchmark/       M7 — ViDoRe loader, PaddleOCR, pytrec_eval evaluator
  mcp_server/      M6 — FastMCP server (tools + resources + prompts)
  config.py        Pydantic config with env-var overrides

scripts/
  00-10            Pipeline scripts (download → parse → train → index → query → benchmark)
  demo_gradio.py   Interactive web demo
  16_run_generalization.py   Multi-subset benchmark runner

tests/             86 unit tests (no GPU required)
```

---

## Key Design Decisions

| Decision | Why |
|---|---|
| DCMH-style hashing | Learnable cross-modal alignment into shared binary space |
| `BatchNorm1d(affine=False)` | Hard anti-collapse: forces zero-mean unit-variance per bit |
| Frozen encoders + trainable heads | Cache encoder outputs once; iterate on hash heads in minutes |
| 256-bit codes (vs 128) | 99.7% of BGE-M3 at 124× compression (vs 92% at 199×) |
| PaddleOCR over Tesseract | +6 pts nDCG on slide-heavy datasets |
| LangGraph state machine | Rule-based routing keeps 90% of queries deterministic |
| Hash-indexed semantic cache | O(N) Hamming + O(K·d) cosine ≪ O(N·d) full scan |
| MCP server (FastMCP) | One implementation, callable from Claude / Cursor / LangGraph |

---

## Tests

```bash
pytest tests/ -v    # 86 tests, no GPU
```

| File | Tests | Coverage |
|---|---|---|
| `test_agent.py` | 25 | Intent, routing, quality checks, refine loop |
| `test_memory.py` | 26 | SessionStore, semantic cache write/lookup/TTL/eviction |
| `test_benchmark.py` | 16 | Evaluator, report rendering, retrievers |
| `test_chunk_extractor.py` | 7 | Chunk ID stability, pair extraction |
| `test_config_and_bits.py` | 7 | Config validation, bit packing |
| `test_retrieval.py` | 5 | RRF fusion, dedup, top-k |

---

## Tech Stack

| Component | Choice |
|---|---|
| Text encoder | BGE-M3 (1024-d, frozen) |
| Image encoder | SigLIP-2 base (768-d, frozen) |
| Hash net | CrossModalHashNet, 128/256-bit |
| Agent | LangGraph ≥0.2.50 |
| LLM | DeepSeek API |
| OCR | PaddleOCR v2.x (GPU-accelerated) |
| MCP | FastMCP (mcp ≥1.2) |
| Evaluation | pytrec_eval |
| Demo | Gradio |
| Runtime | Python 3.12, PyTorch 2.5.1, CUDA 12.4, RTX 4090 |

---


## License

MIT

## References

- DCMH — Jiang & Li, CVPR 2017
- Hash-RAG — Guo et al., ACL 2025 Findings
- RAG-Anything — HKUDS, based on LightRAG (EMNLP 2025)
- ViDoRe v2 — Macé et al., 2025
- ColPali — Faysse et al., 2024
- BGE-M3 — Chen et al., 2024
- Mem0 — ECAI 2025, three-layer memory taxonomy
- PaddleOCR — Du et al., PaddlePaddle
