"""V51 长对话支持：会话级上下文压缩（对标 Claude 的 compaction）。

契约：
- 短对话（条数≤K 或预算内）→ 原样返回，逐字节零变化（保护现有行为）。
- 长对话 → [结构化摘要] + 最近 K 条原文。摘要必须：
  * 锚定开场轮（用户最初要什么是最不能忘的）
  * 提取文件名清单（跨数十轮后仍知道生成过哪些文件）
  * 超长时折叠中间轮并标注（"…中间 N 轮略…"）
  * 防套娃：摘要消息再次进入压缩不会嵌套爆炸
- 任何垃圾输入不抛错（退回尾部截取）。
"""
import pytest

pytestmark = pytest.mark.unit


def _turn(role, content):
    return {"role": role, "content": content}


def _long_history(n_turns=30, pad=1200):
    h = [_turn("user", "帮我写一个 C++ 的红黑树实现，要支持模板和迭代器"),
         _turn("assistant", "好的，我会创建 rbtree.h 和 main.cpp 两个文件。" + "实" * pad)]
    for i in range(n_turns):
        h.append(_turn("user", f"第{i}个追问：调整一下细节{i}"))
        h.append(_turn("assistant", f"第{i}次修改完成，更新了 rbtree.h。" + "改" * pad))
    h.append(_turn("user", "再把注释改成英文"))
    h.append(_turn("assistant", "已通过 str_replace 更新 rbtree.h 注释。"))
    return h


def test_short_history_returned_unchanged():
    """零变化保证：短对话逐字节原样。"""
    from hashmm.agent.conv_compact import compact_history
    h = [_turn("user", "你好"), _turn("assistant", "你好！有什么可以帮你？")]
    out = compact_history(h)
    assert out == h


def test_long_history_summary_plus_recent():
    from hashmm.agent.conv_compact import compact_history, SUMMARY_MARK
    h = _long_history()
    out = compact_history(h, keep_recent=6, char_budget=16000)

    assert out[0]["content"].startswith(SUMMARY_MARK)   # 第一条是摘要
    assert out[1:] == h[-6:]                            # 最近 6 条原文原序
    assert len(out) == 7
    # 开场需求锚定：几十轮之后仍知道用户最初要什么
    assert "红黑树" in out[0]["content"]
    # 文件清单提取
    assert "rbtree.h" in out[0]["content"]


def test_budget_respected():
    from hashmm.agent.conv_compact import compact_history
    h = _long_history(n_turns=60, pad=2000)
    out = compact_history(h, keep_recent=6, char_budget=16000)
    total = sum(len(str(m.get("content") or "")) for m in out)
    # 摘要≤上限 + 最近6条（单条超长也会被截到 4000）→ 总量有硬上界
    assert total < 16000 + 6 * 4200
    # 60 轮的脉络行装不进摘要预算 → 必须出现中间折叠标注（且首轮仍锚定）
    assert "轮略" in out[0]["content"]
    assert "红黑树" in out[0]["content"]


def test_oversized_recent_message_truncated():
    from hashmm.agent.conv_compact import compact_history
    h = _long_history()
    h.append(_turn("assistant", "巨" * 9000))
    out = compact_history(h, keep_recent=6, char_budget=16000)
    assert len(out[-1]["content"]) <= 4100
    assert "已截断" in out[-1]["content"]


def test_no_nesting_explosion():
    """摘要消息再次进压缩：单一标记、不嵌套爆炸。"""
    from hashmm.agent.conv_compact import compact_history, SUMMARY_MARK
    once = compact_history(_long_history(), keep_recent=6, char_budget=16000)
    # 在压缩结果后面继续聊很多轮，再压一次
    again = once + _long_history(n_turns=20)[2:]
    out = compact_history(again, keep_recent=6, char_budget=16000)
    assert out[0]["content"].count(SUMMARY_MARK) == 1
    assert len(out[0]["content"]) < 8000


def test_garbage_input_never_raises():
    from hashmm.agent.conv_compact import compact_history
    assert compact_history(None) == []
    assert compact_history([]) == []
    garbage = [None, "字符串", 42, {"role": "user"}, {"content": None},
               {"role": "assistant", "content": 123}]
    out = compact_history(garbage * 50, keep_recent=4, char_budget=100)
    assert isinstance(out, list)   # 不抛错即可


def test_build_messages_integration_keeps_opening_context():
    """Agent 循环集成：长历史下，开场需求仍出现在发给 LLM 的消息里。"""
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cLong")
    msgs = loop._build_messages("继续优化", _long_history(), "")
    joined = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "红黑树" in joined          # 没有压缩时 [-6:] 硬切会丢掉这个
    assert "继续优化" in joined
    roles = {m["role"] for m in msgs}
    assert roles <= {"system", "user", "assistant"}


# ── V51 附加：worker 子任务轨迹 ──

def test_worker_returns_steps_trail():
    """worker 三个返回出口都带 steps；成功路径记录 工具名(参数摘要)。"""
    import asyncio as _aio

    class _Fn:
        def __init__(s, name, args): s.name, s.arguments = name, args

    class _TC:
        def __init__(s, name, args, id="w1"):
            s.id, s.type, s.function = id, "function", _Fn(name, args)

    class _Msg:
        def __init__(s, content="", tool_calls=None):
            s.content, s.tool_calls = content, tool_calls

    class _Resp:
        def __init__(s, m): s.message = m

    class _WLLM:
        def __init__(s): s.calls = 0
        def call_with_tools(s, messages, tools=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tool_calls=[_TC("kb_search", '{"query": "腾讯营收"}')]))
            return _Resp(_Msg(content="子任务结论。"))

    from hashmm.agent.worker import Worker
    w = Worker(_WLLM(), role="research", user_id="u")

    async def _fake_exec(name, args):
        return "结果片段"
    w._exec = _fake_exec

    res = _aio.run(w.run("查腾讯营收"))
    assert "steps" in res and isinstance(res["steps"], list)
    assert res["steps"] and res["steps"][0].startswith("kb_search(")
    assert "腾讯营收" in res["steps"][0]
