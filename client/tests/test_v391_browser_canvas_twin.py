from __future__ import annotations

from pathlib import Path

import pytest

from hashmm.agent.causal_work_graph import build_causal_work_graph
from hashmm.agent.completion_gate import build_completion_gate
from hashmm.agent.evidence_ref import (
    build_canvas_evidence_link,
    build_evidence_ref,
    sanitize_evidence_url,
    verify_evidence_ref,
)
from hashmm.api.routes import canvas_evidence
from hashmm.api.routes import canvas_locks, conversations
from hashmm.api import workspace


ROOT = Path(__file__).resolve().parents[1]


def test_evidence_ref_is_bounded_content_addressed_and_secret_free():
    ref = build_evidence_ref(
        conv_id="conv-1",
        captured_at=100.0,
        raw={
            "url": "https://Example.com/report?token=secret&keep=1#private",
            "page_title": "  季度   报告 ",
            "selected_text": " 营收   同比增长 20% ",
            "locator": {
                "selector": "html:nth-of-type(1) > body:nth-of-type(1) > p:nth-of-type(2)",
                "prefix": "前文",
                "suffix": "后文",
                "fingerprint": {
                    "tag": "P", "role": "article",
                    "ancestor_path": ["html", "body", "main"],
                },
            },
            "browser_session_id": "browser-1",
        },
    )
    assert ref["schema"] == "hashmm.evidence-ref.v1"
    assert ref["url"] == "https://example.com/report?keep=1"
    assert "secret" not in str(ref)
    assert ref["text_excerpt"] == "营收 同比增长 20%"
    assert len(ref["content_hash"]) == 64
    assert ref["freshness"]["status"] == "current"
    assert ref["trust_level"] == "untrusted_web"
    assert ref["integrity"]["remote_content_is_instruction"] is False
    assert ref["locator"]["fingerprint"]["tag"] == "p"
    assert ref["locator"]["fingerprint"]["role"] == "article"


def test_evidence_verification_marks_changed_or_missing_content():
    ref = build_evidence_ref(
        conv_id="conv-1",
        captured_at=10,
        raw={
            "url": "https://example.com/report",
            "selected_text": "原始结论",
            "locator": {"selector": "body:nth-of-type(1)"},
        },
    )
    current = verify_evidence_ref(
        ref,
        {"url": "https://example.com/report", "selected_text": "原始结论"},
        verified_at=20,
    )
    assert current["freshness"]["status"] == "current"
    changed = verify_evidence_ref(
        current,
        {"url": "https://example.com/report", "selected_text": "修订后的结论"},
        verified_at=30,
    )
    assert changed["freshness"]["status"] == "stale"
    assert changed["freshness"]["reason"] == "content_hash_changed"
    missing = verify_evidence_ref(
        changed,
        {"url": "https://example.com/report", "selected_text": ""},
        verified_at=40,
    )
    assert missing["freshness"]["status"] == "unavailable"
    assert missing["freshness"]["verification_count"] == 3


def test_unsafe_urls_and_css_locators_are_rejected():
    assert sanitize_evidence_url("javascript:alert(1)") == ""
    assert sanitize_evidence_url("https://user:pass@example.com/") == ""
    ref = build_evidence_ref(
        conv_id="conv-1",
        raw={
            "url": "https://example.com/",
            "selected_text": "内容",
            "locator": {"selector": "body > [onclick=evil]"},
        },
    )
    assert ref["locator"]["selector"] == ""


def test_canvas_link_is_deterministic_and_block_scoped():
    first = build_canvas_evidence_link(
        evidence_id="ev_1234567890",
        filename="work.html",
        block_id="decision-1",
        block_hash="a" * 64,
        linked_at=1,
    )
    second = build_canvas_evidence_link(
        evidence_id="ev_1234567890",
        filename="work.html",
        block_id="decision-1",
        block_hash="b" * 64,
        linked_at=2,
    )
    assert first["link_id"] == second["link_id"]
    assert first["relation"] == "supports"
    assert first["block_id"] == "decision-1"


def test_canvas_store_is_atomic_bounded_and_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(canvas_evidence.db, "conv_files_dir", lambda cid: tmp_path / cid)
    store = canvas_evidence._empty_store()
    store["refs"]["ev_1"] = {"schema": "hashmm.evidence-ref.v1", "evidence_id": "ev_1"}
    canvas_evidence._save_store("conv-1", store)
    path = tmp_path / "conv-1" / ".evidence" / "evidence-fabric.json"
    assert path.is_file()
    loaded = canvas_evidence._load_store("conv-1")
    assert loaded["revision"] == 1
    assert loaded["refs"]["ev_1"]["evidence_id"] == "ev_1"
    assert not path.with_suffix(".json.tmp").exists()


def test_evidence_hash_change_invalidates_only_linked_artifact():
    base = {
        "schema": "hashmm.task-evidence-graph.v1",
        "run_id": "run-evidence",
        "root_node_id": "artifact:work.html",
        "nodes": [
            {"id": "source:ev_1", "kind": "source", "label": "网页", "status": "current", "meta": {"source_id": "ev_1"}},
            {"id": "artifact:work.html", "kind": "artifact", "label": "work.html", "status": "ready", "meta": {}},
            {"id": "artifact:other.html", "kind": "artifact", "label": "other.html", "status": "ready", "meta": {}},
        ],
        "edges": [
            {"source": "source:ev_1", "target": "artifact:work.html", "relation": "supports"},
        ],
    }
    first = build_causal_work_graph(
        run_id="run-evidence",
        evidence_graph=base,
        source_snapshots=[{
            "evidence_id": "ev_1", "source_id": "ev_1", "filename": "网页",
            "content_hash": "a" * 64, "artifact_ref": "work.html",
        }],
        observed_at=1,
    )
    second = build_causal_work_graph(
        run_id="run-evidence",
        evidence_graph=base,
        source_snapshots=[{
            "evidence_id": "ev_1", "source_id": "ev_1", "filename": "网页",
            "content_hash": "b" * 64, "artifact_ref": "work.html",
        }],
        previous_graph=first,
        observed_at=2,
    )
    by_label = {node["label"]: node for node in second["nodes"]}
    assert by_label["work.html"]["status"] == "stale"
    assert by_label["other.html"]["status"] == "ready"
    assert second["invalidation"]["strategy"] == "content_hash_dependency_closure"


def test_persisted_stale_evidence_remains_stale_without_previous_generation():
    base = {
        "schema": "hashmm.task-evidence-graph.v1",
        "run_id": "run-restart",
        "root_node_id": "artifact:work.html",
        "nodes": [
            {"id": "artifact:work.html", "kind": "artifact", "label": "work.html", "status": "ready"},
        ],
        "edges": [],
    }
    graph = build_causal_work_graph(
        run_id="run-restart",
        evidence_graph=base,
        source_snapshots=[{
            "evidence_id": "ev_stale", "filename": "网页",
            "content_hash": "c" * 64, "freshness": "stale",
            "artifact_ref": "work.html",
        }],
    )
    assert graph["status"] == "stale"
    assert any(node["kind"] == "source_snapshot" and node["status"] == "stale" for node in graph["nodes"])
    assert next(node for node in graph["nodes"] if node["label"] == "work.html")["status"] == "stale"


def test_canvas_authority_and_frontend_are_fail_closed():
    lock_source = (ROOT / "hashmm/api/routes/canvas_locks.py").read_text("utf-8")
    share_source = (ROOT / "hashmm/api/routes/canvas_share.py").read_text("utf-8")
    panel_source = (ROOT / "frontend-next/components/ArtifactPanel.tsx").read_text("utf-8")
    browser_source = (ROOT / "frontend-next/components/BrowserInspector.tsx").read_text("utf-8")
    assert "require_conv_access(request, conv_id)" in lock_source
    assert "canvas_evidence_stale" not in share_source  # user gets a readable bounded error, not an internal code
    assert "if evidence[\"linked\"] and not evidence[\"ready\"]" in share_source
    assert "老后端无锁端点：直接放行" not in panel_source
    assert "无法确认画布编辑权" in panel_source
    assert "captureBrowserEvidence" in browser_source
    assert "linkCanvasEvidence" in browser_source
    assert "verifyBrowserEvidence" in browser_source
    assert "base_sha256" in panel_source
    assert "lock_session" in panel_source


def test_agent_canvas_patch_is_exact_atomic_and_audited(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace._dbmod, "CONV_FILES_ROOT", tmp_path)
    root = tmp_path / "conv-1"
    root.mkdir()
    canvas = root / "work.html"
    canvas.write_text("<main><section id='a'>old</section><section>keep</section></main>", "utf-8")

    result = workspace.patch_canvas_block(
        "conv-1", "work.html",
        "<section id='a'>old</section>",
        "<section id='a'>new</section>",
        "a",
    )
    assert result.startswith("OK:")
    assert "new" in canvas.read_text("utf-8")
    receipt = root / ".canvas-patches" / "work.html.json"
    assert receipt.is_file()
    audit = __import__("json").loads(receipt.read_text("utf-8"))[-1]
    assert audit["schema"] == "hashmm.canvas-patch-receipt.v1"
    assert "old</section>" not in receipt.read_text("utf-8")

    before = canvas.read_text("utf-8")
    refused = workspace.patch_canvas_block(
        "conv-1", "work.html", "<section>keep</section>", "<p>x</p>", "ambiguous",
    )
    assert refused.startswith("OK:")
    duplicate = workspace.patch_canvas_block(
        "conv-1", "work.html", "section", "aside", "not-unique",
    )
    assert "匹配 2 次" in duplicate
    assert canvas.read_text("utf-8") != before  # first unambiguous second patch committed


def test_canvas_http_commit_requires_live_lease_and_exact_revision(monkeypatch, tmp_path):
    root = tmp_path / "conv-1"
    root.mkdir()
    canvas = root / "work.html"
    canvas.write_text("<main><p>v1</p></main>", "utf-8")
    monkeypatch.setattr(conversations.db, "conv_files_dir", lambda _cid: root)
    monkeypatch.setattr(conversations.db, "add_conversation_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(conversations, "require_conv_access", lambda _request, _cid: {"user_id": "u1"})
    monkeypatch.setattr(conversations, "get_current_user", lambda _request: {"uid": "u1"})
    leases: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        canvas_locks, "require_canvas_lock",
        lambda cid, filename, session: leases.append((cid, filename, session)),
    )

    class _Request:
        def __init__(self, body):
            self.body = body
        async def json(self):
            return self.body

    import asyncio
    import hashlib
    base = hashlib.sha256(canvas.read_text("utf-8").encode()).hexdigest()
    saved = asyncio.run(conversations.put_conv_file(
        "conv-1", "work.html",
        _Request({
            "content": "<main><p>v2</p></main>",
            "base_sha256": base,
            "lock_session": "session-1",
        }),
    ))
    assert saved["sha256"] == hashlib.sha256(canvas.read_text("utf-8").encode()).hexdigest()
    assert leases == [("conv-1", "work.html", "session-1")]

    with pytest.raises(Exception) as conflict:
        asyncio.run(conversations.put_conv_file(
            "conv-1", "work.html",
            _Request({
                "content": "<main><p>lost update</p></main>",
                "base_sha256": base,
                "lock_session": "session-1",
            }),
        ))
    assert getattr(conflict.value, "status_code", None) == 409
    assert "lost update" not in canvas.read_text("utf-8")

    current = saved["sha256"]
    patched = asyncio.run(conversations.patch_canvas_blocks(
        "conv-1", "work.html",
        _Request({
            "base_sha256": current,
            "lock_session": "session-1",
            "idempotency_key": "patch-1",
            "operations": [{
                "op": "replace", "block_id": "body",
                "old_html": "<p>v2</p>", "new_html": "<p>v3</p>",
            }],
        }),
    ))
    assert patched["applied"] == 1
    assert "<p>v3</p>" in canvas.read_text("utf-8")


def test_stale_causal_dependency_closes_completion_gate():
    contract = {
        "schema": "hashmm.task-contract.v1",
        "run_id": "run-1",
        "success_criteria": [{
            "check_id": "delivery", "label": "交付存在", "required": True, "source": "runtime",
        }],
    }
    verification = {"checks": [{
        "check_id": "delivery", "status": "passed", "authority": "runtime_fact",
    }]}
    evidence_graph = {
        "schema": "hashmm.task-evidence-graph.v1",
        "graph_id": "eg-1", "run_id": "run-1",
        "summary": {"blockers": 0},
    }
    frontier = {
        "frontier_id": "ef-1",
        "summary": {"ready_routes": 0, "scope_blocked": 0, "waiting_input": 0},
        "items": [],
    }
    ready = build_completion_gate(
        task_contract=contract, verification=verification,
        evidence_graph=evidence_graph, execution_frontier=frontier,
        termination_reason="completed",
        causal_work_graph={"graph_id": "cw-1", "status": "ready", "summary": {}},
    )
    assert ready["can_claim_complete"] is True
    stale = build_completion_gate(
        task_contract=contract, verification=verification,
        evidence_graph=evidence_graph, execution_frontier=frontier,
        termination_reason="completed",
        causal_work_graph={
            "graph_id": "cw-2", "status": "stale",
            "summary": {"stale_nodes": 2, "invalid_receipts": 0},
        },
    )
    assert stale["status"] == "incomplete"
    assert stale["can_claim_complete"] is False
    assert any(item["code"] == "stale_dependency" for item in stale["failure_modes"])


def test_canvas_evidence_routes_are_registered():
    paths = {route.path for route in canvas_evidence.router.routes}
    assert "/api/conversations/{conv_id}/evidence-refs" in paths
    assert "/api/conversations/{conv_id}/evidence-refs/{evidence_id}/verify" in paths
    assert "/api/conversations/{conv_id}/files/{filename}/evidence-links" in paths
    conversation_source = (ROOT / "hashmm/api/routes/conversations.py").read_text("utf-8")
    assert '"/conversations/{conv_id}/files/{filename}/canvas-blocks"' in conversation_source
