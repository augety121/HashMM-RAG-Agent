"""V52 mini Agent-Bench 的回归保护。

锁住三件事：
1. 自检（脚本化 LLM 端到端）必须 3/3 全过——runner、scorer、多轮、事件捕获都在链路上；
2. 评分器的【负例】：该挂的时候必须挂（否则通过率是假的）；
3. requires/SKIP 语义与报告结构稳定。
"""
import pytest

pytestmark = pytest.mark.unit


def test_selftest_all_pass():
    from hashmm.tools.agent_bench import run_selftest
    report = run_selftest()
    # V308：不再硬编码用例数（原断言 ran==3，但实现已增至 6 个：4 个脚本化任务
    # + stream_delta + step_eval 两个纯函数回归）。这类硬编码每加一个自检就得改测试，
    # 是测试漂移的温床。改为断言【语义属性】：确有用例在跑、且全部通过。
    assert report["ran"] >= 3, f"自检用例数异常偏少: {report['ran']}"
    assert report["passed"] == report["ran"], report["results"]
    assert report["pass_rate"] == 1.0


def test_scorers_fail_when_they_should():
    """负例：评分器不是橡皮图章。"""
    from hashmm.tools.agent_bench import (
        Task, ScriptedLLM, run_task, file_exists, file_contains,
        answer_contains, tool_used, todo_emitted,
    )
    # 脚本只创建 a.py 并回答"完成"，但任务要求 b.py / str_replace / todo / 特定关键词
    task = Task("st_neg", "自检", turns=["做事"], requires=set(),
                scorers=[file_exists("b.py"),
                         file_contains("a.py", "不存在的内容"),
                         tool_used("str_replace"),
                         todo_emitted(2),
                         answer_contains("这个词不会出现")])
    llm = ScriptedLLM([("tool", "create_file",
                        '{"filename": "a.py", "content": "x = 1\\n"}'),
                       ("say", "完成。")])
    r = run_task(task, llm)
    assert r["status"] == "FAIL"
    assert len(r["failures"]) == 5, r["failures"]   # 五个评分器全部正确报告失败


def test_requires_skip_semantics():
    """环境不满足 → SKIP 且不计入通过率分母。"""
    from hashmm.tools.agent_bench import Task, run_bench, answer_nonempty

    class _NoToolsLLM:
        pass  # 没有 call_with_tools → 不具备 llm 能力

    tasks = [Task("needs_magic", "自检", turns=["x"], requires={"llm", "magic"},
                  scorers=[answer_nonempty()])]
    report = run_bench(_NoToolsLLM(), tasks=tasks)
    assert report["results"][0]["status"] == "SKIP"
    assert "magic" in str(report["results"][0]["failures"])
    assert report["ran"] == 0 and report["pass_rate"] == 0.0


def test_report_structure():
    from hashmm.tools.agent_bench import run_selftest
    report = run_selftest()
    for r in report["results"]:
        assert {"id", "category", "status", "failures", "elapsed_s"} <= set(r)
        assert r["status"] in ("PASS", "FAIL", "ERROR", "SKIP")


def test_real_task_definitions_sane():
    """真实任务表的基本健康：id 唯一、轮次非空、评分器非空。"""
    from hashmm.tools.agent_bench import TASKS
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids))
    assert len(TASKS) >= 8
    for t in TASKS:
        assert t.turns and t.scorers, t.id


# ── V52 附加：token 核算 ──

def test_loop_accumulates_real_usage():
    """LLM 响应带 _hashmm_usage 时，done 事件透出累计真值。"""
    import asyncio as _aio
    from hashmm.agent.loop import AgentLoop

    class _Msg:
        def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""

    class _Resp:
        def __init__(s, m, usage): s.message = m; s._hashmm_usage = usage

    class _LLM:
        def __init__(s): s.calls = 0
        def call_with_tools(s, messages, tools=None):
            s.calls += 1
            return _Resp(_Msg(c="答案。"), {"prompt_tokens": 100, "completion_tokens": 7})

    async def _run():
        loop = AgentLoop(llm_fn=_LLM(), system_prompt="助手", user_id="u", conv_id="cUsage")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = _aio.run(_run())
    done = [ed for et, ed in events if et == "done"][-1]
    assert done["usage"]["prompt_tokens"] == 100
    assert done["usage"]["completion_tokens"] == 7
    assert done["usage"]["total_tokens"] == 107


def test_loop_usage_absent_is_none():
    """LLM 不带 usage（本地模型等）→ done.usage 为 None，不编造。"""
    import asyncio as _aio
    from hashmm.agent.loop import AgentLoop

    class _Msg:
        def __init__(s, c=""): s.content, s.tool_calls, s.reasoning_content = c, None, ""

    class _Resp:
        def __init__(s, m): s.message = m

    class _LLM:
        def call_with_tools(s, messages, tools=None): return _Resp(_Msg(c="答。"))

    async def _run():
        loop = AgentLoop(llm_fn=_LLM(), system_prompt="助手", user_id="u", conv_id="cUsage2")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = _aio.run(_run())
    done = [ed for et, ed in events if et == "done"][-1]
    assert done["usage"] is None
