"""V55：file_delta 右栏逐字直播 + 上下文用量表 + 子任务真·流式 + 智能化长任务。

契约：
- file_delta：流式开关下，create_file 的参数增量被解析为 (filename, 新增内容) 并以
  ("file_delta", {filename, t}) 事件直播；JSON 转义正确还原；半截转义不丢字不报错；
  权威落盘流程（tool_call_end → 执行 → file 事件）完全不受影响。
- ctx：每轮发 ("ctx", {chars, budget})，前端据此显示"上下文 n%"。
- 子任务流式：spawn_worker 执行期间，worker 每步实时产出 sub_agent trace（不再等跑完）。
- 智能化：行为准则进系统提示；长任务上限提升（迭代/工具次数），防御体系兜底。
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.unit


# ── 1. file_delta 增量提取（纯函数） ──

def test_extract_file_delta_incremental():
    from hashmm.agent.loop import _extract_file_delta
    # 分片到达：filename 完整后才开始给 content
    buf = '{"filename": "a.py", "content": "def f():'
    fn, new = _extract_file_delta(buf, 0)
    assert fn == "a.py" and new == "def f():"
    # 续片含转义换行；already 按未转义字符计
    buf2 = buf + '\\n    return 1'
    fn2, new2 = _extract_file_delta(buf2, len(new))
    assert fn2 == "a.py" and new2 == "\n    return 1"


def test_extract_file_delta_partial_escape_safe():
    """半截转义（\\u 截一半、孤立反斜杠）不消费、不丢字、不抛错。"""
    from hashmm.agent.loop import _extract_file_delta
    fn, new = _extract_file_delta('{"filename": "b.txt", "content": "你好\\u54', 0)
    assert fn == "b.txt" and new == "你好"           # \\u54 不完整 → 留待下一片
    fn3, new3 = _extract_file_delta('{"filename": "b.txt", "content": "abc\\', 0)
    assert fn3 == "b.txt" and new3 == "abc"
    assert _extract_file_delta("垃圾不是json", 0) == ("", "")
    assert _extract_file_delta('{"content": "无文件名"', 0) == ("", "")


# ── 2. 流式循环里的 file_delta 直播 ──

class _Fn:
    def __init__(s, n, a): s.name, s.arguments = n, a


class _TC:
    def __init__(s, n, a, id="t1"): s.id, s.type, s.function = id, "function", _Fn(n, a)


class _Msg:
    def __init__(s, content="", tool_calls=None):
        s.content, s.tool_calls, s.reasoning_content = content, tool_calls, ""


class _Resp:
    def __init__(s, m): s.message = m


class _FileStreamLLM:
    """第1轮流式发 create_file（参数分片），第2轮普通文本收尾。"""

    ARGS = '{"filename": "live.py", "content": "x = 1\\ny = 2\\n"}'

    def __init__(s):
        s.turn = 0

    def call_with_tools(s, messages, tools=None):
        return _Resp(_Msg(content="（不应走到非流式）"))

    def stream_with_tools(s, messages, tools=None):
        s.turn += 1
        if s.turn == 1:
            yield {"type": "text", "content": "正在创建文件。"}
            yield {"type": "tool_call_start", "id": "fc1", "name": "create_file"}
            for i in range(0, len(s.ARGS), 7):   # 7 字符一片，制造各种半截
                yield {"type": "tool_call_delta", "id": "fc1", "args_partial": s.ARGS[i:i + 7]}
            yield {"type": "tool_call_end", "id": "fc1", "name": "create_file", "args": s.ARGS}
            yield {"type": "done", "finish_reason": "tool_calls"}
        else:
            yield {"type": "text", "content": "完成。"}
            yield {"type": "done", "finish_reason": "stop"}


def test_file_delta_streams_and_authoritative_flow_intact():
    from hashmm.agent.loop import AgentLoop

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append((name, dict(args)))
            return {"status": "ok", "message": "saved"}

    async def _go():
        loop = _Counting(llm_fn=_FileStreamLLM(), system_prompt="助手",
                         user_id="u", conv_id="cFD")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    old = os.environ.get("HASHMM_AGENT_STREAM")
    try:
        os.environ["HASHMM_AGENT_STREAM"] = "1"
        events = asyncio.run(_go())
    finally:
        if old is None:
            os.environ.pop("HASHMM_AGENT_STREAM", None)
        else:
            os.environ["HASHMM_AGENT_STREAM"] = old

    fds = [ed for et, ed in events if et == "file_delta"]
    assert fds, "应有 file_delta 直播事件"
    assert all(d["filename"] == "live.py" for d in fds)
    assert "".join(d["t"] for d in fds) == "x = 1\ny = 2\n"   # 拼起来=完整内容（转义已还原）
    # 权威流程不受影响：create_file 真执行且参数完整
    assert executed and executed[0][0] == "create_file"
    assert executed[0][1]["content"] == "x = 1\ny = 2\n"
    assert "完成" in "".join(ed for et, ed in events if et == "token")


# ── 3. 上下文用量事件 ──

def test_ctx_usage_event_each_iteration():
    from hashmm.agent.loop import AgentLoop, MAX_CONTEXT_CHARS

    class _L:
        def call_with_tools(s, m, t=None): return _Resp(_Msg(content="答。"))

    async def _go():
        loop = AgentLoop(llm_fn=_L(), system_prompt="助手", user_id="u", conv_id="cCtx")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_go())
    ctxs = [ed for et, ed in events if et == "ctx"]
    assert ctxs and ctxs[0]["budget"] == MAX_CONTEXT_CHARS
    assert ctxs[0]["chars"] > 0


# ── 4. 子任务真·流式 ──

def test_spawn_worker_streams_sub_steps():
    """worker 执行期间逐步产出 sub_agent trace，结束后照常出 tool_done。"""
    from hashmm.agent.loop import AgentLoop

    class _SpawnLLM:
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tool_calls=[_TC("spawn_worker", '{"task": "查资料"}')]))
            return _Resp(_Msg(content="汇总完成。"))

    class _FakeStreamWorker(AgentLoop):
        async def _spawn_worker_streaming(self, func_args, user_id, on_step):
            on_step("kb_search(腾讯营收)")
            await asyncio.sleep(0)            # 让事件循环转一圈
            on_step("kb_search(网易营收)")
            return {"status": "ok", "message": "[专员完成] 结论如下"}

    async def _go():
        loop = _FakeStreamWorker(llm_fn=_SpawnLLM(), system_prompt="助手",
                                 user_id="u", conv_id="cSub")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_go())
    subs = [ed for et, ed in events if et == "trace" and ed.get("node") == "sub_agent"]
    assert len(subs) == 2 and "腾讯营收" in subs[0]["detail"]
    dones = [ed for et, ed in events if et == "tool_done"]
    assert dones and dones[0]["name"] == "spawn_worker" and dones[0]["status"] == "done"
    # 子步骤先于 tool_done 出现（真流式，不是事后补发）
    idx_sub = [i for i, (et, ed) in enumerate(events)
               if et == "trace" and ed.get("node") == "sub_agent"]
    idx_done = [i for i, (et, _) in enumerate(events) if et == "tool_done"]
    assert idx_sub[-1] < idx_done[0]


def test_worker_run_invokes_on_step():
    """Worker.run 的 on_step 回调在每次工具调用时被实时触发。"""
    import asyncio as _aio
    from hashmm.agent.worker import Worker

    class _WLLM:
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tool_calls=[_TC("kb_search", '{"query": "营收"}', id="w1")]))
            return _Resp(_Msg(content="结论。"))

    w = Worker(_WLLM(), role="research", user_id="u")

    async def _fake_exec(name, args):
        return "片段"
    w._exec = _fake_exec

    seen = []
    res = _aio.run(w.run("查营收", on_step=seen.append))
    assert seen and seen[0].startswith("kb_search(")
    assert res["steps"] == seen


# ── 5. 智能化与长任务 ──

def test_long_task_ceilings_raised():
    from hashmm.agent.loop import MAX_ITERATIONS, MAX_TOOL_CALLS, MAX_EXEC_CALLS
    assert MAX_ITERATIONS >= 10
    assert MAX_TOOL_CALLS >= 20
    assert MAX_EXEC_CALLS >= 5


def test_system_prompt_has_proactive_principles():
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cSmart")
    sys_content = loop._build_messages("q", [], "")[0]["content"]
    assert "多想一步" in sys_content
    assert "早前对话摘要" in sys_content     # 教模型利用长对话压缩块
