"""Post-retrieval processing: hash-based deduplication and chunk compression.

After retrieval we often see semantically redundant chunks — different
paragraphs saying the same thing, or different captions of the same figure
indexed twice. Sending all of them to the LLM wastes tokens.

We re-use the same K-bit hash codes already in the index to do **O(N²)
Hamming-distance clustering** on the retrieved set (N is small — usually
20-50 chunks — so quadratic is fine). Chunks within `threshold` bits of
each other are merged into one cluster; we keep the chunk with the best
retrieval rank as the cluster representative.

This is fast (pure bit ops) and is what we'll cite in the paper as
"using the same hash space for retrieval AND post-processing — a single
representation does double duty."
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from hashmm.hashing.index import HashIndex
from hashmm.retrieval.base import RetrievedChunk
from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval.post")


def hash_dedup(
    chunks: list[RetrievedChunk],
    hash_index: HashIndex,
    threshold: int = 8,
) -> list[RetrievedChunk]:
    """Cluster `chunks` by Hamming distance ≤ threshold and keep one per cluster.

    Args:
        chunks: retrieval results (preserves order — best first).
        hash_index: the index used at retrieval time. We pull each chunk's
            code from the index's metadata sidecar; if absent we just keep
            the chunk un-clustered (graceful degradation).
        threshold: bits. With K=128, threshold=8 corresponds to ~94% bit
            agreement — a fairly tight near-duplicate criterion.

    Returns:
        A subset of `chunks`, in input order. Each cluster contributes its
        first (best-ranked) member.
    """
    if not chunks:
        return []

    # Pull codes from the index by chunk_id. The HashIndex stores parallel
    # arrays — we need a chunk_id → row_idx map. Compute lazily.
    cid_to_row = _build_cid_to_row(hash_index)

    rows: list[int] = []
    keep_mask: list[bool] = []
    for ch in chunks:
        row = cid_to_row.get(ch.chunk_id)
        if row is None:
            # No code → keep as-is, can't cluster.
            rows.append(-1)
            keep_mask.append(True)
        else:
            rows.append(row)
            keep_mask.append(True)

    # Pull codes from the underlying Faiss index storage. Faiss exposes
    # `.reconstruct(i)` for IndexBinaryFlat.
    if hash_index._index is None or hash_index.n_items == 0:
        return chunks

    bytes_per_code = hash_index.bits // 8
    codes = np.zeros((len(chunks), bytes_per_code), dtype=np.uint8)
    for i, row in enumerate(rows):
        if row < 0:
            continue
        codes[i] = hash_index._index.reconstruct(row)

    # Greedy cluster: walk in retrieval order, drop anyone within threshold
    # bits of an already-kept item.
    kept_codes: list[np.ndarray] = []
    kept_chunks: list[RetrievedChunk] = []
    n_dropped = 0
    for i, ch in enumerate(chunks):
        row = rows[i]
        if row < 0:
            kept_chunks.append(ch)
            continue
        code = codes[i]
        is_dup = False
        for kc in kept_codes:
            if _hamming(code, kc) <= threshold:
                is_dup = True
                break
        if is_dup:
            n_dropped += 1
            continue
        kept_codes.append(code)
        kept_chunks.append(ch)

    if n_dropped:
        logger.info(
            "hash dedup: %d → %d chunks (dropped %d at hamming ≤ %d)",
            len(chunks), len(kept_chunks), n_dropped, threshold,
        )
    return kept_chunks


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────


_POPCOUNT_TABLE = np.array([bin(i).count("1") for i in range(256)], dtype=np.int32)


def _hamming(a: np.ndarray, b: np.ndarray) -> int:
    """Hamming distance between two packed uint8 codes."""
    return int(_POPCOUNT_TABLE[np.bitwise_xor(a, b)].sum())


def _build_cid_to_row(hash_index: HashIndex) -> dict[str, int]:
    """Map chunk_id → row in the Faiss index. Cached on the index object."""
    cache = getattr(hash_index, "_cid_to_row_cache", None)
    if cache is not None:
        return cache
    m: dict[str, int] = {}
    for i, meta in enumerate(hash_index._metadata):
        cid = meta.get("chunk_id")
        if cid:
            m[cid] = i
    hash_index._cid_to_row_cache = m  # type: ignore[attr-defined]
    return m
