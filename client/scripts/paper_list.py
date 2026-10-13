"""Curated arXiv paper list for HashMM-RAG corpus.

100 papers across the project's adjacent areas. Heavy figure/table content
ensures the cross-modal hash net gets diverse training pairs.

The full pipeline (download + parse + chunk + train + index) on all 100
papers takes ~24 hours on a single 4090 with MinerU pipeline backend
(~13 min/paper). Run overnight via scripts/run_parse_overnight.sh.

Categories:
  A. Deep / cross-modal hashing       — the research direction (15 papers)
  B. RAG / Multimodal RAG             — what we BUILD (16 papers)
  C. Late-interaction retrieval       — current SOTA trend (12 papers)
  D. Vision-language alignment        — our backbones (10 papers)
  E. Document understanding / OCR     — parsing context (10 papers)
  F. LLM agents / orchestration       — M4 context (12 papers)
  G. Semantic caching / efficiency    — M5 context (8 papers)
  H. Benchmark / evaluation           — M7 context (9 papers)
  I. Quantisation / efficient retrieval — closely-adjacent (8 papers)

Run:
    python scripts/00_download_arxiv.py --output ./data/pdfs \\
        --ids $(python scripts/paper_list.py)
"""

# fmt: off
PAPERS = [
    # ── A. Deep / cross-modal hashing (15 papers) ──────────────────────
    ("2505.16133", "HASH-RAG — direct text-only baseline (ACL 2025)"),
    ("1602.02255", "DCMH: Deep Cross-Modal Hashing (CVPR 2017)"),
    ("1909.07217", "DJSRH: Deep Joint-Semantics Reconstructing Hashing (ICCV 2019)"),
    ("2012.10567", "DCHUC: Deep Cross-modal Hashing with Universal Code (TKDE 2020)"),
    ("2110.10792", "Contrastive Quantization for Cross-modal Retrieval"),
    ("2207.04575", "Hierarchical Consensus Hashing for Cross-Modal Retrieval"),
    ("2308.15273", "Unified Coarse-to-Fine Alignment for Cross-Modal Hashing"),
    ("2403.07687", "Deep Lifelong Cross-modal Hashing"),
    ("2306.04162", "Asymmetric Hashing for Fast Ranking via Neural Networks"),
    ("2105.04060", "Self-Supervised Product Quantization"),
    ("1804.10685", "Cross-Modal Hamming Hashing (ECCV 2018)"),
    ("2310.00088", "Survey of Cross-Modal Hashing Methods (2023)"),
    ("1702.00758", "HashNet: Deep Learning to Hash by Continuation (ICCV 2017)"),
    ("2106.12320", "Deep Self-Adaptive Hashing for Image Retrieval"),
    ("2305.14014", "SADIH: Self-supervised Adaptive Deep Hashing"),

    # ── B. RAG / Multimodal RAG (16 papers) ────────────────────────────
    ("2510.12323", "RAG-Anything — our base framework (HKUDS 2025)"),
    ("2410.05983", "LightRAG (EMNLP 2025)"),
    ("2501.18636", "Multimodal RAG survey 2025"),
    ("2406.13858", "M3DocRAG: Multi-modal Multi-page Multi-document RAG"),
    ("2411.02571", "VisRAG: Vision-based RAG on multi-modal documents"),
    ("2404.16130", "GraphRAG: Microsoft graph-based RAG"),
    ("2310.11511", "Self-RAG: learning to retrieve, generate, critique"),
    ("2404.06910", "RAG vs long-context: when is RAG needed"),
    ("2401.15884", "Corrective RAG (CRAG): detecting and fixing bad retrievals"),
    ("2305.06983", "Active RAG with forward-looking retrieval"),
    ("2312.10997", "Retrieval-Augmented Generation for LLMs: Survey"),
    ("2402.07076", "Multimodal RAG: charting the future"),
    ("2407.13193", "Speculative RAG: enhancing retrieval-augmented generation"),
    ("2404.07220", "RankRAG: unifying context ranking and answer generation"),
    ("2402.18150", "RAFT: Retrieval-Augmented Fine-Tuning"),
    ("2401.18059", "FlashRAG: a modular toolkit for efficient RAG"),

    # ── C. Late-interaction / ColPali family (12 papers) ──────────────
    ("2407.01449", "ColPali: late-interaction document retrieval (ICLR 2025)"),
    ("2412.13663", "ColPali extended evaluation / training"),
    ("2503.01776", "ColQwen2: late interaction with Qwen2-VL"),
    ("2509.07730", "VideoColBERT: ColBERT-style for video"),
    ("2004.12832", "ColBERT: original late-interaction (SIGIR 2020)"),
    ("2112.01488", "ColBERTv2: denoising late interaction"),
    ("2306.05685", "PLAID: efficient late-interaction search engine"),
    ("2411.04708", "JinaColBERT v2: multilingual ColBERT"),
    ("2505.17166", "ViDoRe Benchmark V2: raising the bar for visual retrieval"),
    ("2407.20761", "Stella + jasper: small open-source retrievers"),
    ("2402.16829", "Token Pooling for ColBERT: 4x faster"),
    ("2305.13393", "XTR: rethinking the role of token retrieval"),

    # ── D. Vision-language alignment (10 papers) ──────────────────────
    ("2502.18139", "SigLIP 2 — our image encoder"),
    ("2402.03216", "BGE-M3 — our text encoder"),
    ("2103.00020", "CLIP: original (OpenAI 2021)"),
    ("2303.15343", "SigLIP: sigmoid loss for image-text pretraining"),
    ("2201.12086", "BLIP: bootstrapping VLM pretraining"),
    ("2301.12597", "BLIP-2: querying transformer Q-Former"),
    ("2403.05525", "InternVL: scaling up vision-language model"),
    ("2308.12966", "Qwen-VL technical report"),
    ("2310.03744", "LLaVA-1.5: improved baselines with visual instruction"),
    ("2404.16821", "Phi-3-Vision: small but capable VLM"),

    # ── E. Document understanding / OCR (10 papers) ───────────────────
    ("2409.18839", "MinerU: open-source PDF data extraction"),
    ("2306.16527", "Docling: IBM document conversion"),
    ("2404.16635", "DocLayNet: large doc layout dataset"),
    ("2408.16500", "GoT: General OCR Theory v2"),
    ("2410.21169", "Mistral OCR: long-context document understanding"),
    ("2311.12351", "PDF-WuKong: long document understanding"),
    ("2403.12895", "GOT: Towards OCR 2.0"),
    ("2410.07073", "DocLLM: a layout-aware generative language model"),
    ("2410.05954", "PIXEL: pixel-based language modeling"),
    ("2502.13923", "Qwen2.5-VL technical report (ViDoRe leader)"),  # moved from H to remove dup

    # ── F. LLM agents / orchestration (12 papers) ─────────────────────
    ("2210.03629", "ReAct: reasoning + acting in LLMs"),
    ("2305.16291", "Voyager: open-ended agent"),
    ("2308.08155", "AutoGen: multi-agent conversations"),
    ("2308.00352", "MetaGPT: meta programming framework"),
    ("2402.19173", "Survey of LLM agents 2024"),
    ("2308.10848", "AgentBench: evaluating LLMs as agents"),
    ("2407.16741", "OpenHands: open agent platform"),
    ("2402.07939", "AgentBoard: comprehensive agent benchmark"),
    ("2402.14848", "Reflexion: agents with verbal reinforcement"),
    ("2308.03688", "AgentSims: a sandbox environment for LLM-based agents"),
    ("2308.07201", "GPTSwarm: multi-agent swarm of LLMs"),
    ("2306.02224", "Tree of Thoughts: deliberate problem solving"),

    # ── G. Semantic caching / efficiency (8 papers) ───────────────────
    ("2411.05276", "GPT Semantic Cache: reducing LLM costs via embedding cache"),
    ("2403.02694", "Cost-efficient LLM serving via response caching"),
    ("2504.02268", "Domain-specific semantic caching with synthetic data"),
    ("2310.18547", "Block-level cache for serving LLMs"),
    ("2310.05736", "Promptcache: modular attention reuse"),
    ("2402.00789", "Hydragen: high-throughput LLM inference"),
    ("2403.01361", "Caching strategies for retrieval-augmented LMs"),
    ("2407.13093", "Adaptive semantic cache for LLM inference"),

    # ── H. Benchmark / evaluation (9 papers) ──────────────────────────
    ("2402.13499", "BEIR: heterogeneous benchmark for IR"),
    ("2210.07316", "MTEB: massive text embedding benchmark"),
    ("2502.06453", "MMTEB: massive multilingual text embedding"),
    ("2406.04744", "MMLU-Pro: more robust multi-task benchmark"),
    ("2403.04132", "LongRAG: enhancing retrieval-augmented generation"),
    ("2406.13121", "RAGCheck: a quality control framework for RAG"),
    ("2310.17473", "RAGAS: automated evaluation of RAG systems"),
    ("2406.05085", "ARES: automated RAG evaluation system"),
    ("2404.13076", "Crag: a comprehensive RAG benchmark"),

    # ── I. Quantisation / efficient retrieval (8 papers) ──────────────
    ("1702.08734", "Faiss: efficient similarity search (Facebook AI)"),
    ("1908.10396", "Billion-scale similarity search with GPUs"),
    ("1908.04391", "ScaNN: scalable nearest neighbors"),
    ("2404.16710", "Matryoshka representation learning for retrieval"),
    ("2404.12404", "Binary embedding quantisation for dense retrieval"),
    ("2208.13629", "DiskANN: fast accurate billion-scale ANN on a single node"),
    ("2105.09613", "HNSW++: improvements to HNSW"),
    ("2305.13245", "Product Quantization for retrieval with neural networks"),
]
# fmt: on


def main():
    """Print all paper IDs space-separated, for shell expansion."""
    print(" ".join(p[0] for p in PAPERS))


if __name__ == "__main__":
    main()
