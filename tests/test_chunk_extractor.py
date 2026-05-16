"""Tests for ChunkExtractor — no torch / no GPU needed."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hashmm.ingestion.chunk_extractor import (
    Chunk,
    ChunkExtractor,
    CrossModalPair,
    read_chunks_jsonl,
    read_pairs_jsonl,
    write_chunks_jsonl,
    write_pairs_jsonl,
)


@pytest.fixture
def mineru_content():
    """A representative MinerU content_list with text/image/table/equation."""
    return [
        {"type": "text", "text": "Introduction to multimodal RAG.", "page_idx": 0},
        {"type": "text", "text": "We propose HashMM-RAG.", "page_idx": 1},
        {
            "type": "image",
            "img_path": "/tmp/fig1.jpg",
            "image_caption": ["Figure 1: System architecture diagram."],
            "image_footnote": [],
            "page_idx": 1,
        },
        {"type": "text", "text": "Figure 1 shows the architecture.", "page_idx": 1},
        {
            "type": "table",
            "img_path": "/tmp/tab1.jpg",
            "table_caption": ["Table 1: Performance comparison."],
            "table_body": "<table><tr><td>Method</td><td>Acc</td></tr></table>",
            "page_idx": 2,
        },
        {"type": "equation", "text": "L = -log(p)", "text_format": "latex", "page_idx": 2},
        {"type": "image", "img_path": "/tmp/fig2.jpg", "page_idx": 3},  # uncaptioned
        {"type": "text", "text": "Above figure illustrates the hash allocation.", "page_idx": 3},
    ]


def test_extract_chunk_counts(mineru_content):
    extractor = ChunkExtractor()
    chunks, pairs = extractor.extract(mineru_content, doc_id="doc-test")
    by_mod = {}
    for c in chunks:
        by_mod[c.modality] = by_mod.get(c.modality, 0) + 1
    assert by_mod == {"text": 4, "image": 2, "table": 1, "equation": 1}


def test_extract_pairs_sources(mineru_content):
    extractor = ChunkExtractor()
    _, pairs = extractor.extract(mineru_content, doc_id="doc-test")
    sources = sorted(p.source for p in pairs)
    # captioned image + table caption + uncaptioned image's neighbour text
    assert "caption" in sources
    assert "table_caption" in sources
    assert "neighbour_text" in sources


def test_uncaptioned_image_uses_neighbour(mineru_content):
    """Uncaptioned image at idx 6 should pair with text at idx 7."""
    extractor = ChunkExtractor(neighbour_window=1)
    _, pairs = extractor.extract(mineru_content, doc_id="doc-test")
    nbr_pairs = [p for p in pairs if p.source == "neighbour_text"]
    assert any(p.image_path == "/tmp/fig2.jpg" for p in nbr_pairs)


def test_chunk_id_stability(mineru_content):
    """Same content → same chunk_id across runs."""
    extractor = ChunkExtractor()
    c1, _ = extractor.extract(mineru_content, doc_id="doc-test")
    c2, _ = extractor.extract(mineru_content, doc_id="doc-test")
    assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]


def test_jsonl_roundtrip(mineru_content):
    extractor = ChunkExtractor()
    chunks, pairs = extractor.extract(mineru_content, doc_id="doc-test")
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td) / "chunks.jsonl"
        pp = Path(td) / "pairs.jsonl"
        write_chunks_jsonl(chunks, cp)
        write_pairs_jsonl(pairs, pp)
        assert read_chunks_jsonl(cp) == chunks
        assert read_pairs_jsonl(pp) == pairs


def test_empty_text_skipped():
    extractor = ChunkExtractor()
    content = [{"type": "text", "text": "   ", "page_idx": 0}]
    chunks, _ = extractor.extract(content, doc_id="d")
    assert chunks == []


def test_image_without_path_skipped():
    extractor = ChunkExtractor()
    content = [{"type": "image", "img_path": "", "page_idx": 0}]
    chunks, pairs = extractor.extract(content, doc_id="d")
    assert chunks == [] and pairs == []
