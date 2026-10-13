"""P3 打磨：统一错误码 + trace 上下文 测试。"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.error_codes") is None or _ilu.find_spec("hashmm.trace_context") is None:
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="P3 模块未部署（旧版本代码）")]


# ── P3-1 错误码 ──

def test_exception_to_response():
    """HashMMError 转标准错误响应（含 HTTP 状态 + 稳定 code）。"""
    from hashmm import error_codes as EC
    from hashmm.exceptions import LLMError
    status, body = EC.to_error_response(LLMError("超时", model="x"))
    assert status == 502
    assert body["error"]["code"] == "llm_error"
    assert body["error"]["category"] == "upstream"


def test_unknown_exception_is_internal():
    """非 HashMMError 归为 internal_error / 500。"""
    from hashmm import error_codes as EC
    status, body = EC.to_error_response(ValueError("x"))
    assert status == 500
    assert body["error"]["code"] == "internal_error"


def test_http_status_mapping():
    """code → HTTP 状态映射正确。"""
    from hashmm import error_codes as EC
    assert EC.http_status_for("auth_error") == 401
    assert EC.http_status_for("not_found") == 404
    assert EC.http_status_for("rate_limited") == 429


def test_trace_id_in_error():
    """trace_id 注入错误体。"""
    from hashmm import error_codes as EC
    from hashmm.exceptions import RetrievalError
    _, body = EC.to_error_response(RetrievalError("x"), trace_id="tid123")
    assert body["error"]["trace_id"] == "tid123"


def test_error_codes_doc():
    """错误码文档可生成且含关键 code。"""
    from hashmm import error_codes as EC
    md = EC.generate_error_codes_md()
    assert "llm_error" in md
    assert "auth_error" in md


# ── S3 可观测闭环：错误码计数 ──

def test_error_count_recorded():
    """S3-2：to_error_response 触发错误码计数（含 trace_id）。"""
    from hashmm import observability as obs, error_codes as EC
    from hashmm.exceptions import LLMError
    obs.reset()
    EC.to_error_response(LLMError("x"), trace_id="t1")
    EC.to_error_response(LLMError("y"), trace_id="t2")
    es = obs.error_stats()
    assert es["total"] == 2
    assert es["by_code"]["llm_error"]["count"] == 2
    assert es["by_code"]["llm_error"]["last_trace_id"] == "t2"
    obs.reset()


def test_errors_in_dashboard():
    """S3-1：错误码统计纳入 dashboard_snapshot。"""
    from hashmm import observability as obs, error_codes as EC
    from hashmm.exceptions import AuthError
    obs.reset()
    EC.to_error_response(AuthError(), trace_id="t9")
    snap = obs.dashboard_snapshot()
    assert "errors" in snap
    assert snap["errors"]["total"] == 1
    obs.reset()


# ── P3-2 trace 上下文 ──

def test_new_trace_and_current():
    """new_trace 生成并可读取。"""
    from hashmm import trace_context as TC
    tid = TC.new_trace()
    assert tid
    assert TC.current_trace_id() == tid


def test_trace_passthrough():
    """透传上游 trace_id。"""
    from hashmm import trace_context as TC
    tid = TC.new_trace("upstream-abc")
    assert tid == "upstream-abc"


def test_trace_context_manager():
    """trace_context 进入设置、退出还原。"""
    from hashmm import trace_context as TC
    TC.new_trace("outer")
    with TC.trace_context("inner"):
        assert TC.current_trace_id() == "inner"
    assert TC.current_trace_id() == "outer"


def test_with_trace_injection():
    """with_trace 给日志 dict 注入 trace_id。"""
    from hashmm import trace_context as TC
    TC.new_trace("xyz")
    rec = TC.with_trace({"event": "x"})
    assert rec["trace_id"] == "xyz"
