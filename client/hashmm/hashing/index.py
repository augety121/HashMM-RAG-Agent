"""Binary hash index built on Faiss.

Stores K-bit codes packed into uint8 (K/8 bytes per item) with a parallel
JSONL metadata file mapping integer index → Chunk info. We use
`IndexBinaryFlat` by default — exact Hamming-distance search, no
approximation. For >10M chunks switch to `IndexBinaryHNSW` (just change
`_build_index`).

Public API:

    idx = HashIndex(bits=128)
    idx.add(codes_uint8, metadata_list)       # codes: (N, K/8) np.uint8
    idx.save(path)                            # persists codes + metadata
    idx = HashIndex.load(path, metadata_path) # reload

    hits = idx.search(query_code_uint8, top_k=20)
    # hits is list[Hit] with .chunk_id, .hamming_dist, .meta
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np

from hashmm.utils import get_logger

logger = get_logger("hashmm.hashing.index")


@dataclass
class Hit:
    """One retrieved item."""

    chunk_id: str
    modality: str
    hamming_dist: int
    rank: int
    meta: dict


class HashIndex:
    """Faiss binary index + JSONL metadata."""

    def __init__(self, bits: int):
        if bits % 8 != 0:
            raise ValueError("bits must be a multiple of 8")
        self.bits = bits
        self._index = None  # lazy faiss init
        self._metadata: list[dict] = []  # one dict per added vector, parallel order

    # ── Lazy init ─────────────────────────────────────────────────────

    def _ensure_index(self):
        if self._index is not None:
            return self._index
        try:
            import faiss
        except ImportError as e:
            raise RuntimeError(
                "faiss not installed. pip install 'hashmm-rag[hash]' or faiss-cpu"
            ) from e
        self._index = faiss.IndexBinaryFlat(self.bits)
        return self._index

    # ── Mutation ──────────────────────────────────────────────────────

    def add(self, codes_uint8: np.ndarray, metadata: Iterable[dict]) -> None:
        """Insert N codes with N metadata dicts in parallel order.

        Args:
            codes_uint8: shape (N, bits//8), dtype uint8.
            metadata: iterable of dicts; one per row. Must include 'chunk_id'
                and 'modality'.
        """
        idx = self._ensure_index()
        if codes_uint8.dtype != np.uint8:
            raise TypeError(f"codes must be uint8, got {codes_uint8.dtype}")
        if codes_uint8.ndim != 2 or codes_uint8.shape[1] != self.bits // 8:
            raise ValueError(
                f"expected shape (N, {self.bits // 8}), got {codes_uint8.shape}"
            )
        meta_list = list(metadata)
        if len(meta_list) != codes_uint8.shape[0]:
            raise ValueError(
                f"metadata length {len(meta_list)} != codes {codes_uint8.shape[0]}"
            )
        # Faiss binary needs contiguous arrays
        codes_uint8 = np.ascontiguousarray(codes_uint8)
        idx.add(codes_uint8)
        self._metadata.extend(meta_list)
        logger.info("added %d codes (total: %d)", codes_uint8.shape[0], len(self._metadata))

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: np.ndarray, top_k: int = 20) -> list[Hit]:
        """Hamming-distance kNN. `query` shape (bits//8,) or (1, bits//8)."""
        idx = self._ensure_index()
        if query.ndim == 1:
            query = query[None, :]
        if query.dtype != np.uint8:
            query = query.astype(np.uint8)
        query = np.ascontiguousarray(query)
        D, I = idx.search(query, top_k)
        hits: list[Hit] = []
        for rank, (d, i) in enumerate(zip(D[0].tolist(), I[0].tolist())):
            if i < 0:
                continue
            meta = self._metadata[i]
            hits.append(
                Hit(
                    chunk_id=meta.get("chunk_id", f"idx-{i}"),
                    modality=meta.get("modality", "unknown"),
                    hamming_dist=int(d),
                    rank=rank,
                    meta=meta,
                )
            )
        return hits

    def search_batch(self, queries: np.ndarray, top_k: int = 20) -> list[list[Hit]]:
        """Batched search. `queries` shape (B, bits//8)."""
        idx = self._ensure_index()
        if queries.dtype != np.uint8:
            queries = queries.astype(np.uint8)
        queries = np.ascontiguousarray(queries)
        D, I = idx.search(queries, top_k)
        out: list[list[Hit]] = []
        for row_d, row_i in zip(D.tolist(), I.tolist()):
            row: list[Hit] = []
            for rank, (d, i) in enumerate(zip(row_d, row_i)):
                if i < 0:
                    continue
                meta = self._metadata[i]
                row.append(
                    Hit(
                        chunk_id=meta.get("chunk_id", f"idx-{i}"),
                        modality=meta.get("modality", "unknown"),
                        hamming_dist=int(d),
                        rank=rank,
                        meta=meta,
                    )
                )
            out.append(row)
        return out

    # ── Persistence ───────────────────────────────────────────────────

    def save(self, index_path: str | Path, metadata_path: str | Path) -> None:
        import faiss

        ip = Path(index_path)
        mp = Path(metadata_path)
        ip.parent.mkdir(parents=True, exist_ok=True)
        mp.parent.mkdir(parents=True, exist_ok=True)
        idx = self._ensure_index()
        faiss.write_index_binary(idx, str(ip))
        with mp.open("w", encoding="utf-8") as f:
            for m in self._metadata:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        logger.info(
            "saved index (%d items) → %s + metadata → %s",
            len(self._metadata), ip, mp,
        )

    @classmethod
    def load(cls, index_path: str | Path, metadata_path: str | Path) -> "HashIndex":
        import faiss

        idx = faiss.read_index_binary(str(index_path))
        bits = idx.d  # faiss binary index reports bits in .d
        obj = cls(bits)
        obj._index = idx
        with Path(metadata_path).open("r", encoding="utf-8") as f:
            obj._metadata = [json.loads(line) for line in f if line.strip()]
        if len(obj._metadata) != idx.ntotal:
            logger.warning(
                "metadata length %d != index ntotal %d (mismatch)",
                len(obj._metadata), idx.ntotal,
            )
        logger.info("loaded index (%d items) from %s", idx.ntotal, index_path)
        return obj

    # ── Stats ─────────────────────────────────────────────────────────

    @property
    def n_items(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal

    def size_bytes(self) -> int:
        """Total raw storage for the codes (excludes metadata)."""
        return self.n_items * (self.bits // 8)

    def size_summary(self) -> str:
        bytes_used = self.size_bytes()
        return (
            f"{self.n_items} items × {self.bits} bits "
            f"= {bytes_used:,} bytes ({bytes_used / 2**20:.2f} MiB)"
        )
