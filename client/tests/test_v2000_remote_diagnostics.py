import sqlite3

from hashmm.api.remote_devices import RemoteDeviceRegistry


def _connect_factory(path):
    def connect():
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def test_remote_diagnostics_distinguishes_ticket_from_host_registration(tmp_path):
    now = [1000.0]
    registry = RemoteDeviceRegistry(_connect_factory(tmp_path / "remote.db"), clock=lambda: now[0], lease_ttl=35)
    ticket = registry.issue_socket_ticket("owner", "desktop-install-01", "host")
    assert ticket["attempt_id"].startswith("ra_")
    diag = registry.diagnostics("owner", "desktop-install-01")
    states = {step["id"]: step["state"] for step in diag["steps"]}
    assert states["host_ticket"] == "ok"
    assert states["host_socket"] == "waiting"
    assert diag["host_registered"] is False


def test_consumed_ticket_preserves_attempt_correlation(tmp_path):
    registry = RemoteDeviceRegistry(_connect_factory(tmp_path / "remote.db"), clock=lambda: 1000.0, lease_ttl=35)
    issued = registry.issue_socket_ticket("owner", "android-install-01", "viewer")
    consumed = registry.consume_socket_ticket_detailed(issued["ticket"])
    assert consumed is not None
    assert consumed["attempt_id"] == issued["attempt_id"]


def test_v4_attempt_failure_is_owner_scoped_and_actionable(tmp_path):
    registry = RemoteDeviceRegistry(_connect_factory(tmp_path / "remote.db"), clock=lambda: 1000.0, lease_ttl=35)
    issued = registry.issue_socket_ticket(
        "owner", "desktop-install-01", "host", trace_id="rt_desktop_boot",
        protocol="hashmm.remote.v4", endpoint_host="hashmm.hashlens.org",
    )
    assert registry.transition_attempt(
        issued["attempt_id"], "transport_rejected", error_code="PROXY_SECURITY_UNVERIFIED",
        close_code=4403, terminal=True,
        security={"secure": False, "source": "secure-protocol-unverified", "cf_ray": "secret-ray"},
    )
    diag = registry.diagnostics("owner", "desktop-install-01")
    assert diag["protocol"] == "hashmm.remote.v4"
    assert diag["latest_failure"]["error_code"] == "PROXY_SECURITY_UNVERIFIED"
    assert diag["latest_failure"]["trace_id"] == "rt_desktop_boot"
    assert diag["latest_failure"]["security"]["cf_ray"] != "secret-ray"
    assert {step["id"]: step["state"] for step in diag["steps"]}["host_socket"] == "blocked"
    assert registry.diagnostics("different-owner")["latest_failure"] is None
