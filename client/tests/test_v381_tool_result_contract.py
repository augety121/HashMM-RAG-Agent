from __future__ import annotations

import asyncio

from hashmm.api.tool_result import parse_tool_result


def test_plain_success_no_longer_depends_on_decorative_marker():
    result = parse_tool_result("执行成功\n42")
    assert result.success is True
    assert result.content.endswith("42")


def test_plain_and_structured_failures_are_normalized():
    assert parse_tool_result("失败 · sandbox unavailable").success is False
    structured = parse_tool_result({"status": "error", "error": "blocked"})
    assert structured.success is False
    assert structured.error == "blocked"


def test_structured_success_preserves_evidence_fields():
    result = parse_tool_result({
        "success": True,
        "output": "ok",
        "files": [{"filename": "result.txt"}],
        "metrics": {"elapsed_ms": 7},
    })
    assert result.success is True
    assert result.content == "ok"
    assert result.files == [{"filename": "result.txt"}]
    assert result.metrics["elapsed_ms"] == 7


def test_direct_execute_route_returns_stable_status(monkeypatch):
    from hashmm.api.routes import conversations

    class Request:
        async def json(self):
            return {"code": "print(42)"}

    monkeypatch.setattr(conversations, "require_conv_access", lambda request, conv_id: {"uid": "u-1"})
    monkeypatch.setattr(conversations, "resolve_user_id", lambda request: "u-1")
    monkeypatch.setattr(
        conversations,
        "execute_tool",
        lambda tool, args, context: "执行成功\n42",
    )

    response = asyncio.run(conversations.execute_code_in_conv("conv-1", Request()))
    assert response["ok"] is True
    assert response["status"] == "done"
    assert response["output"].endswith("42")
    assert response["error"] is None
