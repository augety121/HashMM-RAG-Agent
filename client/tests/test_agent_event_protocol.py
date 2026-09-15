"""V49 Agent 事件协议回归测试（对标 Claude 的思考/说明/工具交错时间线）。

锁住四件事：
1. tool_start / tool_done 携带同一个非空 id（前端据此把"运行中"原地更新为"完成"）。
2. narrate trace 携带模型工具前说明的【完整】文字（不再截断到 200 字）。
3. reasoning_content 只在供应商请求链内部续传，不进入公开 Agent 事件。
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


def test_provider_reasoning_is_not_emitted_as_public_event():
    """供应商私有 reasoning 不能泄露到用户可见事件流。"""
    events = _run_loop(_ToolThenAnswerLLM())
    public_payload = "\n".join(str(data) for _, data in events)
    assert "先查知识库" not in public_payload
    assert "直接总结" not in public_payload
    assert not [data for event_type, data in events if event_type == "thinking"]


def test_public_progress_projection_never_returns_private_reasoning():
    """所有兼容入口必须把私有推理投影为固定、可审计的公开状态。"""
    from hashmm.api.public_progress import public_analysis_status

    private = "这是供应商私有推理，包含不应展示的中间假设。"
    public = public_analysis_status(private)
    assert public == "正在分析任务并选择下一步"
    assert private not in public


def test_text_form_tool_call_preserves_reasoning_for_next_request():
    """DSML 工具调用也必须原样续传 DeepSeek reasoning_content。

    真实故障链是模型先用文本形式返回工具调用；兼容解析器把它转换成
    structured tool_calls 后却丢了 reasoning_content，导致下一轮请求被
    DeepSeek thinking-mode 以 HTTP 400 拒绝。
    """
    from hashmm.agent.loop import AgentLoop

    reasoning = "先打开原始来源，核验标题、发布时间和关键事实。"

    class _TextToolLLM:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            self.requests.append([dict(message) for message in messages])
            if self.calls == 1:
                return _Resp(_Msg(
                    content=(
                        "我先核验原始来源。"
                        '<tool_calls><invoke name="fetch_url">'
                        '<parameter name="url">https://example.com/source</parameter>'
                        "</invoke></tool_calls>"
                    ),
                    reasoning=reasoning,
                ))
            return _Resp(_Msg(content="来源已核验。", reasoning="基于工具结果收尾。"))

    class _NoNetwork(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            return {"status": "ok", "content": "原始来源正文"}

    llm = _TextToolLLM()

    async def _run():
        loop = _NoNetwork(
            llm_fn=llm,
            system_prompt="助手",
            user_id="u",
            conv_id="cReasoningReplay",
        )
        return [
            (event_type, event_data)
            async for event_type, event_data in loop.run(
                query="核验来源", history=[], user_id="u"
            )
        ]

    asyncio.run(_run())
    assert len(llm.requests) >= 2
    replayed = [
        message
        for message in llm.requests[1]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert len(replayed) == 1
    assert replayed[0]["reasoning_content"] == reasoning
    assert replayed[0]["tool_calls"][0]["function"]["name"] == "fetch_url"


def test_publisher_delivery_retry_preserves_reasoning_and_creates_both_files():
    """交付门重试既不能丢 DeepSeek reasoning，也不能只承诺生成文件。"""
    import json
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from hashmm.agent.loop import AgentLoop

    first_reasoning = "来源已经核验，下一步必须落盘两种交付格式。"
    current = datetime.now(ZoneInfo("Asia/Shanghai"))
    source_time = (current - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    cutoff_time = (current - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    valid_markdown = (
        "# AI 行业简报\n\n"
        f"数据截至：{cutoff_time}（北京时间）\n\n"
        "## 模型厂商发布可靠性更新\n\n"
        "厂商在官方发布页公布了面向生产工作负载的可靠性更新。"
        "原始页面已经实际打开，标题、发布时间与关键变更均已逐项核对；"
        "本段只陈述原始页面能够直接支持的内容，并把编辑判断与事实分开。\n\n"
        f"来源：https://example.com/official-release ，发布于 {source_time}"
        "（北京时间）｜状态：已核验\n\n"
        "## 待确认项\n\n发布前仍需用户确认标题和最终排版，不会自动发布。"
    )
    valid_html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>body{font-family:sans-serif;line-height:1.7}</style></head><body>"
        "<h1>AI 行业简报</h1>"
        f"<p>数据截至：{cutoff_time}（北京时间）</p>"
        "<h2>模型厂商发布可靠性更新</h2>"
        "<p>厂商在官方发布页公布了面向生产工作负载的可靠性更新。"
        "原始页面已经实际打开，标题、发布时间与关键变更均已逐项核对；"
        "本段只陈述原始页面能够直接支持的内容，并把编辑判断与事实分开。</p>"
        f"<p>来源：https://example.com/official-release，发布于 {source_time}"
        "（北京时间），状态：已核验。</p>"
        "<h2>待确认项</h2><p>发布前仍需用户确认标题和最终排版，不会自动发布。</p>"
        "</body></html>"
    )

    class _PublisherLLM:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            self.requests.append([dict(message) for message in messages])
            if self.calls == 1:
                return _Resp(_Msg(
                    content="候选已经整理完毕，现在生成 Markdown 和 HTML。",
                    reasoning=first_reasoning,
                ))
            if self.calls == 2:
                return _Resp(_Msg(
                    content="正在生成两份草稿。",
                    tool_calls=[
                        _TC(
                            "create_file",
                            json.dumps(
                                {"filename": "ai-brief.md", "content": valid_markdown},
                                ensure_ascii=False,
                            ),
                            id="md_1",
                        ),
                        _TC(
                            "create_file",
                            json.dumps(
                                {"filename": "ai-brief.html", "content": valid_html},
                                ensure_ascii=False,
                            ),
                            id="html_1",
                        ),
                    ],
                    reasoning="按交付门要求创建两个真实文件。",
                ))
            return _Resp(_Msg(
                content="Markdown 与公众号兼容 HTML 草稿均已生成，发布前等待你的确认。",
                reasoning="交付物齐全，可以收尾。",
            ))

    class _LocalFiles(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            assert name == "create_file"
            return {
                "status": "ok",
                "file": {
                    "filename": args["filename"],
                    "download_url": f"/api/files/{args['filename']}",
                },
            }

    llm = _PublisherLLM()

    async def _run():
        loop = _LocalFiles(
            llm_fn=llm,
            system_prompt="助手",
            user_id="u",
            conv_id="cPublisherReasoning",
            workflow_mode="publisher",
        )
        return [
            (event_type, event_data)
            async for event_type, event_data in loop.run(
                query="生成过去 24 小时 AI 行业简报并交付 Markdown 和 HTML",
                history=[],
                user_id="u",
            )
        ]

    events = asyncio.run(_run())
    replayed = [
        message
        for message in llm.requests[1]
        if message.get("role") == "assistant"
        and message.get("content", "").startswith("候选已经整理")
    ]
    assert replayed == [{
        "role": "assistant",
        "content": "候选已经整理完毕，现在生成 Markdown 和 HTML。",
        "reasoning_content": first_reasoning,
    }]
    files = [data["filename"] for event, data in events if event == "file"]
    assert files == ["ai-brief.md", "ai-brief.html"]
    done = [data for event, data in events if event == "done"][-1]
    assert done["stop_reason"] == "completed"


def test_publisher_without_real_files_fails_closed():
    """连续只说“正在生成”不能把简报任务标记为完成。"""
    class _PromiseOnlyLLM:
        def call_with_tools(self, messages, tools=None):
            return _Resp(_Msg(
                content=(
                    "# AI 行业简报\n\n候选池已经整理，接下来生成 Markdown 和 HTML。"
                ),
                reasoning="仍未调用文件工具。",
            ))

    events = _run_loop(
        _PromiseOnlyLLM(),
        workflow_mode="publisher",
    )
    done = [data for event, data in events if event == "done"][-1]
    assert done["stop_reason"] == "delivery_incomplete"
    terminal_traces = [
        data for event, data in events
        if event == "trace" and data.get("node") in {"done", "error"}
    ]
    assert terminal_traces[-1]["node"] == "error"
    assert not any(
        data.get("node") == "done"
        for event, data in events
        if event == "trace"
    )


def test_publisher_materializes_html_after_model_only_created_markdown():
    """模型漏掉 HTML 时由交付层做安全、确定性的格式转换。

    这条回归对应真实故障：模型已经写入完整 Markdown，随后只说
    “现在生成 HTML”并结束，产品却把这一轮留在半完成状态。
    """
    import json
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from hashmm.agent.loop import AgentLoop

    current = datetime.now(ZoneInfo("Asia/Shanghai"))
    source_time = (current - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    cutoff_time = (current - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    markdown = (
        "# AI 行业简报\n\n"
        f"数据截至：{cutoff_time}（北京时间）\n\n"
        "## 官方发布：模型服务可靠性更新\n\n"
        "模型厂商在官方页面公布了一项面向生产工作负载的可靠性更新。"
        "原始页面已经实际打开，标题、发布时间与关键事实均完成逐项核对。"
        "这里只保留原始页面能够直接支持的事实，并把编辑判断与事实分开。\n\n"
        f"来源：https://example.com/official-release ，发布于 {source_time}"
        "（北京时间）｜状态：已核验\n\n"
        "## 待确认项\n\n发布前仍需用户确认标题与最终版式，不会自动发布。"
    )

    class _MarkdownOnlyLLM:
        def __init__(self):
            self.calls = 0

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return _Resp(_Msg(
                    content="先写入完成核验的 Markdown 草稿。",
                    tool_calls=[_TC(
                        "create_file",
                        json.dumps(
                            {"filename": "daily.md", "content": markdown},
                            ensure_ascii=False,
                        ),
                        id="md_only",
                    )],
                    reasoning="先持久化正文。",
                ))
            return _Resp(_Msg(
                content="Markdown 已生成。",
                reasoning="模型遗漏了 HTML，交付层必须补齐。",
            ))

    created: dict[str, str] = {}

    class _LocalFiles(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            assert name == "create_file"
            created[args["filename"]] = args["content"]
            return {
                "status": "ok",
                "file": {
                    "filename": args["filename"],
                    "download_url": f"/api/files/{args['filename']}",
                },
            }

    async def _run():
        loop = _LocalFiles(
            llm_fn=_MarkdownOnlyLLM(),
            system_prompt="助手",
            user_id="u",
            conv_id="cPublisherMaterialize",
            workflow_mode="publisher",
        )
        return [
            (event_type, event_data)
            async for event_type, event_data in loop.run(
                query="生成过去 24 小时 AI 简报并交付 Markdown 和 HTML",
                history=[],
                user_id="u",
            )
        ]

    events = asyncio.run(_run())
    assert set(created) == {"daily.md", "daily.html"}
    assert "<script" not in created["daily.html"].lower()
    assert "官方发布：模型服务可靠性更新" in created["daily.html"]
    done = [data for event, data in events if event == "done"][-1]
    assert done["stop_reason"] == "completed"
    assert [data["filename"] for event, data in events if event == "file"] == [
        "daily.md",
        "daily.html",
    ]


def test_legacy_handler_keeps_provider_reasoning_private():
    """旧兼容 Handler 也只能公开固定进度，不能泄露 reasoning_content。"""
    from hashmm.api.handlers.agent import AgentLoopHandler

    private_reasoning = "这是供应商的私有推理，不应显示给用户。"

    class _LegacyLLM:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def call_with_tools(self, messages, tools, tool_choice="auto"):
            self.calls += 1
            self.requests.append([dict(item) for item in messages])
            if self.calls == 1:
                return type("Choice", (), {
                    "message": _Msg(
                        content="我先读取资料。",
                        tool_calls=[_TC("kb_search", '{"query":"可靠性"}')],
                        reasoning=private_reasoning,
                    ),
                    "finish_reason": "tool_calls",
                })()
            return type("Choice", (), {
                "message": _Msg(content="资料读取完成。", reasoning="收尾推理"),
                "finish_reason": "stop",
            })()

    handler = AgentLoopHandler(
        query="读取资料",
        conv_id="legacy-private",
        history=[],
        llm_fn=_LegacyLLM(),
        tool_exec_fn=lambda *_: "OK",
        tool_definitions=[{
            "type": "function",
            "function": {
                "name": "kb_search",
                "description": "搜索资料",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    )
    events = list(handler.run())
    public = "\n".join(str(event.data) for event in events)
    assert private_reasoning not in public
    assert "收尾推理" not in public
    assert not [event for event in events if event.event == "thinking"]
    replayed = [
        item
        for item in handler.llm_fn.requests[1]
        if item.get("role") == "assistant" and item.get("tool_calls")
    ]
    assert replayed[0]["reasoning_content"] == private_reasoning


def test_heartbeat_can_restart_after_stop():
    """同一长任务中的第二个慢步骤仍必须有 keepalive。"""
    import time

    from hashmm.api.handlers.base import HeartbeatThread

    heartbeat = HeartbeatThread(interval=0.01)
    heartbeat.start()
    time.sleep(0.03)
    heartbeat.stop()
    assert heartbeat.drain()

    heartbeat.start()
    time.sleep(0.03)
    heartbeat.stop()
    assert heartbeat.drain()


def test_todo_manifest_revision_is_stable_and_monotonic():
    """同一轮任务的清单必须用稳定 manifest_id 和单调 revision 防止重放倒退。"""
    from hashmm.agent.loop import AgentLoop

    class _TodoLLM:
        def __init__(self):
            self.calls = 0

        def call_with_tools(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return _Resp(_Msg(tool_calls=[_TC(
                    "update_todo",
                    '{"items":[{"text":"核验来源","status":"doing"},'
                    '{"text":"生成交付物","status":"pending"}]}',
                    id="todo_1",
                )]))
            if self.calls == 2:
                return _Resp(_Msg(tool_calls=[_TC(
                    "update_todo",
                    '{"items":[{"text":"核验来源","status":"done"},'
                    '{"text":"生成交付物","status":"doing"}]}',
                    id="todo_2",
                )]))
            return _Resp(_Msg(content="继续执行，尚未把未完成事项伪装成完成。"))

    async def _run():
        loop = AgentLoop(
            llm_fn=_TodoLLM(),
            system_prompt="助手",
            user_id="u",
            conv_id="cTodoRevision",
        )
        return [
            (event_type, event_data)
            async for event_type, event_data in loop.run(
                query="完成一个两步任务", history=[], user_id="u"
            )
        ]

    events = asyncio.run(_run())
    todos = [data for event, data in events if event == "todo"]
    assert len(todos) >= 2
    assert todos[0]["manifest_id"]
    assert todos[0]["manifest_id"] == todos[1]["manifest_id"]
    assert [todos[0]["revision"], todos[1]["revision"]] == [1, 2]
    assert todos[0]["items"][0]["status"] == "doing"
    assert todos[1]["items"][0]["status"] == "done"


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
