"""V54 Agent 循环真·流式输出（对标 Claude/Codex 的逐字流，适配自有事件协议）。

设计契约（纯增量，不破坏现有协议）：
- 开关 HASHMM_AGENT_STREAM=1，默认关 → 关闭时事件流与 V53 逐字节一致（零变化保证）。
- 开启且 llm_fn 有 stream_with_tools：每个文本增量发 ("delta", str)；本轮文本归属
  确定时发 ("delta_commit", {"as": "narrate"|"answer"})；随后照常发权威的
  narrate trace / token 事件（前端用权威内容无缝替换直播缓冲，旧客户端忽略 delta 即可）。
- 流式中途异常 → 自动回退非流式 call_with_tools，绝不让流式毁掉回答。
- 流式 turn 拿不到 usage/reasoning（上游 stream 协议未透出）→ 保持缺省，不编造。
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.unit


class _Fn:
    def __init__(s, name, args): s.name, s.arguments = name, args


class _TC:
    def __init__(s, name, args, id="t1"): s.id, s.type, s.function = id, "function", _Fn(name, args)


class _Msg:
    def __init__(s, content="", tool_calls=None):
        s.content, s.tool_calls, s.reasoning_content = content, tool_calls, ""


class _Resp:
    def __init__(s, m): s.message = m


class _StreamLLM:
    """支持 stream_with_tools 的假 LLM。

    脚本（按轮）：[("text", [chunks...]), ("tool", name, args, [前置文本chunks])]
    """

    def __init__(s, script):
        s.script = list(script)
        s.fallback_calls = 0

    def call_with_tools(s, messages, tools=None):
        s.fallback_calls += 1
        return _Resp(_Msg(content="（非流式回退回答）"))

    def stream_with_tools(s, messages, tools=None):
        if not s.script:
            yield {"type": "done", "finish_reason": "stop"}
            return
        step = s.script.pop(0)
        if step[0] == "text":
            for c in step[1]:
                yield {"type": "text", "content": c}
            yield {"type": "done", "finish_reason": "stop"}
        elif step[0] == "tool":
            _, name, args, pre = step
            for c in pre:
                yield {"type": "text", "content": c}
            yield {"type": "tool_call_start", "id": "sc1", "name": name}
            yield {"type": "tool_call_end", "id": "sc1", "name": name, "args": args}
            yield {"type": "done", "finish_reason": "tool_calls"}
        elif step[0] == "boom":
            yield {"type": "text", "content": "半截"}
            raise RuntimeError("stream 断了")


def _run_loop(llm, env_on=True, conv="cStream"):
    from hashmm.agent.loop import AgentLoop

    async def _go():
        loop = AgentLoop(llm_fn=llm, system_prompt="助手", user_id="u", conv_id=conv)
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    old = os.environ.get("HASHMM_AGENT_STREAM")
    try:
        if env_on:
            os.environ["HASHMM_AGENT_STREAM"] = "1"
        else:
            os.environ.pop("HASHMM_AGENT_STREAM", None)
        return asyncio.run(_go())
    finally:
        if old is None:
            os.environ.pop("HASHMM_AGENT_STREAM", None)
        else:
            os.environ["HASHMM_AGENT_STREAM"] = old


def test_flag_off_zero_change():
    """默认关：即使 llm 支持流式，也不产生任何 delta 事件（零变化保证）。"""
    llm = _StreamLLM([("text", ["不", "应", "流"])])
    events = _run_loop(llm, env_on=False)
    assert not [1 for et, _ in events if et in ("delta", "delta_commit")]
    # 非流式路径走 call_with_tools
    assert llm.fallback_calls >= 1


def test_streamed_pure_answer():
    """纯回答轮：delta 拼起来 == 权威 token 全文；commit 归属 answer。"""
    llm = _StreamLLM([("text", ["你", "好", "，最终答案。"])])
    events = _run_loop(llm)
    deltas = "".join(ed for et, ed in events if et == "delta")
    assert deltas == "你好，最终答案。"
    commits = [ed for et, ed in events if et == "delta_commit"]
    assert commits and commits[-1]["as"] == "answer"
    tokens = "".join(ed for et, ed in events if et == "token")
    assert "最终答案" in tokens                      # 权威 token 照常发（前端无缝替换）
    assert llm.fallback_calls == 0                   # 没走回退


def test_streamed_narrate_then_tool_then_answer():
    """叙述→工具→回答：叙述段 commit 为 narrate 且权威 narrate trace 照常携带全文。"""
    llm = _StreamLLM([
        ("tool", "create_file", '{"filename": "s.txt", "content": "x"}', ["我先", "创建文件。"]),
        ("text", ["文件已就绪。"]),
    ])

    from hashmm.agent.loop import AgentLoop

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            return {"status": "ok", "message": "ok"}

    async def _go():
        loop = _Counting(llm_fn=llm, system_prompt="助手", user_id="u", conv_id="cStream2")
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

    commits = [ed["as"] for et, ed in events if et == "delta_commit"]
    assert commits == ["narrate", "answer"]
    narrates = [ed for et, ed in events if et == "trace" and ed.get("node") == "narrate"]
    assert narrates and "我先创建文件。" in narrates[0]["detail"]   # 权威叙述照常
    dones = [ed for et, ed in events if et == "tool_done"]
    assert dones and dones[0]["name"] == "create_file"             # 工具照常执行
    assert "文件已就绪" in "".join(ed for et, ed in events if et == "token")


def test_stream_failure_falls_back():
    """流式中途异常 → 回退非流式，回答照常产出，不崩。"""
    llm = _StreamLLM([("boom",)])
    events = _run_loop(llm, conv="cStream3")
    assert llm.fallback_calls >= 1
    assert "非流式回退回答" in "".join(ed for et, ed in events if et == "token")


class _NoEndStreamLLM:
    """复现真机故障：流发了 tool_call_start/delta 但【没有 tool_call_end、没有 finish】
    （provider 行为差异/流截断）。修复前：工具调用被静默吞掉，叙述变成终答。"""

    def __init__(s):
        s.turn = 0
        s.fallback_calls = 0

    def call_with_tools(s, messages, tools=None):
        s.fallback_calls += 1
        return _Resp(_Msg(content="（回退）"))

    def stream_with_tools(s, messages, tools=None):
        s.turn += 1
        if s.turn == 1:
            yield {"type": "text", "content": "先创建核心模型文件："}
            yield {"type": "tool_call_start", "id": "x1", "name": "create_file"}
            args = '{"filename": "gac.py", "content": "x = 1\\n"}'
            for i in range(0, len(args), 9):
                yield {"type": "tool_call_delta", "id": "x1", "args_partial": args[i:i + 9]}
            return  # 没有 tool_call_end / 没有 done —— 流到此截断
        yield {"type": "text", "content": "文件已创建完成。"}
        yield {"type": "done", "finish_reason": "stop"}


def test_stream_without_tool_call_end_still_executes_tools():
    """真机回归（GAC-Net 案例）：缺 tool_call_end 时从增量缓冲重建调用，工具照常执行。"""
    from hashmm.agent.loop import AgentLoop

    executed = []

    class _Counting(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            executed.append((name, dict(args)))
            return {"status": "ok", "message": "saved"}

    async def _go():
        loop = _Counting(llm_fn=_NoEndStreamLLM(), system_prompt="助手",
                         user_id="u", conv_id="cNoEnd")
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

    assert executed and executed[0][0] == "create_file", "工具调用被吞了"
    assert executed[0][1].get("filename") == "gac.py"      # 参数从增量缓冲完整重建
    assert "文件已创建完成" in "".join(ed for et, ed in events if et == "token")
    # 叙述照常作为 narrate（而不是被当成终答）
    narrs = [ed for et, ed in events if et == "trace" and ed.get("node") == "narrate"]
    assert narrs and "先创建核心模型文件" in narrs[0]["detail"]
