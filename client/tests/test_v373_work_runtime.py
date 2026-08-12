"""V373 unified work runtime, incremental cursors and legacy stream fixes."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


@pytest.mark.parametrize("legacy_version", [23, 24])
def test_v410_repairs_legacy_work_tables_before_creating_v24_index(
    tmp_db, legacy_version
):
    """Cover both a V23 database and an interrupted V24 version update."""
    legacy = sqlite3.connect(tmp_db)
    legacy.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (23);
        CREATE TABLE work_runtime_meta (
            id TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE work_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            conv_id TEXT DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'chat',
            source_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            revision INTEGER NOT NULL DEFAULT 1,
            event_seq INTEGER NOT NULL DEFAULT 0,
            change_seq INTEGER NOT NULL DEFAULT 0,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(user_id, kind, source_id)
        );
        CREATE TABLE work_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            UNIQUE(run_id, seq),
            FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
        );
        INSERT INTO work_runs(
            id,user_id,conv_id,kind,source_id,title,status,revision,event_seq,
            change_seq,snapshot_json,created_at,updated_at
        ) VALUES (
            'legacy-run','legacy-owner','legacy-conv','chat','legacy-source',
            'legacy work','running',3,1,7,'{"kept":true}',100,101
        );
        INSERT INTO work_events(
            id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at
        ) VALUES (
            'legacy-event','legacy-run','legacy-owner',1,'progress','running',
            'legacy evidence','{"kept":true}',101
        );
        """
    )
    legacy.execute("UPDATE schema_version SET version=?", (legacy_version,))
    legacy.commit()
    legacy.close()

    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    # Startup must also be safe after an interrupted/repeated deployment.
    db.init_db()

    with db._conn() as conn:
        run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(work_runs)").fetchall()
        }
        event_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(work_events)").fetchall()
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(work_events)").fetchall()
        }
        sync_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(work_sync_changes)").fetchall()
        }
        sync_indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(work_sync_changes)").fetchall()
        }
        run = conn.execute(
            "SELECT title,revision,snapshot_json,active_generation_id "
            "FROM work_runs WHERE id='legacy-run'"
        ).fetchone()
        event = conn.execute(
            "SELECT summary,payload_json,idempotency_key,expected_revision,generation_id "
            "FROM work_events WHERE id='legacy-event'"
        ).fetchone()
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]

    assert {
        "active_generation_id", "project_id", "execution_target_json",
        "autonomy_level",
    } <= run_columns
    assert {"idempotency_key", "expected_revision", "generation_id"} <= event_columns
    assert "idx_work_events_owner_idempotency" in indexes
    assert {
        "id", "user_id", "change_seq", "entity_type", "entity_id",
        "operation", "revision", "payload_json", "created_at",
    } <= sync_columns
    assert "idx_work_sync_owner_cursor" in sync_indexes
    assert tuple(run) == ("legacy work", 3, '{"kept":true}', "")
    assert tuple(event) == ("legacy evidence", '{"kept":true}', "", 0, "")
    assert version == 32


def test_work_feed_has_monotonic_cursors_owner_isolation_and_redaction(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("work-alice", "pw12345678")
    bob = db.create_user("work-bob", "pw12345678")
    run = runtime.create_run(
        user_id=alice["id"], kind="chat", source_id="turn-1", conv_id="conv-1",
        title="整理一份报告", status="queued",
    )
    same = runtime.create_run(
        user_id=alice["id"], kind="chat", source_id="turn-1", conv_id="conv-1",
        title="不能重复创建", status="running",
    )
    assert same["id"] == run["id"]

    updated = runtime.append_event(
        run["id"], user_id=alice["id"], event_type="tool",
        status="running", summary="读取工作区",
        payload={
            "tool": "read_file", "arguments": {"path": "private.txt"},
            "api_key": "sk-never-store", "nested": {"password": "secret", "ok": 1},
        },
    )
    assert updated and updated["change_cursor"] > run["change_cursor"]
    feed = runtime.list_runs(alice["id"], after_cursor=run["change_cursor"])
    assert [item["id"] for item in feed["items"]] == [run["id"]]
    assert feed["next_cursor"] == updated["change_cursor"]
    bob_feed = runtime.list_runs(bob["id"], after_cursor=0)
    assert bob_feed["items"] == []
    assert bob_feed["high_water_cursor"] == 0
    assert runtime.get_run(run["id"], bob["id"]) is None

    detail = runtime.get_run(run["id"], alice["id"], after_seq=1)
    assert detail and len(detail["events"]) == 1
    serialized = json.dumps(detail["events"], ensure_ascii=False)
    assert "sk-never-store" not in serialized
    assert "private.txt" not in serialized
    assert "secret" not in serialized
    assert serialized.count("[已脱敏]") >= 3
    event = detail["events"][0]
    assert event["envelope"]["schema"] == "hashmm.event.v1"
    assert event["envelope"]["stream_id"] == run["id"]
    assert event["envelope"]["event_seq"] == event["seq"]
    changes = runtime.list_sync_changes(alice["id"], after_cursor=0)
    assert changes["changes"]
    assert all(item["envelope"]["schema"] == "hashmm.event.v1" for item in changes["changes"])


def test_work_event_replay_and_atomic_generation_converge_across_devices(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    user = db.create_user("generation-owner", "pw12345678")
    run = runtime.create_run(
        user_id=user["id"], kind="workflow", source_id="generation-task",
        title="Build a verified report",
    )
    first = runtime.append_event_once(
        run["id"],
        user_id=user["id"],
        event_type="delivered",
        status="delivered",
        summary="Result is ready for review",
        idempotency_key="app-outbox-event-0001",
        expected_revision=run["revision"],
        snapshot_updates={
            "context_capsule": {
                "schema": "hashmm.context-capsule.v1",
                "generation": 3,
                "fingerprint": "a" * 64,
                "rendered_hash": "b" * 64,
                "sections": [
                    {"key": "knowledge", "content_hash": "c" * 64, "trust": "trusted"},
                ],
            },
            "run_manifest": {
                "task_contract": {"goal": "verified report"},
                "verification": {"status": "verified"},
                "artifacts": [{
                    "id": "artifact-report",
                    "filename": "report.pdf",
                    "sha256": "d" * 64,
                    "version": 1,
                }],
            },
        },
    )
    assert first["state"] == "applied"
    assert first["run"]["revision"] == run["revision"] + 1
    assert first["generation"]["status"] == "active"
    assert first["event"]["generation_id"] == first["generation"]["id"]

    replay = runtime.append_event_once(
        run["id"],
        user_id=user["id"],
        event_type="delivered",
        status="delivered",
        idempotency_key="app-outbox-event-0001",
        expected_revision=run["revision"],
    )
    assert replay["state"] == "duplicate"
    assert replay["event"]["id"] == first["event"]["id"]
    assert replay["run"]["revision"] == first["run"]["revision"]

    stale = runtime.append_event_once(
        run["id"],
        user_id=user["id"],
        event_type="progress",
        idempotency_key="desktop-event-0002",
        expected_revision=run["revision"],
    )
    assert stale["state"] == "revision_conflict"
    assert stale["current_revision"] == first["run"]["revision"]

    detail = runtime.get_run(run["id"], user["id"])
    assert detail
    assert detail["active_generation"]["id"] == first["generation"]["id"]
    assert detail["workspace"]["sync"]["active_generation_id"] == first["generation"]["id"]
    assert detail["workspace"]["sync"]["generation_hash"] == first["generation"]["manifest_hash"]
    assert detail["active_generation"]["manifest"]["source_bodies_included"] is False
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM work_generations WHERE run_id=?", (run["id"],),
        ).fetchone()[0] == 1

    before = detail["revision"]
    original_next = runtime._next_change_seq
    monkeypatch.setattr(
        runtime, "_next_change_seq",
        lambda _conn: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        runtime.append_event_once(
            run["id"],
            user_id=user["id"],
            event_type="completed",
            status="completed",
            idempotency_key="desktop-event-crash",
            expected_revision=before,
            snapshot_updates={"run_manifest": {"verification": {"status": "verified"}}},
        )
    monkeypatch.setattr(runtime, "_next_change_seq", original_next)
    after = runtime.get_run(run["id"], user["id"])
    assert after and after["revision"] == before
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM work_generations WHERE run_id=?", (run["id"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM work_events "
            "WHERE run_id=? AND idempotency_key='desktop-event-crash'",
            (run["id"],),
        ).fetchone()[0] == 0


def test_runtime_projection_treats_malformed_numeric_metadata_as_untrusted():
    from hashmm.agent import work_runtime as runtime

    manifest = runtime._generation_manifest(
        run_id="run-untrusted-metadata",
        revision=2,
        snapshot={
            "context_capsule": {"generation": "not-a-number"},
            "run_manifest": {
                "completion_gate": {
                    "summary": {"total": "invalid", "passed": "invalid"},
                },
                "evidence_graph": {"summary": {"evidence": "invalid"}},
            },
        },
    )
    assert manifest["planes"]["knowledge"]["context_generation"] == 0
    presentation = runtime.present_run(
        {
            "status": "running",
            "kind": "workflow",
            "snapshot": {
                "run_manifest": {
                    "completion_gate": {
                        "summary": {"total": "invalid", "passed": "invalid"},
                    },
                    "evidence_graph": {"summary": {"evidence": "invalid"}},
                },
            },
        },
    )
    assert presentation["progress"]["mode"] == "phase"
    assert presentation["progress"]["completed"] == 1
    assert presentation["evidence"]["count"] == 0


def test_artifact_revisions_are_content_addressed_owner_bound_and_atomic(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    owner = db.create_user("artifact-owner", "pw12345678")
    other = db.create_user("artifact-other", "pw12345678")
    run = runtime.create_run(
        user_id=owner["id"], kind="artifact", source_id="artifact-task-1",
        conv_id="conv-artifact", title="Prepare report",
    )
    first = runtime.register_artifact_revision(
        run["id"],
        user_id=owner["id"],
        artifact_id="report-main",
        content_hash="a" * 64,
        media_type="application/pdf",
        size_bytes=1024,
        locator={
            "filename": "report.pdf",
            "download_url": "/api/conversations/conv-artifact/download/report.pdf",
        },
        verification="ready",
        expected_run_revision=run["revision"],
    )
    assert first["state"] == "applied"
    assert first["artifact"]["revision"] == 1
    assert first["event"]["generation_id"] == first["generation"]["id"]

    replay = runtime.register_artifact_revision(
        run["id"],
        user_id=owner["id"],
        artifact_id="report-main",
        content_hash="a" * 64,
        expected_run_revision=run["revision"],
    )
    assert replay["state"] == "duplicate"
    assert replay["artifact"]["id"] == first["artifact"]["id"]
    assert replay["run"]["revision"] == first["run"]["revision"]

    assert runtime.register_artifact_revision(
        run["id"],
        user_id=other["id"],
        artifact_id="report-main",
        content_hash="b" * 64,
        expected_run_revision=first["run"]["revision"],
    )["state"] == "not_found"
    stale = runtime.register_artifact_revision(
        run["id"],
        user_id=owner["id"],
        artifact_id="report-main",
        content_hash="b" * 64,
        expected_run_revision=run["revision"],
    )
    assert stale["state"] == "revision_conflict"

    second = runtime.register_artifact_revision(
        run["id"],
        user_id=owner["id"],
        artifact_id="report-main",
        content_hash="b" * 64,
        media_type="application/pdf",
        size_bytes=2048,
        locator={"filename": "report.pdf"},
        verification="verified",
        expected_run_revision=first["run"]["revision"],
    )
    assert second["state"] == "applied"
    assert second["artifact"]["revision"] == 2
    detail = runtime.get_run(run["id"], owner["id"])
    assert detail
    assert [item["revision"] for item in detail["artifact_revisions"]] == [2, 1]
    assert detail["snapshot"]["latest_artifact"]["content_hash"] == "b" * 64
    assert detail["active_generation"]["id"] == second["generation"]["id"]
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM artifact_revisions WHERE run_id=?",
            (run["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM work_events "
            "WHERE run_id=? AND event_type='artifact_revision'",
            (run["id"],),
        ).fetchone()[0] == 2


def test_v375_projects_existing_chat_without_claiming_historical_success(tmp_db):
    db = _fresh_db(tmp_db)
    user = db.create_user("history-owner", "pw12345678")
    db.create_conversation("conv-history", user["id"], "既有研究对话")
    db.create_message("conv-history", "user", "分析这份资料")
    with db._conn() as conn:
        conn.execute("DELETE FROM work_runs WHERE conv_id=?", ("conv-history",))
        conn.execute("UPDATE schema_version SET version=17")

    db.init_db()
    from hashmm.agent import work_runtime as runtime

    feed = runtime.list_runs(user["id"])
    imported = next(item for item in feed["items"] if item["conversation_id"] == "conv-history")
    assert imported["status"] == "observed"
    assert imported["snapshot"]["historical_projection"] is True
    assert imported["snapshot"]["verification"] == "not_reconstructed"
    assert imported["control"]["available_actions"] == []


def test_sse_projector_is_bounded_and_records_delivery(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    user = db.create_user("work-projector", "pw12345678")
    run = runtime.create_run(user_id=user["id"], kind="chat", source_id="turn-2")
    projector = runtime.SSEProjector(run["id"], user["id"])
    token = 'event: token\ndata: {"content":"模型正文不进账本"}\n\n'
    assert projector.interested(token) is False
    projector.observe('event: trace\ndata: {"steps":[{"node":"retrieve","detail":"检索知识库"}]}\n\n')
    projector.observe('event: trace\ndata: {"steps":[{"node":"retrieve","detail":"重复节点"}]}\n\n')
    projector.observe('event: file\ndata: {"filename":"report.pdf","type":"pdf","content":"secret body"}\n\n')
    projector.observe('event: done\ndata: {"status":"complete","elapsed_ms":42,"run_manifest":{"schema":"hashmm.run-manifest.v2","user_goal":"full prompt","verification":{"status":"verified"}}}\n\n')
    projector.close_if_open()

    detail = runtime.get_run(run["id"], user["id"])
    assert detail and detail["status"] == "delivered"
    types = [event["type"] for event in detail["events"]]
    assert types.count("progress") == 1
    assert types[-1] == "delivered"
    serialized = json.dumps(detail, ensure_ascii=False)
    assert "模型正文不进账本" not in serialized
    assert "secret body" not in serialized
    assert "full prompt" not in serialized


def test_long_loop_and_team_share_the_owner_work_feed(tmp_db, tmp_path, monkeypatch):
    db = _fresh_db(tmp_db)
    from hashmm.agent import loop_engine, team, work_runtime

    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "runtime-state"))
    loop_engine._reset_state_for_tests()
    team._reset_team_store_for_tests()
    monkeypatch.setattr(loop_engine, "_spawn", lambda _loop: True)

    started = loop_engine.start_goal_loop(
        "整理本周资料并生成可核验清单",
        user="owner-1",
        conv_id="conv-shared",
        max_rounds=2,
    )
    assert started["ok"] is True
    loop_run = work_runtime.list_runs("owner-1", conv_id="conv-shared")["items"][0]
    assert loop_run["kind"] == "loop"
    assert loop_run["source_id"] == started["id"]

    team._team_new(
        "tm-shared", {"uid": "owner-1", "sub": "alice"},
        "并行核对同一批资料", "conv-shared", "team.html",
        [{"role": "研究员", "task": "核对来源"}, {"role": "审校员", "task": "检查结论"}],
    )
    team._team_done("tm-shared", "已交付待验收", 2, 2)
    feed = work_runtime.list_runs("owner-1", conv_id="conv-shared")
    kinds = {item["kind"] for item in feed["items"]}
    assert kinds == {"loop", "team"}
    team_run = next(item for item in feed["items"] if item["kind"] == "team")
    assert team_run["status"] in {"delivered", "completed"}
    assert work_runtime.list_runs("owner-2", conv_id="conv-shared")["items"] == []


def test_work_runtime_route_hides_other_owner_and_supports_etag(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime
    from hashmm.api.routes import work_runtime as route

    alice = db.create_user("route-alice", "pw12345678")
    bob = db.create_user("route-bob", "pw12345678")
    run = runtime.create_run(user_id=alice["id"], kind="loop", source_id="loop-1")

    class Request:
        headers: dict[str, str] = {}
        query_params: dict[str, str] = {}

    monkeypatch.setattr(route, "require_auth", lambda _request: {"uid": alice["id"]})
    first = asyncio.run(route.work_runs_feed(Request()))
    body = json.loads(first.body)
    assert body["items"][0]["id"] == run["id"]

    class Cached(Request):
        headers = {"If-None-Match": first.headers["etag"]}

    assert asyncio.run(route.work_runs_feed(Cached())).status_code == 304
    monkeypatch.setattr(route, "require_auth", lambda _request: {"uid": bob["id"]})
    with pytest.raises(Exception) as exc:
        asyncio.run(route.work_run_detail(run["id"], Request()))
    assert getattr(exc.value, "status_code", None) == 404


def test_legacy_stream_executes_on_healthy_db_and_writes_user_once(monkeypatch):
    pytest.importorskip("fastapi")
    from hashmm.api import server
    from hashmm.agent import work_runtime as runtime

    calls = {"agent": 0, "user_rows": 0}

    class Memory:
        def get_or_create_session(self, *args):
            return None

        def add_message(self, *args):
            return None

    monkeypatch.setattr(server, "memory", Memory())
    monkeypatch.setattr(server, "get_current_user", lambda _request: {"uid": "u1", "sub": "alice"})
    monkeypatch.setattr(server, "require_conv_access_or_create", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_is_phone_photo_request", lambda _query: False)
    monkeypatch.setattr(server, "_is_desktop_file_request", lambda _query: False)
    monkeypatch.setattr(server.db, "create_message", lambda _sid, role, *_a, **_k: calls.__setitem__("user_rows", calls["user_rows"] + (1 if role == "user" else 0)))
    monkeypatch.setattr(server.db, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "create_run", lambda **kwargs: {"id": "wr-legacy"})
    monkeypatch.setattr(runtime, "append_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "project_run_manifest", lambda manifest: manifest)

    def agent(*_args):
        calls["agent"] += 1
        return {"answer": "ok", "sources": [], "trace": [], "elapsed_ms": 1}

    monkeypatch.setattr(server, "agent_run", agent)
    req = server.ChatRequest(message="hello", session_id="conv-legacy")
    response = asyncio.run(server.chat_stream(req, object()))

    async def consume():
        chunks = [chunk async for chunk in response.body_iterator]
        return "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)

    body = asyncio.run(consume())
    assert "event: done" in body
    assert calls == {"agent": 1, "user_rows": 1}


def test_cross_device_request_advances_the_same_owned_work_run(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime
    from hashmm.api.routes import conversations

    user = db.create_user("device-owner", "pw12345678")
    owner = user["id"]
    conv_id = "conv-cross-device"
    db.create_conversation(conv_id, owner, "跨设备任务")
    monkeypatch.setattr(conversations, "get_current_user", lambda _request: {"uid": owner})

    dispatched = conversations._dispatch_or_warn(
        conv_id, owner, "[[AGENT]] 调研官方文档并保留来源",
    )
    assert dispatched["ok"] is True
    assert dispatched["work_run_id"]
    admitted = work_runtime.get_run(dispatched["work_run_id"], owner)
    assert admitted and admitted["kind"] == "browser"
    assert admitted["snapshot"]["capability"] == "browser_use"

    class StatusRequest:
        def __init__(self, value: str):
            self.value = value

        async def json(self):
            return {"status": self.value, "result": "这一字段绝不能进入账本"}

    started = asyncio.run(conversations.patch_file_request(
        dispatched["request_id"], StatusRequest("processing"),
    ))
    assert started["work_run_id"] == dispatched["work_run_id"]
    running = work_runtime.get_run(dispatched["work_run_id"], owner)
    assert running and running["status"] == "running"

    asyncio.run(conversations.patch_file_request(
        dispatched["request_id"], StatusRequest("done"),
    ))
    delivered = work_runtime.get_run(dispatched["work_run_id"], owner)
    assert delivered and delivered["status"] == "delivered"
    assert [item["type"] for item in delivered["events"]][-2:] == ["started", "delivered"]
    serialized = json.dumps(delivered, ensure_ascii=False)
    assert "这一字段绝不能进入账本" not in serialized

    other = db.create_user("device-other", "pw12345678")
    monkeypatch.setattr(conversations, "get_current_user", lambda _request: {"uid": other["id"]})
    with pytest.raises(Exception) as exc:
        asyncio.run(conversations.patch_file_request(
            dispatched["request_id"], StatusRequest("done"),
        ))
    assert getattr(exc.value, "status_code", None) == 404
    assert work_runtime.get_run_by_source(dispatched["request_id"], other["id"]) is None
