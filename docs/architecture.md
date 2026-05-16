# HashMM-RAG Architecture

> For engineers, reviewers, and future-you. Deeper than the README.

---

## System Overview

HashMM-RAG is a 6-module system (M1-M5 + M7) where data flows linearly
through ingestion → hashing → indexing, then at query time through a
LangGraph state machine that routes to the appropriate retriever and
persists results in a three-layer memory.

```
                 OFFLINE (build time)                  ONLINE (query time)
    ┌─────────────────────────────────┐    ┌──────────────────────────────────┐
    │                                 │    │                                  │
    │  PDFs ─→ M1 parse ─→ chunks    │    │  query ─→ M4 agent              │
    │              │                  │    │              │                    │
    │              ▼                  │    │         semcache_lookup (M5)     │
    │  pairs ─→ M2 train ─→ hash_net │    │         ↙ HIT      ↘ MISS      │
    │              │                  │    │   return cached   classify →     │
    │              ▼                  │    │                   plan →         │
    │  chunks ─→ M3 index ─→ FAISS   │    │                   retrieve (M3) │
    │          (float + binary)       │    │                   check → gen   │
    │                                 │    │                   write (M5)    │
    └─────────────────────────────────┘    └──────────────────────────────────┘
```

The offline pipeline runs once (or incrementally when new documents arrive).
The online pipeline runs per query, with the semantic cache short-circuiting
when possible.

---

## Module Dependency Graph

```
M1 ingestion ───→ chunks.jsonl ────────┬─────────────────────────────────┐
                                       │                                 │
                                       ▼                                 │
M2 hashing ───→ hash_net.pt ────→ M3 retrieval ←──── float embeddings ──┘
                    │                  │
                    │                  └─→ FAISS float + FAISS binary index
                    │
                    ▼
              M5 semcache ←─── M4 agent ←─── user query
                    │              │
                    ▼              ▼
              M5 episodic ←──── final answer

                                                  M7 benchmark uses M2+M3
                                                  on external ViDoRe data
                                                  (independent of M1/M4/M5)
```

---

## Data Contracts (the interfaces between modules)

### `chunks.jsonl` — M1 output, M2/M3 input

```json
{
  "chunk_id": "doc-77af66473e/p17/0",
  "doc_id": "doc-77af66473e",
  "modality": "text|image|table|chart",
  "text": "the textual content (caption, OCR, etc.)",
  "image_path": "/abs/path/page17_fig3.png",
  "meta": {"page_idx": 17, "source": "MinerU pipeline"}
}
```

Chunk IDs are deterministic: `{doc_id}/p{page}/i{index}`. This means
re-parsing the same PDF produces the same IDs — important for incremental
index updates.

### `pairs.jsonl` — M1 output, M2 input

```json
{"text_chunk_id": "...", "image_chunk_id": "...", "doc_id": "..."}
```

Each pair = (figure caption, figure image) co-located on the same page.
These are positive supervision for the cross-modal hash net. On 14 papers
we get ~500 pairs; on 100 papers, ~3,500.

### `hash_net.pt` — M2 output, M3/M5/M7 input

```python
{
  "text_in_dim": 1024,       # BGE-M3 output dim
  "image_in_dim": 768,       # SigLIP-2 output dim
  "hidden_dim": 2048,
  "bits": 128,
  "state_dict": {...},        # CrossModalHashNet weights
  # After fine-tuning, also includes:
  "finetune_dataset": "biomedical_lectures_eng_v2",
  "baseline_bin_r": 0.6762,
  "final_bin_r": 0.7306,
}
```

The `hash_net.pt.original` backup is always preserved before fine-tuning.
Restore via `cp checkpoints/hash_net.pt.original checkpoints/hash_net.pt`.

### `hash_128bit.faiss` — M3 output

`faiss.IndexBinaryFlat(128)`. Bit ordering is **LSB-first**, matching
`hashmm.hashing.hash_net.pack_bits`. This is a critical invariant: FAISS
binary indexes use the same LSB-first convention, so Hamming distances are
computed correctly. Using `np.packbits` (which defaults to MSB-first)
anywhere in the pipeline would silently produce wrong results — no error,
just garbage rankings.

### `episodic.sqlite` — M5 episodic store

WAL mode for concurrent read/write safety. Two tables:

- `sessions`: `session_id, created_at, title`
- `turns`: `turn_id, session_id, query, answer, intent, strategy,
  quality_ok, created_at`

FK cascade: deleting a session deletes its turns.

### `semcache/` — M5 semantic cache (three-file consistent set)

- `meta.sqlite` — source of truth: entry metadata, stats, TTL, version
- `codes_128bit.faiss` — FAISS binary index of all cached query codes
- `embeddings.npy` — float32 matrix (n_entries, 1024) for Stage 2 cosine

If FAISS/numpy files are missing or inconsistent with SQLite (crash recovery),
the system re-encodes all queries from the DB and rebuilds both files. SQLite
is always the durable truth.

---

## M2: Hash Net Architecture (Deep Dive)

```
text_emb (1024-d, from BGE-M3, frozen)
  │
  ▼
Linear(1024, 2048) → GELU → Dropout(0.1)
  → Linear(2048, 2048) → GELU → Dropout(0.1)
  → Linear(2048, 128)
  → BatchNorm1d(128, affine=False)    ← CRITICAL anti-collapse
  → tanh(τ · z)                        ← temperature annealed
  │
  ▼
b_text ∈ (-1, +1)^128

image_emb (768-d, from SigLIP-2, frozen)
  │
  [same architecture but in_dim=768]
  │
  ▼
b_image ∈ (-1, +1)^128
```

### Why `affine=False`

With `affine=True`, BatchNorm learns a per-channel scale γ and shift β.
The network can learn γ→0 and β→constant, effectively undoing the
standardization and collapsing codes to one quadrant. With `affine=False`,
the standardization is a hard constraint: each bit has zero mean and unit
variance across the batch. On our small dataset (<1k pairs), this was the
difference between 14/22 unique codes (collapsed) and 22/22 (healthy).

### Temperature Annealing

During training, τ starts at 1.0 and increases toward the end (cosine
schedule). At τ=1, `tanh(z)` is smooth — gradients flow easily but the
output is far from binary. At τ→∞, `tanh(τ·z) → sign(z)` — nearly binary
but gradients vanish. The annealing compromise: train with easy gradients
first, then push toward binary at the end so `sign()` at inference loses
minimal information.

### Three-Term Loss

```
L = L_pair + 0.1 · L_quant + 0.05 · L_balance
```

**L_pair (pairwise similarity preserving):**
DCMH-style sigmoid NLL. For a batch of B items:

```
inner = b_text @ b_image.T                    # (B, B)
theta = inner * gamma / sqrt(bits)            # scaled logits
L_pair = mean(-log(sigmoid(S * theta)))       # S ∈ {-1, +1}
```

S comes from `build_similarity_matrix`: +1 if same pair or same-doc
within a page window, -1 otherwise. The gamma/sqrt(bits) scaling keeps
gradients in a good range across different bit widths.

**L_quant (quantization):**
`||b - sign(b)||²` — pulls continuous codes toward ±1. Together with
temperature annealing, ensures that `sign()` at inference is nearly lossless.

**L_balance (bit balance):**
`mean(mean(b, dim=0)²)` — each bit's average over the batch should be 0
(equal counts of +1 and -1). Prevents the network from ignoring bits by
pushing them to a constant.

---

## M4: Agent State Machine (Deep Dive)

### State Schema

```python
class AgentState(TypedDict, total=False):
    # Input
    query: str
    query_image_path: str | None

    # Planning
    intent: Intent           # semantic | cross_modal | hybrid | factual
    strategy: RetrievalStrategy  # vector | hash | hybrid
    modality_filter: str | None
    top_k: int

    # Retrieval
    retrieved: list[dict]    # JSON-serialisable for checkpointing

    # Quality control
    quality_ok: bool
    quality_reason: str
    refine_attempts: int     # capped at 2

    # Generation
    answer: str
    sources_cited: list[str]

    # Diagnostics
    trace: Annotated[list[dict], operator.add]  # append-only log

    # Memory
    session_id: str
    cache_hit: bool
    skip_cache: bool
```

The `total=False` declaration means all fields are optional at initialization;
nodes fill them in as they execute. The only field that must be set at start
is `query`.

### Graph Structure (9 nodes, 2 conditional edges)

```
semcache_lookup ──[conditional]──→ classify_intent  (on MISS)
       │                                 │
       │ HIT                             ▼
       │                          plan_retrieval ←──┐
       │                                 │           │
       │                             retrieve        │
       │                                 │           │
       │                          check_quality      │
       │                           │           │     │
       │                        generate   refine ───┘
       │                           │      (capped at 2)
       │                    semcache_write
       │                           │
       └────────────────→ episodic_write → END
```

### Intent Classification Rules

| Keyword/Pattern | Intent | Example |
|---|---|---|
| "show me", "figure", "image", "图", "chart" | `cross_modal` | "show me architecture diagram" |
| "compare", "versus", "vs", "difference" | `hybrid` | "compare ColPali and ColBERT" |
| < 8 words, no modal keywords | `factual` | "what is BGE-M3?" |
| Everything else | `semantic` | "explain attention mechanism" |
| `query_image_path` is set | forces `cross_modal` | (image uploaded) |

### Quality Check Criteria

The `check_quality` node evaluates retrieval results before passing to
generation. A result set fails if:

1. **Empty** — no results retrieved
2. **All from same document** — no diversity
3. **Modality mismatch** — asked for images but got <2 image chunks
4. **Top result Hamming too high** — Hamming distance > bits/2 suggests
   the hash index is returning random matches

If quality fails, `refine_query` rewrites the query (via LLM or heuristic
stripping of modality words) and re-retrieves. After 2 failed refines,
the agent generates from whatever it has — it never loops forever.

### The `trace` Reducer

```python
trace: Annotated[list[dict], operator.add]
```

Without the `operator.add` annotation, LangGraph uses REPLACE semantics:
each node's `return {"trace": [{...}]}` overwrites the previous value.
With `add`, they concatenate. This is documented but easy to miss — the
symptom is "only the last node's trace is visible".

Each node returns a trace entry with `{node, ts, ...}` keys. The CLI
prints the full trace at query time, showing the complete decision path.

---

## M5: Semantic Cache Algorithm (Deep Dive)

### Lookup Path

```python
def lookup(self, query: str) -> dict | None:
    # 1. Encode query
    float_emb = text_encoder(query)          # (1024,)
    hash_code = hash_net.sign_text(float_emb)  # (128,) → pack → (16,) uint8

    # 2. Stage 1: Hamming pre-filter
    distances, indices = faiss_index.search(hash_code, k=20)
    candidates = [(i, d) for i, d in zip(indices, distances)
                  if d < HAMMING_THRESHOLD]   # default: 12

    # 3. Stage 2: cosine verification
    for idx, ham_dist in candidates:
        cos = cosine_sim(float_emb, embeddings[idx])
        if cos > COSINE_THRESHOLD:            # default: 0.88
            return cache_entries[idx]          # cache hit

    return None  # cache miss
```

### Complexity Analysis

Standard semantic cache: O(N · d) float operations (cosine with every entry).
At N=10k, d=1024: 10M float multiplications per lookup.

HashMM-RAG cache:
- Stage 1: O(N · K/64) XOR operations (Hamming via SIMD). At K=128:
  O(N · 2) uint64 XORs + popcounts = 20k operations for N=10k.
- Stage 2: O(C · d) float operations for C ≤ 20 candidates.
  At C=20, d=1024: 20k float multiplications.

Ratio: 10M / 40k ≈ 250× fewer operations. In practice, ~50× measured
speedup due to cache effects, function call overhead, etc.

### Write Path

On cache write:
1. Check for exact dedup (normalized query match) — update in-place, no
   new FAISS row.
2. Maybe evict (LRU by `last_hit_at`, drop oldest 10% when at capacity).
3. Append: add hash code to FAISS, vstack float embedding to numpy array.
4. Insert SQLite row with metadata.
5. Atomic save: write FAISS and numpy to temp files, then `os.replace`.

### Crash Safety

Two mutable files (FAISS index, numpy array) need to stay in sync with
SQLite. Strategy:

- **Normal operation**: atomic writes (temp + rename) after each mutation.
- **Crash recovery**: on startup, compare `index.ntotal` with SQLite row
  count. If they differ, rebuild FAISS + numpy from SQLite (re-encode
  each cached query through the hash net). SQLite WAL mode survives
  unclean shutdown.

### Index Versioning

When the hash net is retrained, the binary code space changes. Old cache
entries' hash codes are meaningless in the new space. The solution:

- Each entry stores `index_version` (an integer from config).
- Lookup only matches entries with the current version.
- Old entries stay in the DB but are invisible until manually purged.
- This avoids the "serve stale answer from a different hash space" bug.

---

## Cross-Cutting Concerns

### Bit Ordering (LSB-first)

The `pack_bits` function in `hashmm/hashing/hash_net.py` converts ±1 codes
to uint8 with **LSB-first** ordering: bit i of byte j represents code
element (j×8 + i). This matches FAISS's binary index convention.

`numpy.packbits` defaults to **MSB-first**. Mixing the two produces
silently wrong Hamming distances — no error, just garbage retrieval results.
Every module that touches binary codes (M3 retrievers, M5 semcache, M7
benchmark retrievers) uses `pack_bits` exclusively.

### Eval Mode for Hash Net

`load_hash_net()` calls `.eval()` before returning. The `HashHead`'s
`BatchNorm1d` uses running statistics in eval mode and batch statistics in
train mode. If you call `sign_text` on a batch of 1 in train mode,
BatchNorm normalizes that single sample to exactly 0, producing garbage
codes. Always verify `net.training == False` before inference.

### Atomic File Writes

FAISS index files and numpy arrays are binary blobs. If the process dies
mid-write, the file is corrupted and the index is lost. Solution:

```python
tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
write_to(tmp)
os.replace(tmp, path)  # atomic on POSIX
```

For numpy: `np.save` auto-appends `.npy` to the filename unless you pass
an open file handle. The temp filename must account for this, or the
rename will fail silently (temp file has `.npy` extension, destination
doesn't).

---

## Lessons Learned (Failure Post-Mortems)

### 1. MinerU + xethub DNS (6 hours lost)

**Symptom:** MinerU 3.x hanging on model download.
**Root cause:** Default model source is `xethub.hf.co`, which DNS-fails in
mainland China.
**Fix:** `export MINERU_MODEL_SOURCE=modelscope` — swaps to Alibaba mirror.
**Prevention:** Added to `.env.example` and `CONTEXT.md` as critical
landmine.

### 2. Hash Code Collapse on Small Data

**Symptom:** `unique_codes / total_codes` drops to 14/22. Retrieval returns
the same chunks regardless of query.
**Root cause:** Without `BatchNorm(affine=False)`, the network pushes most
codes into the same quadrant of the hash space.
**Fix:** `BatchNorm1d(bits, affine=False)` — hard standardization constraint.
**Post-fix metric:** unique_codes 22/22.

### 3. Fine-Tune Attempt 1: MSE Loss

**Symptom:** Loss stalls at a plateau; binary Pearson r actually decreases.
**Root cause:** Target similarities have mean ~0.7 (BGE-M3 corpus). Hash
similarities have mean ~0 (forced by BatchNorm zero-mean constraint). MSE
says "make all values = 0.7"; BN says "mean must be 0". They fight, and the
structural signal (relative ordering) gets lost.
**Safety check:** Auto-rollback detected no improvement, left checkpoint
untouched.

### 4. Fine-Tune Attempt 2: Listwise Softmax-CE

**Symptom:** Binary Pearson r degrades. Loss decreases but retrieval gets
worse.
**Root cause:** Forgot to mask the diagonal. `S[i,i] = 1.0` for every
document. After softmax, the diagonal entry gets ~99% of the probability
mass. The gradient says "be maximally similar to yourself" — trivially true,
no learning signal. The off-diagonal relationships (the actual content) get
~1% of the gradient.
**Fix:** `mask = ~torch.eye(n)` before any operation on the similarity
matrix.
**Meta-lesson:** The diagonal masking issue is the most common failure mode
in similarity-based losses. If your loss involves a softmax over a
similarity matrix, mask the diagonal first.

### 5. LangGraph Trace Overwrite

**Symptom:** Only the last node's trace entry appears. Debugging impossible.
**Root cause:** `trace: list[dict]` without a reducer defaults to REPLACE
semantics in LangGraph.
**Fix:** `trace: Annotated[list[dict], operator.add]` — concatenates instead
of overwrites.

### 6. np.save Extension Heuristic

**Symptom:** `os.replace(tmp, target)` silently fails (file not found).
**Root cause:** `np.save("foo.npy.tmp.123", arr)` saves to
`"foo.npy.tmp.123.npy"` (auto-appended). The rename path doesn't match.
**Fix:** Either use an open file handle (`np.save(open(tmp, "wb"), arr)`)
or construct the temp name with the `.npy` extension already included.

---

## Performance Characteristics

| Operation | Time on 4090 | Bottleneck |
|---|---|---|
| Parse 1 PDF (MinerU) | 10-15 min | CPU (layout analysis) + GPU (OCR) |
| Parse 100 PDFs | ~22 hours | Resumable via `.processed_files.txt` |
| Train hash net (500 pairs) | 5-10 min | GPU (frozen encoder forward) |
| Build FAISS indexes | 1-2 min | CPU |
| Fine-tune on ViDoRe (996 docs) | 0.8 sec | GPU (full-batch, 100 steps) |
| ViDoRe benchmark eval | < 1 min | CPU (pytrec_eval) |
| Query (M3 hash) | 0.13 ms | CPU (FAISS binary) |
| Query (M3 vector) | 0.01 ms | CPU (FAISS flat) |
| Semantic cache exact hit | 0.24 ms | CPU |
| Semantic cache semantic hit | 70 ms | CPU (Stage 1) + GPU (encode) |
| Agent full pipeline (cache miss) | 2-5 sec | LLM API latency |

---

## VRAM Budget (RTX 4090, 24 GB)

| Component | VRAM | When |
|---|---|---|
| BGE-M3 (frozen) | ~3 GB | Training + inference |
| SigLIP-2 (frozen) | ~1 GB | Training only |
| Hash heads (trainable) | ~50 MB | Training only |
| FAISS indexes | ~0 (CPU) | Always |
| Fine-tune (full batch, 996 docs) | ~2 GB | Fine-tune only |
| **Peak during training** | **~6 GB** | Batch=64 |
| **Peak during fine-tune** | **~5 GB** | Full batch |

Leaves 18+ GB free for a local LLM if needed (Qwen3-14B INT4 ≈ 10 GB).

---

## Future Architecture Considerations

### M6 MCP Server

```
hashmm/mcp_server/
  __init__.py
  server.py       ← MCP Python SDK (stdio/HTTP)
  tools.py        ← cross_modal_search, hybrid_search, hash_dedup
```

Three MCP capabilities:
- **tools**: executable search operations
- **resources**: read-only chunk access
- **prompts**: reusable retrieval prompt templates

One server, callable from LangGraph, Claude Desktop, Cursor.

### Two-Stage Retrieval (HashMM-RAG + ColPali)

```
query → HashMM-RAG hash retrieval (top-100, <1 ms)
     → ColPali rerank (top-10, ~50 ms per candidate)
     → final top-K
```

This combines HashMM-RAG's storage efficiency with ColPali's visual
accuracy. The hash stage eliminates 99% of candidates before the expensive
multi-vector reranking.

### Higher Bit Widths (256/512)

The architecture supports arbitrary bit widths via `HashMMConfig.hash_bits`.
Going from 128 to 256 bits doubles the index size (32 KB for 1k docs) but
preserves more information from the 1024-d source. Expected to close 1-2
more points of the nDCG gap.
