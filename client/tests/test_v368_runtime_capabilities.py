"""V368 real runtime-capability wiring regressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _names(items: list[dict]) -> list[str]:
    return [str(item.get("function", {}).get("name") or "") for item in items]


def test_chat_exposes_central_browser_and_artifact_tools_once():
    from hashmm.agent.loop import AgentLoop

    names = _names(AgentLoop._get_default_tools())
    required = {
        "browser_open", "browser_read", "browser_act", "browser_screenshot",
        "create_document", "create_pdf", "create_xlsx", "create_pptx_from_plan",
    }
    assert required <= set(names)
    assert len(names) == len(set(names)), "duplicate schemas make tool selection ambiguous"


def test_worker_obeys_same_module_switch_as_main_chat(monkeypatch):
    from hashmm.agent.worker import Worker

    monkeypatch.setenv("HASHMM_MODULE_RAG", "0")
    worker = Worker(llm_fn=None, role="research")
    assert "kb_search" not in _names(worker._tools)
    assert "kg_query" not in _names(worker._tools)
    assert "web_search" in _names(worker._tools)


def test_image_search_is_wired_and_owner_scoped(tmp_path, monkeypatch):
    from hashmm.agent.modules import status
    from hashmm.api.tool_registry import execute_tool_structured
    from hashmm.retrieval.image_store import add_image, get_image, search_text

    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    alice = add_image(b"same image", "roadmap.png", caption="产品路线图", owner="alice")
    bob = add_image(b"same image", "roadmap.png", caption="另一份路线图", owner="bob")
    assert alice and bob and alice["id"] != bob["id"]
    assert len(search_text("路线图", owner="alice")) == 1
    assert get_image(alice["id"], owner="bob") is None

    result = execute_tool_structured(
        "image_search", {"query": "路线图"}, {"user_id": "alice", "conv_id": "c1"},
    )
    assert result["status"] == "ok"
    assert [item["id"] for item in result["items"]] == [alice["id"]]
    image = next(item for item in status() if item["key"] == "image")
    assert image["wired"] is True
    assert image["healthy"] is True
    assert image["active_tools"] == ["image_search"]


def test_degraded_module_keeps_only_its_independently_wired_tools(monkeypatch):
    """一个可选工具缺失不能把同模块已接通的 Chat 能力一起隐藏。"""
    from hashmm.agent import modules

    demo = modules.ToolModule(
        "partial_demo",
        "部分装配模块",
        {"ready_tool", "schema_only", "missing_schema"},
    )
    monkeypatch.setattr(modules, "_MODULES", [demo])
    monkeypatch.setattr(
        modules,
        "_registry_inventory",
        lambda: (
            {"ready_tool", "schema_only"},
            {"ready_tool"},
        ),
    )

    assert modules.enabled_tool_names() == {"ready_tool"}
    state = modules.status()[0]
    assert state["wired"] is False
    assert state["active_tools"] == ["ready_tool"]
    assert state["missing_tools"] == ["missing_schema", "schema_only"]
    from hashmm.agent.permissions import PermissionLevel, TOOL_PERMISSIONS
    assert TOOL_PERMISSIONS["image_search"] == PermissionLevel.READ


def test_chat_upload_requires_auth_before_reading_file(monkeypatch):
    from fastapi import HTTPException
    from hashmm.api.routes import files as route

    touched = {"read": False}

    class _Upload:
        filename = "private.png"

        async def read(self):
            touched["read"] = True
            return b"secret"

    def _deny(_request):
        raise HTTPException(401, "not authenticated")

    monkeypatch.setattr(route, "require_auth", _deny)
    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.upload_file(request=object(), file=_Upload(), analyze="0"))
    assert exc.value.status_code == 401
    assert touched["read"] is False, "unauthenticated upload body must not be consumed"


def test_runtime_snapshot_is_evidence_backed_and_secret_free():
    from hashmm.agent.capabilities import build_runtime_capabilities

    payload = build_runtime_capabilities("runtime-test-user")
    assert payload["contract"] == "hashmm.runtime-capabilities.v1"
    assert payload["truth_contract"] == "hashmm.capability-truth.v1"
    assert payload["chat_tool_count"] >= 40
    by_id = {item["id"]: item for item in payload["capabilities"]}
    assert by_id["browser_use"]["state"] == "ready"
    assert by_id["artifacts"]["state"] == "ready"
    assert by_id["canvas"]["state"] == "ready"
    assert by_id["long_tasks"]["state"] == "ready"

    root = Path(__file__).resolve().parents[1]
    for capability in payload["capabilities"]:
        assert capability["availability"] in {
            "available", "degraded", "unavailable",
        }
        assert capability["visibility"] in {"user", "admin", "diagnostic"}
        assert capability["production_ready"] is (
            capability["availability"] == "available"
            and capability["enabled"]
            and capability["wired"]
            and not capability["missing_tools"]
        )
        assert capability["evidence"]["kind"] == "runtime_contract"
        for evidence in capability["tests"]:
            assert (root / evidence).is_file(), f"invented verification path: {evidence}"

    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "authorization", "bearer ", "service_role", "jwt_secret"):
        assert forbidden not in serialized


def test_browser_cockpit_never_substitutes_scripted_demo_for_runtime_evidence():
    root = Path(__file__).resolve().parents[1]
    cockpit = (root / "desktop" / "browser-cockpit.html").read_text(encoding="utf-8")
    assert "hashmmBrowserCockpit" in cockpit
    assert "安全桥不可用" in cockpit
    assert "不会使用脚本演示替代真实执行" in cockpit
    for forbidden in ("播放演示", "FRAMES", "resetDemo", "auto-start", "demoCtrls"):
        assert forbidden not in cockpit


def test_chat_tool_inventory_requires_a_real_executor():
    from hashmm.agent.capabilities import build_runtime_capabilities

    payload = build_runtime_capabilities("runtime-test-user")
    inventory = payload["chat_tool_inventory"]
    declared = set(inventory["declared"])
    effective = set(inventory["effective"])
    missing = set(inventory["missing_executors"])
    assert effective <= declared
    assert not (effective & missing)
    assert payload["chat_declared_tool_count"] == len(declared)
    assert payload["chat_effective_tool_count"] == len(effective)
    assert payload["chat_tool_count"] == len(effective)
    # The current built-in distribution is complete; if a future distribution
    # is partial, the payload must say so rather than silently claiming ready.
    assert not (missing & declared - {
        "update_todo", "spawn_worker", "memory_recall", "remember_preference",
    })


def test_runtime_snapshot_does_not_confuse_imported_router_with_live_mount():
    from hashmm.agent.capabilities import build_runtime_capabilities

    payload = build_runtime_capabilities("runtime-test-user", mounted_paths=set())
    by_id = {item["id"]: item for item in payload["capabilities"]}
    assert by_id["canvas"]["state"] == "unavailable"
    assert by_id["long_tasks"]["state"] == "unavailable"
    assert "未完整挂载" in by_id["canvas"]["reason"]

    mounted = {
        "/api/canvas/ask", "/api/canvas/lock", "/api/canvas/templates",
        "/api/canvas/publish",
        "/api/conversations/{conv_id}/evidence-refs",
        "/api/conversations/{conv_id}/files/{filename}/evidence-links",
        "/api/conversations/{conv_id}/files/{filename}/canvas-blocks",
        "/api/loops", "/api/loops/goal", "/api/loops/{loop_id}/pause",
        "/api/loops/{loop_id}/resume", "/api/loops/{loop_id}/stop",
    }
    live = build_runtime_capabilities("runtime-test-user", mounted_paths=mounted)
    live_by_id = {item["id"]: item for item in live["capabilities"]}
    assert live_by_id["canvas"]["state"] == "ready"
    assert live_by_id["long_tasks"]["state"] == "ready"


def test_graph_capability_is_not_advertised_without_chat_tool(monkeypatch):
    from hashmm.agent import capabilities

    monkeypatch.setattr(capabilities, "_tool_names", lambda: set())
    payload = capabilities.build_runtime_capabilities("runtime-test-user")
    graph = next(item for item in payload["capabilities"] if item["id"] == "graph_rag")
    assert graph["wired"] is False
    assert graph["active_tools"] == []
    assert graph["missing_tools"] == ["kg_query"]
    assert graph["state"] in {"setup_required", "unavailable"}
    sandbox = next(item for item in payload["capabilities"] if item["id"] == "execution_sandbox")
    assert sandbox["wired"] is False
    assert sandbox["active_tools"] == []
    assert sandbox["missing_tools"] == ["execute_code", "run_shell"]


def test_runtime_route_has_private_cache_and_conditional_response(monkeypatch):
    from hashmm.api.routes import runtime_capabilities as route

    monkeypatch.setattr(route, "require_auth", lambda request: {"uid": "u1"})

    class _Request:
        headers: dict[str, str] = {}

    import asyncio

    first = asyncio.run(route.runtime_capabilities(_Request()))
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "private, max-age=30"
    assert first.headers["vary"] == "Authorization"

    class _CachedRequest:
        headers = {"If-None-Match": etag}

    cached = asyncio.run(route.runtime_capabilities(_CachedRequest()))
    assert cached.status_code == 304
