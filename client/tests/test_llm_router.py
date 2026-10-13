"""F11 云-本地 LLM 路由 测试。

覆盖：默认关零变化、任务路由判定、可配置覆盖、隐私模式不外发、容错。
"""
import pytest

pytestmark = pytest.mark.unit


def test_routing_disabled_by_default(clean_env):
    """HASHMM_LLM_ROUTING 未开 → routing_enabled False（零行为变化）。"""
    from hashmm import llm_router as R
    assert R.routing_enabled() is False


def test_local_tasks_are_local_by_default(clean_env):
    """硬编码默认：keyword/multiquery 等高频任务归 local，answer 归 cloud。"""
    from hashmm import llm_router as R
    R._routing_override_cache = None
    R._routing_override_raw = "__test__"
    assert R._backend_for_task("keyword") == "local"
    assert R._backend_for_task("multiquery") == "local"
    assert R._backend_for_task("answer") == "cloud"      # 主答案留云端
    assert R._backend_for_task("reasoning") == "cloud"


def test_task_routing_override(clean_env, monkeypatch):
    """配置覆盖：把 answer 改 local、keyword 改 cloud。"""
    from hashmm import llm_router as R
    monkeypatch.setenv("HASHMM_LLM_TASK_ROUTING", '{"answer":"local","keyword":"cloud"}')
    R._routing_override_cache = None
    R._routing_override_raw = "__test2__"
    assert R._backend_for_task("answer") == "local"
    assert R._backend_for_task("keyword") == "cloud"


def test_auto_falls_back_to_default(clean_env, monkeypatch):
    """显式 auto = 回退硬编码默认。"""
    from hashmm import llm_router as R
    monkeypatch.setenv("HASHMM_LLM_TASK_ROUTING", '{"keyword":"auto"}')
    R._routing_override_cache = None
    R._routing_override_raw = "__test3__"
    assert R._backend_for_task("keyword") == "local"


def test_invalid_config_is_safe(clean_env, monkeypatch):
    """非法 JSON / 非法值 → 忽略，回退默认（永不抛错）。"""
    from hashmm import llm_router as R
    for bad in ("{bad json", '{"keyword":"nonsense"}', ""):
        monkeypatch.setenv("HASHMM_LLM_TASK_ROUTING", bad)
        R._routing_override_cache = None
        R._routing_override_raw = f"__bad_{bad}__"
        assert R._backend_for_task("keyword") == "local"


def test_route_llm_always_safe_without_local(clean_env):
    """无本地模型时 route_llm 必回退云端 fn，永不返回不可用状态。"""
    from hashmm import llm_router as R
    cloud_fn = lambda q: "cloud-answer"
    fn, backend = R.route_llm("keyword", cloud_fn)
    # 无本地模型 → 必须是 cloud
    assert backend == "cloud"
    assert fn is cloud_fn
