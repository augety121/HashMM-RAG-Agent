from types import SimpleNamespace
import sys
import types

from hashmm.retrieval.graph_engineering import expand_graph_evidence
from hashmm.retrieval_pipeline import SearchResult
from hashmm.access_control import DocumentACL


def test_graph_expansion_fetches_original_chunks_and_keeps_provenance(monkeypatch):
    monkeypatch.setenv("HASHMM_GRAPH_ENGINEERING", "1")
    current = [SearchResult(text="seed", score=0.9, chunk_id="c1", doc_id="d1")]
    corpus = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "seed"},
        {"chunk_id": "c2", "doc_id": "d2", "text": "original evidence", "source": "evidence.pdf", "page": 2},
    ]
    evidence = [{
        "chunk_id": "c2", "score": 0.8,
        "entities": ["实体甲"], "relations": ["实体甲 → 关联 → 实体乙"],
    }]

    expanded = expand_graph_evidence(current, corpus, evidence, limit=2)

    assert expanded.added_chunk_ids == ["c2"]
    assert len(expanded.results) == 2
    item = expanded.results[1]
    assert item.text == "original evidence"
    assert item.source_type == "graph_evidence"
    assert item.graph_support["entities"] == ["实体甲"]


def test_graph_expansion_is_bounded_deduplicated_and_fail_closed(monkeypatch):
    monkeypatch.setenv("HASHMM_GRAPH_ENGINEERING", "1")
    current = [SearchResult(text="seed", score=1.0, chunk_id="c1")]
    corpus = [
        {"chunk_id": "c2", "text": "two"},
        {"chunk_id": "c3", "text": "three"},
    ]
    evidence = [
        {"chunk_id": "c1", "score": 1},
        {"chunk_id": "missing", "score": 0.99},
        {"chunk_id": "c3", "score": 0.5},
        {"chunk_id": "c2", "score": 0.9},
    ]

    expanded = expand_graph_evidence(current, corpus, evidence, limit=1)

    assert expanded.added_chunk_ids == ["c2"]
    assert expanded.skipped_missing == 1
    assert [item.chunk_id for item in expanded.results] == ["c1", "c2"]


def test_graph_expansion_kill_switch(monkeypatch):
    monkeypatch.setenv("HASHMM_GRAPH_ENGINEERING", "0")
    seed = SimpleNamespace(chunk_id="seed")
    expanded = expand_graph_evidence([seed], [{"chunk_id": "c2", "text": "x"}], [{"chunk_id": "c2"}])
    assert expanded.results == [seed]
    assert expanded.added_chunk_ids == []


def test_graph_expansion_applies_document_acl_before_context(monkeypatch):
    monkeypatch.setenv("HASHMM_GRAPH_ENGINEERING", "1")
    corpus = [{
        "chunk_id": "secret", "doc_id": "private-id", "source": "private.pdf",
        "text": "restricted evidence",
    }]
    acl = DocumentACL(default=["public*.pdf"])

    expanded = expand_graph_evidence(
        [], corpus, [{"chunk_id": "secret", "score": 1.0}],
        acl=acl, principal="user-a",
    )

    assert expanded.results == []
    assert expanded.added_chunk_ids == []
    assert expanded.skipped_forbidden == 1


def test_kg_retriever_returns_deterministic_chunk_support():
    try:
        from hashmm.kg.kg_retriever import KGRetriever
    except ImportError:
        # Some lightweight test interpreters omit networkx/bz2.  The bundled
        # desktop runtime exercises this path in the release verification.
        import pytest
        pytest.skip("KG runtime dependencies are unavailable in this interpreter")
    entity = SimpleNamespace(
        key="entity-a", name="实体甲", entity_type="ORG", description="",
        score=0.82, source_ids=["c2", "c1"],
    )

    class FakeVdb:
        size = 1

        def __init__(self, matches):
            self.matches = matches

        def search(self, *_args, **_kwargs):
            return self.matches

    class FakeGraph:
        num_entities = 1
        num_relations = 0
        graph = SimpleNamespace(nodes={})

        @staticmethod
        def get_neighbors(*_args, **_kwargs):
            return {"edges": [], "neighbors": []}

    retriever = KGRetriever()
    retriever._kg = FakeGraph()
    retriever._entity_vdb = FakeVdb([entity])
    retriever._relation_vdb = FakeVdb([])

    result = retriever.search("实体甲")

    assert result.chunk_ids == ["c1", "c2"]
    assert [item["chunk_id"] for item in result.evidence] == ["c1", "c2"]
    assert result.evidence[0]["entities"] == ["实体甲"]


def test_chat_acl_never_injects_unfiltered_graph_summary(monkeypatch):
    from hashmm.chat_retrieval import ChatRetrieval
    from hashmm.retrieval_pipeline import SearchResponse

    public = SearchResult(
        text="public text", score=2.0, chunk_id="public", doc_id="public.pdf",
        filename="public.pdf",
    )

    class Pipeline:
        vector_index = SimpleNamespace(_metadata=[{
            "chunk_id": "secret", "doc_id": "private.pdf",
            "filename": "private.pdf", "text": "restricted evidence",
        }])

        @staticmethod
        def search(*_args, **_kwargs):
            return SearchResponse(results=[public], query="query", total_candidates=1)

    class Plan:
        is_multi_step = False
        output_hint = ""

    planner_module = types.ModuleType("hashmm.query_planner")
    planner_module.QueryPlanner = lambda: SimpleNamespace(plan=lambda *_args: Plan())
    planner_module.execute_plan = lambda *_args: []
    monkeypatch.setitem(sys.modules, "hashmm.query_planner", planner_module)

    kg_module = types.ModuleType("hashmm.kg.kg_retriever")
    kg_module.get_kg_retriever = lambda: SimpleNamespace(
        is_available=True,
        search=lambda *_args, **_kwargs: SimpleNamespace(
            entities=[{"name": "secret entity", "type": "ORG", "description": "private detail"}],
            relations=[{"head": "secret", "tail": "hidden", "relation": "owns", "description": "private relation"}],
            evidence=[{"chunk_id": "secret", "score": 1.0, "entities": ["secret entity"], "relations": ["secret owns hidden"]}],
        ),
    )
    monkeypatch.setitem(sys.modules, "hashmm.kg.kg_retriever", kg_module)

    retrieval = ChatRetrieval()
    monkeypatch.setattr(retrieval, "_ensure_pipeline", lambda: None)
    monkeypatch.setattr(retrieval, "should_search", lambda _query: True)
    monkeypatch.setattr(retrieval, "analyze_query", lambda _query: SimpleNamespace(
        is_compare=False, companies=[], rewritten_query="",
    ))
    monkeypatch.setattr(retrieval, "rewrite_query", lambda query, _messages: query)
    retrieval._pipeline = Pipeline()

    enhanced, sources, _strategy = retrieval.enhance(
        "query", [{"role": "user", "content": "query"}],
        retrieval_mode="mix", allowed_docs=["public.pdf"],
    )

    prompt = "\n".join(str(message.get("content") or "") for message in enhanced)
    assert "secret entity" not in prompt
    assert "private detail" not in prompt
    assert "private relation" not in prompt
    assert [source["filename"] for source in sources] == ["public.pdf"]
