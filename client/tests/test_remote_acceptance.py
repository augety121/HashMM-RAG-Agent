import importlib.util
from pathlib import Path

from hashmm.evaluation.remote_acceptance import evaluate_network_acceptance, evaluate_soak_acceptance


ROOT = Path(__file__).resolve().parents[1]


def test_network_acceptance_requires_two_roles_symmetric_nat_and_same_relay_session():
    devices = [
        {"device_id": "pc", "role": "host", "backend_secure": True,
         "nat_mapping": "address_and_port_dependent", "turn_udp_reachable": True, "turn_tls_reachable": True},
        {"device_id": "phone", "role": "viewer", "backend_secure": True,
         "nat_mapping": "endpoint_independent_or_unknown", "turn_udp_reachable": True, "turn_tls_reachable": True},
    ]
    events = [
        {"event": "transport_observed", "session_id": "s1", "detail": {"role": "host", "candidate_type": "relay"}},
        {"event": "transport_observed", "session_id": "s1", "detail": {"role": "viewer", "candidate_type": "relay"}},
    ]
    result = evaluate_network_acceptance(devices, events)
    assert result["status"] == "passed"
    assert result["device_count"] == 2
    assert result["criteria"]["same_session_relay_observed_on_both_ends"] is True
    assert "pc" not in str(result["devices"])


def test_network_acceptance_does_not_treat_reachability_as_relay_proof():
    devices = [{"device_id": "pc", "role": "host", "backend_secure": True,
                "nat_mapping": "address_and_port_dependent", "turn_udp_reachable": True, "turn_tls_reachable": True}]
    result = evaluate_network_acceptance(devices, [])
    assert result["status"] == "incomplete"
    assert "same_session_relay_observed_on_both_ends" in result["failures"]
    assert "at_least_two_devices" in result["failures"]


def test_soak_acceptance_requires_duration_density_availability_and_bounded_streak():
    samples = [{"ok": True, "relay_ok": True, "at": i * 60} for i in range(1441)]
    result = evaluate_soak_acceptance(samples, started_at=0, finished_at=86400)
    assert result["status"] == "passed"
    assert result["availability_pct"] == 100

    short = evaluate_soak_acceptance([{"ok": True, "relay_ok": True}, {"ok": True, "relay_ok": True}],
                                     started_at=0, finished_at=120)
    assert short["status"] == "incomplete"
    assert short["criteria"]["duration_reached"] is False


def test_soak_acceptance_counts_recovery_and_failure_streak():
    samples = ([{"ok": True, "relay_ok": True}] * 286
               + [{"ok": False, "relay_ok": False}] * 4
               + [{"ok": True, "relay_ok": True}] * 1150)
    result = evaluate_soak_acceptance(samples, started_at=0, finished_at=86400)
    assert result["status"] == "incomplete"
    assert result["max_consecutive_failures"] == 4
    assert result["reconnects"] == 1


def test_soak_acceptance_cannot_pass_on_health_without_remote_relay():
    samples = [{"ok": True, "relay_ok": False, "at": i * 60} for i in range(1441)]
    result = evaluate_soak_acceptance(samples, started_at=0, finished_at=86400)
    assert result["status"] == "incomplete"
    assert result["criteria"]["remote_relay_coverage_reached"] is False


def test_soak_probe_requires_both_relay_roles_in_the_same_recent_session(monkeypatch):
    path = ROOT / "scripts" / "remote-acceptance.py"
    spec = importlib.util.spec_from_file_location("remote_acceptance_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def response(_url, _timeout, _token):
        return {"schema": "hashmm.remote.owner-audit.v1", "events": [
            {"event": "transport_observed", "session_id": "same", "detail": {"role": "host", "candidate_type": "relay"}},
            {"event": "transport_observed", "session_id": "same", "detail": {"role": "viewer", "candidate_type": "relay"}},
        ]}

    monkeypatch.setattr(module, "_get_json", response)
    assert module._remote_relay_state("https://example.com", "token", 1, 0) == (True, True)

    monkeypatch.setattr(module, "_get_json", lambda *_args: {
        "schema": "hashmm.remote.owner-audit.v1", "events": [
            {"event": "transport_observed", "session_id": "one", "detail": {"role": "host", "candidate_type": "relay"}},
            {"event": "transport_observed", "session_id": "two", "detail": {"role": "viewer", "candidate_type": "relay"}},
        ],
    })
    assert module._remote_relay_state("https://example.com", "token", 1, 0) == (True, False)
