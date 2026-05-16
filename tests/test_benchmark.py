"""Tests for benchmark module (M7).

Coverage:
  - vidore_loader  : schema detection helpers
  - evaluator      : pytrec_eval wrapper sanity
  - reports        : markdown rendering
  - runner         : RunResult dataclass + JSON serialisation

The model-dependent retrievers (BGEM3Dense, HashMMRetriever) are smoke-
tested for construction only; full e2e runs happen on the GPU box.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


# ── vidore_loader: schema detection ────────────────────────────────────


def test_first_key_picks_present():
    from hashmm.benchmark.vidore_loader import _first_key
    row = {"corpus-id": "doc1", "text": "hello"}
    assert _first_key(row, ("doc-id", "corpus-id", "id")) == "corpus-id"
    assert _first_key(row, ("text",)) == "text"
    assert _first_key(row, ("missing",)) is None


def test_detect_keys_raises_when_none_found():
    from hashmm.benchmark.vidore_loader import _detect_keys
    rows = [{"unrelated": 1}]
    with pytest.raises(KeyError):
        _detect_keys(rows, ("doc-id", "id"))


def test_detect_keys_finds_first_match():
    from hashmm.benchmark.vidore_loader import _detect_keys
    rows = [{"text": "x", "id": "1"}]
    # First in candidate list wins, NOT first in row.
    assert _detect_keys(rows, ("id", "doc-id")) == "id"


# ── evaluator: pytrec_eval wrapper ─────────────────────────────────────


def test_evaluate_perfect_ranking_gives_one():
    pytest.importorskip("pytrec_eval")
    from hashmm.benchmark.evaluator import evaluate

    qrels = {"q1": {"d1": 1, "d2": 1}}
    # Top results are exactly the relevant docs
    results = {"q1": {"d1": 1.0, "d2": 0.9, "d3": 0.5}}
    m = evaluate(qrels, results)
    assert m["ndcg_cut_5"] == pytest.approx(1.0)
    assert m["recall_5"] == pytest.approx(1.0)


def test_evaluate_no_overlap_gives_zero():
    pytest.importorskip("pytrec_eval")
    from hashmm.benchmark.evaluator import evaluate

    qrels = {"q1": {"d1": 1}}
    results = {"q1": {"d99": 1.0}}
    m = evaluate(qrels, results)
    assert m["ndcg_cut_5"] == 0.0
    assert m["recall_5"] == 0.0


def test_evaluate_handles_mixed_queries():
    pytest.importorskip("pytrec_eval")
    from hashmm.benchmark.evaluator import evaluate

    qrels = {
        "q1": {"d1": 1},
        "q2": {"d2": 1, "d3": 1},
    }
    results = {
        "q1": {"d1": 1.0, "d5": 0.5},          # perfect
        "q2": {"d99": 1.0, "d2": 0.5, "d3": 0.2},  # noisy but recovers
    }
    m = evaluate(qrels, results)
    # Recall@5 averaged: q1=1.0, q2=1.0 → 1.0
    assert m["recall_5"] == pytest.approx(1.0)
    # nDCG@5 is between 0 and 1 (q1=1, q2<1 → avg<1)
    assert 0 < m["ndcg_cut_5"] < 1


# ── reports: markdown rendering ────────────────────────────────────────


def test_markdown_table_includes_all_runs():
    from hashmm.benchmark.runner import RunResult
    from hashmm.benchmark.reports import make_markdown_table

    r1 = RunResult(
        model="BGE-M3-dense", dataset="ds",
        metrics={"ndcg_cut_5": 0.45, "ndcg_cut_10": 0.52,
                 "recall_5": 0.60, "recall_10": 0.78,
                 "map": 0.40, "recip_rank": 0.55},
        stats={"n_docs": 100, "n_queries": 50, "n_qrels": 60,
               "index_size_bytes": 1_000_000, "index_size_mb": 0.95,
               "avg_query_ms": 12.5},
    )
    r2 = RunResult(
        model="HashMM-RAG", dataset="ds",
        metrics={"ndcg_cut_5": 0.42, "recall_5": 0.58},
        stats={"index_size_mb": 0.02, "avg_query_ms": 2.3},
    )
    md = make_markdown_table([r1, r2])
    assert "BGE-M3-dense" in md
    assert "HashMM-RAG" in md
    assert "0.4500" in md
    assert "ColPali" in md  # public baselines included


def test_markdown_empty_runs_renders():
    from hashmm.benchmark.reports import make_markdown_table
    md = make_markdown_table([])
    assert "no runs" in md


# ── runner: RunResult serialisation ────────────────────────────────────


def test_run_result_round_trip():
    from hashmm.benchmark.runner import RunResult
    from hashmm.benchmark.reports import dump_json

    tmp = Path(tempfile.mkdtemp(prefix="hashmm-bench-"))
    try:
        r = RunResult(
            model="m", dataset="d",
            metrics={"ndcg_cut_5": 0.5},
            stats={"n_docs": 10},
        )
        path = dump_json(r, tmp / "out.json")
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["model"] == "m"
        assert loaded["metrics"]["ndcg_cut_5"] == 0.5
        assert loaded["stats"]["n_docs"] == 10
    finally:
        import shutil
        shutil.rmtree(tmp)


# ── OCR helper ─────────────────────────────────────────────────────────


def test_ocr_cache_key_stable():
    """Same path + lang yields same cache key; different lang differs."""
    from hashmm.benchmark.ocr import _image_cache_key
    from pathlib import Path

    p = Path("/tmp/test_image.png")
    k1 = _image_cache_key(p, "eng")
    k2 = _image_cache_key(p, "eng")
    k3 = _image_cache_key(p, "fra")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16


def test_ocr_cache_dir_created():
    from hashmm.benchmark.ocr import OCRCache
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-ocr-test-"))
    try:
        cache = OCRCache(tmp / "ocr_cache")
        assert (tmp / "ocr_cache").exists()
        stats = cache.stats()
        assert stats["n_cached"] == 0
    finally:
        shutil.rmtree(tmp)


def test_ocr_image_returns_empty_on_failure(monkeypatch=None):
    """When tesseract is not installed OR image is broken, ocr_image should
    not crash. With pytest-monkeypatch we'd inject; here we just call on
    a nonexistent file and verify we get '' back rather than an exception."""
    from hashmm.benchmark.ocr import ocr_image
    try:
        result = ocr_image("/nonexistent/path/that/does/not/exist.png")
        # If pytesseract is installed but file is missing, we should get ""
        assert result == ""
    except ImportError:
        # If pytesseract isn't installed, ocr_image raises ImportError
        # specifically — that's also acceptable behaviour.
        pass


def test_bgem3_retriever_constructs_without_loading_model():
    """Should not download any model at construction time — encoder is lazy."""
    from hashmm.benchmark.retrievers import BGEM3Dense
    from hashmm.config import HashMMConfig

    cfg = HashMMConfig()
    r = BGEM3Dense(cfg)
    assert r.name == "BGE-M3-dense"
    assert r._text_enc is None  # lazy
    assert r.index_size_bytes() == 0


def test_hashmm_retriever_constructs_without_loading_model():
    from hashmm.benchmark.retrievers import HashMMRetriever
    from hashmm.config import HashMMConfig

    cfg = HashMMConfig()
    r = HashMMRetriever(cfg)
    assert r.name == "HashMM-RAG"
    assert r._text_enc is None
    assert r._hash_net is None


def test_retriever_raises_before_index():
    from hashmm.benchmark.retrievers import BGEM3Dense
    from hashmm.config import HashMMConfig

    cfg = HashMMConfig()
    r = BGEM3Dense(cfg)
    with pytest.raises(RuntimeError):
        r.retrieve({"q1": "hello"})


# ── public baselines table ─────────────────────────────────────────────


def test_public_baselines_has_colpali():
    from hashmm.benchmark.reports import PUBLIC_BASELINES
    assert "ColPali v1.3" in PUBLIC_BASELINES
    for name, info in PUBLIC_BASELINES.items():
        assert "ndcg_cut_5" in info
        assert 0 <= info["ndcg_cut_5"] <= 1
