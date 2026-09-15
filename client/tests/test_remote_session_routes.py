"""HTTP control-plane regressions for owner-bound remote sessions."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import HTTPException

from hashmm.api.routes import remote_sessions as routes
from hashmm.api.remote_sessions import RemoteSessionRegistry


class _Request:
    pass


def test_remote_session_route_owner_boundary_and_ticket(monkeypatch):
    monkeypatch.setattr(routes, "remote_session_registry", RemoteSessionRegistry())
    monkeypatch.setattr(routes, "bind_remote_work", lambda *_args, **_kwargs: "wr-owned")
    uid = "route-owner-" + uuid.uuid4().hex
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": uid})
    host = "host-" + uuid.uuid4().hex
    viewer = "viewer-" + uuid.uuid4().hex

    created = asyncio.run(routes.request_remote_session(
        routes.SessionRequest(host_device_id=host, viewer_device_id=viewer,
                              scopes=["view", "control", "unknown"]), _Request()))
    session_id = created["session"]["id"]
    assert created["session"]["requested_scopes"] == ["control", "view"]
    assert created["session"]["work_run_id"] == "wr-owned"
    assert created["session"]["work_id"] == "wr-owned"

    approved = asyncio.run(routes.approve_remote_session(
        session_id, routes.SessionDecision(host_device_id=host, scopes=["view", "control"]), _Request()))
    assert approved["session"]["state"] == "active"
    ticket = asyncio.run(routes.issue_remote_ticket(
        session_id, routes.TicketRequest(role="viewer", device_id=viewer), _Request()))
    assert ticket["token_type"] == "Remote" and ticket["ticket"]

    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "foreign-owner"})
    with pytest.raises(HTTPException) as foreign:
        asyncio.run(routes.remote_session_audit(session_id, _Request()))
    assert foreign.value.status_code == 404


def test_remote_session_route_rejects_foreign_work_and_conflicting_alias(monkeypatch):
    monkeypatch.setattr(routes, "remote_session_registry", RemoteSessionRegistry())
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner-a"})

    def reject(*_args, **_kwargs):
        raise routes.RemoteWorkNotFound("work_run_not_found")

    monkeypatch.setattr(routes, "bind_remote_work", reject)
    with pytest.raises(HTTPException) as foreign:
        asyncio.run(routes.request_remote_session(
            routes.SessionRequest(
                host_device_id="host", viewer_device_id="viewer",
                work_run_id="wr-foreign",
            ), _Request()
        ))
    assert foreign.value.status_code == 404
    assert routes.remote_session_registry.list_for_owner("owner-a") == []

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(routes.request_remote_session(
            routes.SessionRequest(
                host_device_id="host", viewer_device_id="viewer",
                work_run_id="wr-one", work_id="wr-two",
            ), _Request()
        ))
    assert conflict.value.status_code == 400


def test_remote_session_validates_before_creating_work(monkeypatch):
    monkeypatch.setattr(routes, "remote_session_registry", RemoteSessionRegistry())
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner-a"})
    called = []
    monkeypatch.setattr(routes, "bind_remote_work", lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(HTTPException) as empty:
        asyncio.run(routes.request_remote_session(
            routes.SessionRequest(
                host_device_id="host", viewer_device_id="viewer", scopes=["unknown"],
            ), _Request()
        ))
    assert empty.value.status_code == 400
    assert called == []

    with pytest.raises(HTTPException) as request_id:
        asyncio.run(routes.request_remote_session(
            routes.SessionRequest(
                host_device_id="host", viewer_device_id="viewer",
                client_request_id="../bad",
            ), _Request()
        ))
    assert request_id.value.status_code == 400
    assert called == []


def test_remote_config_never_exposes_turn_credentials(monkeypatch):
    monkeypatch.setattr(routes, "remote_session_registry", RemoteSessionRegistry())
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner"})
    monkeypatch.setenv("HASHMM_ICE_SERVERS", '[{"urls":"turns:relay.example","username":"u","credential":"secret"}]')
    config = asyncio.run(routes.remote_config(_Request()))
    assert config["turn_configured"] is True
    assert "secret" not in str(config)
    assert config["permission_mode"] == "ask"
    assert config["handoff_supported"] is True
    assert config["completion_receipts"] is True


def test_remote_handoff_and_completion_routes_keep_owner_boundary(monkeypatch):
    registry = RemoteSessionRegistry(active_ttl=300)
    monkeypatch.setattr(routes, "remote_session_registry", registry)
    monkeypatch.setattr(routes, "bind_remote_work", lambda *_args, **_kwargs: "wr-owned")
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner-a"})
    created = asyncio.run(routes.request_remote_session(
        routes.SessionRequest(
            host_device_id="host-a", viewer_device_id="viewer-a",
            scopes=["view", "control"],
        ), _Request(),
    ))
    sid = created["session"]["id"]
    asyncio.run(routes.approve_remote_session(
        sid, routes.SessionDecision(host_device_id="host-a", scopes=["view", "control"]),
        _Request(),
    ))
    handed = asyncio.run(routes.handoff_remote_session(
        sid, routes.SessionHandoff(
            actor_device_id="viewer-a", new_viewer_device_id="viewer-b", scopes=["view"],
        ), _Request(),
    ))
    assert handed["schema"] == "hashmm.remote.handoff.v1"
    successor_id = handed["session"]["id"]
    assert handed["session"]["predecessor_session_id"] == sid
    registry.approve("owner-a", successor_id, "host-a", ["view"])
    completed = asyncio.run(routes.complete_remote_session(
        successor_id, routes.SessionComplete(device_id="viewer-b", summary="done"),
        _Request(),
    ))
    assert completed["session"]["state"] == "completed"

    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner-b"})
    with pytest.raises(HTTPException) as foreign:
        asyncio.run(routes.complete_remote_session(
            successor_id, routes.SessionComplete(device_id="viewer-b"), _Request(),
        ))
    assert foreign.value.status_code == 404


def test_remote_readiness_fails_closed_without_real_receipts(monkeypatch):
    registry = RemoteSessionRegistry()
    monkeypatch.setattr(routes, "remote_session_registry", registry)
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner"})
    monkeypatch.setattr(routes, "secure_remote_required", lambda: True)
    monkeypatch.setattr(routes, "turn_configuration_status", lambda: {
        "configured": True, "dynamic_credentials": True, "static_credentials": False,
        "required": True, "urls": 2,
    })
    result = asyncio.run(routes.remote_readiness(_Request()))
    assert result["production_ready"] is False
    assert result["criteria"]["public_multi_device_acceptance"] is False
    assert result["criteria"]["twenty_four_hour_soak"] is False


def test_remote_readiness_projects_fresh_receipts_without_evidence(monkeypatch):
    now = time.time()

    class _Registry:
        @staticmethod
        def latest_acceptance(_uid, kind):
            if kind == "network":
                return {"run_id": "network-run", "status": "passed", "created_at": now,
                        "duration_seconds": 120, "device_count": 2,
                        "evidence": {"criteria": {"same_session_relay_observed_on_both_ends": True,
                                                   "symmetric_nat_observed": True},
                                     "private_probe": "must-not-leak"}}
            return {"run_id": "soak-run", "status": "passed", "created_at": now,
                    "duration_seconds": 86400, "device_count": 1,
                    "evidence": {"criteria": {"remote_relay_coverage_reached": True},
                                 "private_probe": "must-not-leak"}}

    monkeypatch.setattr(routes, "remote_session_registry", _Registry())
    monkeypatch.setattr(routes, "require_auth", lambda _request: {"uid": "owner"})
    monkeypatch.setattr(routes, "secure_remote_required", lambda: True)
    monkeypatch.setattr(routes, "_supabase_identity_ready", lambda: True)
    monkeypatch.setenv("HASHMM_PUBLIC_URL", "https://hashmm.example.com")
    monkeypatch.setattr(routes, "turn_configuration_status", lambda: {
        "configured": True, "dynamic_credentials": True, "static_credentials": False,
        "required": True, "urls": 2,
    })
    result = asyncio.run(routes.remote_readiness(_Request()))
    assert result["production_ready"] is True
    assert "evidence" not in str(result)
