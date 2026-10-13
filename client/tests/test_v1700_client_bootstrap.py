import asyncio
import importlib.util
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

_SPEC = importlib.util.spec_from_file_location(
    "hashmm_v1700_system_route",
    Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "system.py",
)
assert _SPEC and _SPEC.loader
system = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(system)


def _request(headers=None):
    raw_headers = [
        (key.lower().encode("ascii"), value.encode("utf-8"))
        for key, value in (headers or {}).items()
    ]
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/client/bootstrap",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    })
    request.state.request_id = "req-v1700"
    return request


def test_bootstrap_is_authenticated_and_secret_free(monkeypatch):
    monkeypatch.setattr(system, "require_auth", lambda _request: {
        "uid": "sb_0f7629e8-2ec4-4de9-b0a8-2d9d32b71866",
        "email": "person@example.com",
        "role": "user",
    })
    monkeypatch.setattr(system, "_identity_project_ref", lambda: "project-ref")
    monkeypatch.setenv("HASHMM_PUBLIC_URL", "https://hashmm.example/")

    response = Response()
    payload = asyncio.run(system.client_bootstrap(_request(), response))

    assert payload["authenticated"] is True
    assert payload["sync_protocol"] == system.PROTOCOLS["sync"]
    assert payload["canonical_origin"] == "https://hashmm.example"
    assert payload["supabase_project_ref"] == "project-ref"
    assert payload["request_id"] == "req-v1700"
    assert len(payload["user_sub_fingerprint"]) == 12
    rendered = repr(payload).lower()
    assert "person@example.com" not in rendered
    assert "0f7629e8-2ec4-4de9-b0a8-2d9d32b71866" not in rendered
    assert "token" not in rendered
    assert response.headers["cache-control"].startswith("no-store")


def test_project_ref_parsing(monkeypatch):
    from hashmm.api import supabase_auth

    monkeypatch.setattr(supabase_auth, "supabase_url", lambda: "https://abc123.supabase.co")
    assert system._identity_project_ref() == "abc123"
