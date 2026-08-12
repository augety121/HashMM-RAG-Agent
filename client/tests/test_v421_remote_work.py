"""V421 remote-to-WorkRuntime authorization and projection regressions."""
from __future__ import annotations

import pytest

from hashmm.api import remote_work
from hashmm.api.remote_sessions import RemoteSession, RemoteSessionRegistry


def test_bind_remote_work_owner_checks_requested_run(monkeypatch):
    monkeypatch.setattr(
        remote_work.work_runtime,
        "get_run",
        lambda run_id, owner, **_kwargs: (
            {"id": run_id} if (run_id, owner) == ("wr-owned", "owner-a") else None
        ),
    )
    assert remote_work.bind_remote_work("owner-a", "wr-owned") == "wr-owned"
    with pytest.raises(remote_work.RemoteWorkNotFound):
        remote_work.bind_remote_work("owner-a", "wr-foreign")
    with pytest.raises(remote_work.RemoteWorkNotFound):
        remote_work.bind_remote_work("owner-a", "../../unsafe")


def test_bind_remote_work_creates_dedicated_remote_run_without_raw_device_ids(monkeypatch):
    captured = {}

    def create_run(**kwargs):
        captured.update(kwargs)
        return {"id": "wr-created"}

    monkeypatch.setattr(remote_work.work_runtime, "create_run", create_run)
    run_id = remote_work.bind_remote_work(
        "owner-a", host_device_id="private-host", viewer_device_id="private-phone",
        conversation_id="conv-a", source_id="remote:test-request",
    )
    assert run_id == "wr-created"
    assert captured["kind"] == "remote"
    assert captured["status"] == "waiting_approval"
    assert captured["conv_id"] == "conv-a"
    assert "private-host" not in str(captured["snapshot"])
    assert "private-phone" not in str(captured["snapshot"])


def test_remote_client_request_id_reuses_same_work_source(monkeypatch):
    captured = []

    def create_run(**kwargs):
        captured.append(kwargs)
        return {"id": "wr-stable"}

    monkeypatch.setattr(remote_work.work_runtime, "create_run", create_run)
    for _ in range(2):
        assert remote_work.bind_remote_work(
            "owner-a", source_id="remote-request:request-1234"
        ) == "wr-stable"
    assert [item["source_id"] for item in captured] == [
        "remote-request:request-1234", "remote-request:request-1234",
    ]


def test_remote_lifecycle_projects_bounded_idempotent_work_event(monkeypatch):
    admitted = []
    monkeypatch.setattr(
        remote_work.work_runtime, "get_run",
        lambda *_args, **_kwargs: {"id": "wr-a", "kind": "remote"},
    )

    def append(run_id, **kwargs):
        admitted.append((run_id, kwargs))
        return {"state": "applied"}

    monkeypatch.setattr(remote_work.work_runtime, "append_event_once", append)
    session = RemoteSession(
        id="rs-a", uid="owner-a", host_device_id="secret-host",
        viewer_device_id="secret-viewer", requested_scopes=("view", "control"),
        work_run_id="wr-a",
    )
    remote_work.project_remote_transition(session, "requested", {"token": "must-drop"})
    assert len(admitted) == 1
    run_id, event = admitted[0]
    assert run_id == "wr-a"
    assert event["event_type"] == "remote.requested"
    assert event["status"] == "waiting_approval"
    assert event["idempotency_key"] == "remote:rs-a:0:requested"
    assert "secret-host" not in str(event)
    assert "secret-viewer" not in str(event)
    assert "must-drop" not in str(event)


def test_registry_transition_sink_receives_authoritative_work_run():
    projected = []
    registry = RemoteSessionRegistry(
        transition_sink=lambda session, event, detail: projected.append(
            (session.work_run_id, event, detail)
        )
    )
    session = registry.request(
        "owner-a", "host-a", "viewer-a", ["view"], "wr-a"
    )
    public = session.public()
    assert public["work_run_id"] == "wr-a"
    assert public["work_id"] == "wr-a"
    assert projected == [("wr-a", "requested", {"scopes": ["view"]})]


def test_late_socket_close_after_terminal_work_is_idempotent(monkeypatch, caplog):
    monkeypatch.setattr(
        remote_work.work_runtime, "get_run",
        lambda *_args, **_kwargs: {"id": "wr-a", "kind": "remote", "status": "delivered"},
    )
    monkeypatch.setattr(
        remote_work.work_runtime, "append_event_once",
        lambda *_args, **_kwargs: {"state": "invalid_transition"},
    )
    session = RemoteSession(
        id="rs-late", uid="owner-a", host_device_id="host-a", viewer_device_id="viewer-a",
        requested_scopes=("view",), work_run_id="wr-a", state="interrupted",
    )
    remote_work.project_remote_transition(session, "interrupted", {"reason": "socket_closed"})
    assert "Failed to project remote session" not in caplog.text


def test_late_terminal_projection_uses_atomic_runtime_status(monkeypatch, caplog):
    """The pre-append snapshot may be stale; the runtime result is authoritative."""
    monkeypatch.setattr(
        remote_work.work_runtime, "get_run",
        lambda *_args, **_kwargs: {"id": "wr-a", "kind": "remote", "status": "running"},
    )
    monkeypatch.setattr(
        remote_work.work_runtime, "append_event_once",
        lambda *_args, **_kwargs: {
            "state": "invalid_transition", "current_status": "delivered",
            "requested_status": "interrupted",
        },
    )
    session = RemoteSession(
        id="rs-late-race", uid="owner-a", host_device_id="host-a",
        viewer_device_id="viewer-a", requested_scopes=("view",),
        work_run_id="wr-a", state="interrupted",
    )
    remote_work.project_remote_transition(session, "interrupted", {"reason": "socket_closed"})
    assert "Failed to project remote session" not in caplog.text
