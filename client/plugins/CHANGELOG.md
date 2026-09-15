# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.0] — 2026-05-14

### Milestone: M1–M7 closed (M6 deferred)

**Benchmark Results (ViDoRe v2 `biomedical_lectures_eng_v2`):**
- HashMM-RAG fine-tuned: nDCG@5 = 0.3052, index = 0.02 MB
- BGE-M3 dense baseline: nDCG@5 = 0.3250, index = 3.89 MB
- 94% of BGE-M3 nDCG@5, 199× index compression

### Added
- **M7 benchmark** — ViDoRe v2 evaluation with pytrec_eval (nDCG, Recall, MAP, MRR)
  - `09_run_vidore.py`: dataset loader, OCR cache, BGE-M3 + HashMM-RAG retrievers
  - `10_finetune_vidore.py`: in-domain fine-tuning via off-diagonal Pearson-r loss
  - Safety check: auto-rollback if binary Pearson r doesn't improve
  - Best-step tracking captures peak weights across oscillating training
- **Benchmark report generator** (`hashmm/benchmark/reports.py`)
- **Resumable PDF parsing** via `.processed_files.txt` checkpoint file
- **Overnight pipeline** (`scripts/run_parse_overnight.sh`)

### Changed
- Fine-tune loss: replaced failed MSE and listwise-CE with direct off-diagonal
  Pearson-r maximisation (mean/scale invariant, no temperature hyperparameter)

### Fixed
- Diagonal masking in similarity-based losses (root cause of fine-tune attempt 2
  failure — unmasked self-similarity dominated softmax by ~100×)

## [0.3.0] — 2026-05-12

### Added
- **M5 three-layer memory** — working / episodic / semantic cache
  - Semantic cache: two-stage hash-then-cosine lookup (exact hit 0.24 ms)
  - Episodic store: SQLite WAL mode, session/turn tables with FK cascade
  - Working memory: in-process state with failed-query tracking
  - Index versioning for cache invalidation after hash net retraining
  - LRU eviction (oldest 10% on capacity hit)
  - Atomic writes for FAISS + numpy files
- **M5 agent integration** — 3 memory nodes wired into LangGraph graph
  - `semcache_lookup` (entry point, short-circuits on hit)
  - `semcache_write` (post-generate)
  - `episodic_write` (post-generate)
- Scripts: `07_semcache_stats.py`, `08_session_resume.py`
- 26 memory tests

## [0.2.0] — 2026-05-10

### Added
- **M4 LangGraph agent** — 6 core nodes + conditional refine loop
  - Intent classifier (rule-based, 4 intents)
  - Plan→retrieve→check→generate pipeline
  - Quality checks: diversity, modality match, Hamming sanity
  - Refine loop capped at 2 attempts (never loops forever)
  - `trace` field with `Annotated[list, operator.add]` accumulator
- Script: `06_agent_query.py`
- 25 agent tests

## [0.1.0] — 2026-05-07

### Added
- **M1 ingestion** — RAG-Anything MinerU adapter + chunk extractor
  - 4 modality types: text, image, table, chart
  - Cross-modal pair extraction (figure caption ↔ figure image)
  - Deterministic chunk IDs for incremental indexing
- **M2 cross-modal hashing** — DCMH-style hash net
  - BGE-M3 text encoder + SigLIP-2 image encoder (both frozen)
  - 3-layer MLP hash heads with BatchNorm(affine=False)
  - Three-term loss: pairwise + quantization + bit-balance
  - Temperature annealing (tau) on tanh
  - LSB-first bit packing matching FAISS convention
- **M3 retrieval** — vector / hash / hybrid + RRF fusion
  - FAISS flat (float) + FAISS binary (128-bit) indexes
  - Reciprocal Rank Fusion (k=60)
  - Hamming dedup (< 8 bits threshold)
- Scripts: `00_download_arxiv.py` through `05_query.py`
- `quickstart.ipynb`
- 26 tests (chunk extractor, config, bits, retrieval)
- Setup guides: `docs/setup.md`, `docs/setup-zh.md`
