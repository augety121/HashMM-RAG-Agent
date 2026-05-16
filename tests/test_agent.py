"""Tests for the M4 LangGraph agent — no torch / no langgraph install needed.

We test the node functions directly since they're pure-Python rule-based.
"""

from __future__ import annotations

from hashmm.agent.nodes import (
    check_quality,
    classify_intent,
    plan_retrieval,
    route_after_quality,
    _heuristic_refine,
)


# ── classify_intent ────────────────────────────────────────────────────


def test_intent_cross_modal_trigger_word():
    state = {"query": "show me a figure of the transformer"}
    out = classify_intent(state)
    assert out["intent"] == "cross_modal"
    assert out["modality_filter"] == "image"  # 'figure' → image


def test_intent_table_keyword():
    state = {"query": "find the table comparing methods"}
    out = classify_intent(state)
    assert out["intent"] in ("cross_modal", "hybrid")
    assert out["modality_filter"] == "table"


def test_intent_chart_keyword_chinese():
    state = {"query": "找一下性能图表"}
    out = classify_intent(state)
    assert out["intent"] == "cross_modal"
    assert out["modality_filter"] == "chart"


def test_intent_factual_short():
    state = {"query": "what is BGE-M3"}
    out = classify_intent(state)
    assert out["intent"] == "factual"


def test_intent_semantic_default():
    state = {"query": "explain how late-interaction retrieval works in detail"}
    out = classify_intent(state)
    assert out["intent"] == "semantic"
    assert out["modality_filter"] is None


def test_intent_hybrid_compare():
    state = {"query": "compare ColPali and ColBERT in retrieval quality"}
    out = classify_intent(state)
    assert out["intent"] == "hybrid"


def test_intent_image_side_forces_cross_modal():
    state = {"query": "what is this", "query_image_path": "/tmp/x.jpg"}
    out = classify_intent(state)
    assert out["intent"] == "cross_modal"


# ── plan_retrieval ─────────────────────────────────────────────────────


def test_plan_cross_modal_uses_hash():
    out = plan_retrieval({"intent": "cross_modal", "modality_filter": "image"})
    assert out["strategy"] == "hash"
    assert out["top_k"] == 20


def test_plan_factual_uses_vector_low_k():
    out = plan_retrieval({"intent": "factual"})
    assert out["strategy"] == "vector"
    assert out["top_k"] == 10


def test_plan_hybrid_intent_widens_k():
    out = plan_retrieval({"intent": "hybrid"})
    assert out["strategy"] == "hybrid"
    assert out["top_k"] == 30


def test_plan_semantic_with_modality_uses_hybrid():
    out = plan_retrieval({"intent": "semantic", "modality_filter": "image"})
    assert out["strategy"] == "hybrid"


def test_plan_semantic_without_modality_uses_vector():
    out = plan_retrieval({"intent": "semantic", "modality_filter": None})
    assert out["strategy"] == "vector"


# ── check_quality ──────────────────────────────────────────────────────


def _mk_hit(cid, modality="text", hamming=20, doc_id="doc-A"):
    return {
        "chunk_id": cid, "modality": modality, "text": f"text-{cid}",
        "image_path": None, "score": -hamming, "rank": 0, "source": "hash",
        "meta": {"hamming_dist": hamming, "doc_id": doc_id},
    }


def test_quality_empty_fails():
    out = check_quality({"retrieved": []})
    assert out["quality_ok"] is False
    assert "no results" in out["quality_reason"]


def test_quality_modality_no_match_fails():
    out = check_quality({
        "retrieved": [_mk_hit("a", "text"), _mk_hit("b", "text")],
        "modality_filter": "image",
    })
    assert out["quality_ok"] is False
    assert "no image" in out["quality_reason"]


def test_quality_modality_one_match_fails():
    out = check_quality({
        "retrieved": [_mk_hit("a", "text"), _mk_hit("b", "image")],
        "modality_filter": "image",
    })
    assert out["quality_ok"] is False
    assert "only 1 image" in out["quality_reason"]


def test_quality_top_hamming_too_high_fails():
    out = check_quality({
        "retrieved": [_mk_hit("a", "text", hamming=80)],
    })
    assert out["quality_ok"] is False
    assert "hamming=80" in out["quality_reason"]


def test_quality_all_same_doc_fails():
    hits = [_mk_hit(str(i), "text", hamming=20, doc_id="doc-A") for i in range(5)]
    out = check_quality({"retrieved": hits})
    assert out["quality_ok"] is False
    assert "single document" in out["quality_reason"]


def test_quality_good_passes():
    hits = [
        _mk_hit("a", "text", hamming=15, doc_id="doc-A"),
        _mk_hit("b", "image", hamming=20, doc_id="doc-B"),
        _mk_hit("c", "table", hamming=25, doc_id="doc-C"),
    ]
    out = check_quality({"retrieved": hits, "modality_filter": None})
    assert out["quality_ok"] is True


def test_quality_modality_filter_with_enough_passes():
    hits = [
        _mk_hit("a", "image", hamming=20, doc_id="doc-A"),
        _mk_hit("b", "image", hamming=25, doc_id="doc-B"),
        _mk_hit("c", "image", hamming=28, doc_id="doc-C"),
    ]
    out = check_quality({"retrieved": hits, "modality_filter": "image"})
    assert out["quality_ok"] is True


# ── route_after_quality ────────────────────────────────────────────────


def test_route_ok_goes_to_generate():
    assert route_after_quality({"quality_ok": True}) == "generate"


def test_route_bad_first_attempt_refines():
    assert route_after_quality({"quality_ok": False, "refine_attempts": 0}) == "refine_query"


def test_route_bad_max_attempts_gives_up():
    assert route_after_quality({"quality_ok": False, "refine_attempts": 2}) == "generate"


def test_route_bad_attempts_1_still_refines():
    assert route_after_quality({"quality_ok": False, "refine_attempts": 1}) == "refine_query"


# ── heuristic refine ───────────────────────────────────────────────────


def test_heuristic_refine_strips_modality_words():
    out = _heuristic_refine("find an image of the transformer model")
    assert "image" not in out.lower()


def test_heuristic_refine_returns_nonempty_on_modality_only():
    out = _heuristic_refine("image")
    assert out  # fallback to original
