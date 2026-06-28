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
