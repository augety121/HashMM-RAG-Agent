"""Async SSE streaming — extracted from server.py for separation of concerns.

Key improvements over the inline generate_sse():
  1. async generator — doesn't block the event loop for other requests
  2. Wraps sync LLM.stream() in asyncio.to_thread for true non-blocking
  3. Heartbeat keepalive during blocking operations
  4. Structured into clear phases: init → classify → retrieve → stream → finalize
"""
from __future__ import annotations

import asyncio
import json
import time
import re
from typing import Any, AsyncGenerator

from hashmm.api import database as db
from hashmm.api.core.llm_router import LLMRouter
from hashmm.utils import get_logger, log_suppressed

# 上下文工程（方案 P1）：把主路径里的 [:N] 粗暴截断换成在自然边界截断 + 标注省略，
# 让模型不再读到半句话。导入失败则退化为原来的硬切，绝不影响主链路。
try:
    from hashmm.agent.context_pack import clip_at_boundary as _clip
except Exception:
    def _clip(text, budget, marker=""):
        return (text or "")[:budget]

logger = get_logger("hashmm.streaming")

_llm_router = LLMRouter()


def _sse(event: str, data: dict) -> str:
    """Format an SSE event."""
    # Agent Run 时间线（默认关）：仅给 trace 事件补 phase/step_id，前端可据此回放。
    # 关闭时 annotate_trace 原样返回，输出与之前逐字节一致。永不抛错。
    if event == "trace":
        try:
            from hashmm.api.run_timeline import annotate_trace
            data = annotate_trace(data)
        except Exception:
            pass
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_comment(text: str = "heartbeat") -> str:
    return f": {text}\n\n"


async def _in_thread(fn, *args, **kwargs):
    """Run a sync function in a thread pool to avoid blocking the event loop."""
    import functools
    call = functools.partial(fn, *args, **kwargs)
    return await asyncio.get_event_loop().run_in_executor(None, call)


def _stream_llm_sync(llm_fn, msgs: list[dict], temp: float) -> list[dict]:
    """Run LLM streaming in a sync context, collecting all events.

    Returns a list of dicts: [{"type": "think"|"token", "content": "..."}]
    This is called via asyncio.to_thread() so the event loop isn't blocked.
    """
    events = []
    try:
        if hasattr(llm_fn, 'stream'):
            buf = ""
            in_think = False
            done_think = False
            for token in llm_fn.stream(msgs, temp_override=temp):
                if not done_think:
                    buf += token
                    if "<think>" in buf and not in_think:
                        in_think = True
                    if "</think>" in buf and in_think:
                        remaining = buf.split("</think>", 1)[1].lstrip("\n")
                        think_content = buf.split("<think>", 1)[1].split("</think>", 1)[0]
                        if think_content.strip():
                            events.append({"type": "think", "content": think_content})
                        done_think = True
                        if remaining:
                            events.append({"type": "token", "content": remaining})
                        buf = ""
                    elif len(buf) > 500 and not in_think:
                        done_think = True
                        events.append({"type": "token", "content": buf})
                        buf = ""
                else:
                    events.append({"type": "token", "content": token})
            if buf and not done_think:
                events.append({"type": "token", "content": buf})
        elif hasattr(llm_fn, 'chat'):
            ans = llm_fn.chat(msgs)
            events.append({"type": "token", "content": ans})
        else:
            ans = llm_fn(msgs[-1]["content"] if msgs else "")
            events.append({"type": "token", "content": ans})
    except Exception as e:
        events.append({"type": "error", "content": str(e), "repr": repr(e)})
    return events


async def generate_sse_async(
    *,
    conv_id: str,
    query: str,
    user_id: str,
    username: str,
    file_context: str = "",
    custom_prompt: str = "",
    doc_filter: list[str] | None = None,
    retrieval_mode: str = "mix",
    route_reason: str = "",
    answer_style: str | None = None,
    # Injected dependencies (from server.py module-level)
    load_fn: Any = None,
    llm_fn: Any = None,
    classify_rules_fn: Any = None,
    classify_fallback_fn: Any = None,
    parse_llm_intent_fn: Any = None,
    intent_to_task_type_fn: Any = None,
    intent_system: str = "",
    sys_prompts: dict | None = None,
    request: Any = None,  # v15 Phase 7: for client-disconnect detection
) -> AsyncGenerator[str, None]:
    """Async SSE generator for conversation streaming.

    This replaces the old sync generate_sse() in server.py.
    All blocking operations (LLM calls, retrieval) are wrapped in asyncio.to_thread.
    """
    doc_filter = doc_filter or []
    sys_prompts = sys_prompts or {}

    # Agent Run 时间线：每轮请求归零 step_id（默认关时无副作用）。
    try:
        from hashmm.api.run_timeline import reset as _timeline_reset
        _timeline_reset()
    except Exception:
        pass

    yield _sse_comment("heartbeat")

    # v12: Check if services are ready — if not, wait briefly or use degraded mode
    from hashmm.api.core.services import ServiceRegistry
    if ServiceRegistry.status != "ready":
        yield _sse("trace", {"steps": [{"node": "classify", "detail": f"系统启动中 ({ServiceRegistry.status_detail or ServiceRegistry.status})..."}]})
        # Wait up to 15 seconds for services to load
        for _ in range(30):
            await asyncio.sleep(0.5)
            if ServiceRegistry.status == "ready":
                break
        if ServiceRegistry.status != "ready":
            msg = f"⚠️ 系统尚未完全启动（{ServiceRegistry.status_detail or '加载模型中'}），请等待几秒后重试。"
            yield _sse("token", {"content": msg})
            yield _sse("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": conv_id})
            db.create_message(conv_id, "assistant", msg, status="error")
            return
        # Re-get llm_fn after heavy init
        from hashmm.api import app_state
        llm_fn = app_state.llm_fn

    # ── Phase 1: Initialize ──
    try:
        if load_fn:
            await _in_thread(load_fn)
    except Exception as e:
        err = f"服务初始化失败: {repr(e)[:200]}"
        yield _sse("token", {"content": err})
        yield _sse("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": conv_id})
        db.create_message(conv_id, "assistant", err, status="error")
        return

    # The llm_fn parameter is captured at request dispatch time. On the very
    # first request, services may not have finished loading yet, so the captured
    # value can be None. load_fn() above populates the registry — re-fetch here
    # so the Agent Loop and all downstream branches get a live LLM handle.
    if llm_fn is None:
        try:
            from hashmm.api.core.services import ServiceRegistry
            llm_fn = ServiceRegistry.llm_fn
        except Exception as _e:
            log_suppressed(logger, _e)
    if llm_fn is None:
        try:
            from hashmm.api import app_state
            llm_fn = getattr(app_state, "llm_fn", None)
        except Exception as _e:
            log_suppressed(logger, _e)

    assistant_msg_id = db.create_message(conv_id, "assistant", "", status="streaming")
    history = db.get_recent_messages(conv_id, n=12)
    # V51: 长对话记忆专用——取足历史交给 conv_compact 压缩（确定性、零 LLM 调用）。
    # 现有 history(12条) 的所有消费点保持零变化；只有下面三个生成路径换用压缩版。
    long_history = db.get_recent_messages(conv_id, n=80)
    from hashmm.agent.conv_compact import compact_history as _compact_hist

    # v17 Phase 28: prompt-injection / system-prompt extraction → explicit, clean
    # security refusal (not a vague "no relevant info" deflection), before any work.
    try:
        from hashmm.prompt_safety import detect_prompt_injection, security_refusal_message
        if detect_prompt_injection(query):
            _ref = security_refusal_message()
            yield _sse("token", {"content": _ref})
            db.update_message(assistant_msg_id, content=_ref, status="complete")
            yield _sse("done", {"sources": [], "trace": [], "steps": [],
                                "elapsed_ms": 0, "session_id": conv_id})
            db.audit(user_id, username, "security_refusal", f"injection blocked: {query[:60]}")
            return
    except Exception as _e:
        log_suppressed(logger, _e)

    # Single source of truth for elapsed-time tracking. MUST be defined before
    # any branch (Agent Loop / RAG / direct) that reports elapsed_ms, otherwise
    # those branches raise UnboundLocalError and silently fall back.
    t0 = time.time()

    # Build context
    try:
        from hashmm.api.context import ContextBuilder
        ctx_builder = ContextBuilder(conv_id, user_id)
        profile_ctx = ctx_builder.build(history, file_context, custom_prompt, db)
    except Exception:
        profile_ctx = ""

    # ── Phase 2: Classify intent ──
    intent = classify_rules_fn(query, file_context) if classify_rules_fn else None
    classification_method = "rules"

    if intent is None and llm_fn:
        try:
            yield _sse("trace", {"steps": [{"node": "classify", "detail": "分析意图..."}]})
            # F11: intent classification is a LOCAL_TASK → route to local model when
            # enabled. Local-scoped (`_intent_fn`); the main answer fn stays unchanged.
            _intent_fn = llm_fn
            try:
                from hashmm.llm_router import route_llm, record_routing
                _routed, _backend = route_llm("intent", llm_fn)
                _intent_fn = _routed or llm_fn
                record_routing("intent", _backend)
            except Exception:
                pass  # nosem: observability-fallback
            if hasattr(_intent_fn, 'quick_call'):
                raw = await _in_thread(_intent_fn.quick_call, intent_system, query, 150)
            elif callable(_intent_fn):
                raw = await _in_thread(_intent_fn, f"{intent_system}\n\n用户: {query}")
            else:
                raw = ""
            parsed = parse_llm_intent_fn(raw) if raw and parse_llm_intent_fn else None
            if parsed:
                intent = parsed
                classification_method = "llm"
        except Exception as _e:
            log_suppressed(logger, _e)

    if intent is None:
        intent = classify_fallback_fn(query, file_context) if classify_fallback_fn else {"type": "direct", "summary": "直接回答"}
        classification_method = "fallback"

    task_type = intent_to_task_type_fn(intent) if intent_to_task_type_fn else "direct_task"
    _summary = intent.get("summary", "")
    _classify_detail = f"{task_type} ({classification_method}: {_summary})"

    # Smart clarification
    if intent.get("needs_clarification") and task_type not in ("direct_task",):
        clarify_msg = f"我理解你想要{_summary}。为了更好地完成任务，请补充以下信息："
        yield _sse("token", {"content": clarify_msg})
        done_data = {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0,
                     "session_id": conv_id, "suggestions": ["用 Python 实现", "用 Java 实现", "给我详细说明需求"]}
        db.update_message(assistant_msg_id, content=clarify_msg, status="complete",
                         suggestions=["用 Python 实现", "用 Java 实现", "给我详细说明需求"])
        yield _sse("done", done_data)
        return

    yield _sse("trace", {"steps": [{"node": "classify", "detail": _classify_detail}]})

    # v12: Skill matching trace
    matched_skill = None
    try:
        from hashmm.evolution.skill_manager import get_skill_manager
        matched = get_skill_manager().match_skills(query)
        if matched:
            matched_skill = matched[0]
            yield _sse("trace", {"steps": [{"node": "skill_match", "detail": f"匹配技能: {matched_skill.name} (质量分 {int(matched_skill.quality_score*100)}%)"}]})
    except Exception as _e:
        log_suppressed(logger, _e)

    # ═══════════════════════════════════════════════════════════
    # v13/v14: Agent Loop — ReAct 工具循环（对标 Claude Code）
    # 当任务需要工具调用时（文件生成、URL读取、多步分析、代码），
    # 用 AgentLoop 而不是单次 LLM 调用。
    #
    # 触发条件采用"意图 + 关键词"双信号，比纯关键词更稳：
    #   1) 文件生成意图（PPT/Word/Excel/PDF/报告/导出）
    #   2) URL 读取
    #   3) 复杂多步（对比/分析 + 生成动作）
    #   4) task_type 本身就是文件/研究类
    # ═══════════════════════════════════════════════════════════
    _q_lower = query.lower()
    _FILE_WORDS = [
        "ppt", "pptx", "幻灯片", "slides", "演示", "演示文稿",
        "word", "docx", "文档", "报告", "doc",
        "excel", "xlsx", "表格", "spreadsheet",
        "pdf",
        "做个", "做一个", "生成", "导出", "制作",
    ]
    _GEN_VERBS = ["生成", "做", "写", "导出", "制作", "创建", "出一份", "出个"]
    _has_file_intent = any(w in _q_lower for w in _FILE_WORDS) and any(
        w in query for w in _GEN_VERBS
    ) or any(w in _q_lower for w in ["ppt", "pptx", "docx", "xlsx", "幻灯片", "演示文稿"])
    # 代码意图：写代码/实现某算法/某类 → 也走 AgentLoop（loop 会把代码自动存成可下载文件）。
    _CODE_WORDS = ["代码", "程序", "实现", "函数", "类", "算法", "脚本", "code",
                   "python", "c++", "cpp", "java", "javascript", "golang", "rust",
                   "接口", "模块", ".py", ".cpp", ".js", ".ts", ".java"]
    _has_code_intent = any(w in _q_lower for w in _CODE_WORDS) and any(
        w in query for w in _GEN_VERBS + ["实现", "帮我"]
    )
    _has_url = bool(re.search(r'https?://[^\s]+', query))
    _is_multistep = any(w in query for w in ["对比", "比较", "分析"]) and any(
        w in query for w in _GEN_VERBS
    )
    _is_file_tasktype = task_type in ("file_task", "report_task", "research_task")

    # v14 Phase 2: real-world tasks that benefit from task-expansion / workers.
    # e.g. "帮我订去北京的票", "规划周末行程", "安排..." — the agent should
    # proactively consider weather/route/food, and may spawn workers.
    _TASK_WORDS = ["订票", "订机票", "订酒店", "规划", "安排行程", "行程", "攻略",
                   "计划一下", "帮我订", "帮我规划", "帮我安排"]
    _is_realworld_task = any(w in query for w in _TASK_WORDS)
    # V103.26: 关键词漏判时，用更稳的 detect_life_task 兜底——仅行动型（出行/订票/规划）才上
    # AgentLoop（要工具/worker/多步）；推荐/日常协助走轻路径，靠主动式脚手架给出贴心回答即可。
    if not _is_realworld_task:
        try:
            from hashmm.agent.proactive import detect_life_task as _dlt
            _lt = _dlt(query)
            _is_realworld_task = bool(_lt and _lt.get("kind") in ("trip", "booking", "planning"))
        except Exception as _e:
            log_suppressed(logger, _e)
    # Multi-entity comparison even without an explicit gen verb (e.g. "对比A和B")
    _is_comparison = (any(w in query for w in ["对比", "比较"]) and
                      any(w in query for w in ["和", "与", "跟", "vs", "VS"]))

    needs_agent_loop = (_has_file_intent or _has_url or _is_multistep
                        or _is_file_tasktype or _is_realworld_task or _is_comparison
                        or _has_code_intent)

    if needs_agent_loop and task_type not in ("direct_task",):
        try:
            from hashmm.agent.loop import AgentLoop

            # Build system prompt with skill context
            sys_prompt = _build_system_prompt(
                task_type=task_type, rag_sources=[], retrieval_injection="",
                profile_ctx="", sys_prompts=sys_prompts, user_id=user_id, query=query,
            )

            # Pre-fetch RAG context (give Agent knowledge to work with)
            rag_context = ""
            if task_type not in ("code_task", "modify_task"):
                try:
                    pre_sources, pre_injection, _pre_strategy = await _in_thread(
                        _do_retrieval, query, history, retrieval_mode, doc_filter
                    )
                    if pre_sources:
                        rag_context = pre_injection
                        yield _sse("trace", {"steps": [{"node": "retrieve",
                            "detail": f"预检索: {len(pre_sources)} 条来源"}]})
                except Exception as _e:
                    log_suppressed(logger, _e)

            loop = AgentLoop(
                llm_fn=llm_fn,
                system_prompt=sys_prompt,
                temperature=0.1 if task_type in ("knowledge_task", "comparison_task") else 0.3,
                user_id=user_id,
                conv_id=conv_id,
            )
            # Strict Plan Mode: a side-effecting tool is gated unless the user
            # confirmed. Treat a short confirmation message as approval for this
            # turn (the plan-mode hook reads loop.plan_confirmed via ctx).
            _q = (query or "").strip()
            loop.plan_confirmed = _q in ("确认执行", "确认", "执行", "yes", "确认生成", "同意执行")

            full_content = ""
            all_files = []
            trace_steps = []
            agent_usage = None

            async for event_type, event_data in loop.run(
                query=query,
                history=long_history,  # V51: 压缩在 loop._build_messages 内完成
                user_id=user_id,
                retrieval_context=rag_context,
            ):
                # v15 Phase 7: stop burning GPU/LLM if the client went away
                if request is not None:
                    try:
                        if await request.is_disconnected():
                            logger.info("client disconnected mid-stream; aborting agent loop")
                            break
                    except Exception as _e:
                        log_suppressed(logger, _e)
                if event_type == "token":
                    full_content += event_data
                    yield _sse("token", {"content": event_data})
                elif event_type == "trace":
                    trace_steps.append(event_data)
                    yield _sse("trace", {"steps": [event_data]})
                elif event_type == "file":
                    all_files.append(event_data)
                    # 实时把文件事件发给前端（前端据此立即渲染下载卡片），
                    # 同时仍在 done 事件里带 files 作为兜底。
                    yield _sse("file", event_data)
                elif event_type == "thinking":
                    # V49: DeepSeek reasoning_content 透传给前端 ThinkingPanel
                    yield _sse("thinking", {"content": event_data.get("content", "")})
                elif event_type == "delta":
                    # V54: 真·流式直播增量（纯预览；权威内容仍走 narrate/token）
                    yield _sse("delta", {"t": event_data})
                elif event_type == "delta_commit":
                    yield _sse("delta_commit", event_data)
                elif event_type == "file_delta":
                    # V55: 右栏逐字写代码直播（纯预览，权威 file 事件随后照常）
                    yield _sse("file_delta", event_data)
                elif event_type == "ctx":
                    yield _sse("ctx", event_data)
                elif event_type == "todo":
                    # V50: 任务清单（对标 Claude Code TodoWrite）——前端渲染 checklist，
                    # 同名事件全量覆盖（最后一次为准）。
                    yield _sse("todo", {"items": event_data.get("items", [])})
                elif event_type == "tool_start":
                    # V49: 发结构化 step_start（前端 onStepStart 已支持），id 与
                    # step_done 配对 → 同一工具在时间线上原地从"运行中"变"完成"，
                    # 不再被压成两条无 id 的 trace 冗余对。
                    _args_summary = ", ".join(
                        f"{k}={str(v)[:30]}" for k, v in event_data.get("args", {}).items())
                    yield _sse("step_start", {
                        "id": event_data.get("id", ""),
                        "tool": event_data["name"],
                        "detail": _args_summary,
                    })
                elif event_type == "tool_done":
                    yield _sse("step_done", {
                        "id": event_data.get("id", ""),
                        "tool": event_data["name"],
                        "status": event_data.get("status", "done"),
                        "detail": (event_data.get("result") or "")[:120],
                        "duration_ms": event_data.get("elapsed_ms", 0),
                    })
                elif event_type == "done":
                    # V52: 捕获 token 用量（loop 在 done 里透出），随最终 SSE done 下发
                    agent_usage = event_data.get("usage")

            elapsed = round((time.time() - t0) * 1000)

            # Save to DB
            # V57: files/时间线一并落库——此前只存 content，重开会话后文件卡和
            # 执行轨迹全部消失（真机用户实测反馈）。
            db.update_message(assistant_msg_id, content=full_content, status="complete",
                              files=all_files, tool_calls=trace_steps)

            # Evolution —— V103.90: 把 agent loop 算出的忠实度接地率作为奖励信号传入，
            # 让 agentic 多工具/deep_search 回合的自我进化也由"答得对不对"驱动（跨路径统一）。
            _agent_faith = getattr(loop, "_last_faithfulness_ratio", None)
            _run_evolution_safe(user_id, query, full_content, task_type,
                              retrieval_mode=retrieval_mode, elapsed_ms=elapsed,
                              faithfulness_ratio=_agent_faith)

            # v14 Phase 4: learn durable user preferences from this exchange
            # (runs in a thread; failure never affects the response)
            try:
                async def _learn():
                    try:
                        msgs = [{"role": "user", "content": query},
                                {"role": "assistant", "content": full_content}]
                        await _in_thread(loop.memory.extract_and_save_preferences, msgs, llm_fn)
                    except Exception as _e:
                        log_suppressed(logger, _e)
                import asyncio as _aio
                _aio.create_task(_learn())
            except Exception as _e:
                log_suppressed(logger, _e)

            # v15 Phase 10: record token usage + cost (best-effort)
            # V52: 真实 usage 优先（loop 从 API 响应累计），拿不到才退回字符估算
            try:
                if agent_usage:
                    _tin = int(agent_usage.get("prompt_tokens", 0) or 0)
                    _tout = int(agent_usage.get("completion_tokens", 0) or 0)
                else:
                    _tin = int(len(query) / 1.8)
                    _tout = int(len(full_content) / 1.8)
                _model = ""
                try:
                    _model = getattr(llm_fn, "model_name", "") or getattr(llm_fn, "model", "") or ""
                except Exception as _e:
                    log_suppressed(logger, _e)
                from hashmm.api import usage as _usage
                _usage.record(user_id, username, _model or "default",
                              _tin, _tout, task_type)
            except Exception as _e:
                log_suppressed(logger, _e)

            # Suggestions
            suggestions = _generate_suggestions_safe(query, full_content, retrieval_mode, [], task_type)

            # V103.27: 主动澄清 — 回复里带 [[ASK]] 块则 emit clarify 事件（前端渲染可点选项）
            try:
                from hashmm.agent.proactive import parse_clarify
                _clar = parse_clarify(full_content)
                if _clar:
                    yield _sse("clarify", _clar)
            except Exception as _e:
                log_suppressed(logger, _e)

            yield _sse("done", {
                "sources": [], "trace": [], "steps": [],
                "elapsed_ms": elapsed, "session_id": conv_id,
                "suggestions": suggestions,
                "tokens": ({"input": int(agent_usage.get("prompt_tokens", 0) or 0),
                            "output": int(agent_usage.get("completion_tokens", 0) or 0)}
                           if agent_usage else
                           {"input": int(len(query) / 1.8), "output": int(len(full_content) / 1.8)}),
                "usage": agent_usage,  # V52: 真实 token 用量（可能为 None）
                "files": all_files if all_files else None,
            })
            return  # Agent Loop handled everything

        except Exception as e:
            logger.warning(f"Agent Loop failed, falling back to normal flow: {e}", exc_info=True)
            # Fall through to normal RAG flow

    # ── Phase 2.5 (legacy): Sub-Agent check ──
    ppt_requested = False
    try:
        from hashmm.agent.orchestrator import SubAgentOrchestrator
        orch = SubAgentOrchestrator(llm_fn=llm_fn, search_fn=None)
        if orch.should_decompose(query) and task_type not in ("code_task", "modify_task", "direct_task"):
            plan = orch.plan(query)
            if len(plan.subtasks) > 1:
                yield _sse("trace", {"steps": [{"node": "decompose", "detail": f"分解为 {len(plan.subtasks)} 个子任务 ({plan.strategy})"}]})

                # Plan Mode: surface the plan preview (what the agent intends to
                # do) before executing — the structured preview the UI can render.
                # The strict-mode *enforcement* is done at the tool layer (hooks),
                # so it works regardless of which path (Agent Loop / orchestrator)
                # actually generates — see hooks._plan_mode_hook.
                try:
                    _summary = orch.plan_summary(plan)
                    if _summary["needs_confirmation"]:
                        yield _sse("plan", _summary)
                except Exception as _e:
                    log_suppressed(logger, _e)

                # Check if PPT/doc generation was requested
                ppt_requested = any(t.tool_hint == "generate" for t in plan.subtasks)

                # Wire search function with retry
                try:
                    from hashmm.chat_retrieval import get_chat_retrieval
                    cr = get_chat_retrieval()
                    def _search(q):
                        _, sources, _ = cr.enhance(q, [])
                        if sources and sources[0].get("score", -99) > -2.0:
                            return sources
                        for extra in ["营收 净利润 总收入", "收入 盈利 增长率", "合并财务报表"]:
                            _, retry_sources, _ = cr.enhance(f"{q} {extra}", [])
                            if retry_sources:
                                best_retry = retry_sources[0].get("score", -99)
                                best_orig = sources[0].get("score", -99) if sources else -99
                                if best_retry > best_orig:
                                    return retry_sources
                        return sources
                    orch.search_fn = _search
                except Exception as _e:
                    log_suppressed(logger, _e)

                # V103.30: 子 agent 编排实时可视 —— 先把「团队/DAG」发给前端（各 worker 待命），
                # 再把执行从「物化再循环」改成「边跑边流」（execute_plan 是同步生成器 → 丢线程产事件、
                # 主协程从队列取并实时 yield），让用户看着每个子 agent 实时点亮。这是 Marvis/大厂云端
                # 给不了的「在场感」（准确版 §4.1 点名的招牌）。
                try:
                    _team = orch.team_for_plan(plan)
                    _tm_members = _team.get("members", [])
                    _members = []
                    for _i, _st in enumerate(plan.subtasks):
                        _tm = _tm_members[_i] if _i < len(_tm_members) else {}
                        _members.append({"id": _st.id, "step": _i + 1,
                                         "role_label": _tm.get("role_label", ""),
                                         "task": _st.description})
                    yield _sse("orchestration", {"strategy": plan.strategy, "members": _members})
                except Exception as _e:
                    log_suppressed(logger, _e)

                import queue as _queue
                import threading as _threading
                _ev_q: "_queue.Queue" = _queue.Queue()
                _SENT = object()

                def _run_plan_stream():
                    try:
                        for _ev in orch.execute_plan(plan):
                            _ev_q.put(_ev)
                    except Exception as _e:
                        log_suppressed(logger, _e)
                    finally:
                        _ev_q.put(_SENT)

                _threading.Thread(target=_run_plan_stream, daemon=True).start()

                collected_context = ""
                all_rag_sources = []
                while True:
                    ev = await asyncio.to_thread(_ev_q.get)
                    if ev is _SENT:
                        break
                    ev_type, ev_data = ev
                    if ev_type == "subtask_start":
                        # Only show brief trace — NO raw content to user
                        yield _sse("trace", {"steps": [{"node": "sub_agent",
                            "detail": f"({ev_data['step']}/{ev_data['total']}) {ev_data['description']}"}]})
                        yield _sse("subagent", {"id": ev_data.get("id", ""), "status": "running",
                                                "step": ev_data.get("step"), "total": ev_data.get("total"),
                                                "description": ev_data.get("description", "")})
                    elif ev_type == "subtask_result":
                        result_text = ev_data.get("result", "")
                        desc = ev_data.get("description", "")
                        elapsed_ms = ev_data.get("elapsed_ms", 0)
                        # Collect context for synthesis (user never sees this raw)
                        collected_context += f"\n\n### {desc}\n{_clip(result_text, 3000)}"
                        # Extract source refs
                        import re as _re
                        for m in _re.finditer(r'\[(.+?\.pdf)\s+p\.(\d+)\]', result_text):
                            all_rag_sources.append({"filename": m.group(1), "page": int(m.group(2)), "text": "", "score": 0})
                        yield _sse("subagent", {"id": ev_data.get("id", ""), "status": "done",
                                                "elapsed_ms": elapsed_ms, "preview": result_text[:120]})

                if not collected_context.strip():
                    # Nothing collected — fall through to normal flow
                    pass
                else:
                    # ── Synthesize final answer with LLM (stream to user) ──
                    yield _sse("trace", {"steps": [{"node": "generate", "detail": "综合分析中..."}]})

                    synthesis_prompt = (
                        f"你是一名专业分析师。以下是通过检索获得的相关信息，请基于这些信息综合回答用户的问题。\n\n"
                        f"## 检索到的信息\n{_clip(collected_context, 12000)}\n\n"
                        f"## 用户问题\n{query}\n\n"
                        f"## 回答要求\n"
                        f"1. 直接给出结构化分析，不要重复展示原始检索数据\n"
                        f"2. 关键数据用表格呈现，确保表格完整\n"
                        f"3. 所有数据标注来源（文档名和页码），格式如 [腾讯年报 p.5]\n"
                        f"4. 如果某项数据在检索结果中缺失，明确指出并建议如何获取\n"
                        f"5. 先给核心结论，再展开详细分析\n"
                        f"6. 如果信息不足以完成完整对比，说明已有的和缺失的分别是什么\n"
                    )

                    msgs_synth = [
                        {"role": "system", "content": "你是一名专业的财务分析师和研究顾问。请给出准确、结构化、数据驱动的分析。"},
                        {"role": "user", "content": synthesis_prompt},
                    ]

                    # Determine temperature
                    _synth_temp = 0.3

                    # Stream the synthesized answer
                    full_content = ""
                    llm_events = await asyncio.to_thread(_stream_llm_sync, llm_fn, msgs_synth, _synth_temp)
                    for ev in llm_events:
                        if ev["type"] == "token":
                            full_content += ev["content"]
                            yield _sse("token", {"content": ev["content"]})
                        elif ev["type"] == "think":
                            yield _sse("thinking", {"content": ev["content"]})
                        elif ev["type"] == "error":
                            err_msg = _handle_llm_error(ev.get("repr", ""), [])
                            full_content += err_msg
                            yield _sse("token", {"content": err_msg})
                            break

                    elapsed = round((time.time() - t0) * 1000)

                    # Deduplicate sources
                    seen_srcs = set()
                    unique_sources = []
                    for s in all_rag_sources:
                        key = f"{s['filename']}_{s.get('page', 0)}"
                        if key not in seen_srcs:
                            seen_srcs.add(key)
                            unique_sources.append(s)

                    # Truncation detection
                    suggestions = []
                    if full_content:
                        last = full_content[-50:]
                        is_trunc = (last.count("|") >= 2 or last.count("```") % 2 != 0 or
                                   (len(full_content) > 3000 and not any(full_content.rstrip().endswith(c)
                                    for c in ["。", ".", "！", "!", "？", "?", "```", "|", "\n"])))
                        if is_trunc:
                            suggestions = ["继续", "继续输出完整内容"]

                    # If PPT was requested, generate it
                    files_data = []
                    if ppt_requested and full_content:
                        yield _sse("trace", {"steps": [{"node": "tool", "detail": "生成 PPT 文件..."}]})
                        try:
                            ppt_result = await asyncio.to_thread(
                                _generate_pptx_from_context, llm_fn, query, full_content
                            )
                            if ppt_result and ppt_result.get("ok"):
                                files_data.append({
                                    "filename": ppt_result.get("filename", "report.pptx"),
                                    "download_url": ppt_result.get("download_url", ""),
                                    "size": ppt_result.get("size", 0),
                                })
                                yield _sse("token", {"content": f"\n\n📊 **PPT 已生成**: [{ppt_result.get('filename', 'report.pptx')}]({ppt_result.get('download_url', '')})\n"})
                                yield _sse("trace", {"steps": [{"node": "done", "detail": f"PPT 生成完成: {ppt_result.get('filename', '')}"}]})
                        except Exception as ppt_err:
                            logger.warning(f"PPT generation failed: {ppt_err}")
                            yield _sse("trace", {"steps": [{"node": "tool", "detail": f"PPT 生成失败: {str(ppt_err)[:60]}"}]})

                    # Evolution + done
                    yield _sse("trace", {"steps": [{"node": "evolution", "detail": "更新用户画像"}]})
                    _run_evolution_safe(user_id, query, full_content, task_type,
                                      retrieval_mode=retrieval_mode, n_sources=len(unique_sources),
                                      elapsed_ms=elapsed)
                    yield _sse("trace", {"steps": [{"node": "done", "detail": f"完成 ({elapsed}ms, {len(full_content)}字)"}]})

                    db.update_message(assistant_msg_id, content=full_content, status="complete")

                    done_sources = [{"id": f"s{i}", "filename": s.get("filename", ""), "page": s.get("page", -1),
                                    "score": s.get("score", 0), "text": s.get("text", "")[:150]}
                                   for i, s in enumerate(unique_sources[:10])]

                    # V103.27: 主动澄清 — 回复里带 [[ASK]] 块则 emit clarify 事件（前端渲染可点选项）
                    try:
                        from hashmm.agent.proactive import parse_clarify
                        _clar = parse_clarify(full_content)
                        if _clar:
                            yield _sse("clarify", _clar)
                    except Exception as _e:
                        log_suppressed(logger, _e)

                    yield _sse("done", {
                        "sources": done_sources, "trace": [], "steps": [],
                        "elapsed_ms": elapsed, "session_id": conv_id,
                        "suggestions": suggestions,
                        "tokens": {"input": int(len(synthesis_prompt) / 1.8), "output": int(len(full_content) / 1.8)},
                        "files": files_data if files_data else None,
                    })
                    return
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Sub-agent check failed: {e}")

    # ── Phase 3: FAST PATH (all tasks except modify_task) ──
    if task_type not in ("modify_task",):

        effective_query = query
        if file_context:
            effective_query = f"用户上传了文件，内容如下：\n{_clip(file_context, 8000)}\n\n{query}"

        # v12: URL auto-detection — if query contains a URL, auto-fetch it
        url_content = ""
        url_match = re.search(r'https?://[^\s<>\"\']+', query)
        if url_match and not file_context:
            detected_url = url_match.group(0).rstrip(",.;:!?）)")
            yield _sse("trace", {"steps": [{"node": "tool", "detail": f"读取 URL: {detected_url[:60]}..."}]})
            try:
                from hashmm.tools.fetch_url import execute as _fetch_url_exec
                url_result = await _in_thread(_fetch_url_exec, {"url": detected_url}, {})
                if url_result and not url_result.startswith("Error"):
                    url_content = _clip(url_result, 12000)
                    yield _sse("trace", {"steps": [{"node": "tool", "detail": f"URL 内容获取成功 ({len(url_content)} 字符)"}]})
                    # Inject URL content as context
                    effective_query = f"以下是从 URL 获取的内容：\n{url_content}\n\n用户的问题：{query}"
                else:
                    yield _sse("trace", {"steps": [{"node": "tool", "detail": f"URL 获取失败: {url_result[:80]}"}]})
            except Exception as e:
                yield _sse("trace", {"steps": [{"node": "tool", "detail": f"URL 获取异常: {str(e)[:60]}"}]})

        # Retrieval (in thread to not block)
        rag_sources = []
        retrieval_injection = ""
        retrieval_strategy = ""   # V103.47: router verdict (e.g. "insufficient")
        _stage_latency: dict = {}  # per-stage latency (retrieve/rerank/...) for SLO
        # v12: Expanded greeting detection
        _q = query.strip().lower()
        is_greeting = (len(_q) < 10 and any(g in _q for g in
            ["你好", "hello", "hi", "谢谢", "嗨", "在吗", "hey", "nihao", "早上好", "晚上好", "下午好"]))
        # v12: Code tasks don't need RAG retrieval
        is_code = task_type in ("code_task", "modify_task")

        if is_code:
            yield _sse("trace", {"steps": [{"node": "retrieve", "detail": "代码任务，跳过知识检索"}]})
        elif is_greeting:
            pass  # No trace needed for greetings
        elif not file_context and not url_content:
            try:
                yield _sse("trace", {"steps": [{"node": "retrieve", "detail": f"检索知识库 ({retrieval_mode})..." + (f" · {route_reason}" if route_reason else "")}]})
                _t_retr = time.time()
                # v17 Phase 73: agentic multi-hop retrieval (opt-in). Default off
                # → single-shot _do_retrieval, i.e. unchanged behavior.
                import os as _os_ag
                # V103.73: 选项A — 训练好的 Search-R1 策略驱动检索（opt-in，默认关）。
                # 不可用（无 GPU / 未配权重）则返回 None，自动回退到下方 agentic / 单次检索，零风险。
                _sr1_result = None
                if _os_ag.environ.get("HASHMM_SEARCHR1", "0").strip().lower() in ("1", "true", "yes", "on"):
                    _sr1_result = await _in_thread(
                        _searchr1_retrieval, query, history, retrieval_mode, doc_filter
                    )
                if _sr1_result is not None:
                    rag_sources, retrieval_injection, retrieval_strategy = _sr1_result
                elif _os_ag.environ.get("HASHMM_AGENTIC_RETRIEVAL", "0").strip().lower() in ("1", "true", "yes", "on"):
                    rag_sources, retrieval_injection, retrieval_strategy = await _in_thread(
                        _agentic_retrieval, query, history, retrieval_mode, doc_filter
                    )
                else:
                    rag_sources, retrieval_injection, retrieval_strategy = await _in_thread(
                        _do_retrieval, query, history, retrieval_mode, doc_filter
                    )
                _stage_latency["retrieve"] = round((time.time() - _t_retr) * 1000)
                # v17 Phase 80: confidence-gated escalation. If confidence mode is
                # on and the initial (single-shot) retrieval looks low-confidence,
                # escalate to multi-hop retrieval (Phase 73) to gather more
                # evidence. No-op unless HASHMM_CONFIDENCE=1; skipped if agentic
                # already ran; falls back silently on any error.
                _already_agentic = _os_ag.environ.get("HASHMM_AGENTIC_RETRIEVAL", "0").strip().lower() in ("1", "true", "yes", "on")
                if (rag_sources and not _already_agentic
                        and _os_ag.environ.get("HASHMM_CONFIDENCE", "0").strip().lower() in ("1", "true", "yes", "on")):
                    try:
                        from hashmm.agent.confidence import assess_retrieval
                        _ca = assess_retrieval(query, rag_sources, miss_fn=_sources_miss_query_entity)
                        if _ca.get("decision") == "escalate":
                            yield _sse("trace", {"steps": [{"node": "retrieve", "detail": f"低置信({_ca['confidence']:.2f})，升级多跳补检索..."}]})
                            _more, _moreinj, _morestrat = await _in_thread(
                                _agentic_retrieval, query, history, retrieval_mode, doc_filter)
                            if _more and len(_more) >= len(rag_sources):
                                rag_sources, retrieval_injection = _more, _moreinj
                                retrieval_strategy = _morestrat or retrieval_strategy
                                _stage_latency["confidence_escalation"] = 1
                    except Exception as _e:
                        log_suppressed(logger, _e)
                if rag_sources:
                    top = rag_sources[0].get("score", 0)
                    yield _sse("trace", {"steps": [{"node": "rerank", "detail": f"精排完成: {len(rag_sources)} 条来源 (top={top:.2f})"}]})

                    # v17 Phase 18b: If retrieval quality is weak, the corpus
                    # likely lacks the answer → supplement with WEB SEARCH (not
                    # refuse). Enterprise KBs can't contain everything; querying
                    # the web to actually solve the user's problem is the right
                    # behavior (like Perplexity/Claude with web). Threshold raised
                    # from -2.5 (almost never fired) to a level that catches real
                    # out-of-corpus questions. Configurable.
                    import os as _os
                    _web_thresh = float(_os.environ.get("HASHMM_WEB_FALLBACK_THRESHOLD", "2.5"))
                    _web_enabled = _os.environ.get("HASHMM_WEB_FALLBACK", "1") == "1"
                    # Trigger web search if EITHER the score is weak OR the
                    # retrieved sources don't actually mention what was asked.
                    # The score alone is unreliable: the Apple query scored 1.77
                    # ("grounded") yet every source was about 小米/网易 — the
                    # corpus simply lacks Apple. Entity-presence catches that.
                    weak_score = top < _web_thresh
                    missing_entity = _sources_miss_query_entity(query, rag_sources)
                    if ((weak_score or missing_entity)
                            and task_type not in ("code_task", "direct_task")):
                        # Step 1 (CRAG corrective): rewrite + re-search LOCAL KB
                        # first — cheaper/privacy-preserving than web. Only if it
                        # doesn't lift quality do we fall back to the web.
                        _ce = _os.environ.get("HASHMM_CORRECTIVE_RETRIEVAL", "1") == "1"
                        if _ce:
                            yield _sse("trace", {"steps": [{"node": "retrieve", "detail": "检索偏弱，改写查询重检索知识库..."}]})
                            _corrected, _cinj = await _in_thread(
                                _corrective_retrieval, query, history, retrieval_mode, doc_filter, rag_sources)
                            _new_top = _corrected[0].get("score", 0) if _corrected else 0
                            if _corrected and _new_top > top:
                                rag_sources = _corrected
                                retrieval_injection = _cinj or retrieval_injection
                                top = _new_top
                                _stage_latency["corrective"] = 1  # mark that it fired
                                yield _sse("trace", {"steps": [{"node": "rerank", "detail": f"纠正检索提升: top={top:.2f} ({len(rag_sources)} 条)"}]})
                            # re-evaluate weakness after correction
                            weak_score = top < _web_thresh
                            missing_entity = _sources_miss_query_entity(query, rag_sources)

                    # Step 2 (web fallback): still weak after correction → web.
                    if (_web_enabled and (weak_score or missing_entity)
                            and task_type not in ("code_task", "direct_task")):
                        reason = "相关度低" if weak_score else "未覆盖问题主体"
                        yield _sse("trace", {"steps": [{"node": "tool", "detail": f"知识库{reason}，联网搜索补充..."}]})
                        try:
                            web_result = await _in_thread(_web_search_supplement, query)
                            if web_result and not _web_search_failed(web_result):
                                retrieval_injection += f"\n\n[联网搜索补充]\n{web_result}"
                                yield _sse("trace", {"steps": [{"node": "tool", "detail": f"联网搜索完成 ({len(web_result)} 字符)"}]})
                            else:
                                yield _sse("trace", {"steps": [{"node": "tool", "detail": "联网搜索未返回有效结果"}]})
                        except Exception as _e:
                            log_suppressed(logger, _e)
                else:
                    # No local results at all — try web search
                    if task_type not in ("code_task", "direct_task"):
                        yield _sse("trace", {"steps": [{"node": "tool", "detail": "知识库无结果，联网搜索..."}]})
                        try:
                            web_result = await _in_thread(_web_search_supplement, query)
                            if web_result and not _web_search_failed(web_result):
                                retrieval_injection = f"[联网搜索结果]\n{web_result}"
                                yield _sse("trace", {"steps": [{"node": "tool", "detail": f"联网搜索完成 ({len(web_result)} 字符)"}]})
                            else:
                                yield _sse("trace", {"steps": [{"node": "retrieve", "detail": "知识库和联网均无结果"}]})
                        except Exception:
                            yield _sse("trace", {"steps": [{"node": "retrieve", "detail": "未找到相关来源，直接回答"}]})
                    else:
                        yield _sse("trace", {"steps": [{"node": "retrieve", "detail": "未找到强相关来源，直接回答"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

        # Build system prompt
        sys_content = _build_system_prompt(
            task_type, rag_sources, retrieval_injection, profile_ctx, sys_prompts,
            user_id, query, strategy=retrieval_strategy
        )

        # Token budget build
        try:
            from hashmm.context_builder import TokenBudgetBuilder
            ctx_v6 = TokenBudgetBuilder(total_budget=8000)

            memory_context = ""
            try:
                user_memories = db.get_user_memory(user_id, limit=10)
                if user_memories:
                    memory_context = "用户偏好记忆：\n" + "\n".join(
                        f"- {m['key']}: {m['value']}" for m in user_memories
                    )
            except Exception as _e:
                log_suppressed(logger, _e)

            user_model_ctx = ""
            try:
                from hashmm.evolution.user_model import get_user_model
                user_model_ctx = get_user_model().get_personalization_prompt(user_id)
            except Exception as _e:
                log_suppressed(logger, _e)

            # v11.0: Episodic memory — recall past successful strategies
            episodic_ctx = ""
            try:
                from hashmm.evolution.episodic_memory import get_episodic_memory
                episodic_ctx = get_episodic_memory().get_strategy_hint(user_id, query)
                if episodic_ctx:
                    yield _sse("trace", {"steps": [{"node": "memory_recall", "detail": "找到相似经验，注入策略提示"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

            # v17 Phase 19: cross-session task memory — proactively offer to
            # resume an unfinished task on a different subject (Marvis-style).
            task_ctx = ""
            try:
                from hashmm.evolution.task_memory import get_task_memory
                task_ctx = get_task_memory().get_resume_hint(user_id, query)
                if task_ctx:
                    yield _sse("trace", {"steps": [{"node": "memory_recall", "detail": "发现未完成的任务，可主动续接"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

            full_profile = "\n".join(p for p in [profile_ctx, memory_context, user_model_ctx, episodic_ctx, task_ctx] if p)
            msgs = ctx_v6.build(
                task_type=task_type, query=effective_query, system_prompt=sys_content,
                history=_compact_hist(long_history, keep_recent=8, char_budget=16000),
                retrieval_injection=retrieval_injection,
                file_context="", user_profile=full_profile,
            )

            # v12: Context build trace
            total_tokens = sum(len(m.get("content", "")) // 2 for m in msgs)
            yield _sse("trace", {"steps": [{"node": "context_build", "detail": f"构建上下文: ~{total_tokens} tokens ({len(msgs)} 条消息)"}]})
        except Exception:
            msgs = [{"role": "system", "content": sys_content}, {"role": "user", "content": effective_query}]

        # LLMRouter — temperature by intent + user style preference
        user_prefs = {}
        if answer_style:
            user_prefs["temperature"] = {"factual": 0.1, "analytical": 0.5, "creative": 0.8}.get(answer_style, 0.5)
        llm_params = _llm_router.get_params(task_type, user_prefs or None)
        if msgs and msgs[0]["role"] == "system":
            msgs[0]["content"] += llm_params.get("system_suffix", "")
        _stream_temp = llm_params.get("temperature", 0.5)

        # Pre-send sources
        if rag_sources:
            pre_sources = [{"id": s["id"], "filename": s.get("filename", ""),
                           "page": s.get("page", -1), "score": s.get("score", 0),
                           "text": s.get("text", "")[:150]} for s in rag_sources]
            yield _sse("sources", {"sources": pre_sources})

        yield _sse("trace", {"steps": [{"node": "generate", "detail": f"生成回答 (temp={_stream_temp})..."}]})

        # ── Phase 4: Stream LLM response (in thread — non-blocking!) ──
        full_content = ""
        if llm_fn:
            events = await asyncio.to_thread(_stream_llm_sync, llm_fn, msgs, _stream_temp)
            for ev in events:
                if ev["type"] == "think":
                    yield _sse("thinking", {"content": ev["content"]})
                elif ev["type"] == "token":
                    full_content += ev["content"]
                    yield _sse("token", {"content": ev["content"]})
                elif ev["type"] == "error":
                    full_content = _handle_llm_error(ev["repr"], rag_sources)
                    yield _sse("token", {"content": full_content})
        else:
            full_content = "LLM 未配置，请在管理后台添加模型"
            yield _sse("token", {"content": full_content})

        # v17 Phase 25 (B1): never surface an empty answer. Observed empties on
        # overclaim/over-vague prompts — fall back to a safe, honest message.
        if not (full_content or "").strip():
            from hashmm.answer_guard import guard_empty_answer
            full_content = guard_empty_answer(
                full_content, strategy=("grounded" if rag_sources else "insufficient"))
            yield _sse("token", {"content": full_content})

        # v17 Phase 29 (OWASP LLM02): redact any PII that slipped into the output.
        try:
            from hashmm.rag_security import guard_output_pii
            full_content = guard_output_pii(full_content)
        except Exception as _e:
            log_suppressed(logger, _e)

        # V103.47: 确定性诚实拒答兜底。当检索路由判定本次 insufficient（语料未覆盖
        # 该问题主体）时，无论模型怎么措辞，都保证最终答案是诚实的"知识库中没有"，
        # 绝不放出凭参数记忆编造的具体数字。enforce 不会编造、已通过单测。
        if retrieval_strategy == "insufficient":
            try:
                from hashmm.refusal_guard import enforce as _enforce_refusal
                _guarded = _enforce_refusal(full_content, query, mode="insufficient")
                if _guarded != full_content:
                    # 模型的措辞不够诚实/疑似编造 → 用诚实版覆盖。发一个清晰的
                    # 纠正提示给前端（前端按 token 追加），并把权威诚实版落库。
                    yield _sse("token", {"content":
                        "\n\n——\n" + _guarded})
                    full_content = _guarded
                    yield _sse("trace", {"steps": [{"node": "safety_check",
                        "detail": "诚实性校验：本次知识库未覆盖，已确保如实告知"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

        elapsed = round((time.time() - t0) * 1000)

        # ── Phase 5: Post-processing ──
        # Citation validation
        if rag_sources and full_content:
            try:
                from hashmm.api.core.citation_validator import validate_citations
                full_content = validate_citations(full_content, len(rag_sources))
                yield _sse("trace", {"steps": [{"node": "safety_check", "detail": "引用验证通过"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

            # v16 Phase 14: groundedness check — flag citations weakly supported
            # by their sources (catches "看着有引用其实编的"). Lightweight, no LLM.
            try:
                from hashmm.generation.groundedness import (
                    citation_overlap_check, groundedness_caveat)
                gres = citation_overlap_check(full_content, rag_sources)
                caveat = groundedness_caveat(gres)
                if caveat:
                    full_content += caveat
                    yield _sse("trace", {"steps": [{"node": "safety_check",
                        "detail": f"⚠️ 引用依据偏弱 ({gres['supported']}/{gres['checked']})"}]})
                elif gres.get("checked"):
                    yield _sse("trace", {"steps": [{"node": "safety_check",
                        "detail": f"✅ 引用依据校验 {gres['supported']}/{gres['checked']}"}]})
            except Exception as _e:
                log_suppressed(logger, _e)

        # Suggestions
        suggestions = _generate_suggestions_safe(query, full_content, retrieval_mode, rag_sources, task_type)

        # v16 Phase 14: sample real-traffic quality for the online dashboard
        try:
            from hashmm.api import quality_monitor as _qm
            if _qm.should_sample():
                _qm.record_sample(user_id, query, full_content, rag_sources,
                                  task_type, round((time.time() - t0) * 1000))
        except Exception as _e:
            log_suppressed(logger, _e)

        # v12.1: Auto-generate files when user requested (PPTX, DOCX, XLSX, MD)
        doc_files = []
        _q_lower = query.lower()
        # Detect file type request
        file_type = None
        if any(w in _q_lower for w in ["ppt", "做ppt", "生成ppt", "幻灯片", "演示文稿", "slides", "slide"]):
            file_type = "pptx"
        elif any(w in _q_lower for w in ["word", "docx", "word文档"]):
            file_type = "docx"
        elif any(w in _q_lower for w in ["excel", "xlsx", "表格文件", "导出表格", "电子表格"]):
            file_type = "xlsx"
        elif any(w in _q_lower for w in ["导出csv", "csv文件"]):
            file_type = "csv"
        elif any(w in _q_lower for w in ["导出md", "markdown文件"]):
            file_type = "md"
        # "做报告" without specifying format → default to PPTX if analysis, DOCX otherwise
        elif any(w in _q_lower for w in ["做报告", "生成报告", "写报告", "出报告"]):
            file_type = "pptx" if any(w in _q_lower for w in ["分析", "对比", "趋势"]) else "docx"

        if file_type and full_content and len(full_content) > 100:
            type_label = {"pptx": "PPT", "docx": "Word", "xlsx": "Excel", "csv": "CSV", "md": "Markdown"}.get(file_type, file_type)
            yield _sse("trace", {"steps": [{"node": "tool", "detail": f"生成 {type_label} 文件..."}]})
            try:
                gen_result = await asyncio.to_thread(
                    _auto_generate_file, llm_fn, query, full_content, file_type
                )
                if gen_result and gen_result.get("ok"):
                    fname = gen_result.get("filename", f"output.{file_type}")
                    url = gen_result.get("download_url", "")
                    doc_files.append({"filename": fname, "download_url": url, "size": gen_result.get("size", 0)})
                    emoji = {"pptx": "📊", "docx": "📄", "xlsx": "📈", "csv": "📋", "md": "📝"}.get(file_type, "📁")
                    file_msg = f"\n\n{emoji} **{type_label} 已生成**: [{fname}]({url})"
                    full_content += file_msg
                    yield _sse("token", {"content": file_msg})
                    yield _sse("trace", {"steps": [{"node": "done", "detail": f"{type_label}: {fname}"}]})
                else:
                    err = gen_result.get("message", "未知错误") if gen_result else "生成失败"
                    yield _sse("trace", {"steps": [{"node": "tool", "detail": f"{type_label} 生成失败: {err[:60]}"}]})
            except Exception as gen_err:
                logger.warning(f"Auto file generation failed: {gen_err}")

        # v12: Detect truncation — if answer was cut off, add "继续" suggestion
        if full_content:
            last_chars = full_content[-50:]
            # Signs of truncation: ends mid-table, mid-code, mid-sentence without period
            is_truncated = (
                last_chars.count("|") >= 2 and not last_chars.strip().endswith("|")  # mid-table
                or last_chars.count("```") % 2 != 0  # unclosed code block
                or (len(full_content) > 3000 and not any(full_content.rstrip().endswith(c) for c in ["。", ".", "！", "!", "？", "?", "```", "|", "\n"]))
            )
            if is_truncated:
                suggestions = ["继续", "继续输出完整内容"] + (suggestions or [])
                suggestions = suggestions[:4]

        # V103.90: 忠实度合约（直答路径）——逐句核验"事实声明是否引用了、且所引证据真能
        # 支撑它"。这里**提前**算一次：① 接地率喂给自我进化的奖励信号（reward）；
        # ② 未接地声明清单挂到 done 事件，前端据此标灰/加 ⚠️（每句可溯源是企业卖点）。
        # 纯词法判定（不给交互路径加 LLM 延迟）；任何异常都不影响正常返回。
        _faith = None
        _faith_ratio = None
        try:
            if rag_sources and full_content:
                from hashmm.evaluation import faithfulness as _fa
                _frep = _fa.audit_faithfulness(full_content, rag_sources)
                if _frep.checked:
                    _faith = _fa.summarize_for_ui(_frep)
                    _faith_ratio = _frep.ratio
                    if _faith.get("flagged"):
                        yield _sse("trace", {"steps": [{"node": "faithfulness",
                            "detail": (f"忠实度校验：{_frep.supported}/{_frep.total_factual} 条事实可溯源"
                                       f"（接地率 {round(_frep.ratio*100)}%），"
                                       f"{len(_faith['flagged'])} 条待核")}]})
        except Exception as _fe:
            log_suppressed(logger, _fe)

        # Evolution engine —— 把忠实度接地率作为奖励信号传入（驱动"记好经验"）。
        yield _sse("trace", {"steps": [{"node": "evolution", "detail": "更新用户画像 + 记录经验"}]})
        _run_evolution_safe(
            user_id, query, full_content, task_type,
            retrieval_mode=retrieval_mode,
            n_sources=len(rag_sources),
            top_score=rag_sources[0].get("score", 0) if rag_sources else 0,
            elapsed_ms=elapsed,
            faithfulness_ratio=_faith_ratio,
        )

        # v12: Done trace
        content_len = len(full_content)
        yield _sse("trace", {"steps": [{"node": "done", "detail": f"完成 ({elapsed}ms, {content_len}字)"}]})

        # V103.90 方案4：终答阶段不确定性闸——综合「置信度 + 检索分离散度 + 接地率」判该不该让
        # 用户存疑（与忠实度互补：忠实=接没接地，不确定=证据够不够强）。低误报：仅 medium/high 标。
        # 在 evolution 之后算（标注含"资料不足"字样，避免被 compute_reward 当错误措辞误降 reward）。
        _unc_ui = None
        _save_content = full_content
        try:
            from hashmm.agent import uncertainty as _unc_mod
            if _unc_mod.uncertainty_enabled() and (rag_sources or full_content):
                _urep = _unc_mod.assess_uncertainty(
                    query, rag_sources, full_content, grounding_ratio=_faith_ratio)
                _unc_ui = _unc_mod.summarize_for_ui(_urep)
                if _unc_ui.get("flagged"):
                    # 持久化进答案（重开会话仍可见）；用单独变量，保持 full_content 干净不污染上面已算的奖励。
                    _save_content = full_content + "\n\n" + _unc_ui["note"]
                    yield _sse("trace", {"steps": [{"node": "uncertainty",
                        "detail": f"不确定性：{_urep.level}（{round(_urep.uncertainty*100)}%）→ 已标注存疑"}]})
        except Exception as _ue:
            log_suppressed(logger, _ue)

        # Save & emit done
        done_sources = [{"id": s["id"], "text": s.get("text", "")[:200],
                        "filename": s.get("filename", ""), "page": s.get("page", -1),
                        "score": s.get("score", 0)} for s in rag_sources]

        db.update_message(assistant_msg_id, content=_save_content, status="complete",
                         sources=done_sources, suggestions=suggestions)
        done_data = {"sources": done_sources, "trace": [], "steps": [],
                    "elapsed_ms": elapsed, "session_id": conv_id,
                    "suggestions": suggestions,
                    "faithfulness": _faith,
                    "uncertainty": _unc_ui,
                    "tokens": {"input": int(len(query) / 1.8), "output": int(len(full_content) / 1.8)},
                    "files": doc_files if doc_files else None,
                    "retrieval_quality": {
                        "mode": retrieval_mode,
                        "sources": len(rag_sources),
                        "top_score": round(rag_sources[0].get("score", 0), 2) if rag_sources else 0,
                    } if rag_sources else None}
        yield _sse("done", done_data)
        db.audit(user_id, username, "chat", f"Q: {query[:80]}")

        # v17 Phase 27 (B2/B3): record an OTel GenAI-attributed request span +
        # latency/cost for SLO. No-op safe — never affects the response.
        try:
            from hashmm import observability as _obs
            # Correct model lookup: ServiceRegistry.llm_info is the source of truth
            # (the previous app_state path silently returned None → cost mis-attributed).
            _model = None
            try:
                from hashmm.api.core.services import ServiceRegistry as _SR
                if isinstance(getattr(_SR, "llm_info", None), dict):
                    _model = _SR.llm_info.get("model_name") or _SR.llm_info.get("name")
            except Exception as _e:
                log_suppressed(logger, _e)
            _top_score = round(rag_sources[0].get("score", 0.0), 4) if rag_sources else None
            # v17 Phase 69: attribute cost to the tenant (or "default" when
            # multi-tenancy is off → effectively global, still correct).
            _tenant_id = "default"
            try:
                from hashmm import tenancy as _tenancy
                _tenant_id = _tenancy.resolve_tenant(db, user_id)
            except Exception as _e:
                log_suppressed(logger, _e)
            _obs.record_rag_request(
                model=_model,
                input_tokens=int(len(query) / 1.8),
                output_tokens=int(len(full_content) / 1.8),
                total_latency_ms=elapsed,
                n_sources=len(rag_sources),
                retrieval_mode=retrieval_mode,
                data_source_ids=[s.get("filename", "") for s in rag_sources][:10],
                top_score=_top_score,
                retrieval_strategy=("grounded" if rag_sources else "insufficient"),
                stage_latency_ms=_stage_latency,
                tenant_id=_tenant_id,
            )
        except Exception as _e:
            log_suppressed(logger, _e)

        if rag_sources:
            doc_names = set(s.get("filename", "") for s in rag_sources if s.get("filename"))
            db.audit(user_id, username, "retrieval",
                     f"docs={','.join(doc_names)}, sources={len(rag_sources)}, "
                     f"top_score={rag_sources[0].get('score', 0):.2f}")
        return

    # ── modify_task path (ReAct agent) — V103.1 起改为线程+队列异步驱动，不再阻塞事件循环 ──
    from hashmm.react_agent import ReactAgent
    from hashmm.api.tool_registry import execute_tool, _EXECUTORS

    t0 = time.time()
    react_sys = sys_prompts.get("code", sys_prompts.get("open", ""))
    if profile_ctx:
        react_sys += "\n" + profile_ctx

    react_msgs = [{"role": "system", "content": react_sys}]
    from hashmm.agent.conv_compact import compact_history as _ch, SUMMARY_MARK as _SM
    for h in _ch(history, keep_recent=6, char_budget=9000):
        _c = str(h.get("content") or "")
        react_msgs.append({"role": "user" if h.get("role") == "user" else "assistant",
                           "content": _c[:2200] if _c.startswith(_SM) else _c[:500]})
    if file_context:
        react_msgs.append({"role": "user", "content": f"文件内容：\n{_clip(file_context, 6000)}\n\n{query}"})
    else:
        react_msgs.append({"role": "user", "content": query})

    agent = ReactAgent(
        llm_fn=llm_fn,
        tool_exec_fn=execute_tool,
        kb_search_fn=_EXECUTORS.get("kb_search") if "kb_search" in _EXECUTORS else None,
    )

    full_content = ""
    all_steps = []
    try:
        # V103.1 修复"跑 agent 时整个后端卡死"：ReactAgent.run_streaming 是同步生成器
        # （多轮 LLM + 工具执行，重），之前直接在 async 生成器里 for 迭代 → 整段 agent 运行
        # 期间死占事件循环，后端无法响应任何其它请求（用户管理/别的 Chat/文件全卡）。
        # 改为：agent 跑在后台线程，事件经 asyncio.Queue 回传，async 侧 await 取——事件循环
        # 在两次事件之间空出来继续服务其它请求，同时 token 仍是实时流式。
        import threading
        _q: asyncio.Queue = asyncio.Queue()
        _loop = asyncio.get_event_loop()
        _DONE = object()

        def _produce_agent():
            try:
                for ev in agent.run_streaming(query, react_msgs):
                    _loop.call_soon_threadsafe(_q.put_nowait, ev)
            except Exception as _e:
                _loop.call_soon_threadsafe(_q.put_nowait, ("__error__", {"error": repr(_e)[:200]}))
            finally:
                _loop.call_soon_threadsafe(_q.put_nowait, _DONE)

        threading.Thread(target=_produce_agent, daemon=True).start()

        while True:
            item = await _q.get()
            if item is _DONE:
                break
            event_type, event_data = item
            if event_type == "__error__":
                em = f"⚠️ 执行出错：{event_data.get('error', '')}"
                full_content += em
                yield _sse("token", {"content": em})
            elif event_type == "token":
                full_content += event_data.get("content", "")
                yield _sse("token", event_data)
            elif event_type == "tool":
                all_steps.append(event_data)
                step_msg = f"\n\n> 🔧 {event_data['tool']}: {event_data.get('input', '')[:60]}\n"
                yield _sse("token", {"content": step_msg})
                full_content += step_msg
            elif event_type == "thinking":
                yield _sse("thinking", event_data)
    except Exception as e:
        err_msg = f"⚠️ 执行出错：{repr(e)[:200]}"
        full_content += err_msg
        yield _sse("token", {"content": err_msg})

    elapsed = round((time.time() - t0) * 1000)
    db.update_message(assistant_msg_id, content=full_content, status="complete", tool_calls=all_steps)
    yield _sse("done", {"sources": [], "trace": [], "steps": all_steps,
                        "elapsed_ms": elapsed, "session_id": conv_id})
    db.audit(user_id, username, "chat", f"Q: {query[:80]}")


# ── Helper functions ──

def _web_search_supplement(query: str) -> str:
    """v12: Web search fallback when local retrieval is insufficient.

    Uses the web_search tool to find supplementary information online.
    Returns extracted text or empty string.
    """
    try:
        from hashmm.api.tool_registry import execute_tool
        # Clean the query: strip our internal suffixes like "查询"/"是多少" that
        # hurt web search recall.
        clean = query.strip()
        for suffix in ("查询", "的查询"):
            if clean.endswith(suffix):
                clean = clean[: -len(suffix)].strip()
        result = execute_tool("web_search", {"query": clean, "num_results": 5}, {})
        if result and not result.startswith("Error") and not result.startswith("❌"):
            return result[:5000]
    except Exception as _e:
        log_suppressed(logger, _e)
    return ""


def _web_search_failed(result: str) -> bool:
    """Detect a web search that returned no usable content (network blocked,
    rate-limited, or zero hits) so we don't pretend we found something."""
    if not result or len(result.strip()) < 40:
        return True
    markers = ["未找到", "搜索无结果", "搜索暂不可用", "请换个关键词", "请尝试换"]
    return any(m in result for m in markers)


# Extract the main entity/subject from a query for presence checking
_QUERY_STOP = ("的", "是", "多少", "公司", "查询", "年", "营收", "收入", "销量",
               "卖了", "在", "中国", "了", "请", "帮我", "一下", "怎么样", "如何")


def _sources_miss_query_entity(query: str, sources: list) -> bool:
    """Heuristic: do the retrieved sources fail to mention the query's subject?

    Pulls candidate entity tokens from the query (e.g. 苹果/特斯拉) and checks
    whether ANY appears in the retrieved source text/filenames. If none do, the
    corpus probably doesn't cover this subject → web search should kick in.
    Conservative: only fires when we can identify a clear 2-4 char subject.
    """
    import re as _re
    if not sources:
        return False
    # Candidate subjects: 2-4 char CJK runs and ASCII words, minus stopwords
    cjk = _re.findall(r"[\u4e00-\u9fff]{2,4}", query)
    subjects = [c for c in cjk if c not in _QUERY_STOP]
    ascii_words = [w for w in _re.findall(r"[A-Za-z]{3,}", query)]
    subjects = subjects[:1] + ascii_words[:1]  # take the most salient
    if not subjects:
        return False
    blob = " ".join(
        (str(s.get("text", "")) + " " + str(s.get("filename", "")))
        for s in sources
    )
    # If NONE of the salient subjects appear in retrieved content → missing
    return not any(subj in blob for subj in subjects)


def _generate_pptx_from_context(llm_fn, query: str, analysis_text: str) -> dict:
    """v12: Generate PPTX from analysis context."""
    return _auto_generate_file(llm_fn, query, analysis_text, "pptx")


def _auto_generate_file(llm_fn, query: str, content: str, file_type: str) -> dict:
    """v12.1: Universal file generation — supports pptx, docx, xlsx, csv, md.

    Routes to the appropriate generator based on file type.
    Returns {"ok": bool, "filename": str, "download_url": str, "message": str}.
    """
    try:
        from hashmm.tools import get_doc_generator
        from hashmm.tools import FILES_DIR
        gen = get_doc_generator(llm_fn)

        if file_type == "pptx":
            return gen.generate_pptx(query, data_context=_clip(content, 6000))
        elif file_type == "docx":
            return gen.generate_docx(query, data_context=_clip(content, 6000))
        elif file_type == "xlsx":
            return gen.generate_xlsx(query, data_context=_clip(content, 6000))
        elif file_type == "csv":
            import csv as _csv
            import uuid as _uuid
            filename = f"data_{_uuid.uuid4().hex[:8]}.csv"
            output_path = FILES_DIR / filename
            lines = content.strip().split("\n")
            table_rows = [l for l in lines if "|" in l and not l.strip().startswith("---")]
            if table_rows:
                with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = _csv.writer(f)
                    for row in table_rows:
                        cells = [c.strip() for c in row.split("|") if c.strip()]
                        if cells:
                            writer.writerow(cells)
                return {"ok": True, "filename": filename, "download_url": f"/api/files/{filename}",
                        "message": f"CSV ({len(table_rows)} 行)"}
            return {"ok": False, "message": "内容中未发现表格数据"}
        elif file_type == "md":
            import uuid as _uuid
            filename = f"report_{_uuid.uuid4().hex[:8]}.md"
            output_path = FILES_DIR / filename
            output_path.write_text(content, encoding="utf-8")
            return {"ok": True, "filename": filename, "download_url": f"/api/files/{filename}",
                    "message": "Markdown 已创建"}
        return {"ok": False, "message": f"不支持的文件类型: {file_type}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


def _corrective_retrieval(query: str, history: list, retrieval_mode: str,
                          doc_filter: list, existing: list) -> tuple:
    """CRAG-style corrective retrieval: when the first pass is weak, rewrite the
    query into a few alternatives and re-search the LOCAL KB before falling back
    to the web.

    Rationale: many weak retrievals are a *phrasing* problem (pronouns, colloquial
    wording, missing keywords), not a "corpus lacks it" problem. Rewriting and
    re-searching locally is faster, cheaper, and privacy-preserving compared to
    going straight to the web — exactly what CRAG prescribes. Web fallback still
    happens afterwards if this doesn't help.

    Returns (merged_sources, injection). Never raises — on any failure it returns
    the existing sources unchanged so the caller's flow is unaffected.
    """
    try:
        from hashmm.retrieval.advanced import generate_multiqueries
        from hashmm.api.model_manager import get_active_llm_fn
        llm_fn = None
        try:
            llm_fn, _ = get_active_llm_fn()
        except Exception as _e:
            log_suppressed(logger, _e)
        # F11 cloud/local routing: query rewrite is a LOCAL_TASK → prefer local
        # model when routing enabled. No-op when off (returns same llm_fn).
        try:
            from hashmm.llm_router import route_llm, record_routing
            _routed, _backend = route_llm("multiquery", llm_fn)
            llm_fn = _routed or llm_fn
            record_routing("multiquery", _backend)
        except Exception as _e:
            log_suppressed(logger, _e)
        variants = generate_multiqueries(query, llm_fn, n=2)  # original + up to 2
        # Skip the original (already tried); only re-search the rewrites.
        rewrites = [v for v in variants if v != query][:2]
        if not rewrites:
            return existing, ""

        best = {s.get("id"): s for s in (existing or [])}
        for rq in rewrites:
            try:
                srcs, _inj, _strat = _do_retrieval(rq, history, retrieval_mode, doc_filter)
                for s in srcs or []:
                    sid = s.get("id")
                    # keep the higher-scoring copy when ids collide
                    if sid not in best or s.get("score", 0) > best[sid].get("score", 0):
                        best[sid] = s
            except Exception as _e:
                log_suppressed(logger, _e)

        merged = sorted(best.values(), key=lambda s: s.get("score", 0), reverse=True)[:5]
        # Rebuild injection from merged set (same format as _do_retrieval).
        if merged:
            ctx = []
            for s in merged:
                pg = f" p.{s['page']}" if s.get("page", -1) > 0 else ""
                ctx.append(f"[{s['id']}] {s.get('filename','')}{pg}\n{s.get('text','')[:500]}")
            injection = (
                "以下是知识库检索结果。请严格基于这些内容回答，"
                "并在每个来自文档的事实或数字句末紧跟方括号数字角标（如 [1]、[2]），"
                "对应下方编号；至少出现一个角标。文档未提及的内容不要编造：\n\n"
                + "\n\n".join(ctx)
            )
            return merged, injection
        return existing, ""
    except Exception as _e:
        log_suppressed(logger, _e)
        return existing, ""


def _format_plan_for_user(summary: dict) -> str:
    """Render a plan summary as a confirmable message (strict plan mode)."""
    lines = [f"我计划分 {summary['n_steps']} 步完成「{summary['query']}」："]
    for s in summary["steps"]:
        mark = "⚠️ " if s["side_effect"] else ""
        lines.append(f"  {s['step']}. {mark}{s['label']}：{s['description']}")
    lines.append("\n其中含会产生文件/执行操作的步骤（标 ⚠️）。如确认执行，请回复「确认执行」。")
    return "\n".join(lines)


def _do_retrieval(query: str, history: list, retrieval_mode: str, doc_filter: list) -> tuple:
    """Run retrieval pipeline (sync — called via to_thread).

    v14: results are cached (memory LRU + Redis if configured). A repeated query
    within the TTL skips the ~2s embed+search+rerank entirely. The cache key
    includes mode + doc_filter so different views don't collide. History is
    intentionally excluded from the key — retrieval is driven by the query, and
    including volatile history would destroy the hit rate.

    V103.47: returns a 3-tuple ``(sources, injection, strategy_mode)``. The
    ``strategy_mode`` ("grounded" | "augmented" | "supplement" | "insufficient"
    | "direct" | "") is the router's verdict and MUST be propagated so the
    generation path can honour an ``insufficient`` (corpus-doesn't-cover-this)
    decision instead of answering from parametric memory. Previously this verdict
    was discarded, which let the model fabricate answers for out-of-corpus
    questions (the refusal-eval failures).
    """
    from hashmm.chat_retrieval import get_chat_retrieval

    chat_rag = get_chat_retrieval()

    # V103.51: 指代消解后再做缓存键。否则「它去年呢?」这种问句在不同对话里指向不同
    # 公司，却因原文相同而命中同一条缓存，串味。用消解后的独立查询(含被补全的实体)
    # 作键既正确又能跨对话命中相同的标准查询。消解失败安全退回原 query。
    hist_msgs = [{"role": h["role"], "content": h["content"]} for h in history[-6:]]
    resolved_query = chat_rag.resolve_query(query, hist_msgs)

    # Cache lookup (best-effort; never let cache errors break retrieval)
    cache = None
    cache_key = f"{retrieval_mode}|{','.join(sorted(doc_filter))}|{resolved_query}"
    try:
        from hashmm.agent.cache import get_cache
        cache = get_cache()
        cached = cache.get_retrieval(cache_key)
        if cached is not None:
            # V103.47: cached entries may predate strategy storage → default "".
            return (cached.get("sources", []), cached.get("injection", ""),
                    cached.get("strategy", ""))
    except Exception:
        cache = None

    if not chat_rag.should_search(query):
        result = ([], "", "direct")
        if cache is not None:
            try:
                cache.set_retrieval(cache_key, {"sources": [], "injection": "", "strategy": "direct"})
            except Exception as _e:
                log_suppressed(logger, _e)
        return result

    # v17 Phase 31: ② multi-tenant doc-level ACL. No-op unless data/acl.json exists.
    _allowed = None
    try:
        from hashmm.access_control import load_default_acl
        _acl = load_default_acl()
        if _acl is not None:
            _allowed = _acl.allowed_patterns(user_id)
    except Exception:
        _allowed = None
    if _allowed is not None:
        _, rag_sources, _strategy = chat_rag.enhance(query, hist_msgs, retrieval_mode=retrieval_mode,
                                             allowed_docs=_allowed)
    else:
        _, rag_sources, _strategy = chat_rag.enhance(query, hist_msgs, retrieval_mode=retrieval_mode)

    if rag_sources and doc_filter:
        rag_sources = [s for s in rag_sources if any(f in s.get("filename", "") for f in doc_filter)]

    retrieval_injection = _build_retrieval_injection(rag_sources)

    # V103.47: surface the router's verdict (mode) so the generation path can
    # honour an "insufficient" decision. AnswerStrategy → its .mode string.
    strategy_mode = getattr(_strategy, "mode", "") or ""

    # Store in cache (only cache non-empty results to avoid masking transient misses)
    if cache is not None and rag_sources:
        try:
            cache.set_retrieval(cache_key, {"sources": rag_sources,
                                            "injection": retrieval_injection,
                                            "strategy": strategy_mode})
        except Exception as _e:
            log_suppressed(logger, _e)

    return rag_sources, retrieval_injection, strategy_mode


def _build_retrieval_injection(rag_sources: list) -> str:
    """Build the system-prompt injection from retrieval sources (shared by the
    single-shot and agentic-multi-hop paths so the format stays identical)."""
    if not rag_sources:
        return ""
    ctx_parts = []
    for s in rag_sources:
        idx = s["id"]
        fn = s.get("filename", "")
        pg = f" p.{s['page']}" if s.get("page", -1) > 0 else ""
        ctx_parts.append(f"[{idx}] {fn}{pg}\n{s.get('text', '')[:500]}")
    return (
        "以下是知识库检索结果。请严格基于这些内容回答，"
        "并在每个来自文档的事实或数字句末紧跟方括号数字角标（如 [1]、[2]），"
        "对应下方编号；至少出现一个角标。文档未提及的内容不要编造：\n\n"
        + "\n\n".join(ctx_parts)
    )


def _searchr1_retrieval(query: str, history: list, retrieval_mode: str, doc_filter: list):
    """选项A：用训练好的 Search-R1 策略(run_search_loop)驱动检索取证据，再交给下游 LLM 合成答案。

    返回 ``(sources, injection, "searchr1")``；策略不可用（无 GPU / 未配权重 / 加载失败）时返回
    ``None``，调用方据此回退到 ``_do_retrieval``，零风险。

    说明：当前 SFT 为单跳，模型子查询≈原问题，故单跳下召回≈单次检索；多跳训练后此路才显增益。
    需 env：``HASHMM_SEARCHR1=1`` 开启；可选 ``HASHMM_SEARCHR1_LORA`` / ``HASHMM_BASE_MODEL`` 指定权重。
    """
    try:
        from hashmm.retrieval.searchr1_serving import answer_with_searchr1
    except Exception as _e:  # noqa: BLE001 —— 适配器缺失必须回退，不能让聊天崩
        log_suppressed(logger, _e)
        return None
    try:
        res = answer_with_searchr1(query, top_k=5, max_hops=3)
    except Exception as _e:  # noqa: BLE001
        log_suppressed(logger, _e)
        return None
    if not res:
        return None
    sources = res.get("sources") or []
    if sources and doc_filter:
        sources = [s for s in sources if any(f in s.get("filename", "") for f in doc_filter)]
    # 兼容下游：补 id（_build_retrieval_injection 用 s["id"] 作角标）与 content 字段
    for _i, s in enumerate(sources, 1):
        s.setdefault("id", _i)
        s.setdefault("content", s.get("text", ""))
    injection = _build_retrieval_injection(sources)
    return (sources, injection, "searchr1")


def _trained_policy_llm_fn():
    """V103.60：若配置了训练好的检索策略模型（env HASHMM_TRAINED_POLICY_DIR 指向 LoRA 目录），
    返回它的 llm_fn 用于驱动多跳回路；否则返回 None（主路径自动走通用 LLM 判停，零风险）。

    模型只加载一次（模块级单例，7B 加载耗时十几秒，不能每请求重载）。加载失败也只返回 None，
    绝不影响线上。需要 env：HASHMM_TRAINED_POLICY_DIR（LoRA 目录）、HASHMM_BASE_MODEL_DIR（基座）。
    """
    import os
    lora_dir = os.environ.get("HASHMM_TRAINED_POLICY_DIR", "").strip()
    if not lora_dir:
        return None
    global _TRAINED_POLICY_CACHE
    try:
        cache = _TRAINED_POLICY_CACHE
    except NameError:
        cache = None
    if cache is not None:
        return cache.get("llm_fn")  # 命中单例（可能是 None，表示之前加载失败，不再重试）
    # 首次：尝试加载
    result = {"llm_fn": None}
    try:
        base_dir = os.environ.get("HASHMM_BASE_MODEL_DIR", "").strip()
        if not base_dir:
            logger.warning("[TrainedPolicy] 配置了 LoRA 但未设 HASHMM_BASE_MODEL_DIR，跳过。")
        else:
            from hashmm.training.searchr1_policy import SearchR1Policy
            policy = SearchR1Policy(model_dir=base_dir, lora_dir=lora_dir)
            result["llm_fn"] = policy.as_llm_fn()
            if result["llm_fn"]:
                logger.info("[TrainedPolicy] 训练好的检索策略模型已接入主路径：%s", lora_dir)
    except Exception as e:
        log_suppressed(logger, e)
    globals()["_TRAINED_POLICY_CACHE"] = result
    return result["llm_fn"]


def _agentic_retrieval(query: str, history: list, retrieval_mode: str, doc_filter: list) -> tuple:
    """v17 Phase 73: agentic multi-hop retrieval. Uses the existing single-shot
    _do_retrieval as the search tool and ServiceRegistry.call_llm to decide
    sub-queries. Falls back to single-shot on ANY problem (so enabling it can
    never do worse than today's behavior).

    V103.53 (P0): 注入一个 confidence_fn（基于 confidence.assess_retrieval），让多跳
    回路用量化置信度参与停止判断（不确定性闸）——免训练实现 Search-R1 + UncertaintyRAG
    的核心思想。confidence_fn 不可用时闸自动关闭，退回纯 LLM 判停，零风险。

    V103.60: 若配置了训练好的检索策略模型（HASHMM_TRAINED_POLICY_DIR），用它驱动回路的
    判停/续检（取代通用 LLM）；未配置则照旧。这就是把 P2 训练成果接进产品主路径。
    """
    try:
        from hashmm.retrieval.agentic import AgenticRetriever, max_hops as _mh, uncertainty_gate_enabled as _uge

        def _search(sub: str) -> list:
            return _do_retrieval(sub, history, retrieval_mode, doc_filter)[0]

        # V103.60：优先用训练好的检索策略模型；没有则用通用 LLM（原行为）。
        llm_fn = _trained_policy_llm_fn()
        if llm_fn is None:
            try:
                from hashmm.api.core.services import ServiceRegistry
                llm_fn = ServiceRegistry.call_llm
            except Exception as _e:
                log_suppressed(logger, _e)

        # V103.53 不确定性闸：用 confidence.py 把「当前证据有多强」量化成 [0,1]。
        # 只在闸开启时注入；任何异常返回中性 0.5，绝不影响检索本体。
        _conf_fn = None
        if _uge():
            def _conf_fn(q: str, sources: list) -> float:
                try:
                    from hashmm.agent.confidence import assess_retrieval
                    return float(assess_retrieval(q, sources, miss_fn=_sources_miss_query_entity).get("confidence", 0.5))
                except Exception:
                    return 0.5

        ar = AgenticRetriever(_search, llm_fn, max_hops=_mh(), confidence_fn=_conf_fn)
        result = ar.retrieve(query)
        sources = result.get("sources", [])
        if doc_filter and sources:
            sources = [s for s in sources if any(f in s.get("filename", "") for f in doc_filter)]
        # V103.47: 多跳检索后用同一套实体判定补一个 strategy 提示——若汇总到的
        # 来源里仍找不到查询主体，标记 insufficient（与单跳一致地诚实拒答）。
        _strat = ""
        try:
            if sources and _sources_miss_query_entity(query, sources):
                _strat = "insufficient"
        except Exception:
            _strat = ""
        return sources, _build_retrieval_injection(sources), _strat
    except Exception as e:
        log_suppressed(logger, e)
        return _do_retrieval(query, history, retrieval_mode, doc_filter)


def _is_numeric_task(q: str) -> bool:
    """问题是否需要计算/数值推理（检索到数字后要'算对'，而不仅是'找到'）。"""
    if not q:
        return False
    s = q.strip()
    kws = ("计算", "算一下", "算出", "算成", "增长率", "增长了多少", "增加了多少", "增长多少",
           "加起来", "总和", "合计", "一共多少", "共多少", "占比", "比例是", "相差", "差额",
           "同比", "环比", "多少倍", "几倍", "平均", "求和", "百分之", "百分比", "%", "CAGR",
           "复合增长", "复合年增长", "净增", "降幅", "涨幅", "增幅", "减少了多少")
    return any(k in s for k in kws)


def _build_system_prompt(
    task_type: str, rag_sources: list, retrieval_injection: str,
    profile_ctx: str, sys_prompts: dict, user_id: str, query: str,
    strategy: str = "",
) -> str:
    """Build the system prompt based on task type and context.

    V103.47: ``strategy`` carries the retrieval router's verdict. When it is
    ``insufficient`` (the corpus does NOT cover the query subject) we must NOT
    answer from parametric memory — we inject an explicit honest-refusal
    instruction so the model says "the knowledge base doesn't contain this".
    This is the prompt-side half of the fix; ``refusal_guard.enforce`` is the
    deterministic post-generation guarantee that backs it up.
    """
    # V103.47: out-of-corpus → honest refusal prompt, regardless of whether a
    # few weak (wrong-subject) chunks were retrieved. Takes precedence over the
    # normal "kb" branch, because the presence of *some* sources used to (wrongly)
    # force the grounded prompt and let the model fabricate.
    if strategy == "insufficient":
        sys_content = sys_prompts.get("kb", "") or sys_prompts.get("chat", "")
        sys_content += (
            "\n\n## 本次知识库检索未命中该问题\n"
            "检索到的资料与用户问题的主体对象不匹配——知识库里没有这个问题的现成答案。\n"
            "先判断这是哪一类问题，再据此回答（这一步决定你显得机械还是真的懂用户）：\n"
            "1. 【要具体事实型】用户在问某个具体主体（公司 / 产品 / 人物等）的具体事实"
            "（营收、销量、数据、日期、名称、个人信息等），而知识库里没有：如实说明知识库中"
            "没有该信息、无法据此回答（明确说「没有」或「无法」），可简短建议补充资料或换个问法。"
            "绝不用你自己的记忆去编或填这些具体数字 / 事实——即使你可能知道，也以知识库为准、"
            "不替知识库'补'外部数据。\n"
            "2. 【通用能力型】这本就是不依赖该企业知识库的问题——通用知识 / 概念解释 / 写代码 / "
            "数学计算 / 逻辑推理 / 闲聊等：别纠结'知识库没有'、也别敷衍，直接凭你的能力把它答好、"
            "答到位，该给的思路、代码、推理、例子都给足，像 Claude 那样真正帮用户把事办成；"
            "只有当回答里涉及具体的外部真实数字 / 事实时，才标明这是一般了解、可能需要核实，"
            "同样不编造看起来精确的假数据。\n"
            "3. 绝对禁止声称「根据联网搜索」「据公开报道」或编造任何外部来源。\n"
            "4. 若问题涉及个人隐私（身份证号、私人电话、家庭住址等），拒绝提供，"
            "并说明此类信息受法律保护、知识库中也没有。"
        )
        if profile_ctx:
            sys_content += "\n" + profile_ctx
        # 仍然把（弱相关的）检索内容附上，便于模型说明"找到的资料与问题无关"，
        # 但上面的硬约束确保它不会拿这些内容硬凑答案。
        if retrieval_injection:
            sys_content += "\n\n" + retrieval_injection
        return sys_content

    if rag_sources:
        sys_content = sys_prompts.get("kb", "")
    elif task_type == "code_task":
        sys_content = sys_prompts.get("code", sys_prompts.get("open", ""))
    elif task_type == "doc_task":
        sys_content = sys_prompts.get("doc", sys_prompts.get("open", ""))
    elif task_type == "analysis_task":
        sys_content = sys_prompts.get("analysis", sys_prompts.get("open", ""))
    else:
        sys_content = sys_prompts.get("chat", "")

    if profile_ctx:
        sys_content += "\n" + profile_ctx
    if retrieval_injection:
        sys_content += "\n\n" + retrieval_injection

    # v13: Task-specific instructions
    if task_type == "code_task":
        sys_content += (
            "\n\n## 代码任务要求\n"
            "用户要求写代码。请直接输出完整的、可运行的代码。"
            "不要只给思路或描述，必须给出完整代码实现，包含代码块。"
            "代码要有注释、有测试用例、可直接复制运行。"
        )

    # 数值/计算任务：检索到数字后要"算对"——这是大厂 RAG-Agent 与普通 RAG 的关键差距。
    # 强制模型先取数→列公式→分步计算→自查，缺数则诚实拒算，不臆造。
    if _is_numeric_task(query):
        sys_content += (
            "\n\n## 数值计算要求\n"
            "本问题涉及计算或数值比较，必须严格按步骤作答，不能只给一个结论数字：\n"
            "1. 先从上面的资料中找出计算所需的每一个具体数字，并标注它来自哪条来源；\n"
            "2. 写出所用公式（如 同比增长率=(本期-上期)/上期×100%；占比=部分/总体×100%；"
            "复合年增长率CAGR=(末期/初期)^(1/年数)−1），再把数字代入；\n"
            "3. 给出分步计算过程和最终结果，注意单位与题目要求的小数位数；\n"
            "4. 如果资料中缺少某个必需的数字，明确说明「资料中缺少 X，无法计算」，"
            "绝不臆造、估算或猜一个数字来凑答案；\n"
            "5. 算完后自查一遍：量级、正负号、单位是否合理。"
        )

    # Skill injection
    try:
        from hashmm.evolution.skill_manager import get_skill_manager
        matched = get_skill_manager().match_skills(query)
        if matched:
            sys_content += f"\n\n参考策略({matched[0].name}): {matched[0].prompt_template}"
    except Exception as _e:
        log_suppressed(logger, _e)

    # v11: Episodic memory — inject strategy hints from past experiences
    try:
        from hashmm.evolution.episodic_memory import get_episodic_memory
        hint = get_episodic_memory().get_strategy_hint(user_id, query)
        if hint:
            sys_content += f"\n\n{hint}"
    except Exception as _e:
        log_suppressed(logger, _e)

    # V103.26: 主动式助理脚手架 — 在生活/真实世界任务（出行/订票/规划/推荐/日常协助）上，
    # 让 Agent 推断深层需求、主动想全维度、按用户偏好个性化、帮做选择并预判下一步。
    # 非生活任务返回空串，不污染普通提示。
    try:
        from hashmm.agent.proactive import build_proactive_scaffold
        _scaffold = build_proactive_scaffold(query, user_id)
        if _scaffold:
            sys_content += _scaffold
    except Exception as _e:
        log_suppressed(logger, _e)

    # v12.1: If user wants any file type, instruct LLM to focus on content
    _q = query.lower()
    file_instruction = ""
    if any(w in _q for w in ["ppt", "做ppt", "幻灯片", "slides", "演示"]):
        file_instruction = "PPT幻灯片"
    elif any(w in _q for w in ["word", "docx", "word文档"]):
        file_instruction = "Word文档"
    elif any(w in _q for w in ["excel", "xlsx", "表格文件", "电子表格"]):
        file_instruction = "Excel表格"
    elif any(w in _q for w in ["做报告", "生成报告", "写报告", "出报告"]):
        file_instruction = "报告文件"

    if file_instruction:
        sys_content += (
            f"\n\n## 用户要求生成{file_instruction}——按这个顺序来，体验才好\n"
            f"系统会自动把你下面写的内容转成{file_instruction}并附上下载链接。所以不要讲"
            f"「怎么用 PowerPoint/Word 操作」这类步骤、也不要给制作代码；但要把成品的实质"
            f"内容写足写好，让用户既能在对话里看清、又能直接拿文件用。请按顺序：\n"
            f"1. 先给简短的【大纲】：一两句说清整份{file_instruction}的思路，再列出每页 / 每节"
            f"的标题，让用户一眼看清结构、能立刻指出要加或删哪一部分；\n"
            f"2. 再给【每页内容】：逐页 / 逐节展开——标题写结论而非主题，每页 3-6 个要点、每个"
            f"要点一句话、加粗关键术语，数据用表格；先结论后论据；\n"
            f"3. 正文写完即止，系统会在下方自动生成文件，你不必自己宣布「文件已生成」；\n"
            f"4. 末尾用一句话邀请用户调整，例如「哪页要改、要加哪块内容，告诉我就行」。\n"
            f"大纲与正文都必须基于检索到的真实资料，缺数据就如实说明、不编造。"
        )

    return sys_content


def _handle_llm_error(err_repr: str, rag_sources: list) -> str:
    """Map LLM errors to user-friendly messages with graceful degradation."""
    err = err_repr.lower()
    if "timeout" in err or "timed out" in err:
        return "⏳ AI 响应超时，请稍后重试。如持续出现，请在管理后台检查模型配置。"
    if "401" in err_repr or "unauthorized" in err:
        return "🔑 AI 服务认证失败，请在管理后台检查 API Key 是否正确。"
    if "429" in err_repr or "rate" in err:
        return "⚡ AI 服务请求过于频繁，请等待几秒后重试。"
    if "connect" in err or "connection" in err:
        return "🌐 无法连接到 AI 服务，请检查网络连接和 API 地址。"
    # Degrade to raw retrieval results
    if rag_sources:
        snippets = []
        for s in rag_sources[:3]:
            fn = s.get("filename", "")
            pg = f" p.{s['page']}" if s.get("page", -1) > 0 else ""
            snippets.append(f"**{fn}{pg}**\n{s.get('text', '')[:300]}")
        return "⚠️ AI 生成暂时不可用，以下是知识库中的相关内容：\n\n" + "\n\n---\n\n".join(snippets)
    return f"⚠️ 处理时遇到问题：{err_repr[:100]}"


def _generate_suggestions_safe(query: str, content: str, retrieval_mode: str,
                                rag_sources: list, task_type: str = "") -> list:
    """v12: Generate smart follow-up suggestions based on answer content.

    Strategy: Extract key entities/topics from the answer, then generate
    contextual follow-up questions the user might want to ask.
    """
    # Code tasks → code-specific suggestions
    if task_type in ("code_task", "modify_task"):
        return _code_suggestions(query, content)

    # Extract entities and topics from the answer for contextual suggestions
    suggestions = []
    try:
        # 1. Company names mentioned in answer
        companies = re.findall(r'([\u4e00-\u9fff]{2,6}(?:集团|控股|公司|科技))', content[:2000])
        companies = list(dict.fromkeys(companies))[:3]  # Dedupe, keep order

        # 2. Financial metrics mentioned
        metrics = []
        for kw in ["营收", "净利润", "毛利率", "增长率", "市值", "现金流", "ROE", "出货量"]:
            if kw in content:
                metrics.append(kw)

        # 3. Generate contextual suggestions
        if companies and metrics:
            c = companies[0]
            m = metrics[0] if metrics else "财务数据"
            suggestions.append(f"详细分析{c}的{m}趋势")
            if len(companies) > 1:
                suggestions.append(f"对比{companies[0]}和{companies[1]}的业绩")
            suggestions.append(f"生成{c}分析报告PPT")
        elif companies:
            c = companies[0]
            suggestions.append(f"深入分析{c}的核心业务")
            suggestions.append(f"生成{c}分析PPT")
        elif "论文" in query or "paper" in query.lower() or "arxiv" in query.lower():
            suggestions.append("总结这篇论文的创新点")
            suggestions.append("这篇论文有哪些局限性")
            suggestions.append("推荐相关论文")
        elif rag_sources:
            # Use source filenames for suggestions
            filenames = list(set(s.get("filename", "") for s in rag_sources if s.get("filename")))[:2]
            for fn in filenames:
                name = fn.replace(".pdf", "").replace(".docx", "")
                suggestions.append(f"详细分析{name}的核心内容")

        # 4. Always offer PPT/document if analysis-type
        if any(w in query for w in ["分析", "对比", "比较", "总结"]) and not any("PPT" in s for s in suggestions):
            suggestions.append("生成分析报告PPT")

    except Exception as _e:
        log_suppressed(logger, _e)

    # Deduplicate and limit to 3
    seen = set()
    unique = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:3]


def _code_suggestions(query: str, content: str = "") -> list:
    """Generate code-related follow-up suggestions based on content."""
    suggestions = []
    if "```" in content:
        suggestions.append("添加完整的单元测试")
        suggestions.append("优化代码性能")
        if "class " in content or "def " in content:
            suggestions.append("添加详细的注释和文档")
        else:
            suggestions.append("添加错误处理")
    elif "实现" in query or "写" in query:
        suggestions.append("添加单元测试")
        suggestions.append("优化性能")
        suggestions.append("添加错误处理")
    return suggestions[:3]


def _run_evolution_safe(user_id: str, query: str, answer: str, task_type: str,
                        retrieval_mode: str = "", n_sources: int = 0,
                        top_score: float = 0, elapsed_ms: int = 0,
                        faithfulness_ratio: float | None = None):
    """Run evolution engine updates without crashing the response.

    v12: Added self-evaluation — Agent auto-assesses answer quality without user feedback.
    V103.90: 把忠实度接地率作为奖励信号透传给 episodic 记录（奖励驱动自我进化）。
    """
    # 1. User memory extraction
    try:
        if "关注" in query or "感兴趣" in query:
            db.save_user_memory(user_id, "interest", "关注领域", query[:100])
        for name, pat in [("company", r'([\u4e00-\u9fff]{2,6}(?:集团|公司|科技|汽车))'),
                          ("topic", r'(营收|利润|毛利率|研发|市值|股价|出货量)')]:
            for m in re.findall(pat, query)[:2]:
                db.save_user_memory(user_id, name, m, f"用户查询过{m}")
    except Exception as _e:
        log_suppressed(logger, _e)

    # 2. User model update
    try:
        from hashmm.evolution.user_model import get_user_model
        get_user_model().update_from_conversation(user_id=user_id, query=query, answer=answer, task_type=task_type)
    except Exception as _e:
        log_suppressed(logger, _e)

    # 2b. v17 Phase 19: cross-session task memory — advance the user's task thread
    try:
        if task_type not in ("greeting", "direct_task"):
            from hashmm.evolution.task_memory import get_task_memory
            get_task_memory().update(user_id=user_id, query=query)
    except Exception as _e:
        log_suppressed(logger, _e)

    # 3. Episodic memory — record this interaction
    try:
        import uuid as _uuid
        from hashmm.evolution.episodic_memory import get_episodic_memory
        get_episodic_memory().record(
            episode_id=_uuid.uuid4().hex[:12],
            user_id=user_id,
            query=query,
            query_type=task_type,
            strategy="grounded" if n_sources > 0 else "direct",
            answer=answer,
            retrieval_mode=retrieval_mode,
            n_sources=n_sources,
            top_score=top_score,
            answer_length=len(answer),
            elapsed_ms=elapsed_ms,
            faithfulness_ratio=faithfulness_ratio,
        )
    except Exception as _e:
        log_suppressed(logger, _e)

    # 4. v12: Self-evaluation — Agent assesses its own answer quality
    #    This runs async (fire-and-forget) to not delay the response
    try:
        _self_eval_async(user_id, query, answer, task_type, n_sources, top_score)
    except Exception as _e:
        log_suppressed(logger, _e)


def _self_eval_async(user_id: str, query: str, answer: str, task_type: str,
                     n_sources: int, top_score: float):
    """v12: Agent self-evaluation — assess answer quality without user feedback.

    Uses heuristic rules (fast, free) + optional LLM eval (when budget allows).
    Results feed back into episodic memory and skill evolution.
    """
    score = 0.5  # Default: neutral

    # Rule-based quick eval (no LLM cost)
    # 1. Answer length heuristics
    if len(answer) < 50:
        score -= 0.2  # Too short — probably failed
    elif len(answer) > 200:
        score += 0.1  # Reasonable length

    # 2. Source grounding
    if n_sources > 0 and top_score > 1.0:
        score += 0.15  # Well-grounded answer
    elif n_sources == 0 and task_type not in ("code_task", "direct_task"):
        score -= 0.1  # Should have used sources but didn't

    # 3. Code task: check for code blocks
    if task_type == "code_task":
        if "```" in answer:
            score += 0.2  # Has code block
        else:
            score -= 0.2  # Code task but no code

    # 4. Error indicators
    error_phrases = ["抱歉", "无法", "错误", "Error", "失败", "sorry"]
    if any(p in answer[:200] for p in error_phrases):
        score -= 0.15

    score = max(0.0, min(1.0, score))

    # V103.90: 用可移植的 update_reward 回填 reward（旧实现用 `UPDATE ... ORDER BY LIMIT`
    # 依赖特定 SQLite 编译选项、并把分数写进 feedback 串"self_eval:0.8"——而 recall 只认
    # up/down，该串两边不沾、对召回零贡献；现统一写入数值型 reward 列，真正驱动召回/淘汰。
    # 注：episode 已在 _run_evolution_safe 落库（含基于忠实度的 reward），这里用自评分做
    # 兜底/校准回填（取两者较低值更保守，避免把没接地的答案评高）。
    try:
        from hashmm.evolution.episodic_memory import get_episodic_memory
        mem = get_episodic_memory()
        mem.update_reward(user_id, score, only_if_lower=True)
    except Exception as _e:
        log_suppressed(logger, _e)

    # If score is low, increment a counter for this task_type
    # After 5 low scores, trigger skill optimization
    if score < 0.4:
        try:
            from hashmm.evolution.prompt_optimizer import get_prompt_optimizer
            opt = get_prompt_optimizer()
            opt.record_feedback(task_type, feedback="down", query=query)
        except Exception as _e:
            log_suppressed(logger, _e)
