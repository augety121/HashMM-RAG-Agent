from contextlib import contextmanager
import json
import sqlite3

from hashmm.api.remote_persistence import RemotePersistence
from hashmm.api.remote_sessions import RemoteSessionRegistry


def _factory(path):
    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return connect


def test_persistent_session_restarts_fail_closed_and_audit_chain_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "audit-secret-" + "x" * 40)
    monkeypatch.setenv("HASHMM_JWT_SECRET", "jwt-secret-" + "y" * 40)
    db_path = tmp_path / "remote.sqlite"
    first = RemoteSessionRegistry(store=RemotePersistence(_factory(db_path)), active_ttl=300)
    session = first.request("owner-a", "host-a", "viewer-a", ["view", "control"])
    assert first.approve("owner-a", session.id, "host-a", ["view", "control"])
    ticket = first.issue_ticket("owner-a", session.id, "viewer", "viewer-a")
    assert ticket and first.verify_ticket(ticket, required_scope="view")

    second = RemoteSessionRegistry(store=RemotePersistence(_factory(db_path)), active_ttl=300)
    second.initialize()
    restored = second.get("owner-a", session.id)
    assert restored is not None
    assert restored.state == "interrupted"
    assert restored.reason == "server_restarted"
    assert second.verify_ticket(ticket, required_scope="view") is None
    events = second.audit_for_session("owner-a", session.id)
    assert [event["event"] for event in events] == ["requested", "approved", "ticket_issued", "interrupted"]
    assert all(event["integrity"] is True for event in events)
    assert second.audit_for_session("owner-b", session.id) == []


def test_audit_chain_detects_row_modification(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "audit-secret-" + "z" * 40)
    db_path = tmp_path / "remote.sqlite"
    registry = RemoteSessionRegistry(store=RemotePersistence(_factory(db_path)))
    session = registry.request("owner-a", "host-a", "viewer-a", ["view"])
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT event_id FROM remote_audit_events ORDER BY at LIMIT 1").fetchone()
        conn.execute("UPDATE remote_audit_events SET detail_json=? WHERE event_id=?",
                     (json.dumps({"reason": "tampered"}), row[0]))
    events = registry.audit_for_session("owner-a", session.id)
    assert events[0]["integrity"] is False


def test_acceptance_receipts_are_owner_bound_and_bounded(tmp_path):
    store = RemotePersistence(_factory(tmp_path / "remote.sqlite"))
    store.initialize()
    run_id = store.record_acceptance("owner-a", "soak", "passed", 100, 86500, 1,
                                     {"samples": 1440, "availability_pct": 99.9, "secret": "must-drop"})
    latest = store.latest_acceptance("owner-a", "soak")
    assert latest and latest["run_id"] == run_id
    assert latest["status"] == "incomplete"
    assert latest["evidence"]["samples"] == 1440
    assert "secret" not in latest["evidence"]
    assert store.latest_acceptance("owner-b", "soak") is None


def test_acceptance_pass_requires_current_full_criteria(tmp_path):
    store = RemotePersistence(_factory(tmp_path / "remote.sqlite"))
    store.initialize()
    criteria = {
        "duration_reached": True, "sample_density_reached": True,
        "availability_reached": True, "remote_relay_coverage_reached": True,
        "failure_streak_bounded": True,
    }
    store.record_acceptance("owner-a", "soak", "passed", 100, 86_500, 1, {
        "schema": "hashmm.remote.soak-acceptance.v1", "criteria": criteria,
        "samples": 1440, "availability_pct": 99.9, "relay_coverage_pct": 99.9,
    })
    latest = store.latest_acceptance("owner-a", "soak")
    assert latest and latest["status"] == "passed"


def test_legacy_remote_work_backfill_requires_same_owner(tmp_path):
    db_path = tmp_path / "remote-work.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE work_runs (id TEXT PRIMARY KEY, user_id TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO work_runs (id,user_id) VALUES (?,?)", ("wr-a", "owner-a"))
        conn.execute("INSERT INTO work_runs (id,user_id) VALUES (?,?)", ("wr-b", "owner-b"))
        conn.execute("""CREATE TABLE remote_sessions (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, host_device_id TEXT NOT NULL,
            viewer_device_id TEXT NOT NULL, requested_scopes_json TEXT NOT NULL DEFAULT '[]',
            granted_scopes_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'interrupted',
            generation INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            expires_at REAL NOT NULL DEFAULT 0, work_id TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)""")
        def legacy_row(row_id, work_id):
            return (
                row_id, "owner-a", "host", "viewer", "[]", "[]",
                "interrupted", 1, 1.0, 1.0, work_id, "", 1.0,
            )
        conn.execute(
            "INSERT INTO remote_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            legacy_row("same-owner", "wr-a"),
        )
        conn.execute(
            "INSERT INTO remote_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            legacy_row("foreign-owner", "wr-b"),
        )
        conn.execute(
            "INSERT INTO remote_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            legacy_row("unknown", "not-a-work"),
        )

    store = RemotePersistence(_factory(db_path))
    store.initialize()
    with sqlite3.connect(db_path) as conn:
        rows = dict(conn.execute(
            "SELECT id,work_run_id FROM remote_sessions ORDER BY id"
        ).fetchall())
    assert rows["same-owner"] == "wr-a"
    assert rows["foreign-owner"] == ""
    assert rows["unknown"] == ""
