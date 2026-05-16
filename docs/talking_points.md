# HashMM-RAG — Interview Talking Points

Ready-to-deliver answers, each 15-20 seconds. Every number is real,
reproducible, from the benchmark this project ships with.

---

## Elevator Pitch (30 seconds)

> I built a multimodal RAG agent that replaces dense-vector retrieval with
> learned cross-modal hash codes. On the ViDoRe v2 biomedical benchmark it
> retains 94% of BGE-M3's retrieval quality while using a 199× smaller index
> — 16 KB versus 3.89 MB. The agent uses LangGraph for state-machine routing,
> and the semantic cache reuses the same hash codes as its first-stage index,
> giving O(N) Hamming pre-filter before cosine verification. In-domain
> fine-tuning via direct Pearson-r maximisation closed half the zero-shot gap
> in under a second of training.

---

## Module-by-Module (STAR format)

### M1 — Ingestion

**Situation:** Real-world PDFs contain text, figures, tables, charts, and
formulas interleaved on the same page. Most RAG systems treat them as flat
text, losing cross-modal relationships.

**Task:** Parse heterogeneous PDFs into typed, structured chunks while
preserving which figure belongs to which caption.

**Action:** Built on RAG-Anything's MinerU adapter. Added cross-modal pair
extraction (text-figure caption pairs on the same page) as positive
supervision for the hash net. Wrote a resumable overnight pipeline
(`run_parse_overnight.sh`) that checkpoints per-file progress, so a 22-hour
100-paper run survives interruption.

**Result:** 100 papers → ~2,300 chunks (text/image/table/chart), ~500
cross-modal pairs. Each failed PDF doesn't block the others.

**Follow-up — "Why MinerU and not Docling/Marker?"**
> MinerU is the only open-source parser that handles layout analysis + OCR +
> table recognition in one pass with modelscope mirror support for China
> mainland environments. Docling was my fallback if MinerU failed, but it
> worked first try after I set `MINERU_MODEL_SOURCE=modelscope` to avoid the
> xethub DNS failure — which cost me 6 hours to debug.

---

### M2 — Cross-Modal Hashing (core module)

**Situation:** Cross-modal retrieval (text→image, image→text) typically
requires storing 1024-d float vectors per chunk. At scale this becomes a
storage and latency bottleneck.

**Task:** Project text (BGE-M3, 1024-d) and image (SigLIP-2, 768-d)
embeddings into a shared 128-bit binary space where Hamming distance
approximates semantic similarity.

**Action:** Designed a DCMH-style architecture: two independent MLP hash
heads (3-layer, 2048 hidden), each ending with `BatchNorm1d(affine=False)`
and `tanh(τ·z)` with temperature annealing. Loss is three-term: pairwise
similarity preserving (DCMH-style sigmoid NLL), quantization
(`||b - sign(b)||²`), and bit balance (per-bit mean → 0). Encoders are
frozen; only the hash heads train.

**Result:** val mAP@10 = 0.501 (t2i=0.532, i2t=0.470). 22/22 unique codes
(no collapse). Training takes 5-10 minutes on a 4090 with ~500 pairs.

**Follow-up — "Why `BatchNorm(affine=False)` specifically?"**
> With `affine=True`, the network learns to undo the standardization via the
> scale/shift parameters, defeating the constraint. With `affine=False` it's
> a hard zero-mean unit-variance guarantee per bit — on our small dataset
> (<1k pairs) this was the difference between 14/22 unique codes (collapsed)
> and 22/22 (healthy). This is a standard trick from HashNet (ICCV 2017) but
> knowing when to apply it matters.

**Follow-up — "Why not LSH or Product Quantization?"**
> LSH is data-independent — it can't learn cross-modal alignment. PQ splits
> the vector into sub-vectors and quantizes each, which preserves more
> information but doesn't naturally handle asymmetric modalities (text=1024-d,
> image=768-d). DCMH-style learning projects both modalities through separate
> heads into a shared binary code space, which is exactly what cross-modal
> RAG needs.

---

### M3 — Retrieval Layer

**Situation:** Different queries have different optimal retrieval paths:
text-to-text queries work best with dense vectors, cross-modal queries
need the hash index, and complex comparisons benefit from both.

**Task:** Build a retrieval layer that supports three modes and fuses
results intelligently.

**Action:** Implemented vector (FAISS flat cosine), hash (FAISS binary
Hamming), and hybrid (parallel retrieval + Reciprocal Rank Fusion with
k=60). Added Hamming-distance dedup: chunks within Hamming < 8 are treated
as duplicates, keeping only the highest-scoring representative. RRF was
chosen over weighted-sum because the two retrievers' scores have different
scales — Hamming is an integer, cosine is a float.

**Result:** Hybrid mode on `"compare ColPali and ColBERT"` → 6/10 results
hit the ColBERT original paper. Hamming dedup typically reduces chunk count
by 30-40%, cutting downstream LLM token consumption proportionally.

**Follow-up — "Why RRF over learned reranking?"**
> RRF is zero-parameter and works across score scales — it only cares about
> rank positions. For a two-retriever setup this is the right engineering
> choice: adding a learned reranker would need training data we don't have
> and adds a model we'd need to maintain. If I had a ColPali reranker I'd
> use it on top of HashMM-RAG's hash-based first-stage, but that's a natural
> next step, not a v1 requirement.

---

### M4 — LangGraph Agent

**Situation:** Most RAG demos hardcode the retrieval strategy. In production
you need inspectable routing, failure recovery, and auditability.

**Task:** Build a state-machine agent where every decision is logged and
the retrieval strategy adapts to query type.

**Action:** 9-node LangGraph StateGraph: `semcache_lookup → classify_intent
→ plan_retrieval → retrieve → check_quality → {generate | refine_query}
→ semcache_write → episodic_write → END`. Two conditional edges. The
`trace` field uses `Annotated[list[dict], operator.add]` so every node
appends to a shared decision log instead of overwriting. Quality check
verifies diversity (not all chunks from same doc), modality match, and
Hamming distance sanity. Refine loop caps at 2 attempts then falls back.

**Result:** Rule-based routing makes 90% of queries deterministic. The full
trace is printable at query time — in a demo, I can show exactly why the
agent picked `strategy=hybrid` and `top_k=30` for "compare ColPali and
ColBERT".

**Follow-up — "Why not a multi-agent supervisor architecture?"**
> This is a single-task pipeline, not a multi-domain system. LangGraph's
> StateGraph is the canonical pattern for linear-with-branches workflows:
> classify → retrieve → generate with optional refinement. A supervisor
> architecture would add coordination overhead with no benefit — every query
> follows the same graph, just taking different conditional edges.

**Follow-up — "What happens when the LLM call fails?"**
> `refine_query` uses the LLM to rewrite the query. If the LLM times out
> or returns garbage, the refine node returns the original query unchanged
> and increments `refine_attempts`. After 2 failed refines, the quality
> check forces a fallback to `generate` with whatever retrieval results we
> have. The agent never loops forever.

---

### M5 — Three-Layer Memory (research novelty)

**Situation:** Standard semantic caches (GPTCache, RedisSemanticCache) do
O(N·d) float cosine scans. At N=10k, d=1024, that's tens of milliseconds
per lookup — fine for a demo, problematic at scale.

**Task:** Build a memory system that (a) persists across sessions and
(b) accelerates cache lookup by an order of magnitude.

**Action:** Three layers following the Mem0 (ECAI 2025) taxonomy:
- **Working memory**: in-process LangGraph state, tracks failed queries to
  avoid re-retrieval
- **Episodic memory**: SQLite WAL-mode store, sessions + turns with FK
  cascade, records `(query, strategy, quality_ok, answer)` per turn
- **Semantic cache**: the novel piece — two-stage lookup reusing the
  retriever's 128-bit hash codes as Stage 1 (Hamming < 12 on FAISS binary
  top-20, ~1 ms) then Stage 2 (cosine > 0.88 on ≤20 candidates, ~0.1 ms).
  Index versioning (`semcache_index_version`) invalidates stale entries when
  the hash net is retrained. LRU eviction drops oldest 10% when capacity hit.
  Atomic writes for FAISS + numpy files.

**Result:** Exact cache hit: 0.24 ms. Semantic hit (Hamming=11, cos=0.971):
70 ms. At N=10k, theoretical ~50× speedup over flat cosine.

**Follow-up — "How do you handle cache invalidation after retraining?"**
> Every cache entry stores `index_version`. When the hash net is retrained
> (new code space), bumping the version makes all old entries invisible
> without deleting them. If we need to rebuild (eviction or crash recovery),
> we re-encode from the SQLite source of truth. FAISS + numpy files are
> secondary — SQLite is the durable surface.

**金句 (one-liner for CV):**
> "We reused the cross-modal hash codes from retrieval as the semantic
> cache's first-stage index, getting an order-of-magnitude speedup on cache
> lookup — this is Hash-RAG (ACL 2025)'s idea applied at the cache layer,
> where their paper only discusses retrieval."

---

### M7 — Benchmark

**Situation:** Claims without numbers don't survive a technical interview.
Need reproducible, standard-metric evaluation against a recognized baseline.

**Task:** Run a full benchmark on a public dataset with industry-standard
metrics and honest comparison against BGE-M3.

**Action:** Used ViDoRe v2 `biomedical_lectures_eng_v2` (1,016 docs, 160
queries). Implemented both a BGE-M3 dense retriever and a HashMM-RAG
retriever, evaluated with pytrec_eval (nDCG@5/10, Recall@5/10, MAP, MRR).
Then ran in-domain fine-tuning with three loss designs (two failures, one
success).

**Result:**
- Zero-shot: nDCG@5 = 0.285, -4 pts vs BGE-M3
- After fine-tuning: nDCG@5 = 0.305, -2 pts vs BGE-M3
- Fine-tuning took 0.8 seconds (100 full-batch GD steps)
- The 50% gap closure came from a loss function insight, not from more data

**Follow-up — "Why did fine-tuning fail twice?"**
> First attempt: MSE on raw cosine similarities. BGE-M3 corpus mean cosine
> is ~0.7, but BatchNorm forces hash similarities to center at 0. MSE
> literally tells the network "make all similarities = 0.7" which BN
> refuses. Loss stalls, structure degrades silently.
>
> Second attempt: Listwise softmax-CE, standard knowledge distillation. But
> I forgot to mask the diagonal. S[i,i]=1 dominates the softmax target by
> ~100× versus off-diagonal values of ~0.7. All gradient signal goes to
> "be similar to yourself" — true but useless.
>
> Third attempt: Direct off-diagonal Pearson r. Zero hyperparameters,
> mean/scale invariant (works with BN), and IS the metric we're evaluating.
> Worked immediately.
>
> The safety check — if binary Pearson r doesn't improve, don't overwrite
> the checkpoint — caught both failures automatically. No manual rollback
> was ever needed.

---

## Cross-Cutting Questions

### "What problem motivated this project?"

> Multimodal RAG over real PDFs is expensive at index time. ColPali stores
> ~512 KB per page; for 1M documents that's 500 GB of index. My research
> direction is cross-modal hashing — projecting text and images into a
> shared binary space. HashMM-RAG is the practical bridge: a RAG system
> that uses learned hash codes both for first-stage retrieval AND for
> semantic caching, trading 2 points of nDCG for 199× storage compression.

### "What's the engineering contribution beyond the ML?"

> Three things. (1) The LangGraph agent's rule-based routing makes 90% of
> queries deterministic and inspectable — useful for production debugging.
> (2) The hash-indexed semantic cache repurposes the retriever's codes for
> cache lookup acceleration, an application Hash-RAG (ACL 2025) didn't
> explore. (3) Defensive engineering throughout: atomic FAISS writes,
> fine-tune safety checks with auto-rollback, resumable overnight pipelines,
> index versioning for cache invalidation after retraining.

### "What's the bottleneck? What would you improve?"

> Three bottlenecks, in order of impact:
>
> **Information bottleneck**: 1024-d float → 128 bits is 256× compression.
> Going to 256 or 512 bits would close more of the 2-point gap. The
> architecture supports this — it's a config change plus retraining.
>
> **OCR quality**: Tesseract on slide-heavy biomedical PDFs only captures
> ~50 words per page average. PaddleOCR or a VLM-based OCR would likely
> add 3-5 points of nDCG by giving the retriever more text to work with.
>
> **Training data scale**: The hash net was trained on ~500 pairs from 14
> papers. The overnight pipeline produces ~3,500 pairs from 100 papers;
> combined with domain-specific fine-tuning, I'd expect val mAP to rise
> from 0.50 to 0.55-0.60.

### "Why not just use ColPali?"

> ColPali is the visual SOTA but it's a different trade-off: ~1024 patch
> tokens × 128 dim = ~512 KB per page. For 1M documents, 500 GB of index.
> HashMM-RAG stores 16 bytes per document — 30,000× less.
>
> The right production architecture is HashMM-RAG as coarse first-stage
> retrieval (fast, cheap), ColPali for reranking the top-K (accurate, heavy).
> This is the classic coarse-to-fine pattern from image retrieval, and it's
> what the ECIR 2026 LIR Workshop explicitly identifies as an open research
> direction for late-interaction retrieval.

### "What if the interview panel runs the code?"

> Everything is reproducible. `pytest tests/ -v` runs 86 tests in ~2 seconds
> with no GPU. The full pipeline (parse → train → index → query → benchmark)
> runs end-to-end on a single RTX 4090 in under an hour excluding PDF
> parsing. The fine-tune script (`10_finetune_vidore.py`) takes 0.8 seconds.
> All benchmark numbers come from `pytrec_eval` on a public dataset
> (ViDoRe v2) — anyone can download it and verify.

### "How does this compare to Hash-RAG (ACL 2025)?"

> Hash-RAG is text-only; they train binary codes for a text retriever.
> I extend the idea in two directions: (1) cross-modal — both text and image
> are hashed into the same binary space via separate MLP heads, enabling
> text→image and image→text retrieval; (2) the hash codes serve double duty
> as the semantic cache's first-stage index, which Hash-RAG doesn't explore.

### "If you had 3 more months, what would you build?"

> (1) 256/512-bit hash codes to close the remaining 2-point nDCG gap.
> (2) Replace Tesseract with PaddleOCR for better slide text extraction.
> (3) Two-stage retrieval: HashMM-RAG first-stage + ColPali reranker.
> (4) M6 MCP Server so Claude Desktop / Cursor can call the retriever
> directly. (5) Benchmark on 3+ more ViDoRe v2 subsets (economics, ESG).

---

## CV / Resume Bullet (drop-in)

> **HashMM-RAG** — Production-grade multimodal RAG agent: LangGraph state
> machine orchestrates cross-modal hash retrieval (DCMH-style, 128-bit) over
> a 3-layer memory system (working / episodic / hash-indexed semantic cache).
> Achieves 94% of BGE-M3 nDCG@5 on ViDoRe v2 biomedical with 199× index
> compression (16 KB vs 3.9 MB). In-domain fine-tuning via off-diagonal
> Pearson-r maximisation closes 50% of zero-shot gap in 0.8s on a 4090.
> 86 unit tests, atomic writes, auto-rollback safety checks.
> Python / PyTorch / LangGraph / FAISS / pytrec_eval.
