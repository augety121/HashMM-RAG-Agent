"""MCP session, pagination and dynamic-execution boundary regressions."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, body=None, *, status=200, headers=None, text=None):
        self._body = body
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}
        self.text = text if text is not None else ("" if body is None else "json")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _Client:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.closed = False

    def post(self, endpoint, *, json, headers):
        self.calls.append({"endpoint": endpoint, "json": json, "headers": dict(headers)})
        return self.responder(json, headers, len(self.calls))

    def close(self):
        self.closed = True


def test_discovery_negotiates_session_and_follows_all_tool_pages(monkeypatch):
    from hashmm.tools import mcp_client as mcp

    def respond(payload, headers, _n):
        method = payload["method"]
        if method == "initialize":
            return _Response({
                "jsonrpc": "2.0", "id": payload["id"],
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                },
            }, headers={"content-type": "application/json", "Mcp-Session-Id": "sess-1"})
        if method == "notifications/initialized":
            assert headers["Mcp-Session-Id"] == "sess-1"
            assert "id" not in payload
            return _Response(status=202, headers={"content-type": "application/json"}, text="")
        if method == "tools/list" and not payload.get("params"):
            assert headers["Mcp-Session-Id"] == "sess-1"
            return _Response({"jsonrpc": "2.0", "id": payload["id"], "result": {
                "tools": [{"name": "read_a", "inputSchema": {"type": "object"}}],
                "nextCursor": "page-2",
            }})
        assert payload["params"] == {"cursor": "page-2"}
        assert headers["Mcp-Session-Id"] == "sess-1"
        return _Response({"jsonrpc": "2.0", "id": payload["id"], "result": {
            "tools": [{"name": "write_b", "inputSchema": {"type": "object"}}],
        }})

    client = _Client(respond)
    monkeypatch.setattr(mcp, "_new_http_client", lambda _timeout: client)
    tools, meta = mcp._discover({"endpoint": "https://mcp.example/rpc", "headers": {"X-Test": "1"}})

    assert [tool["name"] for tool in tools] == ["read_a", "write_b"]
    assert meta["protocol_version"] == "2025-11-25"
    assert meta["server_info"]["name"] == "fixture"
    assert [call["json"]["method"] for call in client.calls] == [
        "initialize", "notifications/initialized", "tools/list", "tools/list",
    ]
    assert client.closed is True


def test_sse_response_must_match_current_rpc_id(monkeypatch):
    from hashmm.tools import mcp_client as mcp

    def respond(payload, _headers, n):
        if n == 1:
            return _Response({"jsonrpc": "2.0", "id": payload["id"], "result": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "serverInfo": {},
            }})
        if n == 2:
            return _Response(status=202, text="")
        return _Response(status=200, headers={"content-type": "text/event-stream"},
                         text='data: {"jsonrpc":"2.0","id":"other","result":{}}\n\n')

    client = _Client(respond)
    monkeypatch.setattr(mcp, "_new_http_client", lambda _timeout: client)
    with pytest.raises(RuntimeError, match="未包含当前 JSON-RPC id"):
        mcp.discover_tools({"endpoint": "https://mcp.example/rpc"})


def test_endpoint_and_headers_reject_credential_redirect_vectors():
    from hashmm.tools import mcp_client as mcp

    with pytest.raises(ValueError, match="用户名或密码"):
        mcp._validate_endpoint("https://user:secret@example.com/rpc")
    with pytest.raises(ValueError, match="非法换行"):
        mcp._request_headers({"Authorization": "Bearer ok\r\nX-Evil: yes"})
    assert "Mcp-Session-Id" not in mcp._request_headers({"Mcp-Session-Id": "attacker"})


def test_missing_mcp_annotations_are_conservative():
    from hashmm.tools.mcp_client import annotation_for_tool

    unknown = annotation_for_tool({"name": "side_effect"})
    assert unknown["read_only"] is False
    assert unknown["destructive"] is True
    assert unknown["open_world"] is True
    read = annotation_for_tool({"name": "lookup", "annotations": {
        "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False,
    }})
    assert read["read_only"] is True and read["destructive"] is False


def test_dynamic_executor_crosses_central_hook_boundary():
    from hashmm import hooks
    from hashmm.api.tool_registry import execute_tool_structured

    hooks.reset_hooks()
    seen = []
    hooks.register_pre_hook(
        "fixture", lambda name, args, ctx: (
            seen.append(("pre", name)) or hooks.HookDecision(allow=True)
        ),
    )
    hooks.register_post_hook(
        "fixture_post", lambda name, args, ctx, ok, latency: seen.append(("post", name, ok)),
    )
    try:
        result = execute_tool_structured(
            "mcp__fixture__lookup", {"q": "x"}, {},
            executor_override=lambda args, ctx: {"status": "ok", "message": args["q"]},
        )
        assert result["message"] == "x"
        assert seen == [("pre", "mcp__fixture__lookup"),
                        ("post", "mcp__fixture__lookup", True)]
    finally:
        hooks.reset_hooks()
        hooks.install_default_hooks()
