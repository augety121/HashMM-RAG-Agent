"""V705 regressions for the durable public Agent platform protocol."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest


def _isolated_db(tmp_db, monkeypatch):
    from hashmm.api import database as db
    db._close_pool()
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_db))
    db.init_db()
    return db


def test_database_startup_upgrades_legacy_approval_table_before_schema_indexes(tmp_db, monkeypatch):
    """A real pre-V700 server DB must reach migrations instead of failing boot."""
    from hashmm.api import database as db

    db._close_pool()
    db_path = Path(tmp_db)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""CREATE TABLE tool_approval_requests (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conv_id TEXT NOT NULL,
            message_id TEXT DEFAULT '', fingerprint TEXT NOT NULL,
            tool_name TEXT NOT NULL, args_json TEXT NOT NULL DEFAULT '{}',
            cwd TEXT DEFAULT '', reason TEXT DEFAULT '', risk TEXT DEFAULT 'high',
            status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL,
            decided_at REAL DEFAULT 0, expires_at REAL NOT NULL,
            consumed_at REAL DEFAULT 0, decided_by TEXT DEFAULT '')""")
        conn.execute(
            "INSERT INTO tool_approval_requests"
            "(id,user_id,conv_id,fingerprint,tool_name,created_at,expires_at) "
            "VALUES('approval-old','alice','conv-old','fp','shell',1,9999999999)"
        )

    db.init_db()

    with db._conn() as conn:
        columns = {str(row[1]) for row in conn.execute(
            "PRAGMA table_info('tool_approval_requests')"
        )}
        indexes = {str(row[1]) for row in conn.execute(
            "PRAGMA index_list('tool_approval_requests')"
        )}
        preserved = conn.execute(
            "SELECT id,user_id,work_run_id,step_id,call_id,scope_json "
            "FROM tool_approval_requests WHERE id='approval-old'"
        ).fetchone()
    assert {"work_run_id", "step_id", "call_id", "scope_json"} <= columns
    assert "idx_tool_approval_run" in indexes
    assert tuple(preserved) == ("approval-old", "alice", "", "", "", "{}")


def test_idempotency_is_atomic_replayable_and_rejects_payload_reuse(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_protocol as p

    p.ensure_schema()
    request = {"input": "one effect"}

    def claim():
        try:
            return p.begin_idempotent("alice", "POST:/v1/responses", "same-key", request)["state"]
        except p.ProtocolError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=24) as pool:
        states = list(pool.map(lambda _index: claim(), range(48)))
    assert states.count("claimed") == 1
    assert set(states) <= {"claimed", "request_in_progress"}

    p.finish_idempotent(
        "alice", "POST:/v1/responses", "same-key", status_code=201,
        response={"id": "resp-one"}, resource_id="resp-one",
    )
    replay = p.begin_idempotent("alice", "POST:/v1/responses", "same-key", request)
    assert replay == {"state": "replay", "status_code": 201,
                      "response": {"id": "resp-one"}, "resource_id": "resp-one"}
    with pytest.raises(p.ProtocolError) as conflict:
        p.begin_idempotent("alice", "POST:/v1/responses", "same-key", {"input": "different"})
    assert conflict.value.code == "idempotency_conflict"


def test_thread_turn_response_and_events_are_owner_scoped_and_forkable(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_protocol as p

    thread = p.create_thread("alice", title="research")
    turn = p.create_turn_with_input(
        "alice", thread["id"], [{"type": "message", "role": "user", "content": "find evidence"}],
    )
    response = p.create_response(
        "alice", request={"input": "find evidence"}, thread_id=thread["id"],
        turn_id=turn, run_id="wr-owned", model="test-model",
    )
    p.append_response_event("alice", response["id"], "response.created", {"run_id": "wr-owned"})
    p.append_response_event("alice", response["id"], "response.completed", {"run_id": "wr-owned"})

    assert p.get_thread("bob", thread["id"]) is None
    assert p.get_response("bob", response["id"]) is None
    assert p.list_response_events("bob", response["id"]) == []
    assert [item["event"] for item in p.list_response_events("alice", response["id"], after_sequence=1)] == ["response.completed"]

    forked = p.fork_thread("alice", thread["id"])
    assert forked and forked["id"] != thread["id"]
    assert forked["metadata"]["forked_from"] == thread["id"]


def test_budget_reserve_settle_and_limit_are_fail_closed(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import platform_protocol as p

    reservation = p.reserve_budget(
        "alice", run_id="wr-1", amount=0.6, currency="CNY",
        idempotency_key="reserve-1", limit=1.0,
    )
    with pytest.raises(p.ProtocolError) as exceeded:
        p.reserve_budget(
            "alice", run_id="wr-2", amount=0.5, currency="CNY",
            idempotency_key="reserve-2", limit=1.0,
        )
    assert exceeded.value.code == "budget_exceeded"
    event = p.settle_usage(
        "alice", reservation_id=reservation["id"], run_id="wr-1", step_id="generate",
        provider="provider", service="llm", model="model", quantity=100, unit="token",
        estimated_cost=0.6, currency="CNY", price_version="test-v1",
        idempotency_key="usage-1",
    )
    assert event["state"] == "settled"
    assert p.usage_summary("alice")["events"] == 1


def test_expected_gain_penalises_duplicate_and_risky_evidence(monkeypatch):
    monkeypatch.setenv("HASHMM_EXPECTED_GAIN", "1")
    from hashmm.retrieval.expected_gain import select_expected_gain

    selected, contract = select_expected_gain("q", [
        {"content": "strong evidence", "filename": "a.pdf", "score": 0.9},
        {"content": "strong evidence", "filename": "b.pdf", "score": 0.89},
        {"content": "risky", "filename": "c.pdf", "score": 0.95, "source_risk": 1.0},
    ], top_k=2)
    assert selected[0]["filename"] == "a.pdf"
    assert contract["trace"][1]["redundancy"] > 0 or contract["trace"][2]["redundancy"] > 0
    assert contract["formula"].startswith("0.72*relevance")


def test_explicit_document_scope_filters_sparse_rrf_leak(monkeypatch):
    from hashmm import retriever_bridge as bridge

    selected = SimpleNamespace(
        text="selected evidence", filename="selected.pdf", doc_id="selected",
        page=1, section="", score=0.9,
    )
    leaked = SimpleNamespace(
        text="other evidence", filename="other.pdf", doc_id="other",
        page=2, section="", score=0.99,
    )
    response = SimpleNamespace(
        results=[leaked, selected], expanded_query="q", total_candidates=2,
        elapsed_ms=1, rerank_method="rrf",
    )
    monkeypatch.setattr(bridge, "_pipeline", SimpleNamespace(search=lambda *_args, **_kwargs: response))
    monkeypatch.setenv("HASHMM_EXPECTED_GAIN", "1")

    result = bridge.kb_search_bridge(
        {"query": "q", "top_k": 5}, {"user_id": "alice", "doc_filter": ["selected.pdf"]},
    )
    assert [item["filename"] for item in result["results"]] == ["selected.pdf"]
    assert result["retrieval_contract"]["scope_enforced"] is True
    assert result["retrieval_contract"]["scope_leakage_count"] == 0


def test_canonical_state_machine_supports_pause_retry_and_verification():
    from hashmm.agent.task_state import can_transition, public_contract

    assert can_transition("running", "paused")
    assert can_transition("paused", "retrying")
    assert can_transition("running", "verifying")
    assert can_transition("verifying", "completed")
    assert public_contract("paused")["terminal"] is False


def test_knowledge_evolution_requires_provenance_review_eval_and_can_rollback(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.kg import knowledge_evolution as evolution

    evidence = [{"source": "paper.pdf", "sha256": "a" * 64, "page": 3, "chunk_id": "c-1"}]
    first = evolution.propose(
        owner_id="alice", project_id="project-1", head="A", relation="capital", tail="B",
        confidence=0.9, evidence=evidence, source_run_id="wr-search-1", extractor="verified-search",
    )
    assert first["status"] == "pending"
    with pytest.raises(evolution.EvolutionError) as no_eval:
        evolution.promote("alice", "project-1", [first["id"]], actor_id="alice",
                          evaluation={"passed": False})
    assert no_eval.value.code == "evaluation_required"

    approved = evolution.review_candidate(
        "alice", "project-1", first["id"], actor_id="alice", decision="approve",
    )
    assert approved and approved["status"] == "approved"
    version_one = evolution.promote(
        "alice", "project-1", [first["id"]], actor_id="alice",
        evaluation={"passed": True, "suite": "frozen-regression"}, expected_parent_version="",
    )
    assert version_one["generation"] == 1
    assert version_one["manifest"]["facts"][0]["evidence"][0]["sha256"] == "a" * 64

    conflict = evolution.propose(
        owner_id="alice", project_id="project-1", head="A", relation="capital", tail="C",
        confidence=0.95, evidence=evidence,
    )
    assert conflict["status"] == "conflict"
    with pytest.raises(evolution.EvolutionError) as unresolved:
        evolution.review_candidate(
            "alice", "project-1", conflict["id"], actor_id="alice", decision="approve",
        )
    assert unresolved.value.code == "conflict_resolution_required"

    second = evolution.propose(
        owner_id="alice", project_id="project-1", head="A", relation="population", tail="10",
        confidence=0.9, evidence=evidence,
    )
    evolution.review_candidate("alice", "project-1", second["id"], actor_id="alice", decision="approve")
    version_two = evolution.promote(
        "alice", "project-1", [second["id"]], actor_id="alice",
        evaluation={"passed": True}, expected_parent_version=version_one["id"],
    )
    assert version_two["generation"] == 2
    restored = evolution.rollback("alice", "project-1", version_one["id"], actor_id="alice")
    assert restored and restored["id"] == version_one["id"]
    assert evolution.active_version("bob", "project-1") is None


def test_lohosearch_requires_hash_manifest_and_never_promotes_smoke_to_leaderboard(tmp_path, monkeypatch):
    from hashmm.evaluation.benchmarks import loho_search

    data_path = tmp_path / "loho.json"
    cases = [{"id": index, "question": f"q{index}", "answer": f"a{index}",
              "domain": "test", "search_scope": "wide", "logic_complexity": "multi-hop"}
             for index in range(20)]
    data_path.write_text(json.dumps(cases), encoding="utf-8")
    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    manifest = {"dataset_id": "LoHoSearch", "case_count": 20, "sha256": digest,
                "snapshot_id": "test-snapshot", "source_url": "https://example.invalid/frozen"}
    data_path.with_suffix(".json.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("HASHMM_LOHOSEARCH_DATA", str(data_path))

    class Adapter:
        def run_task(self, query, _scorers, **_kwargs):
            return {"answer": "a" + query[1:], "status": "OK", "steps": 3,
                    "tools_used": ["search", "open", "search"]}

    result = loho_search.run(Adapter(), mode="smoke")
    assert result["score_pct"] == 100.0
    assert result["total"] == 20
    assert result["comparable"] is False
    assert result["kind"] == "frozen_external"
    assert result["metrics"]["mean_tool_calls"] == 3.0

    manifest["sha256"] = "0" * 64
    data_path.with_suffix(".json.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert loho_search.detect()["installed"] is False


def test_background_job_metadata_survives_restart_and_projects_interruption(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    from hashmm.api import jobs
    from hashmm.agent import work_runtime

    jobs._RECONCILED_PATHS.discard(str(Path(tmp_db)))
    job_id = jobs.create_job("index_rebuild", total=4, owner_id="alice")
    jobs.update_job(job_id, status="running", done=2, message="half")
    before = jobs.get_job(job_id, owner_id="alice")
    assert before and before["work_run_id"]
    assert work_runtime.get_run(before["work_run_id"], "bob") is None

    # A new process cannot reconstruct an arbitrary Python closure, so the
    # safe recovery state is explicit interruption, never silent replay.
    jobs._RECONCILED_PATHS.discard(str(Path(tmp_db)))
    recovered = jobs.get_job(job_id, owner_id="alice")
    assert recovered and recovered["status"] == "interrupted"
    run = work_runtime.get_run(recovered["work_run_id"], "alice")
    assert run and run["status"] == "interrupted"


def test_public_v1_writes_require_idempotency_and_background_response_is_replayable(tmp_db, monkeypatch):
    _isolated_db(tmp_db, monkeypatch)
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")
    monkeypatch.setenv("HASHMM_API_KEY", "api-key-a")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hashmm.api import job_queue, jobs
    from hashmm.api.routes import public_api

    app = FastAPI()
    app.include_router(public_api.router)
    job_queue.reset_job_queue()
    monkeypatch.setattr(public_api, "_execute_text",
                        lambda prompt, history, sources: f"completed: {prompt}")
    auth = {"Authorization": "Bearer api-key-a"}

    with TestClient(app) as client:
        missing = client.post("/v1/threads", headers=auth, json={"title": "x"})
        assert missing.status_code == 400
        headers = {**auth, "Idempotency-Key": "thread-create-1"}
        created = client.post("/v1/threads", headers=headers, json={"title": "x"})
        assert created.status_code == 201
        replayed = client.post("/v1/threads", headers=headers, json={"title": "x"})
        assert replayed.status_code == 201
        assert replayed.headers["Idempotent-Replayed"] == "true"
        assert replayed.json()["id"] == created.json()["id"]
        conflict = client.post("/v1/threads", headers=headers, json={"title": "different"})
        assert conflict.status_code == 409

        response_headers = {**auth, "Idempotency-Key": "response-create-1"}
        queued = client.post("/v1/responses", headers=response_headers,
                             json={"input": "long task", "background": True})
        assert queued.status_code == 202
        value = queued.json()
        assert value["status"] == "queued"
        assert value["run_id"].startswith("wr_")
        for _ in range(100):
            fetched = client.get(f"/v1/responses/{value['id']}", headers=auth)
            if fetched.json()["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "completed"
        events = client.get(f"/v1/responses/{value['id']}/events", headers=auth).json()["data"]
        event_names = [item["event"] for item in events]
        assert event_names[:3] == ["response.created", "run.status.changed",
                                   "response.background_queued"]
        assert "response.started" not in event_names  # WorkRun event, not response stream event.
        assert event_names[-1] == "response.completed"
        owner_id = "api_" + hashlib.sha256(b"api-key-a").hexdigest()[:24]
        for _ in range(100):
            owned_jobs = jobs.list_jobs(owner_id=owner_id, limit=5)
            if owned_jobs and owned_jobs[0]["status"] in {"done", "error"}:
                break
            time.sleep(0.01)
        assert owned_jobs[0]["status"] == "done"
        assert owned_jobs[0]["work_run_id"] == value["run_id"]
        replayed_response = client.post("/v1/responses", headers=response_headers,
                                        json={"input": "long task", "background": True})
        assert replayed_response.headers["Idempotent-Replayed"] == "true"


def test_python_client_sends_idempotency_key_for_agent_writes(monkeypatch):
    from hashmm.client import HashMMClient

    seen = {}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps({"id": "thread-one"}).encode("utf-8")

    def open_request(request, timeout):
        seen.update(method=request.get_method(), key=request.get_header("Idempotency-key"),
                    body=json.loads(request.data.decode("utf-8")), timeout=timeout)
        return Reply()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = HashMMClient("http://localhost:6006", api_key="key", timeout=3)
    result = client.create_thread(idempotency_key="thread-once", title="Agent work")
    assert result["id"] == "thread-one"
    assert seen == {"method": "POST", "key": "thread-once",
                    "body": {"title": "Agent work", "project_id": "", "metadata": {}},
                    "timeout": 3.0}
    with pytest.raises(ValueError):
        client.create_thread(idempotency_key="")
