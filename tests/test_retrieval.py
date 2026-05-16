"""Tests for retrieval helpers — no torch / no GPU needed."""

from __future__ import annotations

from hashmm.retrieval.base import RetrievedChunk
from hashmm.retrieval.hybrid_router import rrf_fuse


def _mk(cid, modality, rank, source):
    return RetrievedChunk(
        chunk_id=cid,
        modality=modality,
        text=f"text for {cid}",
        image_path=None,
        score=-rank,
        rank=rank,
        source=source,
    )


def test_rrf_overlap_wins():
    """A chunk appearing in BOTH lists at high ranks should rank first."""
    hash_list = [_mk("A", "text", 0, "hash"), _mk("B", "text", 1, "hash")]
    vec_list = [_mk("B", "text", 0, "vector"), _mk("C", "text", 1, "vector")]
    fused = rrf_fuse([hash_list, vec_list], k=60, top_k=3)
    # B is rank 1 in hash + rank 0 in vector → must beat A (rank 0 hash only)
    # and C (rank 1 vector only).
    assert fused[0].chunk_id == "B"


def test_rrf_math():
    """Score = 1/(k+rank). Verify the arithmetic exactly."""
    hash_list = [_mk("X", "text", 0, "hash")]
    fused = rrf_fuse([hash_list], k=60, top_k=1)
    assert abs(fused[0].score - 1.0 / 60.0) < 1e-9


def test_rrf_preserves_metadata():
    h = [_mk("A", "image", 0, "hash")]
    fused = rrf_fuse([h], k=60, top_k=1)
    assert fused[0].chunk_id == "A"
    assert fused[0].modality == "image"
    assert fused[0].source == "hybrid"
    assert fused[0].meta["original_source"] == "hash"
    assert "fused_score" in fused[0].meta


def test_rrf_top_k_limit():
    listA = [_mk(f"chk-{i}", "text", i, "hash") for i in range(10)]
    fused = rrf_fuse([listA], k=60, top_k=3)
    assert len(fused) == 3
    # In input list, rank-0 has the highest 1/(k+rank), so first.
    assert fused[0].chunk_id == "chk-0"


def test_rrf_disjoint_lists():
    a = [_mk("A", "text", 0, "hash")]
    b = [_mk("B", "text", 0, "vector")]
    fused = rrf_fuse([a, b], k=60, top_k=2)
    # Both at rank 0 → same score, so a tied result. Order is then by
    # insertion / dict ordering. Just check both are present.
    assert {f.chunk_id for f in fused} == {"A", "B"}
