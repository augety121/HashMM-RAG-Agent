# Resuming HashMM-RAG in a new chat session

> Paste the **PROMPT TO USE** block at the bottom into a new chat. Attach the
> repo zip / latest source. The new Claude instance will have full context.

---

## Current state (as of v0.4.0)

**Closed:** M1 (ingestion), M2 (hash net), M3 (retrieval), M4 (LangGraph agent),
M5 (3-layer memory), M7 (ViDoRe benchmark, including in-domain fine-tune).
**Pending:** M6 (MCP server).

**Benchmark numbers** (`biomedical_lectures_eng_v2`, 1016 docs, 160 queries):

| Model | nDCG@5 | Index | Δ |
|---|---|---|---|
| BGE-M3 dense | 0.3250 | 3.89 MB | (baseline) |
| HashMM-RAG zero-shot | 0.2854 | 0.02 MB | -3.96 pts, 199× compression |
| HashMM-RAG fine-tuned | **0.3052** | 0.02 MB | **-1.98 pts, 199× compression** |

**Pipeline numbers** (after overnight expansion to 100 papers, your goal):
- Pre-expansion: 14 papers, 2,286 chunks, 495 pairs
- Target: ~100 papers, ~15,000 chunks, ~3,500 pairs
- Hash net val mAP@10 should rise from 0.501 → 0.55-0.60 with more data

## Server / environment

- AutoDL container `cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1`
- RTX 4090, 23 GB VRAM, 24-core CPU, 31 GB RAM
- Work dir: `/root/autodl-tmp/` (flat layout, code at top level)
- Local models cached: `/root/autodl-tmp/.local_models/` (bge-m3, siglip2-base-patch16-256)
- `HF_ENDPOINT=https://hf-mirror.com`, `MINERU_MODEL_SOURCE=modelscope` (BOTH REQUIRED)
- LLM via DeepSeek API; embeddings via local BGE-M3

## Critical landmines (don't relearn the hard way)

1. **`MINERU_MODEL_SOURCE=modelscope`** is required; without it MinerU hits
   xethub.hf.co which DNS-fails in China.
2. **`use_safetensors=True`** on transformers loaders to avoid CVE-2025-32434.
3. **`BatchNorm1d(affine=False)`** on HashHead — hard anti-collapse. Don't remove.
4. **`pack_bits` is LSB-first**; don't use raw `np.packbits` (defaults to MSB-first)
   anywhere bit codes flow between modules.
5. **Hash net must be in `.eval()`** before calling `sign_text` (BN running stats).
6. **`trace: Annotated[list[dict], operator.add]`** — without the reducer,
   LangGraph overwrites instead of accumulating.
7. **Mask the diagonal** in any softmax-over-similarity-matrix loss — the
   self-similarity = 1 dominates and signal drowns.
8. **Atomic writes for FAISS/npy** — write to `.tmp.{pid}` then `os.replace`.
9. **`np.save` auto-appends `.npy`** to filenames; either pass file handles or
   construct the temp name with the suffix already included.

## File layout (top-level, in working dir)

```
hashmm/                package source (8 modules)
scripts/               00 download → 10 fine-tune, plus shell helpers
tests/                 86 unit tests (pytest)
docs/                  README + this CONTEXT + talking_points + architecture
benchmarks/            JSON + markdown reports from 09_run_vidore.py
benchmark_cache/       cached ViDoRe datasets + OCR text + BGE-M3 corpus embeds
data/                  pdfs/, parsed/, chunks.jsonl, pairs.jsonl
checkpoints/           hash_net.pt + hash_net.pt.original (backup)
indexes/               hash_128bit.faiss + metadata.jsonl
memory/                episodic.sqlite + semcache/
.env                   secrets + paths (see .env.example)
```

## What works, what's slow, what's pending

| Stage | Time on 4090 | Resumable? | Notes |
|---|---|---|---|
| Download 100 PDFs | ~30 min | Yes | 00_download_arxiv.py skips existing |
| Parse 100 PDFs | ~22 hours | **Yes (since v0.4.0)** | `.processed_files.txt` in output dir |
| Chunk + pair extract | ~1 min | Yes (idempotent) | 02_extract_pairs.py |
| Train hash net | 5-10 min | No (cheap to rerun) | 03_train_hash_net.py |
| Build FAISS index | 1-2 min | No (cheap) | 04_build_index.py |
| ViDoRe v2 download | ~5 min | Yes (HF cache) | 09_run_vidore.py |
| ViDoRe Tesseract OCR | ~2 min | **Yes (per-image cache)** | first run only |
| ViDoRe benchmark eval | < 1 min | n/a | repeat freely |
| ViDoRe fine-tune | 0.8 sec | n/a (so cheap) | 10_finetune_vidore.py |
| MCP server | NOT BUILT | — | M6, pending |

## Style preferences (state these to the new Claude)

- Chinese for conversational replies, **English for code/comments**.
- Complete copy-pasteable shell commands (no placeholder paths).
- Honest about failures — diagnose root cause before patching.
- No bullshit positivity; if it doesn't work, say so clearly.
- Avoid emojis unless the user uses them first.
- When uncertain, search the web (HuggingFace docs, arxiv) — don't bluff.
- Sandbox limitations: no torch, no faiss, no rich, no datasets package.
  Test via AST checks + stub modules; full e2e tests run on the user's box.

---

## PROMPT TO USE (paste this in a new chat)

```
我在继续一个叫 HashMM-RAG 的项目 — 跨模态哈希增强的多模态 RAG agent。
基于 RAG-Anything + LightRAG,用 DCMH-style 跨模态哈希做检索层,
LangGraph 做 agent 编排,三层记忆系统(working/episodic/semcache),
最后用 ViDoRe v2 benchmark 评测。

当前版本:v0.4.0,M1-M5 + M7 已闭合,M6 (MCP server) 待做。

最新 benchmark 数字(ViDoRe v2 biomedical_lectures_eng_v2):
- BGE-M3 dense baseline:  nDCG@5 = 0.3250,  index = 3.89 MB
- HashMM-RAG fine-tuned:  nDCG@5 = 0.3052,  index = 0.02 MB
- 即 94% BGE-M3 nDCG@5 + 199× 索引压缩

服务器: AutoDL CUDA 12.4 / Python 3.12 / torch 2.5.1 / RTX 4090
工作目录: /root/autodl-tmp/ (扁平,代码在根)
关键环境变量: HF_ENDPOINT=https://hf-mirror.com  MINERU_MODEL_SOURCE=modelscope
本地模型: /root/autodl-tmp/.local_models/{bge-m3, siglip2-base-patch16-256}
LLM: DeepSeek API

我把项目源码作为附件给你。先看 docs/CONTEXT.md(就是这个文档)了解
完整背景,看 docs/talking_points.md 了解项目卖点,看 docs/architecture.md
看模块依赖和踩坑历史。

风格:
- 中文回复 + 英文代码/注释
- 完整可复制命令(不用占位符)
- 失败时严肃 post-mortem,先找根因再补丁
- 不假装,搜不到就说搜不到

我接下来想做的事(选 1-2 个,你看怎么排):
1. M6 MCP server — 把 agent 包装成 Claude/ChatGPT desktop 可调用的服务
2. 在更多 ViDoRe v2 子集上跑 benchmark(economics, ESG 等)
3. 把 OCR 从 Tesseract 换成 PaddleOCR,看 nDCG 能不能再涨 3-5 pts
4. 用 256/512 bit 重训 hash net,看能否进一步逼近 BGE-M3
5. 实现 "HashMM-RAG first-stage + ColPali rerank" 的两阶段检索
6. 其他(我告诉你)

请先打印你对项目当前状态的理解(尤其 v0.4.0 的具体数字 + 86 个测试名单),
然后等我说接下来做哪一项。
```

---

## Tips for the handoff

- **Send the zip**: `hashmm-rag.zip` (~140 KB). The new Claude can extract
  it and `view` any file.
- **Send the latest benchmark report**: `benchmarks/biomedical_lectures_eng_v2_report.md`.
- **Mention if `hash_net.pt` has been overwritten by fine-tune** — the
  `hash_net.pt.original` backup is the ML-papers-domain model; current
  `hash_net.pt` is the ViDoRe-finetuned one. Restore via
  `cp checkpoints/hash_net.pt.original checkpoints/hash_net.pt` before
  doing M3 demos on your ML papers.
