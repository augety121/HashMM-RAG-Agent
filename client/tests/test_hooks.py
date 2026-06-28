"""Hooks 多生命周期 测试。

覆盖：PreTool/PostTool/PreCompact/SubagentStop 注册触发、无注册零变化、坏hook容错、reset。
"""
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_hooks():
    """每个测试前后清空 hooks，保证独立。"""
    from hashmm import hooks as H
    H.reset_hooks()
    yield
    H.reset_hooks()


def test_no_hooks_is_noop():
    """无注册时所有 run_* 都是 no-op，不抛错（零行为变化）。"""
    from hashmm import hooks as H
    H.run_compact_hooks([{"role": "user", "content": "x"}])
    H.run_subagent_stop_hooks("t1", "result")
    # 不抛错即通过


def test_precompact_hook_fires_with_messages():
    """PreCompact hook 在压缩丢历史前触发，收到被丢弃的消息。"""
    from hashmm import hooks as H
    captured = {}
    H.register_compact_hook("archive", lambda msgs, ctx: captured.update(n=len(msgs), dropped=ctx.get("dropped")))
    H.run_compact_hooks([{"a": 1}, {"b": 2}, {"c": 3}], {"dropped": 3})
    assert captured["n"] == 3
    assert captured["dropped"] == 3


def test_subagent_stop_hook_fires_with_result():
    """SubagentStop hook 在子代理结束时触发，收到 result 和 status。"""
    from hashmm import hooks as H
    captured = {}
    H.register_subagent_stop_hook("agg", lambda sid, r, ctx: captured.update(id=sid, result=r, status=ctx.get("status")))
    H.run_subagent_stop_hooks("step_1", "分析完成", {"status": "done"})
    assert captured == {"id": "step_1", "result": "分析完成", "status": "done"}


def test_bad_hook_does_not_break_main_flow():
    """坏 hook 抛异常时被吞掉，不影响主流程（永不抛错）。"""
    from hashmm import hooks as H
    H.register_compact_hook("bad", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    # 不应抛出
    H.run_compact_hooks([{"x": 1}])


def test_reset_clears_all_lifecycles():
    """reset_hooks 清空所有生命周期 hook。"""
    from hashmm import hooks as H
    H.register_compact_hook("c", lambda *a: None)
    H.register_subagent_stop_hook("s", lambda *a: None)
    H.reset_hooks()
    assert len(H._PRECOMPACT_HOOKS) == 0
    assert len(H._SUBAGENT_STOP_HOOKS) == 0
