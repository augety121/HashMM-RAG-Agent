from __future__ import annotations

import asyncio

import pytest


def test_owner_filter_is_strict_and_legacy_rows_do_not_become_shared():
    from hashmm.retrieval_pipeline import RetrievalPipeline, SearchResult

    rows = [
        SearchResult(text="mine", score=1.0, owner_id="u1"),
        SearchResult(text="other", score=0.9, owner_id="u2"),
        SearchResult(text="legacy", score=0.8),
    ]
    visible = RetrievalPipeline._apply_filters(rows, {"owner_id": "u1"})
    assert [row.text for row in visible] == ["mine"]


def test_document_scope_preserves_admin_view_and_fails_closed_for_users():
    from hashmm.access_control import DocumentACL, filter_principal_documents

    rows = [
        {"filename": "mine.pdf", "owner_id": "u1"},
        {"filename": "other.pdf", "owner_id": "u2"},
        {"filename": "legacy.pdf", "owner_id": ""},
    ]
    assert filter_principal_documents(
        rows, principal="u1", is_admin=False,
    ) == [rows[0]]
    assert filter_principal_documents(
        rows, principal="u1", is_admin=True,
    ) == rows
    acl = DocumentACL(rules={"u1": ["legacy.pdf"]}, default=[])
    assert filter_principal_documents(
        rows, principal="u1", is_admin=False, acl=acl,
    ) == [rows[2]]


def test_chat_context_owner_scope_drops_other_and_legacy_chunks():
    from types import SimpleNamespace

    from hashmm.chat_retrieval import _filter_owner_results

    rows = [
        SimpleNamespace(text="mine", owner_id="u1"),
        SimpleNamespace(text="other", owner_id="u2"),
        SimpleNamespace(text="legacy", owner_id=""),
    ]
    assert [row.text for row in _filter_owner_results(rows, "u1")] == ["mine"]
    assert _filter_owner_results(rows, "") == []
    assert _filter_owner_results(rows, None) == rows


def test_chat_acl_adapter_binds_missing_acl_to_authenticated_owner(monkeypatch):
    from hashmm import access_control

    class FakeChatRetrieval:
        kwargs = None

        def enhance(self, query, messages, **kwargs):
            self.kwargs = kwargs
            return messages, [], object()

    fake = FakeChatRetrieval()
    monkeypatch.setattr(
        access_control,
        "resolve_acl_scope",
        lambda principal: (None, None, "scope"),
    )
    access_control.enhance_with_acl(
        fake, "question", [], principal="u1", retrieval_mode="mix",
    )
    assert fake.kwargs["owner_id"] == "u1"
    assert "allowed_docs" not in fake.kwargs


def test_legacy_retrieval_adapter_is_only_compatible_without_principal(
    monkeypatch,
):
    from hashmm import access_control

    class LegacyRetrieval:
        def enhance(self, query, messages, retrieval_mode="mix"):
            return messages, [], {"mode": retrieval_mode}

    monkeypatch.setattr(
        access_control,
        "resolve_acl_scope",
        lambda principal: (None, None, "scope"),
    )
    result = access_control.enhance_with_acl(
        LegacyRetrieval(), "question", [], principal=None,
        retrieval_mode="mix",
    )
    assert result[1] == []

    with pytest.raises(TypeError):
        access_control.enhance_with_acl(
            LegacyRetrieval(), "question", [], principal="u1",
            retrieval_mode="mix",
        )


def test_graph_visualization_filters_nodes_and_edges_by_source():
    from hashmm.kg.extractor import Entity, Relation
    from hashmm.kg.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    graph.add_entity(Entity(name="A", entity_type="概念", source_ids=["u1-c1"]))
    graph.add_entity(Entity(name="B", entity_type="概念", source_ids=["u1-c1"]))
    graph.add_entity(Entity(name="C", entity_type="概念", source_ids=["u2-c1"]))
    graph.add_relation(Relation(
        head="A", tail="B", head_type="概念", tail_type="概念",
        relation="supports", source_ids=["u1-c1"],
    ))
    graph.add_relation(Relation(
        head="B", tail="C", head_type="概念", tail_type="概念",
        relation="leaks", source_ids=["u2-c1"],
    ))

    scoped = graph.to_vis_data(allowed_source_ids={"u1-c1"})
    assert {node["label"] for node in scoped["nodes"]} == {"A", "B"}
    assert [(edge["from"], edge["to"]) for edge in scoped["edges"]] == [("a", "b")]
    serialized = str(scoped)
    assert "u1-c1" not in serialized
    assert "u2-c1" not in serialized


def test_kb_document_route_is_principal_scoped(monkeypatch):
    from hashmm.api.routes import kb
    from hashmm import retriever_bridge

    class Pipeline:
        def list_documents(self):
            return [
                {"filename": "mine.pdf", "doc_id": "d1", "owner_id": "u1", "num_chunks": 1},
                {"filename": "other.pdf", "doc_id": "d2", "owner_id": "u2", "num_chunks": 1},
                {"filename": "legacy.pdf", "doc_id": "d3", "owner_id": "", "num_chunks": 1},
            ]

    monkeypatch.setattr(retriever_bridge, "get_pipeline", lambda: Pipeline())
    monkeypatch.setattr(kb, "load_default_acl", lambda: None)
    monkeypatch.setattr(kb, "require_auth", lambda request: {"uid": "u1", "role": "user"})
    payload = asyncio.run(kb.list_kb_documents(object()))
    assert [item["filename"] for item in payload["documents"]] == ["mine.pdf"]

    monkeypatch.setattr(kb, "require_auth", lambda request: {"uid": "admin", "role": "admin"})
    payload = asyncio.run(kb.list_kb_documents(object()))
    assert {item["filename"] for item in payload["documents"]} == {
        "mine.pdf", "other.pdf", "legacy.pdf",
    }
