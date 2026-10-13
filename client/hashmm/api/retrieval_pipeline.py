"""Retrieval Pipeline — unified search across vector + keyword indexes.

Architecture:
  Query → preprocess → expand synonyms
    → FAISS dense search (top-50)
    + BM25 sparse search (top-50)
    → RRF fusion → top-20
    → Reranker (optional) → top-5
    → Return with metadata (filename, page, section)
"""
from __future__ import annotations
import re
import json
import pickle
from pathlib import Path
from dataclasses import dataclass, field
from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval_pipeline")

BM25_PATH = Path("data/bm25_index.pkl")

# ── Enterprise synonym expansion ──

_SYNONYMS = {
    "营收": ["收入", "营业收入", "revenue", "总收入"],
    "利润": ["盈利", "净利润", "profit", "纯利"],
    "员工": ["雇员", "职工", "人员", "staff", "employee"],
    "子公司": ["附属公司", "全资子公司", "subsidiary"],
    "增长": ["增加", "同比增长", "growth", "上升"],
    "下降": ["减少", "下滑", "decline", "降低"],
    "董事": ["director", "董事会成员", "board member"],
    "股东": ["shareholder", "持股人"],
    "合同": ["合约", "协议", "contract", "agreement"],
    "产品": ["产品线", "product", "业务"],
    "客户": ["用户", "customer", "client"],
    "市场": ["market", "市场份额"],
    "成本": ["费用", "cost", "expense", "开支"],
    "投资": ["investment", "注资"],
    "收购": ["并购", "acquisition"],
    "风险": ["risk", "风险因素"],
}


@dataclass
class SearchResult:
    """A single search result with metadata."""
    text: str
    score: float
    doc_id: str = ""
    filename: str = ""
    page: int = -1
    section: str = ""
    chunk_id: str = ""
    source_type: str = ""  # "dense" | "sparse" | "fused"


@dataclass
class SearchResponse:
    """Complete search response."""
    results: list[SearchResult]
    query: str
    expanded_query: str = ""
    total_candidates: int = 0
    elapsed_ms: int = 0
    sources: list[dict] = field(default_factory=list)


# ── BM25 Keyword Index ──

class BM25Index:
    """Chinese-aware BM25 keyword index.

    Uses jieba for tokenization. Falls back to character n-grams
    if jieba is not installed.
    """

    def __init__(self):
        self._bm25 = None
        self._corpus: list[dict] = []  # Metadata parallel to BM25 corpus
        self._tokenized: list[list[str]] = []
        self._jieba_ready = False
        try:
            import jieba
            jieba.setLogLevel(20)
            self._jieba_ready = True
        except ImportError:
            pass

    def add(self, texts: list[str], metadata_list: list[dict]):
        """Add documents to BM25 index."""
        for text, meta in zip(texts, metadata_list):
            tokens = self._tokenize(text)
            self._tokenized.append(tokens)
            self._corpus.append(meta)
        self._rebuild_bm25()

    def search(self, query: str, top_k: int = 50) -> list[SearchResult]:
        """Search by keywords."""
        if not self._bm25 or not self._corpus:
            return []

        tokens = self._tokenize(query)
        if not tokens:
            return []

        try:
            scores = self._bm25.get_scores(tokens)
        except Exception:
            return []

        top_indices = scores.argsort()[-top_k:][::-1]
        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            meta = self._corpus[idx]
            results.append(SearchResult(
                text=meta.get("text", ""),
                score=float(scores[idx]),
                doc_id=meta.get("doc_id", ""),
                filename=meta.get("filename", ""),
                page=meta.get("page", -1),
                section=meta.get("section", ""),
                chunk_id=meta.get("chunk_id", ""),
                source_type="sparse",
            ))
        return results

    def remove_by_doc(self, doc_id: str):
        """Remove documents and rebuild."""
        keep = [(t, m) for t, m in zip(self._tokenized, self._corpus)
                if m.get("doc_id") != doc_id]
        if len(keep) != len(self._corpus):
            self._tokenized = [t for t, _ in keep]
            self._corpus = [m for _, m in keep]
            self._rebuild_bm25()

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text for BM25."""
        if self._jieba_ready:
            import jieba
            tokens = list(jieba.cut(text))
            return [t for t in tokens if len(t.strip()) >= 2]
        # Fallback: character bigrams + space-separated words
        words = text.lower().split()
        tokens = [w for w in words if len(w) >= 2]
        # Add Chinese character bigrams
        for i in range(len(text) - 1):
            if '\u4e00' <= text[i] <= '\u9fff' and '\u4e00' <= text[i + 1] <= '\u9fff':
                tokens.append(text[i:i + 2])
        return tokens

    def _rebuild_bm25(self):
        """Rebuild the BM25 index from tokenized corpus."""
        if not self._tokenized:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._tokenized)
        except ImportError:
            logger.info("rank_bm25 not installed (pip install rank-bm25), BM25 disabled")
            self._bm25 = None

    def save(self, path: Path = BM25_PATH):
        """Persist to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"corpus": self._corpus, "tokenized": self._tokenized}, f)

    def load(self, path: Path = BM25_PATH) -> bool:
        """Load from disk."""
        if not path.exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._corpus = data["corpus"]
            self._tokenized = data["tokenized"]
            self._rebuild_bm25()
            logger.info(f"Loaded BM25 index: {len(self._corpus)} documents")
            return True
        except Exception as e:
            logger.warning(f"Failed to load BM25 index: {e}")
            return False

    @property
    def size(self) -> int:
        return len(self._corpus)


# ── Unified Retrieval Pipeline ──

class RetrievalPipeline:
    """Enterprise retrieval pipeline: dense + sparse + fusion.

    Usage:
        pipeline = RetrievalPipeline()
        pipeline.load()  # Load indexes from disk
        results = pipeline.search("小米2024年营收")
    """

    def __init__(self, vector_index=None, bm25_index=None):
        from hashmm.vector_index import VectorIndex
        self.vector_index = vector_index or VectorIndex()
        self.bm25_index = bm25_index or BM25Index()
        self._reranker = None

    def load(self):
        """Load indexes from disk."""
        self.vector_index._ensure_index()
        self.bm25_index.load()
        logger.info(f"Retrieval ready: {self.vector_index.total_vectors} vectors, "
                    f"{self.bm25_index.size} BM25 docs")

    def search(self, query: str, top_k: int = 5,
               filters: dict | None = None) -> SearchResponse:
        """Full retrieval pipeline."""
        import time
        t0 = time.time()

        # Step 1: Preprocess query
        from hashmm.pipeline.text_preprocessor import preprocess
        query = preprocess(query)

        # Step 2: Expand synonyms
        expanded = self._expand_query(query)

        # Step 3: Dense search (FAISS)
        dense_results = []
        if self.vector_index.total_vectors > 0:
            from hashmm.encoder_pool import EncoderPool
            query_emb = EncoderPool.encode_query(query)
            raw = self.vector_index.search(query_emb, top_k=50, filters=filters)
            for r in raw:
                dense_results.append(SearchResult(
                    text=r.get("text", ""),
                    score=r.get("score", 0),
                    doc_id=r.get("doc_id", ""),
                    filename=r.get("filename", ""),
                    page=r.get("page", -1),
                    section=r.get("section", ""),
                    chunk_id=r.get("chunk_id", ""),
                    source_type="dense",
                ))

        # Step 4: Sparse search (BM25)
        sparse_results = self.bm25_index.search(expanded, top_k=50)

        # Step 5: RRF Fusion
        fused = self._rrf_fusion(dense_results, sparse_results, top_k=top_k * 4)

        # Step 6: Reranker (if available)
        if self._reranker and len(fused) > top_k:
            fused = self._rerank(query, fused, top_k)

        final = fused[:top_k]
        elapsed = round((time.time() - t0) * 1000)

        # Build sources for citation
        sources = []
        for i, r in enumerate(final):
            sources.append({
                "id": i + 1,
                "text": r.text[:200],
                "filename": r.filename,
                "page": r.page,
                "section": r.section,
                "score": round(r.score, 3),
                "chunk_id": r.chunk_id,   # V174: 暴露给 Navigate 扩展做 seed/查找
                "doc_id": r.doc_id,
            })

        # V174 Navigate 扩展（Knowhere 派生）：沿单文档结构图（section 树 + chunk 连接）把
        # 相邻块 / 本节首块 / 同节兄弟补进 sources，治"命中一句、答案要靠上下文/整节"的碎片化。
        # 仅 HASHMM_NAVIGATE_EXPAND=1 时启用；语料取自向量索引 _metadata；任何异常都安全降级为原 sources。
        try:
            from hashmm.retrieval import section_graph as _sg
            if _sg.navigate_enabled() and sources:
                corpus = getattr(self.vector_index, "_metadata", None)
                if corpus:
                    before = len(sources)
                    sources = _sg.enrich_sources(sources, corpus_chunks=corpus)
                    for s in sources[before:]:
                        s["text"] = (s.get("text") or "")[:500]   # navigate 上下文截到 500 字，避免膨胀
        except Exception:
            pass

        return SearchResponse(
            results=final,
            query=query,
            expanded_query=expanded,
            total_candidates=len(dense_results) + len(sparse_results),
            elapsed_ms=elapsed,
            sources=sources,
        )

    def _expand_query(self, query: str) -> str:
        """Expand query with enterprise synonyms."""
        tokens = list(query)
        expanded_parts = [query]
        for term, synonyms in _SYNONYMS.items():
            if term in query:
                expanded_parts.extend(synonyms[:2])  # Add top-2 synonyms
        return " ".join(expanded_parts)

    def _rrf_fusion(self, dense: list[SearchResult], sparse: list[SearchResult],
                    top_k: int = 20, k: int = 60) -> list[SearchResult]:
        """Reciprocal Rank Fusion — combine dense and sparse results.

        RRF score = sum(1 / (k + rank)) across all result lists.
        k=60 is the standard constant from the RRF paper.
        """
        scores: dict[str, float] = {}
        items: dict[str, SearchResult] = {}

        for rank, r in enumerate(dense):
            key = r.chunk_id or r.text[:100]
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            if key not in items:
                items[key] = r

        for rank, r in enumerate(sparse):
            key = r.chunk_id or r.text[:100]
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            if key not in items:
                items[key] = r

        # Sort by fused score
        sorted_keys = sorted(scores.keys(), key=lambda x: -scores[x])
        results = []
        for key in sorted_keys[:top_k]:
            r = items[key]
            r.score = scores[key]
            r.source_type = "fused"
            results.append(r)

        return results

    def _rerank(self, query: str, candidates: list[SearchResult],
                top_k: int) -> list[SearchResult]:
        """Cross-encoder reranking (if FlagEmbedding installed)."""
        try:
            if self._reranker is None:
                from FlagEmbedding import FlagReranker
                self._reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)

            pairs = [(query, r.text) for r in candidates]
            scores = self._reranker.compute_score(pairs)
            if isinstance(scores, float):
                scores = [scores]
            for r, s in zip(candidates, scores):
                r.score = float(s)
            candidates.sort(key=lambda r: -r.score)
        except ImportError:
            pass  # Reranker not available, skip
        except Exception as e:
            logger.debug(f"Reranker failed: {e}")
        return candidates[:top_k]
