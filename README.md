# HashMM-RAG

**Cross-modal hash-augmented multimodal RAG agent.**

HashMM-RAG 将跨模态哈希检索与 Agentic RAG 系统结合，实现了 **98.5% of BGE-M3 检索精度，索引压缩 124 倍**。系统包含意图分类、两阶段检索（hash 粗排 + cosine 精排）、三层记忆、语义缓存、安全防护等生产级能力。

---

## Benchmark

ViDoRe v2 benchmark（biomedical + economics，PaddleOCR，256-bit）

| Model | nDCG@5 | Index Size | Compression |
|---|---|---|---|
| BGE-M3 dense | 0.2761 | 2.8 MB | 1× |
| **HashMM-RAG 256-bit** | **0.2721** | **23 KB** | **124×** |

98.5% of BGE-M3 quality at 1/124th the storage.

## Architecture

```
User Query
  → Safety Check (prompt injection / banned words / XSS)
  → Follow-up Detection (理解"它""这个"等代词)
  → Intent Classification (academic_kb / academic_open / compare / chitchat)
  → Semantic Cache (cosine > 0.92 → cache hit, 0 LLM calls)
  → Query Rewrite (中文 → 英文关键词)
  → Two-Stage Retrieval:
      Stage 1: FAISS Binary Hamming (hash 粗排, top-20)
      Stage 2: BGE-M3 Cosine Similarity (精排, top-5)
      Fallback: BM25 Keyword Search
  → LLM Generation (DeepSeek / OpenAI compatible)
  → Answer Evaluation (LLM 1-5 质量评分, 不达标自动重试)
  → Persistent Memory (sessions / profiles / episodes 写磁盘)
  → Structured Metrics (延迟 / 缓存命中率 / token 用量)
```

## Tech Stack

| Component | Choice |
|---|---|
| Text Encoder | BGE-M3 (1024-d, frozen) |
| Image Encoder | SigLIP-2 base (768-d, frozen) |
| Hash Net | CrossModalHashNet, 256-bit |
| Agent | LangGraph + FastAPI |
| LLM | DeepSeek / OpenAI compatible |
| OCR | PaddleOCR v2.x |
| Vector DB | FAISS Binary |
| MCP | FastMCP (mcp ≥1.2) |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/augety121/hashmm-rag.git
cd hashmm-rag

# 2. Install
pip install -e ".[hash,agent,eval,mcp]"
pip install "paddleocr<3" fastapi uvicorn --break-system-packages

# 3. Train (需要 GPU)
python scripts/01_download_models.py
python scripts/02_extract_pairs.py --parsed data/parsed --chunks data/chunks.jsonl --pairs data/pairs.jsonl
HASH_BITS=256 python scripts/03_train_hash_net.py --pairs data/pairs.jsonl
HASH_BITS=256 python scripts/04_build_index.py --chunks data/chunks.jsonl

# 4. Clean index (去除 48% 噪音 chunk)
HASH_BITS=256 python scripts/17_rebuild_clean_index.py

# 5. Launch
export LLM_API_KEY="your-deepseek-key"
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-chat"
HASH_BITS=256 PYTHONPATH=. uvicorn hashmm.api.server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` for the web UI.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web UI |
| `/api/chat` | POST | Agent chat (JSON: `{message, session_id}`) |
| `/api/corpus/stats` | GET | Index statistics |
| `/api/sessions` | GET | List conversation sessions |
| `/api/sessions/{id}` | GET | Get session history |
| `/api/metrics` | GET | Latency / cache hit rate / LLM calls |
| `/api/experience` | GET | Episode log + user profiles |
| `/api/health` | GET | Readiness probe |
| `/docs` | GET | OpenAPI documentation |

## Benchmark Commands

```bash
# Run ViDoRe v2 benchmark
HASH_BITS=256 python scripts/09_run_vidore.py \
    --dataset biomedical_lectures_eng_v2 --retriever both --ocr paddleocr

# Multi-subset generalization test
HASH_BITS=256 python scripts/16_run_generalization.py --ocr paddleocr

# Fine-tune on target domain
python scripts/10_finetune_vidore.py --dataset biomedical_lectures_eng_v2
```

## MCP Server (Claude Desktop / Cursor)

```bash
PYTHONPATH=. python -m hashmm.mcp_server
```

Claude Desktop config (`~/.config/claude/claude_desktop_config.json`):
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

## Project Structure

```
hashmm/
  ingestion/       M1 — MinerU adapter, chunk + pair extraction
  hashing/         M2 — CrossModalHashNet, FAISS bridge, training
  retrieval/       M3 — vector / hash / hybrid, RRF
  agent/           M4 — LangGraph state machine (9 nodes)
  memory/          M5 — working / episodic / semantic cache
  benchmark/       M7 — ViDoRe loader, PaddleOCR, pytrec_eval
  mcp_server/      M6 — FastMCP server
  api/             REST API — Agentic RAG server (v0.9)
  config.py        Pydantic config

frontend/          Web UI
scripts/           Pipeline scripts (01-17)
tests/             86 unit tests
Dockerfile         Production container
docker-compose.yml Full-stack deployment
.github/workflows/ CI (lint + test + docker)
```

## Changelog

### v0.1.1 (2025-05-16) — Agentic RAG

- Agentic pipeline: intent classification → skill routing → evaluation → retry
- Two-stage retrieval: hash coarse ranking → BGE-M3 cosine re-ranking
- BM25 keyword search as fallback retrieval skill
- Hermes-style 3-layer memory (working / semantic / episodic)
- User profiling: topic tracking, language preference, adaptive responses
- Semantic cache: similar queries hit cache, 0 LLM calls
- Persistent storage: sessions/profiles/episodes survive restart
- Safety: prompt injection detection, banned words, XSS filtering, output sanitization
- Follow-up detection: understands pronouns and context references
- Structured metrics: latency, cache hit rate, LLM call count
- Answer quality evaluation: LLM scores 1-5, auto-retry if below threshold
- Experience logging: tracks which strategies work for which query types
- Clean index rebuild script (removed 48% junk chunks)
- max_tokens increased from 800 to 4096 (fixes table truncation)

### v0.1.0 (2025-05-16) — Benchmark Release

- Cross-modal hash network: BGE-M3 + SigLIP-2 → 256-bit binary codes
- ViDoRe v2 benchmark: 98.5% of BGE-M3 nDCG@5 at 124× compression
- PaddleOCR integration: +6 pts nDCG over Tesseract
- LangGraph agent (9 nodes, 2 conditional edges)
- Three-layer memory system
- MCP server (FastMCP)
- Gradio demo
- 86 unit tests
- Docker + GitHub Actions CI

## References

- DCMH — Jiang & Li, CVPR 2017
- Hash-RAG — Guo et al., ACL 2025 Findings
- ViDoRe v2 — Macé et al., 2025
- BGE-M3 — Chen et al., 2024
- PaddleOCR — Du et al., PaddlePaddle

## License

MIT
