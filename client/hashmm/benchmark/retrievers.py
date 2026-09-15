"""Benchmark retrievers.

A retriever in benchmark scope is anything with:
    .index(corpus: dict[doc_id, dict])  → builds whatever index it needs
    .retrieve(queries: dict[qid, str], top_k: int) → results dict
    .index_size_bytes()  → for the cost-vs-quality table
    .avg_query_ms        → property, set during retrieve()
    .name                → short identifier

Three concrete implementations:
    BGEM3Dense      single-vector dense retrieval (text only). Baseline.
    HashMMDense     same encoder, but cosine over normalized BGE-M3
                    (a sanity sibling of BGEM3Dense for differential debug).
    HashMMRetriever HashMM-RAG: 128-bit hash codes via the trained hash net.
                    Used in M7.2 once we train on the ViDoRe corpus.

Each implementation is self-contained: it owns the model lifecycle.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from hashmm.config import HashMMConfig
from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.retrievers")


class BaseRetriever:
    name = "base"

    def __init__(self, cfg: HashMMConfig):
        self.cfg = cfg
        self.avg_query_ms: float = 0.0
        self._index_size_bytes: int = 0

    def index(self, corpus: dict[str, dict]) -> None:
        raise NotImplementedError

    def retrieve(self, queries: dict[str, str],
                 top_k: int = 100) -> dict[str, dict[str, float]]:
        raise NotImplementedError

    def index_size_bytes(self) -> int:
        return self._index_size_bytes


# ── BGE-M3 dense baseline ──────────────────────────────────────────────


class BGEM3Dense(BaseRetriever):
    """Standard single-vector dense retrieval over BGE-M3 text embeddings.

    Build phase: encode every doc text → matrix D (n_docs, dim).
    Retrieve  : encode each query → q ; scores = D @ q ; top-k argpartition.

    Storage: float32 → 4 KB per doc at d=1024. For 1k docs ~4 MB.
    """

    name = "BGE-M3-dense"

    def __init__(self, cfg: HashMMConfig, batch_size: int = 32):
        super().__init__(cfg)
        self.batch_size = batch_size
        self._text_enc = None
        self._doc_ids: list[str] = []
        self._doc_embs: np.ndarray | None = None  # (n_docs, d)
        self._index_build_sec: float = 0.0

    def _ensure_encoder(self):
        if self._text_enc is not None:
            return self._text_enc
        from hashmm.hashing.encoders import TextEncoder

        self._text_enc = TextEncoder(
            model_name=self.cfg.hash_text_encoder,
            device=self.cfg.hash_device,
        )
        return self._text_enc

    def index(self, corpus: dict[str, dict]) -> None:
        enc = self._ensure_encoder()
        ids: list[str] = []
        texts: list[str] = []
        for did, entry in corpus.items():
            t = (entry.get("text") or "").strip()
            if not t:
                # benchmarks need *something*; skip empty docs to avoid
                # zero vectors poisoning cosine ranking.
                continue
            ids.append(did)
            texts.append(t)

        if not texts:
            raise RuntimeError(
                "no docs have text — cannot build BGE-M3 dense index. "
                "ViDoRe v2 datasets typically include corpus_texts; "
                "if yours doesn't, run OCR first."
            )

        logger.info("encoding %d docs with BGE-M3 (batch=%d)",
                    len(texts), self.batch_size)
        t0 = time.time()
        embs = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            with _torch_no_grad():
                e = enc(batch).cpu().numpy().astype(np.float32)
            embs.append(e)
            if (i // self.batch_size) % 5 == 0:
                logger.info("  ... %d / %d", i + len(batch), len(texts))
        D = np.vstack(embs)
        self._index_build_sec = time.time() - t0

        # L2-normalise (TextEncoder already does this but be defensive)
        norms = np.linalg.norm(D, axis=1, keepdims=True) + 1e-9
        D = D / norms

        self._doc_ids = ids
        self._doc_embs = D.astype(np.float32)
        self._index_size_bytes = int(D.nbytes)

        logger.info(
            "BGE-M3 index built: %d × %d = %.1f MB in %.1fs",
            D.shape[0], D.shape[1], D.nbytes / 1024 / 1024,
            self._index_build_sec,
        )

    def retrieve(self, queries: dict[str, str],
                 top_k: int = 100) -> dict[str, dict[str, float]]:
        if self._doc_embs is None:
            raise RuntimeError("call .index() before retrieve()")
        enc = self._ensure_encoder()

        # Batch-encode queries first; cosine is then a single matmul.
        qids = list(queries.keys())
        qtexts = [queries[q] for q in qids]

        logger.info("encoding %d queries", len(qtexts))
        q_embs = []
        for i in range(0, len(qtexts), self.batch_size):
            with _torch_no_grad():
                e = enc(qtexts[i:i + self.batch_size]).cpu().numpy()
            q_embs.append(e.astype(np.float32))
        Q = np.vstack(q_embs)
        # Normalise (defensive)
        Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)

        logger.info("scoring %d queries × %d docs", len(qids), len(self._doc_ids))
        t0 = time.time()
        scores = Q @ self._doc_embs.T  # (n_queries, n_docs)
        elapsed = time.time() - t0
        self.avg_query_ms = (elapsed * 1000) / max(len(qids), 1)

        top_k = min(top_k, scores.shape[1])
        # argpartition is faster than argsort for top-k
        idx_part = np.argpartition(-scores, top_k - 1, axis=1)[:, :top_k]
        out: dict[str, dict[str, float]] = {}
        for row, qid in enumerate(qids):
            cand = idx_part[row]
            # Refine order within the candidate set
            cand_scores = scores[row, cand]
            order = np.argsort(-cand_scores)
            out[qid] = {
                self._doc_ids[int(cand[j])]: float(cand_scores[j])
                for j in order
            }
        return out


# ── HashMM-RAG retriever (M7.2 full implementation) ────────────────────


class HashMMRetriever(BaseRetriever):
    """Two-stage hash + cosine retrieval over the SAME corpus.

    Build phase:
        - Encode all docs with BGE-M3 → float embeddings (kept for rerank).
        - Pass through trained HashHead → 128-bit packed binary codes.
        - Build FAISS IndexBinaryFlat for fast Hamming search.

    Retrieve phase:
        - Encode query identically.
        - Stage 1: Hamming top-K' (K' = stage1_k > top_k) via binary index.
        - Stage 2: float cosine on the K' candidates for final ranking.

    For M7.2 this uses the hash net from `cfg.hash_net_ckpt`.
    """

    name = "HashMM-RAG"

    def __init__(self, cfg: HashMMConfig, stage1_k: int = 200,
                 batch_size: int = 32):
        super().__init__(cfg)
        self.stage1_k = stage1_k
        self.batch_size = batch_size
        self._text_enc = None
        self._hash_net = None
        self._faiss = None
        self._index = None
        self._doc_ids: list[str] = []
        self._doc_float_embs: np.ndarray | None = None  # for stage 2 rerank
        self._doc_codes: np.ndarray | None = None       # uint8 packed

    def _ensure_models(self):
        if self._text_enc is not None and self._hash_net is not None:
            return
        from hashmm.hashing.encoders import TextEncoder
        from hashmm.hashing.train import load_hash_net

        self._text_enc = TextEncoder(
            model_name=self.cfg.hash_text_encoder,
            device=self.cfg.hash_device,
        )
        # load_hash_net returns (net, ckpt_meta); net is already in eval mode.
        net, _meta = load_hash_net(self.cfg)
        self._hash_net = net

    def index(self, corpus: dict[str, dict]) -> None:
        import faiss
        from hashmm.hashing.hash_net import pack_bits

        self._faiss = faiss
        self._ensure_models()

        ids: list[str] = []
        texts: list[str] = []
        for did, entry in corpus.items():
            t = (entry.get("text") or "").strip()
            if not t:
                continue
            ids.append(did)
            texts.append(t)

        logger.info("encoding %d docs through BGE-M3 → hash head", len(texts))
        all_floats = []
        all_codes = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            with _torch_no_grad():
                f = self._text_enc(batch)               # (b, 1024) torch
                s = self._hash_net.sign_text(f)         # (b, K) ±1 torch
                # Canonical packing — LSB-first byte order, matches the
                # rest of the codebase (M3 hash_retriever + M5 semcache).
                packed = pack_bits(s).cpu().numpy()
            f_np = f.detach().cpu().numpy().astype(np.float32)
            f_np /= (np.linalg.norm(f_np, axis=1, keepdims=True) + 1e-9)
            all_floats.append(f_np)
            all_codes.append(packed)

            if (i // self.batch_size) % 5 == 0:
                logger.info("  ... %d / %d", i + len(batch), len(texts))

        D_float = np.vstack(all_floats)
        D_codes = np.vstack(all_codes)

        idx = faiss.IndexBinaryFlat(self.cfg.hash_bits)
        idx.add(D_codes)

        self._doc_ids = ids
        self._doc_float_embs = D_float
        self._doc_codes = D_codes
        self._index = idx

        # Storage = binary codes only (the float side is kept here just for
        # rerank, but in production you'd offload to slow disk because it's
        # only touched for top-K' candidates per query).
        self._index_size_bytes = int(D_codes.nbytes)

        logger.info(
            "HashMM index built: %d × %d bits = %.1f KB binary "
            "(+ %.1f MB float kept for rerank, off the hot path)",
            D_codes.shape[0], self.cfg.hash_bits,
            D_codes.nbytes / 1024,
            D_float.nbytes / 1024 / 1024,
        )

    def retrieve(self, queries: dict[str, str],
                 top_k: int = 100) -> dict[str, dict[str, float]]:
        if self._index is None:
            raise RuntimeError("call .index() before retrieve()")

        self._ensure_models()
        qids = list(queries.keys())
        qtexts = [queries[q] for q in qids]

        logger.info("encoding %d queries through BGE-M3 → hash head",
                    len(qtexts))
        from hashmm.hashing.hash_net import pack_bits
        q_floats = []
        q_codes = []
        for i in range(0, len(qtexts), self.batch_size):
            batch = qtexts[i:i + self.batch_size]
            with _torch_no_grad():
                f = self._text_enc(batch)
                s = self._hash_net.sign_text(f)
                packed = pack_bits(s).cpu().numpy()
            f_np = f.detach().cpu().numpy().astype(np.float32)
            f_np /= (np.linalg.norm(f_np, axis=1, keepdims=True) + 1e-9)
            q_floats.append(f_np)
            q_codes.append(packed)
        Q_float = np.vstack(q_floats)
        Q_codes = np.vstack(q_codes)

        # Stage 1: Hamming top-K' for each query
        stage1_k = min(self.stage1_k, self._index.ntotal)
        top_k = min(top_k, stage1_k)
        logger.info("stage 1: Hamming top-%d × %d queries", stage1_k, len(qids))
        t0 = time.time()
        D_ham, I_ham = self._index.search(Q_codes, stage1_k)

        # Stage 2: float cosine rerank within stage1_k candidates
        out: dict[str, dict[str, float]] = {}
        for row, qid in enumerate(qids):
            cand_rows = I_ham[row]
            cand_float = self._doc_float_embs[cand_rows]   # (K', d)
            cos = cand_float @ Q_float[row]                 # (K',)
            order = np.argsort(-cos)[:top_k]
            out[qid] = {
                self._doc_ids[int(cand_rows[j])]: float(cos[j])
                for j in order
            }
        elapsed = time.time() - t0
        self.avg_query_ms = (elapsed * 1000) / max(len(qids), 1)
        return out


# ── helpers ────────────────────────────────────────────────────────────


class _NullContext:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _torch_no_grad():
    try:
        import torch
        return torch.no_grad()
    except ImportError:
        return _NullContext()
