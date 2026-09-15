from __future__ import annotations

from types import SimpleNamespace


def test_retrieval_contract_is_deterministic_and_does_not_self_grade_answer():
    from hashmm.retrieval.contract import build_retrieval_contract

    contract = build_retrieval_contract(
        query="营收",
        rewritten_query="2025 营业收入",
        requested_top_k=3,
        total_candidates=20,
        candidate_top_k=12,
        results=[
            {"content": "证据一", "filename": "a.pdf", "method": "dense"},
            {"content": "证据二", "filename": "b.pdf", "method": "graph_evidence"},
        ],
        strategy="grounded",
        rerank_method="cross_encoder",
        graph={"considered": 4, "added": 1, "missing": 2, "forbidden": 1},
    )
    assert contract["schema"] == "hashmm.retrieval-result.v1"
    assert contract["total_candidates"] == 20
    assert contract["evidence_count"] == 2
    assert contract["unique_documents"] == 2
    assert contract["citation_ready"] is True
    assert contract["has_more"] is True
    assert contract["source_methods"] == {"dense": 1, "graph_evidence": 1}
    assert contract["graph"] == {"considered": 4, "added": 1, "missing": 2, "forbidden": 1}
    # The contract reports observable evidence; it must not invent an answer score/pass.
    assert "score" not in contract
    assert "passed" not in contract


def test_pipeline_search_response_keeps_backward_compatible_defaults():
    from hashmm.retrieval_pipeline import SearchResponse, SearchResult

    response = SearchResponse(results=[SearchResult("x", 0.1)], query="q", total_candidates=1)
    assert response.results[0].text == "x"
    assert response.requested_top_k == 0
    assert response.rerank_method == "rrf"


def test_contract_handles_object_results_and_missing_sources_without_claiming_ready():
    from hashmm.retrieval.contract import build_retrieval_contract

    contract = build_retrieval_contract(
        query="q", results=[SimpleNamespace(text="evidence", filename="")],
    )
    assert contract["evidence_status"] == "available"
    assert contract["citation_ready"] is False
    assert contract["citation_ready_count"] == 0
