"""V700: user-visible Chat continuity, document scope and quiet polling."""
from __future__ import annotations

import logging

import pytest


pytestmark = pytest.mark.unit


def test_selected_document_scope_is_deduplicated_bounded_and_acl_intersected():
    from hashmm.access_control import (
        DocumentACL,
        effective_document_scope,
        normalize_document_scope,
    )

    selected = normalize_document_scope(["a.pdf", "a.pdf", " b.pdf ", ""])
    assert selected == ["a.pdf", "b.pdf"]

    acl = DocumentACL(rules={"u1": ["a.pdf"]}, default=[])
    effective = effective_document_scope(
        acl=acl,
        allowed=["a.pdf"],
        principal="u1",
        document_scope=selected,
    )
    assert effective == ["a.pdf"]


def test_selected_document_scope_is_applied_before_and_after_retrieval(monkeypatch):
    from hashmm import access_control

    class FakeChatRetrieval:
        kwargs = None

        def enhance(self, query, messages, **kwargs):
            self.kwargs = kwargs
            return messages, [
                {"filename": "selected.pdf", "owner_id": "u1"},
                {"filename": "not-selected.pdf", "owner_id": "u1"},
            ], {"mode": "test"}

    fake = FakeChatRetrieval()
    monkeypatch.setattr(
        access_control,
        "resolve_acl_scope",
        lambda principal: (None, None, "tenant"),
    )

    _messages, sources, _strategy, fingerprint = access_control.enhance_with_acl(
        fake,
        "question",
        [],
        principal="u1",
        document_scope=["selected.pdf"],
    )

    assert fake.kwargs["owner_id"] == "u1"
    assert fake.kwargs["allowed_docs"] == ["selected.pdf"]
    assert [item["filename"] for item in sources] == ["selected.pdf"]
    assert len(fingerprint) == 20


def test_successful_polling_is_silent_but_failures_remain_visible(monkeypatch):
    from hashmm.api import middleware

    records: list[str] = []
    monkeypatch.setattr(
        middleware.logger,
        "info",
        lambda message, *args, **kwargs: records.append(str(message)),
    )
    middleware._AGG.clear()

    path = "/api/conversations/conv-1/messages"
    middleware._agg_log("GET", path, 200, 12)
    assert records == []
    assert middleware._AGG == {}

    middleware._agg_log("GET", path, 500, 23)
    assert records == ["GET /api/conversations/conv-1/messages → 500 (23ms)"]
    assert middleware._AGG[("GET", path)]["status"] == 500

    records.clear()
    middleware._AGG.clear()
    selftest_path = "/api/selftest/job/214425a4f64040e3"
    middleware._agg_log("GET", selftest_path, 200, 7)
    assert records == []
    assert middleware._AGG == {}
