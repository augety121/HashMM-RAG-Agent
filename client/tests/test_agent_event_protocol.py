"""V49 Agent 事件协议回归测试（对标 Claude 的思考/说明/工具交错时间线）。

锁住四件事：
1. tool_start / tool_done 携带同一个非空 id（前端据此把"运行中"原地更新为"完成"）。
2. narrate trace 携带模型工具前说明的【完整】文字（不再截断到 200 字）。
3. reasoning_content 存在时，每轮发 ("thinking", {"content": ...}) 事件且含原文。
4. 工具循环不再发 "调用 xxx..." 噪音 trace；工具失败时 tool_done.status == "error"。
"""
import asyncio

import pytest

pytestmark = pytest.mark.unit


# ── Fakes（与 test_code_file_download.py 同款约定）──

class _Fn:
    def __init__(s, name, arguments):
        s.name = name
        s.arguments = arguments


class _TC:
    def __init__(s, name, arguments, id="call_1"):
        s.id = id
        s.function = _Fn(name, arguments)


class _Msg:
    def __init__(s, content="", tool_calls=None, reasoning=None):
        s.content = content
        s.tool_calls = tool_calls
        s.reasoning_content = reasoning


class _Resp:
    def __init__(s, msg):
        s.message = msg


NARRATION = "我先检索一下相关资料，确认数据齐全后再生成文件。" * 12  # >200 字，锁不截断


class _ToolThenAnswerLLM:
    """第一轮：思考 + 说明 + 调一个工具；第二轮：给最终回答。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        if s.calls == 1:
            return _Resp(_Msg(
                content=NARRATION,
                tool_calls=[_TC("kb_search", '{"query": "红黑树"}')],
                reasoning="用户要红黑树实现，我先查知识库里有没有现成资料。",
            ))
        return _Resp(_Msg(content="查完了，结论如下。", reasoning="资料够了，直接总结。"))


def _run_loop(llm, **kw):
    from hashmm.agent.loop import AgentLoop

    async def _run():
        loop = AgentLoop(llm_fn=llm, system_prompt="助手", user_id="u",
                         conv_id="cEvt", **kw)
        return [(et, ed) async for et, ed in loop.run(query="写红黑树", history=[], user_id="u")]

    return asyncio.run(_run())


def test_tool_events_paired_by_id():
    """tool_start 与 tool_done 通过同一非空 id 配对。"""
    events = _run_loop(_ToolThenAnswerLLM())
    starts = [ed for et, ed in events if et == "tool_start"]
    dones = [ed for et, ed in events if et == "tool_done"]
    assert len(starts) == 1 and len(dones) == 1
    assert starts[0]["id"] and starts[0]["id"] == dones[0]["id"]
    assert starts[0]["name"] == "kb_search"
    assert dones[0]["status"] in ("done", "error")  # 沙箱里工具可能不可用，但字段必须有


def test_narrate_full_text_not_truncated():
    """工具前说明完整透传（>200 字不被截断）。"""
    events = _run_loop(_ToolThenAnswerLLM())
    narrates = [ed for et, ed in events
                if et == "trace" and ed.get("node") == "narrate"]
    assert len(narrates) == 1
    assert narrates[0]["detail"] == NARRATION  # 一字不少


def test_thinking_emitted_every_iteration():
    """每轮 reasoning_content 都通过 thinking 事件实时上报（含原文）。"""
    events = _run_loop(_ToolThenAnswerLLM())
    thinkings = [ed["content"] for et, ed in events if et == "thinking"]
    assert len(thinkings) == 2
    assert "先查知识库" in thinkings[0]
    assert "直接总结" in thinkings[1]


def test_no_calling_noise_trace():
    """不再有 '调用 xxx...' 噪音 trace（与 tool_start 重复）。"""
    events = _run_loop(_ToolThenAnswerLLM())
    noisy = [ed for et, ed in events
             if et == "trace" and str(ed.get("detail", "")).startswith("调用 ")]
    assert noisy == []


def test_tool_error_status_mapped():
    """工具返回 error 时，tool_done.status == 'error'（前端据此标红）。"""
    from hashmm.agent.loop import AgentLoop

    class _Boom(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            return {"status": "error", "message": "boom"}

    async def _run():
        loop = _Boom(llm_fn=_ToolThenAnswerLLM(), system_prompt="助手",
                     user_id="u", conv_id="cErr")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_run())
    dones = [ed for et, ed in events if et == "tool_done"]
    assert len(dones) == 1
    assert dones[0]["status"] == "error"


def test_final_answer_still_streams():
    """协议升级不影响正文产出（最终回答仍以 token 事件给出）。"""
    events = _run_loop(_ToolThenAnswerLLM())
    text = "".join(ed for et, ed in events if et == "token")
    assert "结论如下" in text


# ── V49 循环防御：连续重复调用去重 + execute_code 预算 ──

class _StuckLLM:
    """模拟卡死模型：连续 3 轮发完全相同的 kb_search，第 4 轮才回答。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        if s.calls <= 3:
            return _Resp(_Msg(tool_calls=[_TC("kb_search", '{"query": "x"}', id=f"c{s.calls}")]))
        return _Resp(_Msg(content="完成。"))


def test_consecutive_duplicate_call_not_reexecuted():
    """连续重复的同名同参调用只真执行一次，后续复用并提醒模型推进。"""
    from hashmm.agent.loop import AgentLoop

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append((name, dict(args)))
            return {"status": "ok", "message": "找到 3 条结果"}

    async def _run():
        loop = _Counting(llm_fn=_StuckLLM(), system_prompt="助手", user_id="u", conv_id="cDup")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_run())
    assert len(executed) == 1  # 第 2、3 次相同调用被去重，未真执行
    dones = [ed for et, ed in events if et == "tool_done"]
    assert len(dones) == 3     # 但时间线上对模型/用户透明：每次调用都有配对事件
    assert "重复调用" in dones[1]["result"] or "复用" in dones[1]["result"]


class _ReadWriteReadLLM:
    """读→写→读：第二次读与第一次读参数相同，但中间隔了写——必须真执行。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        seq = {
            1: _TC("read_file", '{"filename": "a.txt"}', id="r1"),
            2: _TC("create_file", '{"filename": "a.txt", "content": "new"}', id="w1"),
            3: _TC("read_file", '{"filename": "a.txt"}', id="r2"),
        }
        if s.calls in seq:
            return _Resp(_Msg(tool_calls=[seq[s.calls]]))
        return _Resp(_Msg(content="完成。"))


def test_read_write_read_not_deduped():
    """中间隔了其他调用（状态可能已变）的重复读不会被误伤。"""
    from hashmm.agent.loop import AgentLoop

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append(name)
            return {"status": "ok", "message": f"{name} ok"}

    async def _run():
        loop = _Counting(llm_fn=_ReadWriteReadLLM(), system_prompt="助手",
                         user_id="u", conv_id="cRWR")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    asyncio.run(_run())
    assert executed == ["read_file", "create_file", "read_file"]  # 三次都真执行


class _ExecSpamLLM:
    """模拟"反复 execute_code 直到超时"：每轮发一个不同代码的 execute_code。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        tool_names = {t.get("function", {}).get("name") for t in (tools or [])}
        if "execute_code" in tool_names:
            return _Resp(_Msg(tool_calls=[
                _TC("execute_code", f'{{"code": "print({s.calls})"}}', id=f"e{s.calls}")]))
        return _Resp(_Msg(content="基于已有执行结果，最终答案如下。"))


def test_execute_code_budget_enforced():
    """execute_code 超过 MAX_EXEC_CALLS 后不再真执行，且工具被移出工具集。"""
    from hashmm.agent.loop import AgentLoop, MAX_EXEC_CALLS

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append(name)
            return {"status": "ok", "message": "ran"}

    async def _run():
        loop = _Counting(llm_fn=_ExecSpamLLM(), system_prompt="助手",
                         user_id="u", conv_id="cExec")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_run())
    assert len(executed) <= MAX_EXEC_CALLS  # 真执行不超过预算
    text = "".join(ed for et, ed in events if et == "token")
    assert "最终答案" in text  # 预算耗尽后模型被迫产出回答


class _BatchSearchSpamLLM:
    """模拟真机 bench 抓到的故障：单批次并发发 10 个 kb_search（V52 真机实测漏洞）。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        tool_names = {t.get("function", {}).get("name") for t in (tools or [])}
        if s.calls == 1 and "kb_search" in tool_names:
            return _Resp(_Msg(tool_calls=[
                _TC("kb_search", f'{{"query": "关键词{i}"}}', id=f"s{i}") for i in range(10)]))
        return _Resp(_Msg(content="基于已检索内容回答。"))


def test_search_budget_enforced_within_single_batch():
    """同一批次的并发搜索也必须吃预算：真执行 ≤ MAX_SEARCH_CALLS，超出的拒绝。

    根因（真机 agent_bench 实测）：V49 只给 execute_code 加了单调用级短路，
    search 只有"下一轮移出工具集"的过滤——单批 10 个 kb_search 全部放行。
    """
    from hashmm.agent.loop import AgentLoop, MAX_SEARCH_CALLS

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append(name)
            return {"status": "ok", "message": "检索到 3 条"}

    async def _run():
        loop = _Counting(llm_fn=_BatchSearchSpamLLM(), system_prompt="助手",
                         user_id="u", conv_id="cBatchSearch")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_run())
    assert len(executed) <= MAX_SEARCH_CALLS, f"真执行 {len(executed)} 次"
    dones = [ed for et, ed in events if et == "tool_done"]
    assert len(dones) == 10                       # 10 个调用都有配对事件（透明）
    denied = [d for d in dones if "上限" in str(d.get("result", ""))]
    assert len(denied) == 10 - MAX_SEARCH_CALLS   # 超出的带拒绝说明
    assert "回答" in "".join(ed for et, ed in events if et == "token")
