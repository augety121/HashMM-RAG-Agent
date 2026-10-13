"""V50 第二批：任务清单系统（对标 Claude Code TodoWrite）+ 上下文老化。

任务清单（update_todo）契约：
- 纯 UI 通道：发 ("todo", {items}) 事件，不发 tool_start/tool_done（清单本身就是 UI），
  不占工具预算。
- 全量覆盖语义：每次调用传完整清单。
- 载荷防御：非法 status 钳到 pending、超长截断、垃圾输入不崩。

上下文老化（_age_tool_results）契约：
- 只折叠【旧的】工具结果正文（保留最近 K 条全文），消息条数/角色/配对一律不动。
- 比一刀切 _compact_context 温和，作为它的前置步骤。
"""
import asyncio

import pytest

pytestmark = pytest.mark.unit


class _Fn:
    def __init__(s, name, args):
        s.name, s.arguments = name, args


class _TC:
    def __init__(s, name, args, id="t1"):
        s.id, s.type, s.function = id, "function", _Fn(name, args)


class _Msg:
    def __init__(s, content="", tool_calls=None):
        s.content, s.tool_calls, s.reasoning_content = content, tool_calls, ""


class _Resp:
    def __init__(s, m):
        s.message = m


# ── 1. 任务清单 ──

def test_update_todo_in_agent_tools():
    from hashmm.agent.loop import AGENT_TOOLS
    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert "update_todo" in names


def test_system_prompt_teaches_todo():
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cTodo")
    sys_content = loop._build_messages("q", [], "")[0]["content"]
    assert "update_todo" in sys_content


class _TodoLLM:
    """第1轮列计划（含脏数据），第2轮更新进度，第3轮回答。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        if s.calls == 1:
            return _Resp(_Msg(tool_calls=[_TC("update_todo",
                '{"items": ['
                '{"text": "检索资料", "status": "doing"},'
                '{"text": "写实现", "status": "pending"},'
                '{"text": "  ", "status": "pending"},'
                '{"text": "坏状态", "status": "exploded"},'
                '42]}', id="td1")]))
        if s.calls == 2:
            return _Resp(_Msg(tool_calls=[_TC("update_todo",
                '{"items": [{"text": "检索资料", "status": "done"},'
                '{"text": "写实现", "status": "doing"},'
                '{"text": "坏状态", "status": "pending"}]}', id="td2")]))
        return _Resp(_Msg(content="全部完成。"))


def test_todo_event_emitted_and_free():
    """todo 事件按调用次序发出、脏数据被钳制、不产生工具卡、不占预算。"""
    from hashmm.agent.loop import AgentLoop

    async def _run():
        loop = AgentLoop(llm_fn=_TodoLLM(), system_prompt="助手",
                         user_id="u", conv_id="cTodo")
        return [(et, ed) async for et, ed in loop.run(query="做个东西", history=[], user_id="u")]

    events = asyncio.run(_run())
    todos = [ed for et, ed in events if et == "todo"]
    assert len(todos) == 2
    first = todos[0]["items"]
    assert [i["text"] for i in first] == ["检索资料", "写实现", "坏状态"]  # 空文本/非dict 被丢弃
    assert first[2]["status"] == "pending"                               # 非法状态钳到 pending
    second = todos[1]["items"]
    assert second[0]["status"] == "done" and second[1]["status"] == "doing"
    # 纯 UI 通道：不产生 tool_start/tool_done
    assert not [1 for et, ed in events if et in ("tool_start", "tool_done")
                and ed.get("name") == "update_todo"]
    # 正文照常产出
    assert "完成" in "".join(ed for et, ed in events if et == "token")


def test_todo_garbage_payload_safe():
    """items 不是数组 → 不发事件、不崩，循环继续到正文。"""
    from hashmm.agent.loop import AgentLoop

    class _BadLLM:
        def __init__(s): s.calls = 0
        def call_with_tools(s, messages, tools=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tool_calls=[_TC("update_todo", '{"items": "不是数组"}')]))
            return _Resp(_Msg(content="OK"))

    async def _run():
        loop = AgentLoop(llm_fn=_BadLLM(), system_prompt="助手", user_id="u", conv_id="cTodoBad")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_run())
    assert not [1 for et, _ in events if et == "todo"]
    assert "OK" in "".join(ed for et, ed in events if et == "token")


# ── 2. 上下文老化 ──

def _mk_messages(n_tools: int, big: int = 1000):
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "q"}]
    for i in range(n_tools):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"R{i}-" + "x" * big})
    msgs.append({"role": "assistant", "content": "叙述性回答片段"})
    return msgs


def test_age_tool_results_keeps_recent_and_structure():
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cAge")
    msgs = _mk_messages(6)
    before_roles = [m["role"] for m in msgs]
    aged = loop._age_tool_results(list(msgs))

    assert [m["role"] for m in aged] == before_roles          # 结构一律不动
    tools = [m for m in aged if m["role"] == "tool"]
    assert all(len(m["content"]) > 900 for m in tools[-3:])   # 最近 3 条全文保留
    assert all(len(m["content"]) < 400 for m in tools[:-3])   # 旧的被折叠
    assert all("已折叠" in m["content"] for m in tools[:-3])  # 带可读标记
    assert all(m["content"].startswith(f"R{i}-") for i, m in enumerate(tools))  # 头部内容保留可辨识
    # 助手叙述与配对字段不动
    assert aged[-1]["content"] == "叙述性回答片段"
    assert all("tool_call_id" in m for m in tools)


def test_age_tool_results_idempotent_and_small_noop():
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cAge2")
    msgs = _mk_messages(6)
    once = loop._age_tool_results(list(msgs))
    twice = loop._age_tool_results([dict(m) for m in once])
    assert [m["content"] for m in twice] == [m["content"] for m in once]  # 幂等，不二次折叠
    # 少量工具结果（≤K）→ 原样
    small = _mk_messages(2)
    assert [m["content"] for m in loop._age_tool_results(list(small))] \
        == [m["content"] for m in small]
