from __future__ import annotations

import sqlite3

from hashmm.api.remote_devices import RemoteDeviceRegistry
from hashmm.api.remote_hub import Conn, SignalHub


def _registry(tmp_path, now=None):
    db = tmp_path / "remote-fabric.sqlite3"
    connection = sqlite3.connect(db, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    clock = now or [1000.0]
    registry = RemoteDeviceRegistry(lambda: connection, clock=lambda: clock[0], lease_ttl=35)
    registry.initialize()
    return registry, connection, clock


def test_device_registry_uses_stable_owner_scoped_leases(tmp_path):
    registry, connection, now = _registry(tmp_path)
    first = registry.register(
        "sb_owner-a", "desktop-stable-001", connection_id="conn-old",
        role="host", name="Office PC", platform="win32", routable=True,
    )
    assert first["generation"] == 1
    devices = registry.list_devices("sb_owner-a")
    assert [item["id"] for item in devices] == ["desktop-stable-001"]
    assert devices[0]["online"] is True
    assert devices[0]["remote_ready"] is True
    assert registry.list_devices("sb_owner-b") == []

    second = registry.register(
        "sb_owner-a", "desktop-stable-001", connection_id="conn-new",
        role="host", name="Office PC", platform="win32", routable=True,
    )
    assert second["generation"] == 2
    assert registry.get_connection_id("sb_owner-a", "desktop-stable-001") == "conn-new"
    # A stale socket cannot clear or refresh the newer generation.
    assert registry.disconnect(
        "sb_owner-a", "desktop-stable-001", first["lease_id"], first["generation"]
    ) is False
    assert registry.heartbeat(
        "sb_owner-a", "desktop-stable-001", first["lease_id"], first["generation"]
    ) is False
    assert registry.heartbeat(
        "sb_owner-a", "desktop-stable-001", second["lease_id"], second["generation"],
        remote_ready=True,
    ) is True

    now[0] += 36
    assert registry.list_devices("sb_owner-a")[0]["online"] is False
    assert registry.get_connection_id("sb_owner-a", "desktop-stable-001") == ""
    connection.close()


def test_http_registration_cannot_advertise_an_unroutable_host(tmp_path):
    registry, connection, _ = _registry(tmp_path)
    registry.register(
        "sb_owner", "desktop-diagnostic", connection_id="http-diagnostic",
        role="host", name="Diagnostic", platform="win32",
    )
    item = registry.list_devices("sb_owner")[0]
    assert item["online"] is True
    assert item["remote_ready"] is False
    assert registry.list_devices("sb_owner", remote_only=True) == []
    connection.close()


def test_socket_ticket_is_owner_bound_short_lived_and_single_use(tmp_path):
    registry, connection, now = _registry(tmp_path)
    issued = registry.issue_socket_ticket("sb_owner", "android-install-001", "viewer")
    assert issued["ticket"].startswith("rst_")
    assert registry.consume_socket_ticket(issued["ticket"]) == {
        "uid": "sb_owner", "device_id": "android-install-001", "role": "viewer",
    }
    assert registry.consume_socket_ticket(issued["ticket"]) is None

    expired = registry.issue_socket_ticket("sb_owner", "android-install-001", "viewer", ttl=10)
    now[0] += 11
    assert registry.consume_socket_ticket(expired["ticket"]) is None
    connection.close()


def test_diagnostics_keep_consumed_viewer_attempt_and_use_live_viewer_lease(tmp_path):
    registry, connection, _ = _registry(tmp_path)
    viewer_ticket = registry.issue_socket_ticket("sb_owner", "android-install-001", "viewer")
    consumed = registry.consume_socket_ticket_detailed(viewer_ticket["ticket"])
    assert consumed and consumed["attempt_id"] == viewer_ticket["attempt_id"]
    registry.register(
        "sb_owner", "android-install-001", connection_id="viewer-live",
        role="viewer", name="Android", platform="android", routable=True,
    )

    # A later host ticket must not erase the evidence for the already
    # registered viewer, which previously produced a contradictory UI.
    registry.issue_socket_ticket("sb_owner", "desktop-stable-001", "host")
    diagnostics = registry.diagnostics("sb_owner", "desktop-stable-001")
    assert diagnostics["viewer_registered"] is True
    viewer_step = next(step for step in diagnostics["steps"] if step["id"] == "viewer_socket")
    assert viewer_step["state"] == "ok"
    assert "已完成 WSS 注册" in viewer_step["detail"]
    assert any(
        item["role"] == "viewer" and item["consumed"]
        for item in diagnostics["last_attempts"]
    )
    connection.close()


def test_signal_hub_lists_and_connects_by_stable_device_id(tmp_path):
    registry, connection, _ = _registry(tmp_path)
    hub = SignalHub(device_registry=registry)
    host = Conn("sb_owner", "host", "Desktop", "win32", "desktop-stable-001")
    viewer = Conn("sb_owner", "viewer", "Phone", "android", "android-install-001")
    host_actions = hub.add(host)
    hub.add(viewer)
    auth = next(message for target, message in host_actions if target is host and message["type"] == "authOk")
    assert auth["stableDeviceId"] == "desktop-stable-001"
    assert auth["protocol"] == "hashmm.remote.v2"

    listed = hub.on_message(viewer, {"type": "listDevices"})[0][1]["list"]
    assert [item["id"] for item in listed] == ["desktop-stable-001"]
    ready = hub.on_message(viewer, {"type": "connect", "target": "desktop-stable-001"})
    assert any(target is viewer and message["type"] == "ready" for target, message in ready)

    replacement = Conn("sb_owner", "host", "Desktop", "win32", "desktop-stable-001")
    actions = hub.add(replacement)
    assert any(target is host and message["type"] == "deviceReplaced" for target, message in actions)
    assert hub.on_message(host, {"type": "heartbeat"})[0][1] == {
        "type": "authFail", "reason": "stale_generation",
    }
    hub.remove(host)
    assert registry.get_connection_id("sb_owner", "desktop-stable-001") == replacement.id
    connection.close()


def test_transport_policy_is_direct_first_and_turn_only_is_explicit(tmp_path):
    registry, connection, _ = _registry(tmp_path)
    hub = SignalHub(device_registry=registry)
    host = Conn("sb_owner", "host", "Desktop", "win32", "desktop-stable-001")
    viewer = Conn("sb_owner", "viewer", "Phone", "android", "android-install-001")
    host_auth = next(message for target, message in hub.add(host) if target is host)
    hub.add(viewer)
    assert host_auth["transportPolicy"] == "ice-direct-turn-fallback"

    actions = hub.on_message(viewer, {
        "type": "connect", "target": "desktop-stable-001", "transportMode": "turn-only",
    })
    joined = next(message for target, message in actions if target is host)
    ready = next(message for target, message in actions if target is viewer)
    assert joined["relayOnly"] is True
    assert joined["legacyRelayAfterMs"] == 12000
    assert ready["transportMode"] == "turn-only"
    connection.close()
