"""V56 harness 工程回归：守卫管线 / Hooks / 瞬态重试 / 运行遥测。

重构铁律：V49-V53 的全部防御语义逐字迁入守卫——既有 165 个测试是行为冻结层，
本文件只测新抽象本身（每个守卫独立可测、顺序显式、扩展点可用）。
"""
import asyncio
import json
import os

import pytest

pytestmark = pytest.mark.unit


def _mk_state(**kw):
    from hashmm.agent.tool_pipeline import TurnState
    return TurnState(**kw)


# ── 1. 守卫单测 ──

def test_exec_budget_guard():
    from hashmm.agent.tool_pipeline import ExecBudgetGuard, MAX_EXEC_CALLS
    g = ExecBudgetGuard()
    assert g.check("execute_code", {}, ("execute_code", "{}"),
                   _mk_state(exec_calls=MAX_EXEC_CALLS - 1)) is None       # 额度内放行
    d = g.check("execute_code", {}, ("execute_code", "{}"),
                _mk_state(exec_calls=MAX_EXEC_CALLS))
    assert d is not None and d.result["status"] == "denied" and "上限" in d.result["message"]
    assert g.check("kb_search", {}, None, _mk_state(exec_calls=99)) is None  # 非 exec 工具不管


def test_search_budget_guard_off_by_one_semantics():
    """计数在循环顶【先】加 → 守卫用 >（第 N 次调用时 search_calls 已是 N）。"""
    from hashmm.agent.tool_pipeline import SearchBudgetGuard, MAX_SEARCH_CALLS
    g = SearchBudgetGuard()
    assert g.check("kb_search", {}, None, _mk_state(search_calls=MAX_SEARCH_CALLS)) is None
    d = g.check("kb_search", {}, None, _mk_state(search_calls=MAX_SEARCH_CALLS + 1))
    assert d is not None and "检索次数已达本轮上限" in d.result["message"]


def test_dedup_guard_consecutive_only():
    from hashmm.agent.tool_pipeline import ConsecutiveDedupGuard
    g = ConsecutiveDedupGuard()
    key = ("read_file", '{"f": "a"}')
    st = _mk_state(last_call_key=key, last_result_text="旧结果ABC")
    d = g.check("read_file", {}, key, st)
    assert d is not None and d.result["status"] == "ok" and "旧结果ABC" in d.result["message"]
    # 不同 key（中间隔了别的调用）→ 放行
    assert g.check("read_file", {}, ("read_file", '{"f": "b"}'), st) is None


def test_permission_guard():
    from hashmm.agent.tool_pipeline import PermissionGuard

    class _Perms:
        def check(self, name, args, user_id):
            return (name != "danger"), "高危工具"

    g = PermissionGuard()
    assert g.check("kb_search", {}, None, _mk_state(), permissions=_Perms()) is None
    d = g.check("danger", {}, None, _mk_state(), permissions=_Perms())
    assert d is not None and "权限不足" in d.result["message"]
    assert g.check("danger", {}, None, _mk_state(), permissions=None) is None  # 无权限系统=放行


# ── 2. 管线：顺序、守卫异常容错、Hooks ──

def test_pipeline_order_permission_first():
    """权限在预算之前：高危且超预算时，给模型的理由应是权限。"""
    from hashmm.agent.tool_pipeline import ToolPipeline, MAX_EXEC_CALLS

    class _DenyAll:
        def check(self, name, args, user_id):
            return False, "全拒"

    p = ToolPipeline()
    d = p.evaluate("execute_code", {}, ("execute_code", "{}"),
                   _mk_state(exec_calls=MAX_EXEC_CALLS), permissions=_DenyAll())
    assert d.guard == "permission"


def test_pipeline_guard_exception_never_blocks():
    from hashmm.agent.tool_pipeline import ToolPipeline

    class _Boom:
        name = "boom"
        def check(self, *a, **k):
            raise RuntimeError("守卫崩了")

    p = ToolPipeline(guards=[_Boom()])
    assert p.evaluate("x", {}, None, _mk_state()) is None  # harness 故障不放大


def test_hooks_pre_short_circuit_and_post_observe():
    from hashmm.agent import tool_pipeline as TP
    pre_bak, post_bak = list(TP.PRE_TOOL_HOOKS), list(TP.POST_TOOL_HOOKS)
    try:
        TP.PRE_TOOL_HOOKS.clear(); TP.POST_TOOL_HOOKS.clear()
        seen = []
        TP.register_pre_tool_hook(lambda n, a: {"status": "denied", "message": "钩子拦下"}
                                  if n == "blocked" else None)
        TP.register_post_tool_hook(lambda n, a, r: seen.append((n, r.get("status"))))
        TP.register_post_tool_hook(lambda n, a, r: 1 / 0)   # 异常钩子必须被吞
        p = TP.ToolPipeline()
        d = p.evaluate("blocked", {}, None, _mk_state())
        assert d is not None and d.guard == "pre_hook" and "钩子拦下" in d.result["message"]
        assert p.evaluate("normal", {}, None, _mk_state()) is None
        p.notify_post("normal", {}, {"status": "ok"})
        assert seen == [("normal", "ok")]
    finally:
        TP.PRE_TOOL_HOOKS[:] = pre_bak
        TP.POST_TOOL_HOOKS[:] = post_bak


# ── 3. 瞬态错误重试（循环集成） ──

def test_transient_error_retried_once():
    from hashmm.agent.loop import AgentLoop

    class _Fn:
        def __init__(s, n, a): s.name, s.arguments = n, a

    class _TC:
        def __init__(s, n, a, id="t1"): s.id, s.type, s.function = id, "function", _Fn(n, a)

    class _Msg:
        def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""

    class _Resp:
        def __init__(s, m): s.message = m

    class _L:
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tc=[_TC("fetch_url", '{"url": "http://x"}')]))
            return _Resp(_Msg(c="拿到了。"))

    attempts = []

    class _Flaky(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            attempts.append(name)
            if len(attempts) == 1:
                raise TimeoutError("connection timed out")   # 瞬态
            return {"status": "ok", "message": "页面内容"}

    async def _go():
        loop = _Flaky(llm_fn=_L(), system_prompt="助手", user_id="u", conv_id="cRetry")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_go())
    assert len(attempts) == 2                                   # 重试了恰好一次
    dones = [ed for et, ed in events if et == "tool_done"]
    assert dones[0]["status"] == "done"                          # 第二次成功
    assert "拿到了" in "".join(ed for et, ed in events if et == "token")


def test_permanent_error_not_retried():
    from hashmm.agent.loop import AgentLoop

    class _Fn:
        def __init__(s, n, a): s.name, s.arguments = n, a

    class _TC:
        def __init__(s, n, a, id="t1"): s.id, s.type, s.function = id, "function", _Fn(n, a)

    class _Msg:
        def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""

    class _Resp:
        def __init__(s, m): s.message = m

    class _L:
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tc=[_TC("fetch_url", '{"url": "http://x"}')]))
            return _Resp(_Msg(c="好的。"))

    attempts = []

    class _Broken(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            attempts.append(name)
            raise ValueError("参数非法")    # 永久性错误

    async def _go():
        loop = _Broken(llm_fn=_L(), system_prompt="助手", user_id="u", conv_id="cPerm")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_go())
    assert len(attempts) == 1                                    # 不重试
    dones = [ed for et, ed in events if et == "tool_done"]
    assert dones[0]["status"] == "error"


def test_is_transient_classifier():
    from hashmm.agent.tool_pipeline import is_transient_error
    assert is_transient_error(TimeoutError("x"))
    assert is_transient_error(ConnectionError("x"))
    assert is_transient_error(RuntimeError("Read timed out after 30s"))
    assert is_transient_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert not is_transient_error(ValueError("filename 缺失"))


# ── 4. 运行遥测 ──

def test_run_record_off_by_default(tmp_path, monkeypatch):
    from hashmm.agent.run_record import RunRecord
    monkeypatch.delenv("HASHMM_AGENT_TRACE", raising=False)
    monkeypatch.setenv("HASHMM_TRACE_DIR", str(tmp_path))
    rec = RunRecord("c1", "查询")
    rec.add("tool_done", name="kb_search")
    assert rec.flush("done") is None
    assert not list(tmp_path.glob("*.jsonl"))


def test_run_record_writes_jsonl_when_enabled(tmp_path, monkeypatch):
    from hashmm.agent.run_record import RunRecord
    monkeypatch.setenv("HASHMM_AGENT_TRACE", "1")
    monkeypatch.setenv("HASHMM_TRACE_DIR", str(tmp_path))
    rec = RunRecord("c2", "帮我写个红黑树")
    rec.add("tool_done", name="create_file", status="done", ms=42)
    path = rec.flush("done", iterations=2)
    assert path is not None and path.exists()
    line = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["conv_id"] == "c2" and line["status"] == "done"
    assert line["events"][0]["name"] == "create_file"
    assert line["iterations"] == 2


# ── 5. 常量迁移兼容 + bench 阈值跟随 ──

def test_constants_reexported_from_loop():
    """既有 import 路径全部保持可用（loop 侧 re-export）。"""
    from hashmm.agent.loop import (MAX_TOOL_CALLS, MAX_SEARCH_CALLS,   # noqa: F401
                                   MAX_EXEC_CALLS, _SEARCH_TOOLS, _EXEC_TOOLS)
    from hashmm.agent import tool_pipeline as TP
    assert MAX_EXEC_CALLS == TP.MAX_EXEC_CALLS
    assert MAX_TOOL_CALLS == TP.MAX_TOOL_CALLS


def test_bench_budget_tracks_constants():
    from hashmm.tools.agent_bench import TASKS
    from hashmm.agent.loop import MAX_EXEC_CALLS, MAX_SEARCH_CALLS
    exec_task = next(t for t in TASKS if t.id == "exec_budget_guard")
    names = [getattr(sc, "__name__", "") for sc in exec_task.scorers]
    assert f"tool_calls_at_most(execute_code,{MAX_EXEC_CALLS})" in names
    search_task = next(t for t in TASKS if t.id == "search_budget")
    names2 = [getattr(sc, "__name__", "") for sc in search_task.scorers]
    assert f"tool_calls_at_most(kb_search,{MAX_SEARCH_CALLS})" in names2


# ── V58 附加：Worker 子代理接入同一条守卫管线 ──

def test_worker_uses_guard_pipeline_dedup_and_budget():
    """worker 与主循环同一套守卫：连续重复去重生效、search 预算生效。"""
    import asyncio as _aio
    from hashmm.agent.worker import Worker
    from hashmm.agent.tool_pipeline import MAX_SEARCH_CALLS

    class _Fn:
        def __init__(s, n, a): s.name, s.arguments = n, a

    class _TC:
        def __init__(s, n, a, id): s.id, s.type, s.function = id, "function", _Fn(n, a)

    class _SpamLLM:
        """第1轮：同参数连发2次（第2次该被去重）+ 不同参数连发4次（超额该被预算拦）。"""
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            if s.calls == 1:
                tcs = [_TC("kb_search", '{"query": "营收"}', "a1"),
                       _TC("kb_search", '{"query": "营收"}', "a2")]
                tcs += [_TC("kb_search", f'{{"query": "其他{i}"}}', f"b{i}") for i in range(4)]
                return _Resp(_Msg(tc=tcs))
            return _Resp(_Msg(c="结论。"))

    class _Msg:
        def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""

    class _Resp:
        def __init__(s, m): s.message = m

    w = Worker(_SpamLLM(), role="research", user_id="u")
    executed = []

    async def _fake_exec(name, args):
        executed.append(args)
        return "片段"
    w._exec = _fake_exec

    res = _aio.run(w.run("查营收"))
    # 同参重复的第2次被去重 + search 预算封顶 → 真执行 ≤ MAX_SEARCH_CALLS
    assert len(executed) <= MAX_SEARCH_CALLS, f"真执行 {len(executed)}"
    assert res["tool_calls"] == 6                       # 调用都被记账（透明）
    assert len(res["steps"]) == 6                       # 轨迹完整
