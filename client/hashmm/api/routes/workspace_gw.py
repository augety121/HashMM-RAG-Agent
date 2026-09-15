"""全局工作区（总控中枢）REST + SSE（V254）。

前端总控台的数据源：
  GET  /api/gw/state            一屏快照（焦点 / 意识缓冲 / 模块状态 / 事件史）
  GET  /api/gw/stream           SSE 实时事件（进入意识缓冲的广播 + 心跳）
  POST /api/gw/focus            手动设定 / 清除全局焦点（总控：人拍板系统该忙什么）
  POST /api/gw/broadcast        手动广播一条（备注 / 里程碑，进意识缓冲让全员可见）
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hashmm.api.auth import get_current_user, require_auth
from hashmm.agent.global_workspace import gw, verbalize

router = APIRouter(prefix="/api/gw", tags=["global-workspace"])


@router.get("/context", summary="工作区可读化读出（J-lens 式，供任意外部 API 注入）")
async def gw_context(request: Request, format: str = "text"):
    """把工作区状态解码成任何模型都能消费的读出（verbalizable readout）。

    format 适配不同厂商的接入姿势（V258）：
      · text（默认）：纯文本，自己拼进任意 prompt；
      · system：OpenAI 兼容的单条 system 消息对象——OpenAI / Qwen / GLM / Kimi /
        Doubao / DeepSeek 等大厂 chat.completions 接口直接 unshift 进 messages 即用；
      · messages：包一层 {"messages": [system]}，方便整体展开合并。
    示例（任何 OpenAI 兼容 API）：
      ctx = GET /api/gw/context?format=system
      body.messages = [ctx] + 原有 messages   ← 该 API 立刻获得本系统的全局感知
    """
    get_current_user(request)
    snap = gw().snapshot(history_n=20)
    txt = verbalize(snap)
    fmt = (format or "text").strip().lower()
    if fmt == "system":
        return {"role": "system", "content": txt, "seq": snap.get("seq")}
    if fmt == "messages":
        return {"messages": [{"role": "system", "content": txt}], "seq": snap.get("seq")}
    return {"context": txt, "seq": snap.get("seq"), "ts": snap.get("ts")}


@router.get("/rules", summary="事件自动化规则（V257）")
async def gw_rules(request: Request):
    get_current_user(request)
    from hashmm.agent.event_rules import list_rules
    return {"rules": list_rules()}


@router.post("/rules", summary="开关某条事件自动化规则")
async def gw_rules_set(request: Request):
    require_auth(request)
    body = await request.json()
    from hashmm.agent.event_rules import set_rule
    ok = set_rule(str(body.get("id") or ""), bool(body.get("on", True)))
    return {"ok": ok}


@router.post("/ask", summary="工作区增强回答（可指定任意已配置模型 API）")
async def gw_ask(request: Request):
    """问工作区：问题 + 工作区读出 → 交给模型回答。
    model_id 可选——传了就用「模型/后端」里配置的**那个 API**（deepseek / OpenAI 兼容 /
    本地…都行，这正是"接入其他 api 也可以使用这个空间"）；不传用当前默认模型。
    问与答都会广播回工作区（回答本身也进入意识，可被后续模块引用）。"""
    import asyncio as _aio
    user = require_auth(request)
    body = await request.json()
    q = str(body.get("question") or "").strip()
    if not q:
        return {"ok": False, "error": "empty_question"}
    model_id = str(body.get("model_id") or "").strip()
    fn = None
    model_name = ""
    try:
        from hashmm.api import database as db
        from hashmm.api.model_manager import get_active_llm_fn, make_llm_fn_from_model
        if model_id:
            cfg = db.get_model(model_id)
            if cfg:
                fn = make_llm_fn_from_model(cfg)
                model_name = str(cfg.get("name") or model_id)
        if fn is None:
            fn, cfg = get_active_llm_fn()
            model_name = str((cfg or {}).get("name") or "默认模型")
    except Exception:
        pass
    if fn is None:
        return {"ok": False, "error": "no_model", "answer": "没有可用模型：请先在「模型/后端」里配置至少一个 API。"}
    snap = gw().snapshot(history_n=20)
    ctx = verbalize(snap)
    prompt = (f"{ctx}\n\n你是这套系统的总控助手。结合上面的全局工作区读出（系统当前在做什么、"
              f"各模块状态、最近广播），回答用户的问题；与工作区相关就引用相关事实，"
              f"无关就正常回答，不要编造工作区里没有的事。\n\n用户问题：{q}\n\n回答：")
    gw().submit("chat", "gw_ask", f"总控提问：{q[:120]}", salience=0.6, user=user.get("sub", ""))
    try:
        ans = await _aio.to_thread(fn, prompt)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}
    ans = str(ans or "").strip()
    if ans:
        gw().submit("chat", "gw_answer", f"总控回答（{model_name}）：{ans[:140]}",
                    salience=0.55, user=user.get("sub", ""))
    return {"ok": True, "answer": ans or "（模型返回为空）", "model": model_name,
            "context_used": ctx}


@router.get("/state", summary="总控台一屏快照")
async def gw_state(request: Request, history_n: int = 60):
    get_current_user(request)   # 允许匿名只读（与 deepsearch 同宽松度）；写操作仍要登录
    return gw().snapshot(history_n=history_n)


@router.post("/focus", summary="设定/清除全局焦点")
async def gw_focus(request: Request):
    user = require_auth(request)
    body = await request.json()
    goal = str(body.get("goal") or "").strip()
    if not goal:
        gw().clear_focus()
        return {"ok": True, "focus": None}
    f = gw().set_focus(goal, source="user", by=user.get("sub", "user"))
    gw().submit("chat", "focus", f"焦点设定：{goal}", salience=0.8,
                user=user.get("sub", ""))
    return {"ok": True, "focus": f}


@router.post("/broadcast", summary="手动广播一条到意识缓冲")
async def gw_broadcast(request: Request):
    user = require_auth(request)
    body = await request.json()
    summary = str(body.get("summary") or "").strip()[:300]
    if not summary:
        return {"ok": False, "error": "empty"}
    module = str(body.get("module") or "chat").strip()[:32]
    ev = gw().submit(module, str(body.get("kind") or "note")[:32], summary,
                     salience=max(0.55, float(body.get("salience") or 0.7)),
                     user=user.get("sub", ""))
    return {"ok": True, "event": ev}


@router.get("/stream", summary="SSE 实时广播流")
async def gw_stream(request: Request):
    get_current_user(request)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)

    def _on_event(ev: dict) -> None:
        # 订阅回调可能来自任意线程 → 线程安全投递到事件循环
        try:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        except Exception:
            pass

    unsub = gw().subscribe(_on_event)

    async def _gen():
        try:
            # 先推一帧快照，前端秒开
            yield f"event: snapshot\ndata: {json.dumps(gw().snapshot(history_n=40), ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: gw\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            unsub()

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
