"""Deterministic criteria for public-network and long-running remote acceptance.

The evaluators consume measurements.  They never create successful evidence by
themselves and never reinterpret missing samples as a pass.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable


NETWORK_SCHEMA = "hashmm.remote.network-acceptance.v1"
SOAK_SCHEMA = "hashmm.remote.soak-acceptance.v1"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def evaluate_network_acceptance(device_reports: Iterable[dict[str, Any]],
                                transport_events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reports = [report for report in device_reports if isinstance(report, dict)]
    events = [event for event in transport_events if isinstance(event, dict)]
    device_ids = {str(report.get("device_id") or "") for report in reports if report.get("device_id")}
    roles = {str(report.get("role") or "") for report in reports}
    symmetric = [report for report in reports if report.get("nat_mapping") == "address_and_port_dependent"]
    backend_secure = bool(reports) and all(report.get("backend_secure") is True for report in reports)
    turn_udp = any(report.get("turn_udp_reachable") is True for report in reports)
    turn_tcp_tls = any(report.get("turn_tls_reachable") is True for report in reports)

    relay_by_session: dict[str, set[str]] = {}
    for event in events:
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else event
        if str(event.get("event") or "") != "transport_observed":
            continue
        if str(detail.get("candidate_type") or "").lower() != "relay":
            continue
        sid = str(event.get("session_id") or "")
        role = str(detail.get("role") or "")
        if sid and role in {"host", "viewer"}:
            relay_by_session.setdefault(sid, set()).add(role)
    relay_sessions = sorted(sid for sid, seen in relay_by_session.items() if seen == {"host", "viewer"})

    criteria = {
        "at_least_two_devices": len(device_ids) >= 2,
        "host_and_viewer_measured": {"host", "viewer"}.issubset(roles),
        "secure_backend_reachable": backend_secure,
        "symmetric_nat_observed": bool(symmetric),
        "turn_udp_reachable": turn_udp,
        "turn_tls_reachable": turn_tcp_tls,
        "same_session_relay_observed_on_both_ends": bool(relay_sessions),
    }
    failures = [name for name, passed in criteria.items() if not passed]
    return {
        "schema": NETWORK_SCHEMA,
        "status": "passed" if not failures else "incomplete",
        "device_count": len(device_ids),
        "devices": [hashlib.sha256(item.encode()).hexdigest()[:16] for item in sorted(device_ids)],
        "nat_observations": {
            "address_and_port_dependent": len(symmetric),
            "other_or_unknown": max(0, len(reports) - len(symmetric)),
        },
        "relay_sessions": [hashlib.sha256(item.encode()).hexdigest()[:16] for item in relay_sessions],
        "criteria": criteria,
        "failures": failures,
    }


def evaluate_soak_acceptance(samples: Iterable[dict[str, Any]], *, started_at: float,
                             finished_at: float, minimum_duration: int = 86_400,
                             minimum_availability_pct: float = 99.5,
                             maximum_consecutive_failures: int = 3) -> dict[str, Any]:
    rows = [sample for sample in samples if isinstance(sample, dict)]
    duration = max(0.0, _finite(finished_at) - _finite(started_at))
    successes = sum(1 for row in rows if row.get("ok") is True)
    relay_samples = sum(1 for row in rows if row.get("relay_ok") is True)
    failures_n = len(rows) - successes
    availability = (successes / len(rows) * 100.0) if rows else 0.0
    relay_coverage = (relay_samples / len(rows) * 100.0) if rows else 0.0
    consecutive = maximum = 0
    reconnects = 0
    previously_ok: bool | None = None
    for row in rows:
        ok = row.get("ok") is True
        if ok:
            consecutive = 0
            if previously_ok is False:
                reconnects += 1
        else:
            consecutive += 1
            maximum = max(maximum, consecutive)
        previously_ok = ok
    # At least one observation per five minutes prevents a single start/end
    # request from being presented as a 24-hour stability run.
    minimum_samples = max(2, math.floor(minimum_duration / 300))
    criteria = {
        "duration_reached": duration >= minimum_duration,
        "sample_density_reached": len(rows) >= minimum_samples,
        "availability_reached": availability >= minimum_availability_pct,
        "remote_relay_coverage_reached": relay_coverage >= minimum_availability_pct,
        "failure_streak_bounded": maximum <= maximum_consecutive_failures,
    }
    failed = [name for name, passed in criteria.items() if not passed]
    return {
        "schema": SOAK_SCHEMA,
        "status": "passed" if not failed else "incomplete",
        "started_at": _finite(started_at), "finished_at": _finite(finished_at),
        "duration_seconds": duration, "samples": len(rows), "successes": successes,
        "failures": failures_n, "availability_pct": round(availability, 5),
        "relay_samples": relay_samples, "relay_coverage_pct": round(relay_coverage, 5),
        "max_consecutive_failures": maximum, "reconnects": reconnects,
        "criteria": criteria, "failed_criteria": failed,
    }
