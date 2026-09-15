"""V422-V425 remote authorization, evidence, handoff and completion regressions."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3

import pytest

from hashmm.agent import work_runtime
from hashmm.api import remote_work
from hashmm.api.remote_hub import Conn, SignalHub
from hashmm.api.remote_persistence import RemotePersistence
from hashmm.api.remote_sessions import RemoteSession, RemoteSessionRegistry


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


def _active(registry: RemoteSessionRegistry, *, viewer: str = "viewer-a"):
    session = registry.request(
        "owner-a", "host-a", viewer,
        ["view", "control", "clipboard"], "wr-a",
    )
    assert registry.approve(
        "owner-a", session.id, "host-a", ["view", "control", "clipboard"]
    )
    return session


def test_action_inbox_contains_bounded_remote_authorization_context():
    inbox = work_runtime.action_inbox([{
        "id": "wr-a", "conversation_id": "conv-a", "kind": "remote",
        "status": "waiting_approval", "updated_at": 100.0,
        "title": "远程协作", "snapshot": {"remote_session": {
            "session_id": "rs-a", "state": "pending", "generation": 2,
            "scopes": ["view", "control"], "predecessor_session_id": "rs-old",
            "host_device_id": "must-not-leak", "ticket": "must-not-leak",
        }},
    }])
    assert inbox["schema"] == "hashmm.action-inbox.v1"
    assert inbox["high_priority_count"] == 1
    context = inbox["items"][0]["context"]
    assert context == {
        "kind": "remote_session", "session_id": "rs-a", "state": "pending",
        "generation": 2, "scopes": ["view", "control"],
        "predecessor_session_id": "rs-old",
    }
    assert "must-not-leak" not in str(inbox)


def test_transport_projection_is_bounded_and_time_bucketed(monkeypatch):
    admitted = []
    monkeypatch.setattr(remote_work.time, "time", lambda: 125.0)
    monkeypatch.setattr(
        remote_work.work_runtime, "get_run",
        lambda *_args, **_kwargs: {"id": "wr-a", "kind": "remote", "snapshot": {}},
    )
    monkeypatch.setattr(
        remote_work.work_runtime, "append_event_once",
        lambda run_id, **kwargs: admitted.append((run_id, kwargs)) or {"state": "applied"},
    )
    session = RemoteSession(
        id="rs-a", uid="owner-a", host_device_id="private-host",
        viewer_device_id="private-viewer", requested_scopes=("view",),
        granted_scopes=("view",), state="active", generation=1, work_run_id="wr-a",
    )
    remote_work.project_remote_transition(session, "transport_observed", {
        "role": "viewer", "candidate_type": "relay", "protocol": "udp",
        "rtt_ms": 42, "packet_loss_pct": 0.5, "bytes_sent": 99,
        "candidateAddress": "10.0.0.1", "token": "secret",
    })
    _, event = admitted[0]
    assert event["idempotency_key"] == "remote:rs-a:1:transport_observed:viewer:2"
    assert event["snapshot_updates"]["remote_transport"]["viewer"]["candidate_type"] == "relay"
    assert "bytes_sent" not in str(event)
    assert "10.0.0.1" not in str(event)
    assert "secret" not in str(event)


def test_handoff_rotates_generation_preserves_lineage_and_cannot_widen_scope():
    registry = RemoteSessionRegistry(active_ttl=300)
    session = _active(registry)
    old_ticket = registry.issue_ticket("owner-a", session.id, "viewer", "viewer-a")
    assert old_ticket

    with pytest.raises(ValueError, match="scope_escalation"):
        registry.handoff(
            "owner-a", session.id, "viewer-a", "viewer-b",
            ["view", "control", "power"],
        )
    assert registry.get("owner-a", session.id).state == "active"

    successor = registry.handoff(
        "owner-a", session.id, "viewer-a", "viewer-b", ["view", "control"],
    )
    assert successor is not None
    assert successor.state == "pending"
    assert successor.predecessor_session_id == session.id
    assert successor.work_run_id == "wr-a"
    assert successor.requested_scopes == ("control", "view")
    assert registry.get("owner-a", session.id).state == "superseded"
    assert registry.verify_ticket(old_ticket, required_scope="view") is None
    assert registry.handoff(
        "owner-a", session.id, "viewer-a", "viewer-b", ["view", "control"]
    ).id == successor.id
    assert registry.handoff(
        "owner-b", session.id, "viewer-a", "viewer-c", ["view"]
    ) is None


def test_completion_receipt_is_distinct_from_revocation_and_verifies_both_roles():
    projected = []
    registry = RemoteSessionRegistry(
        active_ttl=300,
        transition_sink=lambda session, event, detail: projected.append((event, detail)),
    )
    session = _active(registry)
    ticket = registry.issue_ticket("owner-a", session.id, "viewer", "viewer-a")
    assert ticket
    assert registry.record_transport(
        "owner-a", session.id, "host", "host-a",
        {"candidateType": "relay", "protocol": "udp", "rttMs": 12},
    )
    assert registry.record_transport(
        "owner-a", session.id, "viewer", "viewer-a",
        {"candidateType": "relay", "protocol": "udp", "rttMs": 14},
    )
    assert registry.complete("owner-a", session.id, "stranger") is None
    completed = registry.complete("owner-a", session.id, "viewer-a", "user_finished")
    assert completed is not None and completed.state == "completed"
    assert completed.reason == "user_finished"
    assert registry.verify_ticket(ticket, required_scope="view") is None
    event, detail = projected[-1]
    assert event == "completed"
    assert detail["verification"] == "verified"
    assert detail["transport_roles"] == ["host", "viewer"]
    assert detail["transport_samples"] == 2
    assert registry.revoke("owner-a", session.id, "viewer-a") is None


def test_hub_normal_complete_notifies_both_peers_and_unpairs():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-a")
    viewer = Conn("owner-a", "viewer", device_id="viewer-a")
    hub.add(host); hub.add(viewer)
    pending = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "scopes": ["view", "control"],
    })
    sid = pending[0][1]["sessionId"]
    hub.on_message(host, {
        "type": "permissionDecision", "sessionId": sid,
        "decision": "approve", "scopes": ["view", "control"],
    })
    actions = hub.on_message(viewer, {
        "type": "complete", "sessionId": sid, "summary": "finished",
    })
    assert len(actions) == 2
    assert {target for target, _ in actions} == {host, viewer}
    assert all(message["type"] == "remoteCompleted" for _, message in actions)
    assert registry.get("owner-a", sid).state == "completed"
    assert viewer.peer is None and viewer.id not in host.viewers
    assert viewer.id not in host.session_ids


def test_persistence_round_trips_handoff_lineage(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "audit-secret-" + "x" * 40)
    store = RemotePersistence(_factory(tmp_path / "remote-v425.sqlite"))
    registry = RemoteSessionRegistry(store=store, active_ttl=300)
    session = _active(registry)
    successor = registry.handoff(
        "owner-a", session.id, "host-a", "viewer-b", ["view"],
    )
    assert successor is not None
    with sqlite3.connect(tmp_path / "remote-v425.sqlite") as conn:
        row = conn.execute(
            "SELECT predecessor_session_id,work_run_id,state FROM remote_sessions WHERE id=?",
            (successor.id,),
        ).fetchone()
    assert row == (session.id, "wr-a", "pending")


def test_persistent_completion_keeps_roles_structured_and_drops_unknown_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_SECRET", "audit-secret-" + "z" * 40)
    registry = RemoteSessionRegistry(
        store=RemotePersistence(_factory(tmp_path / "remote-complete.sqlite")),
        active_ttl=300,
    )
    session = _active(registry)
    registry.record_transport(
        "owner-a", session.id, "host", "host-a",
        {"candidateType": "relay", "protocol": "udp"},
    )
    registry.record_transport(
        "owner-a", session.id, "viewer", "viewer-a",
        {"candidateType": "relay", "protocol": "udp"},
    )
    assert registry.complete("owner-a", session.id, "host-a")
    completed = registry.audit_for_session("owner-a", session.id)[-1]
    assert completed["event"] == "completed"
    assert completed["detail"]["transport_roles"] == ["host", "viewer"]
    assert set(completed["detail"]) <= {
        "reason", "transport_roles", "transport_samples", "verification",
    }
