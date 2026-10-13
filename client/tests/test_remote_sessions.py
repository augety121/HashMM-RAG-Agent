import time

from hashmm.api.remote_hub import Conn, SignalHub
from hashmm.api.remote_sessions import RemoteSessionRegistry, normalize_scopes


def test_scopes_are_allowlisted_and_owner_is_non_enumerable():
    registry = RemoteSessionRegistry(active_ttl=300)
    session = registry.request("owner-a", "host-1", "viewer-1", ["view", "control", "shell", "power"])
    assert session.requested_scopes == ("control", "power", "view")
    assert registry.get("owner-b", session.id) is None
    assert registry.approve("owner-a", session.id, "wrong-host", ["view"]) is None


def test_ticket_is_role_scope_generation_and_session_bound():
    registry = RemoteSessionRegistry(active_ttl=300)
    session = registry.request("owner-a", "host-1", "viewer-1", ["view", "control"])
    active = registry.approve("owner-a", session.id, "host-1", ["view", "control", "power"])
    assert active is not None
    assert active.granted_scopes == ("control", "view")  # cannot grant an unrequested scope

    viewer_ticket = registry.issue_ticket("owner-a", session.id, "viewer", "viewer-1")
    assert viewer_ticket
    assert registry.verify_ticket(viewer_ticket, role="viewer", session_id=session.id, required_scope="view")
    assert registry.verify_ticket(viewer_ticket, role="host") is None
    assert registry.verify_ticket(viewer_ticket, required_scope="power") is None
    assert registry.verify_ticket(viewer_ticket, session_id="another-session") is None

    registry.revoke("owner-a", session.id, "viewer-1")
    assert registry.verify_ticket(viewer_ticket, required_scope="view") is None


def test_control_envelope_rejects_replay_stale_and_future_messages():
    registry = RemoteSessionRegistry(active_ttl=300)
    session = registry.request("owner-a", "host-1", "viewer-1", ["view", "control"])
    registry.approve("owner-a", session.id, "host-1", ["view", "control"])
    now = int(time.time() * 1000)
    assert registry.validate_envelope("owner-a", session.id, "viewer", "viewer-1", 1, now)
    assert not registry.validate_envelope("owner-a", session.id, "viewer", "viewer-1", 1, now)
    assert not registry.validate_envelope("owner-a", session.id, "viewer", "viewer-1", 2, now - 31_000)
    assert not registry.validate_envelope("owner-a", session.id, "viewer", "viewer-1", 2, now + 11_000)
    assert registry.validate_envelope("owner-a", session.id, "viewer", "viewer-1", 2, now)


def test_v4_milestones_are_owner_device_scoped_and_secret_free():
    registry = RemoteSessionRegistry(active_ttl=300)
    session = registry.request("owner-a", "host-1", "viewer-1", ["view"])
    assert registry.record_milestone("owner-a", session.id, "viewer", "viewer-1",
                                     "connect_requested", {"stage": "admission"})
    assert not registry.record_milestone("owner-b", session.id, "viewer", "viewer-1",
                                         "connect_requested", {})
    registry.approve("owner-a", session.id, "host-1", ["view"])
    assert registry.record_milestone("owner-a", session.id, "viewer", "viewer-1",
                                     "terminal_error", {"errorCode": "RELAY_HTTP_404",
                                                        "token": "must-not-persist"})
    latest = registry.audit_snapshot("owner-a")[-1]
    assert latest["error_code"] == "RELAY_HTTP_404"
    assert "token" not in latest


def test_v4_pair_gets_time_budgets_but_legacy_pair_keeps_v3_policy():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-v4", protocol="hashmm.remote.v4")
    viewer = Conn("owner-a", "viewer", device_id="viewer-v4", protocol="hashmm.remote.v4")
    hub.add(host); hub.add(viewer)
    ready = hub.on_message(viewer, {"type": "connect", "target": host.id, "scopes": ["view"]})
    assert ready[1][1]["transportPolicy"] == "direct-turn-migrate-compat-preview"
    assert ready[1][1]["timeBudgets"]["firstFrameMs"] == 8000
    assert ready[1][1]["approvalMode"] == "same-account-auto"


def test_same_account_v4_auto_approves_without_desktop_prompt():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-v4", protocol="hashmm.remote.v4")
    viewer = Conn("owner-a", "viewer", device_id="viewer-v4", protocol="hashmm.remote.v4")
    hub.add(host); hub.add(viewer)
    actions = hub.on_message(viewer, {"type": "connect", "target": host.id, "scopes": ["view", "control"]})
    assert [message["type"] for _, message in actions] == ["viewerJoined", "ready"]
    assert actions[0][1]["approvalMode"] == "same-account-auto"
    assert viewer.peer == host.id


def test_hub_requires_host_decision_before_pairing_and_enforces_scopes():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", "Office PC", "win", "host-device")
    viewer = Conn("owner-a", "viewer", "Phone", "android", "viewer-device")
    hub.add(host)
    hub.add(viewer)

    actions = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "scopes": ["view", "control"], "workId": "work-1",
    })
    assert [message["type"] for _, message in actions] == ["permissionRequest", "permissionPending"]
    assert viewer.peer is None
    session_id = actions[0][1]["sessionId"]

    actions = hub.on_message(host, {
        "type": "permissionDecision", "sessionId": session_id,
        "decision": "approve", "scopes": ["view", "control"],
    })
    assert [message["type"] for _, message in actions] == ["viewerJoined", "ready"]
    assert viewer.peer == host.id
    assert actions[1][1]["ticket"]

    now = int(time.time() * 1000)
    forwarded = hub.on_message(viewer, {"type": "input", "seq": 1, "timestamp": now, "data": {"kind": "move"}})
    assert len(forwarded) == 1 and forwarded[0][0] is host
    replay = hub.on_message(viewer, {"type": "input", "seq": 1, "timestamp": now, "data": {"kind": "move"}})
    assert replay[0][1] == {"type": "controlRejected", "reason": "stale_or_replayed"}
    assert hub.on_message(viewer, {"type": "fileOffer", "name": "secret.txt"}) == []


def test_host_can_deny_without_pairing():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-device")
    viewer = Conn("owner-a", "viewer", device_id="viewer-device")
    hub.add(host)
    hub.add(viewer)
    request_actions = hub.on_message(viewer, {"type": "connect", "target": host.id, "scopes": ["view"]})
    sid = request_actions[0][1]["sessionId"]
    denied = hub.on_message(host, {"type": "permissionDecision", "sessionId": sid, "decision": "deny"})
    assert denied == [(viewer, {"type": "pairFail", "reason": "denied", "sessionId": sid})]
    assert viewer.peer is None
    assert registry.get("owner-a", sid).state == "denied"


def test_ready_fails_closed_when_either_media_ticket_cannot_be_issued(monkeypatch):
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-device", protocol="hashmm.remote.v4")
    viewer = Conn("owner-a", "viewer", device_id="viewer-device", protocol="hashmm.remote.v4")
    hub.add(host); hub.add(viewer)
    original = registry.issue_ticket

    def fail_viewer_ticket(uid, session_id, role, device_id, ttl=120):
        if role == "viewer":
            return None
        return original(uid, session_id, role, device_id, ttl)

    monkeypatch.setattr(registry, "issue_ticket", fail_viewer_ticket)
    actions = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "scopes": ["view"],
    })
    assert [message["type"] for _, message in actions] == ["permissionFailed", "pairFail"]
    assert actions[1][1]["reason"] == "ticket_issue_failed"
    assert viewer.peer is None
    session = registry.list_for_owner("owner-a")[0]
    assert session["state"] == "revoked"


def test_ready_contains_generation_bound_ticket_metadata():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-device", protocol="hashmm.remote.v4")
    viewer = Conn("owner-a", "viewer", device_id="viewer-device", protocol="hashmm.remote.v4")
    hub.add(host); hub.add(viewer)
    actions = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "scopes": ["view"],
    })
    ready = actions[1][1]
    sid = ready["sessionId"]
    assert ready["ticket"]
    assert ready["ticketGeneration"] == registry.get("owner-a", sid).generation
    assert ready["expiresAt"] > time.time()


def test_production_hub_resolves_owner_work_before_permission_prompt():
    registry = RemoteSessionRegistry(active_ttl=300)
    calls = []

    def binder(owner, requested, **kwargs):
        calls.append((owner, requested, kwargs))
        if requested == "wr-foreign":
            raise LookupError("work_run_not_found")
        return requested or "wr-created"

    hub = SignalHub(session_registry=registry, work_binder=binder)
    host = Conn("owner-a", "host", device_id="host-device")
    viewer = Conn("owner-a", "viewer", device_id="viewer-device")
    hub.add(host)
    hub.add(viewer)

    rejected = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "workRunId": "wr-foreign",
    })
    assert rejected == [(viewer, {"type": "pairFail", "reason": "work_run_not_found"})]
    assert registry.list_for_owner("owner-a") == []

    admitted = hub.on_message(viewer, {
        "type": "connect", "target": host.id, "conversationId": "conv-a",
    })
    prompt = admitted[0][1]
    assert prompt["type"] == "permissionRequest"
    assert prompt["workRunId"] == "wr-created"
    assert prompt["workId"] == "wr-created"
    assert calls[-1][2]["conversation_id"] == "conv-a"


def test_host_rejects_overlapping_pending_remote_sessions():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-device")
    viewer_a = Conn("owner-a", "viewer", device_id="viewer-a")
    viewer_b = Conn("owner-a", "viewer", device_id="viewer-b")
    for conn in (host, viewer_a, viewer_b):
        hub.add(conn)
    first = hub.on_message(viewer_a, {"type": "connect", "target": host.id, "scopes": ["view"]})
    assert first[0][1]["type"] == "permissionRequest"
    assert hub.on_message(viewer_b, {"type": "connect", "target": host.id, "scopes": ["view"]}) == [
        (viewer_b, {"type": "pairFail", "reason": "busy"})
    ]


def test_audio_control_requires_audio_scope_not_pointer_scope():
    registry = RemoteSessionRegistry(active_ttl=300)
    hub = SignalHub(session_registry=registry)
    host = Conn("owner-a", "host", device_id="host-device")
    viewer = Conn("owner-a", "viewer", device_id="viewer-device")
    hub.add(host); hub.add(viewer)
    pending = hub.on_message(viewer, {"type": "connect", "target": host.id, "scopes": ["view", "audio"]})
    sid = pending[0][1]["sessionId"]
    hub.on_message(host, {"type": "permissionDecision", "sessionId": sid, "decision": "approve", "scopes": ["view", "audio"]})
    now = int(time.time() * 1000)
    forwarded = hub.on_message(viewer, {"type": "control", "action": "audio", "on": True, "seq": 1, "timestamp": now})
    assert len(forwarded) == 1 and forwarded[0][0] is host
    assert hub.on_message(viewer, {"type": "control", "action": "quality", "seq": 2, "timestamp": now}) == []
