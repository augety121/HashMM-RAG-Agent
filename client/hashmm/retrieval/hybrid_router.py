"""Hybrid retrieval router.

Three strategies:

1. ``static``   — fixed rule: cross-modal query → hash, same-modal → vector.
2. ``hash_first`` — always coarse-to-fine: hash top-K_coarse, then re-rank
                    top-K_coarse by vector similarity to get final top-K_fine.
                    This is the cross-modal IR classic pattern.
3. ``adaptive`` — run BOTH in parallel, fuse with Reciprocal Rank Fusion.
                  No latency penalty if the underlying retrievers run in
                  parallel; trivially superior at quality.

We default to ``adaptive`` because RRF is robust, near-parameter-free, and
makes the strongest end-to-end benchmark story ("union of hash and vector
beats either alone"). Switch to ``hash_first`` for tight-latency scenarios.

Reciprocal Rank Fusion (Cormack et al., SIGIR 2009):
    score(d) = Σ_r 1 / (k + rank_r(d))
With k=60 the score is bounded and small-rank items dominate, which matches
intuition. We use k from cfg.rrf_k.
"""

from __future__ import annotations

from typing import Sequence

from hashmm.config import HashMMConfig
from hashmm.retrieval.base import BaseRetriever, RetrievedChunk
from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval.hybrid")


class HybridRouter:
    """Compose multiple retrievers behind a single retrieve() call."""

    def __init__(
        self,
        cfg: HashMMConfig,
        hash_retriever: BaseRetriever,
        vector_retriever: BaseRetriever | None = None,
    ):
        """
        Args:
            cfg: HashMMConfig (uses cfg.hybrid_mode, cfg.rrf_k).
            hash_retriever: the hash side. Always required.
            vector_retriever: the dense side. If None, we fall back to hash-only.
        """
        self.cfg = cfg
        self.hash = hash_retriever
        self.vector = vector_retriever
        self.mode = cfg.hybrid_mode  # 'static' | 'hash_first' | 'adaptive'

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        modality_hint: str | None = None,
        query_image_path: str | None = None,
    ) -> list[RetrievedChunk]:
        """Apply routing strategy and return fused results."""
        top_k = top_k or self.cfg.retrieval_top_k

        # Without a vector retriever we can only do hash.
        if self.vector is None:
            return self.hash.retrieve(
                query, top_k=top_k, modality_hint=modality_hint,
                query_image_path=query_image_path,
            )

        if self.mode == "static":
            return self._route_static(query, top_k, modality_hint, query_image_path)
        if self.mode == "hash_first":
            return self._route_hash_first(query, top_k, modality_hint, query_image_path)
        return self._route_adaptive(query, top_k, modality_hint, query_image_path)

    # ── Strategies ────────────────────────────────────────────────────

    def _route_static(
        self,
        query: str,
        top_k: int,
        modality_hint: str | None,
        query_image_path: str | None,
    ) -> list[RetrievedChunk]:
        # Cross-modal cue: image in the query, or the user explicitly asked
        # for a different modality than text → hash.
        is_cross_modal = bool(query_image_path) or modality_hint in (
            "image",
            "table",
            "equation",
        )
        retriever = self.hash if is_cross_modal else self.vector
        logger.debug("static routing: cross_modal=%s → %s", is_cross_modal, retriever.name)
        return retriever.retrieve(
            query, top_k=top_k, modality_hint=modality_hint,
            query_image_path=query_image_path,
        )

    def _route_hash_first(
        self,
        query: str,
        top_k: int,
        modality_hint: str | None,
        query_image_path: str | None,
    ) -> list[RetrievedChunk]:
        # Coarse-to-fine: pull a wide net with hash, then re-rank the
        # candidates using the vector retriever.
        k_coarse = max(top_k * 5, 50)
        coarse = self.hash.retrieve(
            query, top_k=k_coarse, modality_hint=modality_hint,
            query_image_path=query_image_path,
        )
        # Hand the union to the vector retriever as a restricted candidate
        # set. For a v1 we approximate by simply running vector on the same
        # query and intersecting with the coarse set.
        vec = self.vector.retrieve(
            query, top_k=k_coarse, modality_hint=modality_hint,
            query_image_path=query_image_path,
        )
        coarse_ids = {c.chunk_id for c in coarse}
        filtered = [v for v in vec if v.chunk_id in coarse_ids][:top_k]
        if len(filtered) < top_k:
            # Fall back to plain hash results if the intersection is small
            # (happens early in indexing or when modalities don't overlap).
            seen = {v.chunk_id for v in filtered}
            for c in coarse:
                if c.chunk_id not in seen:
                    filtered.append(c)
                    if len(filtered) >= top_k:
                        break
        # Re-stamp ranks
        for i, ch in enumerate(filtered):
            ch.rank = i
            ch.source = "hybrid"
        return filtered

    def _route_adaptive(
        self,
        query: str,
        top_k: int,
        modality_hint: str | None,
        query_image_path: str | None,
    ) -> list[RetrievedChunk]:
        # Run both retrievers and fuse with RRF.
        k_each = max(top_k * 3, 30)
        hash_hits = self.hash.retrieve(
            query, top_k=k_each, modality_hint=modality_hint,
            query_image_path=query_image_path,
        )
        vec_hits = self.vector.retrieve(
            query, top_k=k_each, modality_hint=modality_hint,
            query_image_path=query_image_path,
        )
        fused = rrf_fuse([hash_hits, vec_hits], k=self.cfg.rrf_k, top_k=top_k)
        return fused


# ───────────────────────────────────────────────────────────────────────
# Reciprocal Rank Fusion
# ───────────────────────────────────────────────────────────────────────


def rrf_fuse(
    result_lists: Sequence[Sequence[RetrievedChunk]],
    k: int = 60,
    top_k: int = 20,
) -> list[RetrievedChunk]:
    """Fuse N lists with RRF. Each list contributes 1/(k + rank) per chunk_id.

    Returns the top_k chunks by fused score, with rank re-stamped. The chunk
    metadata is taken from the first list that contains the chunk (assumes
    that whichever retriever has it has good info).
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, RetrievedChunk] = {}
    for results in result_lists:
        for chunk in results:
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + chunk.rank)
            if chunk.chunk_id not in first_seen:
                first_seen[chunk.chunk_id] = chunk

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    out: list[RetrievedChunk] = []
    for i, (cid, sc) in enumerate(ranked):
        ch = first_seen[cid]
        # Replace fields rather than mutate (RetrievedChunk is a dataclass).
        out.append(
            RetrievedChunk(
                chunk_id=ch.chunk_id,
                modality=ch.modality,
                text=ch.text,
                image_path=ch.image_path,
                score=sc,
                rank=i,
                source="hybrid",
                meta={**ch.meta, "fused_score": sc, "original_source": ch.source},
            )
        )
    return out
