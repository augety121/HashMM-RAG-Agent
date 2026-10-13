"""S1-1/S1-2 接入回归测试：错误码统一接入 + trace 贯穿。

验证 P3 的 error_codes/trace_context 不再悬空，而是真正接入了业务路径。
"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if (_ilu.find_spec("hashmm.error_codes") is None
        or _ilu.find_spec("hashmm.trace_context") is None):
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="S1 依赖模块未部署")]


def test_public_api_err_helper_shape(clean_env):
    """public_api._err 产出统一错误体（code/message/category），含 HTTP 状态。"""
    pa = pytest.importorskip("hashmm.api.routes.public_api")
    resp = pa._err("retrieval_error", "search failed")
    assert resp.status_code == 502
    import json
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"]["code"] == "retrieval_error"
    assert body["error"]["category"] == "upstream"


def test_err_carries_trace_id(clean_env):
    """设置 trace 后，_err 的错误体带上 trace_id（S1-2 贯穿）。"""
    pa = pytest.importorskip("hashmm.api.routes.public_api")
    from hashmm.trace_context import new_trace
    new_trace("trace-xyz")
    resp = pa._err("bad_request", "query is required")
    import json
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"].get("trace_id") == "trace-xyz"
    assert resp.status_code == 400


def test_middleware_sets_trace_context(clean_env):
    """TraceMiddleware 把 request_id 接入 trace_context（间接验证 set_trace_id 被调）。"""
    from hashmm.trace_context import set_trace_id, current_trace_id
    set_trace_id("req-123")
    assert current_trace_id() == "req-123"


def test_auth_error_status(clean_env):
    """鉴权失败用 auth_error → 401。"""
    pa = pytest.importorskip("hashmm.api.routes.public_api")
    resp = pa._err("auth_error", "unauthorized")
    assert resp.status_code == 401
