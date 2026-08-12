"""对外接口契约 测试。

覆盖：/v1 API、MCP server、OTLP trace 导出、dashboard 的"默认关"与"返回结构稳定"。
契约测试保证：这些对外承诺的结构不被意外改坏。
"""
import pytest

pytestmark = pytest.mark.contract


def test_public_api_disabled_by_default(clean_env):
    """对外 /v1 API 默认关（零暴露面）。"""
    from hashmm.api.routes import public_api as P
    assert P.public_api_enabled() is False


def test_public_api_enable_switch(clean_env, monkeypatch):
    """开关生效。"""
    from hashmm.api.routes import public_api as P
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")
    assert P.public_api_enabled() is True


def test_public_api_auth_required_without_key(clean_env, monkeypatch):
    """无 API key / 非 open / 非 admin → 不授权。"""
    from hashmm.api.routes import public_api as P
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")

    class _Req:
        headers = {}
        query_params = {}
    assert P._authorized(_Req()) is False


def test_public_api_open_mode(clean_env, monkeypatch):
    """显式 open 模式放行（仅可信内网用）。"""
    from hashmm.api.routes import public_api as P
    monkeypatch.setenv("HASHMM_PUBLIC_API", "1")
    monkeypatch.setenv("HASHMM_PUBLIC_API_OPEN", "1")

    class _Req:
        headers = {}
        query_params = {}
    assert P._authorized(_Req()) is True


def test_mcp_server_disabled_by_default(clean_env):
    """MCP server 默认关。"""
    from hashmm.api.routes import mcp_server as M
    assert M.server_enabled() is False


def test_otlp_export_structure(clean_env):
    """OTLP/JSON 导出结构稳定（resourceSpans → scopeSpans → spans）。"""
    from hashmm import observability as obs
    obs.record_rag_request(model="t", input_tokens=10, output_tokens=5,
                           total_latency_ms=100, n_sources=2,
                           stage_latency_ms={"retrieve": 40, "generate": 60})
    out = obs.export_otlp_traces()
    assert "resourceSpans" in out
    rs = out["resourceSpans"]
    assert rs and "scopeSpans" in rs[0]
    spans = rs[0]["scopeSpans"][0]["spans"]
    assert spans
    s = spans[0]
    for field in ("traceId", "spanId", "startTimeUnixNano", "endTimeUnixNano", "attributes"):
        assert field in s


def test_otlp_export_empty_safe(clean_env):
    """空数据时导出不抛错。"""
    from hashmm import observability as obs
    obs.reset()
    out = obs.export_otlp_traces()
    assert "resourceSpans" in out


def test_dashboard_snapshot_structure(clean_env):
    """运维 dashboard 快照返回 dict，永不抛错。"""
    from hashmm import observability as obs
    snap = obs.dashboard_snapshot()
    assert isinstance(snap, dict)


# ══════════════════════ MCP Server 暴露端安全（V320.2 补测）══════════════════════
# 背景：MCP 暴露端把工具开放给外部客户端（Claude Code/Cursor 等）。此前只测了"默认关"，
# 没测最关键的【只暴露只读知识工具、危险工具进不来】。补齐安全回归护栏——将来有人误把
# run_shell/execute_code 加进 _EXECUTORS，这些测试会立刻红。

_MCP_DANGEROUS = {
    "run_shell", "execute_code", "create_file", "edit_file", "str_replace", "insert_lines",
    "read_file", "read_file_range", "list_files", "search_files", "file_tree", "file_versions",
    "file_restore", "clean_workspace", "fetch_url", "browser_open", "browser_act",
    "browser_read", "browser_screenshot", "create_document", "create_xlsx", "create_pdf",
    "create_pptx_from_plan", "convert_file", "pptx_edit_slide",
}
_MCP_ALLOWED = {"kb_search", "kg_query", "corpus_stats"}


def test_mcp_only_exposes_safe_readonly_tools(clean_env):
    """★ 安全护栏：MCP 暴露端绝不能暴露任何写/执行/文件/浏览器/网络类工具。"""
    from hashmm.api.routes import mcp_server as M
    exposed = set(M._EXECUTORS.keys())
    leaked = exposed & _MCP_DANGEROUS
    assert not leaked, f"MCP 暴露了危险工具（灾难级漏洞）: {leaked}"
    assert exposed <= _MCP_ALLOWED, f"MCP 暴露了白名单外的工具: {exposed - _MCP_ALLOWED}"
    # tools/list 声明的工具名必须与执行器一致（不能声明了却无执行器，或反之）
    listed = {t["name"] for t in M._TOOLS}
    assert listed == exposed, f"声明与执行器不一致: 声明{listed} vs 执行{exposed}"


def test_mcp_rejects_dangerous_tool_call(clean_env):
    """点名调用危险/未暴露工具 → JSON-RPC error，绝不触达执行（哪怕带恶意参数）。"""
    from hashmm.api.routes import mcp_server as M
    for evil in ("run_shell", "execute_code", "read_file", "create_file", "fetch_url"):
        r = M._handle("tools/call", {"name": evil, "arguments": {"x": "rm -rf /"}}, 1)
        assert "error" in r, f"危险工具 {evil} 未被拒绝！"
        assert "result" not in r


def test_mcp_protocol_shape(clean_env):
    """MCP JSON-RPC 协议结构稳定：initialize/tools/list/未知方法。"""
    from hashmm.api.routes import mcp_server as M
    init = M._handle("initialize", {}, 1)
    assert init["jsonrpc"] == "2.0"
    assert init["result"]["protocolVersion"]
    tl = M._handle("tools/list", {}, 2)
    names = {t["name"] for t in tl["result"]["tools"]}
    assert names == _MCP_ALLOWED
    # 每个工具都有 inputSchema（MCP 客户端据此生成调用界面）
    for t in tl["result"]["tools"]:
        assert "inputSchema" in t and "description" in t
    # 未知方法 → -32601
    err = M._handle("nonexistent/method", {}, 3)
    assert err["error"]["code"] == -32601


def test_mcp_auth_rejects_wrong_token(clean_env, monkeypatch):
    """设了 HASHMM_MCP_TOKEN 后：错误 token 不授权，正确 token 授权（常数时间比较）。"""
    from hashmm.api.routes import mcp_server as M
    monkeypatch.setenv("HASHMM_MCP_TOKEN", "secret-abc-123")

    class _Wrong:
        headers = {"Authorization": "Bearer wrong-token"}
        query_params = {}
    assert M._authorized(_Wrong()) is False

    class _Right:
        headers = {"Authorization": "Bearer secret-abc-123"}
        query_params = {}
    assert M._authorized(_Right()) is True


def test_mcp_batch_requests(clean_env):
    """批量 JSON-RPC：一次多条请求，逐条处理（MCP 客户端会用）。"""
    from hashmm.api.routes import mcp_server as M
    results = [M._handle(m["method"], m.get("params", {}), m["id"])
               for m in [{"method": "initialize", "id": 1},
                         {"method": "tools/list", "id": 2}]]
    assert len(results) == 2
    assert results[0]["id"] == 1 and results[1]["id"] == 2
