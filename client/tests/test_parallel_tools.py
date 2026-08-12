"""P1-2 并行工具执行测试（对标 Codex FuturesOrdered）。

覆盖：默认关、只读才并发、含副作用串行、保序、容错。
"""
import asyncio

import pytest

import importlib.util as _ilu
pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.agent.parallel_tools") is None:
    import pytest as _pt
    pytestmark = [pytest.mark.unit, _pt.mark.skip(reason="hashmm.agent.parallel_tools 未部署（旧版本代码）")]


class _TC:
    def __init__(self, name, args="{}"):
        self.function = type("F", (), {"name": name, "arguments": args})()


def test_disabled_by_default(clean_env):
    """HASHMM_PARALLEL_TOOLS 未开 → 不并发（零变化）。"""
    from hashmm.agent import parallel_tools as PT
    assert PT.parallel_enabled() is False
    assert PT.should_parallelize([_TC("kb_search"), _TC("kg_query")]) is False


def test_parallel_only_readonly(clean_env, monkeypatch):
    """开启后：全只读且 ≥2 才并发。"""
    from hashmm.agent import parallel_tools as PT
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS", "1")
    assert PT.should_parallelize([_TC("kb_search"), _TC("kg_query")]) is True


def test_side_effect_tools_stay_serial(clean_env, monkeypatch):
    """含副作用工具（create_file/execute_code）→ 不并发（串行更安全）。"""
    from hashmm.agent import parallel_tools as PT
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS", "1")
    assert PT.should_parallelize([_TC("kb_search"), _TC("create_file")]) is False
    assert PT.should_parallelize([_TC("execute_code"), _TC("kb_search")]) is False


def test_network_and_pre_hook_calls_never_prefetch_before_guard(clean_env, monkeypatch):
    """Strict-mode network approval and pre-hooks must run before execution."""
    from hashmm.agent import parallel_tools as PT
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS", "1")
    assert PT.should_parallelize([_TC("web_search"), _TC("web_search")]) is False
    assert PT.should_parallelize(
        [_TC("kb_search"), _TC("kg_query")], pre_hooks_active=True
    ) is False


def test_single_tool_serial(clean_env, monkeypatch):
    """单个工具不并发。"""
    from hashmm.agent import parallel_tools as PT
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS", "1")
    assert PT.should_parallelize([_TC("kb_search")]) is False


def test_results_ordered(clean_env):
    """并发执行但结果严格保序（对标 FuturesOrdered）。"""
    from hashmm.agent import parallel_tools as PT
    calls = [_TC("kb_search"), _TC("kg_query"), _TC("web_search")]

    async def _ex(tc):
        # 故意让先发起的睡更久，验证不是靠完成顺序而是靠输入顺序
        delays = {"kb_search": 0.03, "kg_query": 0.02, "web_search": 0.01}
        await asyncio.sleep(delays.get(tc.function.name, 0))
        return {"name": tc.function.name}

    res = asyncio.run(PT.run_tools_ordered(calls, _ex))
    assert [r["name"] for r in res] == ["kb_search", "kg_query", "web_search"]


def test_failure_isolated(clean_env):
    """单个工具失败被隔离成 error，不影响其他结果，仍保序。"""
    from hashmm.agent import parallel_tools as PT
    calls = [_TC("kb_search"), _TC("kg_query")]

    async def _ex(tc):
        if tc.function.name == "kb_search":
            raise RuntimeError("boom")
        return {"name": tc.function.name}

    res = asyncio.run(PT.run_tools_ordered(calls, _ex))
    assert res[0]["status"] == "error"      # 失败的被隔离
    assert res[1]["name"] == "kg_query"      # 另一个正常
