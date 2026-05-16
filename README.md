<div align="center">

# HashMM-RAG

**Cross-Modal Hash-Augmented Multimodal RAG Agent**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-86%20passed-22c55e?logo=pytest)](tests/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-ef4444.svg)](https://github.com/astral-sh/ruff)
[![FAISS](https://img.shields.io/badge/FAISS-Binary-7c3aed)](https://github.com/facebookresearch/faiss)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-0ea5e9)](https://deepseek.com)

*98.5% of BGE-M3 retrieval quality · 124× index compression · Agentic RAG with two-stage retrieval*

[Quick Start](#quick-start) · [Benchmark](#benchmark) · [Architecture](#architecture) · [API Reference](#api-endpoints) · [Changelog](#changelog)

</div>

---

## Highlights

- 🔥 **98.5% of BGE-M3** retrieval quality with **124× smaller index** (23 KB vs 2.8 MB)
- 🧠 **Agentic RAG** — intent classification → skill routing → evaluation → auto-retry
- 🔍 **Two-stage retrieval** — hash coarse ranking (20 candidates) → BGE-M3 cosine re-ranking (top-5)
- 💾 **Semantic cache** — similar queries hit cache with 0 LLM calls
- 🛡️ **Safety layer** — prompt injection / banned words / XSS detection
- 🧩 **MCP server** — plug into Claude Desktop / Cursor
- 📊 **ViDoRe v2 validated** — benchmarked on 2 subsets with pytrec_eval

---

## Benchmark

> ViDoRe v2 (biomedical + economics, PaddleOCR, 256-bit, averaged across 2 subsets)

| Model | nDCG@5 | Index Size | Compression | vs BGE-M3 |
|:---|:---:|:---:|:---:|:---:|
| BGE-M3 dense (baseline) | 0.2761 | 2.8 MB | 1× | — |
| **HashMM-RAG 256-bit** | **0.2721** | **23 KB** | **124×** | **98.5%** |

<details>
<summary>📊 Per-dataset results (click to expand)</summary>

**biomedical_lectures_eng_v2** (1,016 docs, 160 queries)

| Model | nDCG@5 | nDCG@10 | R@5 | R@10 | MAP | Index |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| BGE-M3 dense | 0.3861 | 0.4193 | 0.4341 | 0.5322 | 0.3570 | 3.97 MB |
| HashMM-RAG 256-bit | 0.3851 | 0.4203 | 0.4325 | 0.5333 | 0.3516 | 0.03 MB |
| HashMM-RAG 128-bit | 0.3567 | 0.3887 | 0.3946 | 0.4890 | 0.3204 | 0.02 MB |

**economics_reports_v2** (452 docs, 232 queries)

| Model | nDCG@5 | nDCG@10 | R@5 | R@10 | MAP | Index |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| BGE-M3 dense | 0.1661 | 0.1847 | 0.0843 | 0.1549 | 0.1536 | 1.65 MB |
| HashMM-RAG 256-bit | **0.1696** | **0.1868** | **0.0855** | **0.1571** | **0.1599** | 0.01 MB |

> HashMM-RAG **outperforms** BGE-M3 on economics (102.1%).

</details>

<details>
<summary>📈 PaddleOCR impact</summary>

| OCR Engine | BGE-M3 nDCG@5 | HashMM-RAG nDCG@5 |
|:---|:---:|:---:|
| Tesseract (v0.1.0) | 0.3250 | 0.3052 |
| PaddleOCR (v0.1.1) | 0.3861 (+6.1 pts) | 0.3567 (+5.2 pts) |

</details>

---

## Architecture

```
User Query
  │
  ├─ 🛡️ Safety Check ──── prompt injection / banned words / XSS
  ├─ 🔗 Follow-up Detection ── "它""这个" → expand to full question
  ├─ 🏷️ Intent Classification ── academic_kb / academic_open / compare / chitchat
  ├─ ⚡ Semantic Cache ── cosine > 0.92 → cache hit (0 LLM calls)
  ├─ 🌐 Query Rewrite ── 中文 → English keywords
  │
  ├─ 🔍 Two-Stage Retrieval
  │     Stage 1: FAISS Binary Hamming ─── hash 粗排 (top-20)
  │     Stage 2: BGE-M3 Cosine ────────── 精排 (top-5)
  │     Fallback: BM25 Keyword Search
  │
  ├─ 🤖 LLM Generation ── DeepSeek / OpenAI compatible
  ├─ ✅ Answer Evaluation ── LLM scores 1-5, auto-retry if < 3
  ├─ 💾 Persistent Memory ── sessions / profiles / episodes → disk
  └─ 📊 Structured Metrics ── latency / cache hit rate / LLM calls
```

**Core innovation**: DCMH-style cross-modal hash network maps BGE-M3 (1024-d) + SigLIP-2 (768-d) → shared 256-bit binary codes via MLP heads with `BatchNorm1d(affine=False)` anti-collapse.

---

## Quick Start

```bash
# Clone
git clone https://github.com/augety121/hashmm-rag.git
cd hashmm-rag

# Install
pip install -e ".[hash,agent,eval,mcp]"
pip install "paddleocr<3" fastapi uvicorn --break-system-packages

# Train hash net + build index (GPU required)
HASH_BITS=256 python scripts/03_train_hash_net.py --pairs data/pairs.jsonl
HASH_BITS=256 python scripts/04_build_index.py --chunks data/chunks.jsonl
HASH_BITS=256 python scripts/17_rebuild_clean_index.py  # remove 48% junk chunks

# Launch
export LLM_API_KEY="your-deepseek-key"
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-chat"
HASH_BITS=256 PYTHONPATH=. uvicorn hashmm.api.server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` → Web UI with chat, agent trace, source citations.

### Pre-trained Weights

Download from [Releases](https://github.com/augety121/hashmm-rag/releases):

| File | Description |
|:---|:---|
| `hash_net.pt` | 256-bit cross-modal hash network (fine-tuned) |
| `hash_256bit.faiss` | FAISS binary index (10,032 clean chunks) |
| `metadata.jsonl` | Chunk metadata |

Place in `checkpoints/` and `indexes/` respectively.

---

## API Endpoints

| Endpoint | Method | Description |
|:---|:---:|:---|
| `/` | GET | Web UI |
| `/api/chat` | POST | Agent chat `{message, session_id}` |
| `/api/corpus/stats` | GET | Index stats, LLM status |
| `/api/sessions` | GET | Conversation history list |
| `/api/metrics` | GET | Latency, cache hit rate, LLM calls |
| `/api/experience` | GET | Episode log + user profiles |
| `/api/health` | GET | Readiness probe |
| `/docs` | GET | OpenAPI (Swagger) |

### MCP Server

```bash
PYTHONPATH=. python -m hashmm.mcp_server
```

<details>
<summary>Claude Desktop config</summary>

```json
{
  "mcpServers": {
    "hashmm-rag": {
      "command": "python",
      "args": ["-m", "hashmm.mcp_server"],
      "cwd": "/path/to/hashmm-rag"
    }
  }
}
```

</details>

---

## Project Structure

```
hashmm/
  api/             Agentic RAG server (v0.9, 617 lines)
  ingestion/       MinerU adapter → typed chunks
  hashing/         CrossModalHashNet, FAISS bridge
  retrieval/       Vector / hash / hybrid, RRF
  agent/           LangGraph state machine (9 nodes)
  memory/          Working / episodic / semantic cache
  benchmark/       ViDoRe loader, PaddleOCR, pytrec_eval
  mcp_server/      FastMCP (tools + resources + prompts)
  config.py        Pydantic config (env-var overrides)
frontend/          Web UI (chat + trace + citations)
scripts/           Pipeline scripts (01-17)
tests/             86 unit tests
```

---

## Key Design Decisions

| Decision | Rationale |
|:---|:---|
| DCMH-style hash heads | Learnable cross-modal alignment into shared binary space |
| `BatchNorm1d(affine=False)` | Hard anti-collapse: forces zero-mean unit-variance per bit |
| Frozen encoders + trainable heads | Cache BGE-M3/SigLIP outputs once, iterate hash heads in minutes |
| 256-bit codes | 99.7% of BGE-M3 at 124× compression (vs 92% at 199× for 128-bit) |
| Hash粗排 → Cosine精排 | Two-stage retrieval bridges hash approximation and true similarity |
| PaddleOCR over Tesseract | +6 pts nDCG on slide-heavy biomedical datasets |
| Semantic cache (cosine > 0.92) | Repeated/similar queries return instantly, 0 API cost |
| BM25 fallback | When hash codes miss, keyword matching catches exact term matches |
| Answer evaluation + retry | LLM self-judges quality 1-5, bad answers auto-regenerated |

---

## Changelog

### v0.1.1 (2025-05-16) — Agentic RAG

- Agentic pipeline: intent classification → skill routing → evaluation → retry
- Two-stage retrieval: hash coarse → BGE-M3 cosine re-ranking
- BM25 keyword search fallback
- Hermes-style 3-layer memory (working / semantic / episodic)
- User profiling: topic tracking, language preference
- Semantic cache: similar queries hit cache, 0 LLM calls
- Persistent storage: sessions / profiles survive restart
- Safety: prompt injection, banned words, XSS, output sanitization
- Follow-up detection: pronoun resolution via LLM
- Structured metrics: `/api/metrics` with latency, cache rate
- Answer evaluation: LLM 1-5 quality scoring, auto-retry
- Clean index rebuild: removed 48% junk chunks (19,399 → 10,032)

### v0.1.0 (2025-05-16) — Benchmark Release

- Cross-modal hash network: BGE-M3 + SigLIP-2 → 256-bit binary codes
- ViDoRe v2: 98.5% of BGE-M3 nDCG@5 at 124× compression
- PaddleOCR: +6 pts over Tesseract
- LangGraph agent, three-layer memory, MCP server
- 86 unit tests, Docker, CI

---


## References

DCMH (CVPR 2017) · Hash-RAG (ACL 2025) · ViDoRe v2 (2025) · BGE-M3 (2024) · ColPali (2024) · Mem0 (ECAI 2025) · PaddleOCR

**[DCMH](https://openaccess.thecvf.com/content_cvpr_2017/html/Jiang_Deep_Cross-Modal_Hashing_CVPR_2017_paper.html) (CVPR 2017)**：

**[Hash-RAG](https://aclanthology.org/2025.findings-acl.1376/) (ACL 2025)**：

**[ViDoRe v2](https://arxiv.org/abs/2505.17166) (2025)**：

**[BGE-M3](https://arxiv.org/abs/2402.03216) (2024)**：

**[ColPali](https://arxiv.org/abs/2407.01449) (2024)**：

**[Mem0](https://arxiv.org/abs/2504.19413) (ECAI 2025)**：

**[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)**：


## License

MIT
