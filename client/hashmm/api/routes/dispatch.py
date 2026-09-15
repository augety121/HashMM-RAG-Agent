"""远端派活 REST（V205 P2-9）——create / poll / complete / status。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.requests import ClientDisconnect

from hashmm.api import database as db
from hashmm.api.auth import require_auth, require_conv_access
from hashmm.agent import dispatch as dq

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


def _require_task_access(user: dict, task: dict | None) -> dict:
    """Task IDs are object IDs: hide missing and foreign tasks behind the same 404."""
    if not task:
        raise HTTPException(404, "任务不存在")
    if user.get("role") != "admin" and str(task.get("created_by") or "") != str(user.get("sub") or ""):
        raise HTTPException(404, "任务不存在")
    return task


def _admit_dispatch_work(
    user: dict,
    *,
    task_id: str,
    kind: str,
    payload: dict,
    retry_of: str = "",
) -> dict:
    """Attach every desktop queue item to the shared owner-scoped Work ledger."""
    from hashmm.agent import work_runtime

    safe_kind = {
        "browser": "browser",
        "browser_use": "browser",
        "file": "computer",
        "computer": "computer",
        "computer_use": "computer",
    }.get(str(kind or "").strip().lower(), "workflow")
    goal = str(payload.get("goal") or payload.get("task") or kind or "桌面工作").strip()
    conv_id = str(payload.get("conv_id") or "").strip()
    return work_runtime.create_run(
        user_id=str(user.get("uid") or ""),
        kind=safe_kind,
        source_id=task_id,
        conv_id=conv_id,
        title=goal[:240],
        status="queued",
        execution_target={
            "kind": "waiting_device",
            "state": "waiting",
            "requested_by": "dispatch",
        },
        snapshot={
            "goal": goal,
            "task_type": f"{safe_kind}_task",
            "execution_mode": "owner_scoped_desktop_dispatch",
            "work_method": (
                payload.get("work_method")
                if isinstance(payload.get("work_method"), dict)
                else {
                    "run_mode": (
                        "browser" if safe_kind == "browser"
                        else "computer" if safe_kind == "computer"
                        else "auto"
                    ),
                }
            ),
            "dispatch": {
                "task_id": task_id,
                "queue_kind": str(kind or "")[:40],
                "retry_of": str(retry_of or "")[:80],
            },
        },
    )


# ── V232 任务树状态回写：task_id → 画布节点 侧车映射（7 天自清） ──
import json as _pt_json
import os as _pt_os
import threading as _pt_threading
import time as _pt_time
from pathlib import Path as _PtPath

_PT_LOCK = _pt_threading.Lock()


def _pt_store() -> _PtPath:
    p = _PtPath(_pt_os.environ.get("HASHMM_DATA_DIR", "data")) / "plan_trees.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _pt_load() -> dict:
    try:
        p = _pt_store()
        if p.exists():
            d = _pt_json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                now = int(_pt_time.time())
                return {k: v for k, v in d.items() if now - int(v.get("ts", 0)) < 7 * 86400}
    except Exception:
        pass
    return {}


def _pt_wait_span(node: str) -> str:
    return f"<span class='st' id='st-{node}'>待执行</span>"


def _pt_retrying_span(node: str) -> str:
    return f"<span class='st' id='st-{node}'>重试中…</span>"


def _pt_ok_span(node: str) -> str:
    return f"<span class='st st-ok' id='st-{node}'>已完成 ✓</span>"


def _pt_dead_span(node: str, reason: str = "") -> str:
    t = f" title=\"失败原因：{reason}\"" if reason else ""
    return (f"<span class='st st-dead' id='st-{node}'{t}>死信 ☠ 已重试 3 次 "
            f"<button class='wc-replan' data-tid='{node}'>重新规划</button></span>")


def _pt_dead_done_span(node: str) -> str:
    return f"<span class='st st-dead' id='st-{node}'>死信 ☠ · 已转重新规划 ↗</span>"


def _pt_fail_span(node: str, reason: str = "") -> str:
    t = f" title=\"失败原因：{reason}\"" if reason else ""
    return (f"<span class='st st-fail' id='st-{node}'{t}>失败 ✗ "
            f"<button class='wc-retry' data-tid='{node}'>重试</button></span>")


def _pt_register(tids: list, conv_id: str, fname: str) -> None:
    try:
        with _PT_LOCK:
            d = _pt_load()
            now = int(_pt_time.time())
            for t in tids:
                d[t] = {"conv_id": conv_id, "fname": fname, "ts": now, "node": t}
            tmp = _pt_store().with_suffix(".tmp")
            tmp.write_text(_pt_json.dumps(d, ensure_ascii=False), encoding="utf-8")
            _pt_os.replace(tmp, _pt_store())
    except Exception:
        pass


def _pt_mark(task_id: str, success: bool, reason: str = "") -> None:
    """runner 回填时给任务树画布对应节点着色 + 刷新进度计数；任何异常静默不拦回填。
    V233: 失败**保留映射**（供一键重试查 conv/fname/node），成功才出队；
    匹配双态（待执行 / 重试中…），失败态内嵌「重试」按钮（点击经宿主走 /retry 端点）。"""
    try:
        with _PT_LOCK:
            d = _pt_load()
            v = d.get(task_id)
            if v and success:
                d.pop(task_id, None)
                tmp = _pt_store().with_suffix(".tmp")
                tmp.write_text(_pt_json.dumps(d, ensure_ascii=False), encoding="utf-8")
                _pt_os.replace(tmp, _pt_store())
        if not v:
            return
        node = v.get("node", task_id)
        fp = db.conv_files_dir(v["conv_id"]) / v["fname"]
        if not fp.exists():
            return
        html = fp.read_text(encoding="utf-8", errors="ignore")
        # V234 死信闸：重试满 3 次仍失败 → 终态死信（无按钮，防无限重试）
        if success:
            new_node = _pt_ok_span(node)
        elif int(v.get("retries", 0)) >= 3:
            new_node = _pt_dead_span(node, reason)
        else:
            new_node = _pt_fail_span(node, reason)
        hit = None
        for cand in (_pt_wait_span(node), _pt_retrying_span(node)):
            if cand in html:
                hit = cand
                break
        if hit is None:
            return   # 已标过 / 用户改过节点：静默
        html = html.replace(hit, new_node, 1)
        import re as _re2
        done = html.count("class='st st-ok'")
        html = _re2.sub(r"<b id='wc-prog'>\d+</b>", f"<b id='wc-prog'>{done}</b>", html, count=1)
        # V235 收官总结：全部步骤完成且未发过 → 回填一条完成消息（幂等标记进画布注释）
        _mm = _re2.search(r"</b>/(\d+) 完成", html)
        if success and _mm and done == int(_mm.group(1)) and "<!--wc-alldone-->" not in html:
            html += "\n<!--wc-alldone-->"
            try:
                db.create_message(v["conv_id"], "user",
                                  f"✅ 任务树《{v['fname']}》{done} 步全部完成——点开画布看全绿；如需复盘可让我基于任务树写总结。")
            except Exception:
                pass
        fp.write_text(html, encoding="utf-8")
    except Exception:
        pass


async def _do_plan(user: dict, runner: str, goal: str, conv_id: str) -> dict:
    """V235: plan 拆解主体外提——create(kind=plan) 与 死信 replan 共用同一条链。"""
    if not goal:
        raise HTTPException(400, "plan 需要 payload.goal")
    # V251 孤岛修复（与 team 同款）：conv_id 为空时自建后端会话——否则任务树画布无落点，
    # App 派多步规划后无处看进度。自建后 App 动态页「最新产物」即见任务树。
    if not conv_id:
        try:
            import uuid as _uuid
            conv_id = "c" + _uuid.uuid4().hex[:12]
            db.create_conversation(conv_id, user.get("uid", "anonymous"), ("规划·" + goal)[:30])
        except Exception:
            conv_id = ""

    steps = []
    try:
        from starlette.concurrency import run_in_threadpool
        from hashmm.api.model_manager import get_active_llm_fn
        fn, _model = get_active_llm_fn()
        prompt = ("把下面的目标拆成 2-5 个可独立执行的子任务，严格输出 JSON 数组，"
                  "每项 {\"kind\": \"browser_use|file|task\", \"goal\": \"一句话\"}，不要任何多余文字。\n目标：" + goal)
        def _call():
            try:
                return fn(prompt, max_tok=500)
            except TypeError:
                return fn(prompt)
        raw = await run_in_threadpool(_call)
        import json as _json, re as _re
        m = _re.search(r"\[.*\]", str(raw), _re.S)
        if m:
            arr = _json.loads(m.group(0))
            for it in arr[:5]:
                g = str((it or {}).get("goal") or "").strip()
                k = str((it or {}).get("kind") or "task").strip()
                if g:
                    steps.append({"kind": k if k in ("browser_use", "file", "task") else "task", "goal": g})
    except Exception:
        steps = []
    if not steps:   # 拆解失败：诚实降级为单任务
        steps = [{"kind": "task", "goal": goal}]
    tids = []
    for st in steps:
        child_payload = {"goal": st["goal"], "task": st["goal"], "conv_id": conv_id}
        child_id = dq.create_task(
            runner, st["kind"], child_payload, created_by=user.get("sub", "")
        )
        tids.append(child_id)
        _admit_dispatch_work(
            user, task_id=child_id, kind=st["kind"], payload=child_payload,
        )
    if conv_id:
        try:   # V231 自治纪元：任务树落成画布附件（点开即 ArtifactPanel，可编辑可发布）
            import time as _t
            esc = lambda x: str(x).replace("&", "&amp;").replace("<", "&lt;")
            fname = f"任务树-{_t.strftime('%m%d-%H%M%S')}.html"
            rows = "".join(
                f"<li><span class='k'>{esc(st['kind'])}</span>{esc(st['goal'])}"
                f"{_pt_wait_span(tid)}</li>"
                for st, tid in zip(steps, tids))
            page = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    f"<title>任务树</title><style>:root{{--accent:#2563eb;--bg:#fff;--fg:#1a1a1a;--muted:#6b7280;--border:#e5e7eb}}"
                    "html.dark{--bg:#0b0b0d;--fg:#ececf1;--muted:#9ca3af;--border:#27272a}"
                    "body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.8 Inter,'Noto Sans SC',sans-serif;padding:26px 30px}"
                        "::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-thumb{background:rgba(128,128,128,0.25);border-radius:4px}"
                    "h1{font-size:19px;margin:0 0 2px}.sub{color:var(--muted);font-size:12px;margin-bottom:14px}"
                    "ol{padding-left:22px}li{margin:8px 0}"
                    ".k{display:inline-block;font-size:10.5px;padding:1px 8px;border-radius:99px;background:#2563eb22;color:var(--accent);margin-right:8px}"
                    ".st{float:right;font-size:11px;color:var(--muted)}"
                    ".st-ok{color:#15803d;font-weight:600}.st-fail{color:#b42318;font-weight:600}"
                    ".wc-retry{margin-left:6px;font-size:10px;padding:1px 8px;border-radius:99px;border:1px solid #b42318;background:transparent;color:#b42318;cursor:pointer}"
                    ".wc-retry:disabled{opacity:.5;cursor:default}"
                    ".st-dead{color:#7c2d12;font-weight:600}"
                    ".wc-replan{margin-left:6px;font-size:10px;padding:1px 8px;border-radius:99px;border:1px solid #b45309;background:transparent;color:#b45309;cursor:pointer}"
                    "#wc-retry-all{float:none;margin:2px 0 12px;font-size:11px}</style></head><body>"
                    f"<h1>🤖 任务树</h1><div class='sub'>目标：{esc(goal)} · <b id='wc-prog'>0</b>/{len(steps)} 完成 · 电脑端逐个执行，本页节点自动着色（发布后团队同链接实时看进度）</div>"
                    "<button id='wc-retry-all' class='wc-retry'>↻ 全部重试失败项</button>"
                    f"<ol>{rows}</ol>"
                    "<script>(function(){try{window.parent.postMessage({type:'wc:ready'},'*')}catch(e){}"
                    "window.addEventListener('message',function(e){var m=e.data||{};"
                    "if(m.type==='wc:theme'){document.documentElement.classList.toggle('dark',!!m.dark);}});"
                    "document.addEventListener('click',function(e){var b=e.target&&e.target.closest?e.target.closest('.wc-retry'):null;"
                    "if(!b||b.disabled)return;"
                    "if(b.id==='wc-retry-all'){var list=Array.prototype.slice.call(document.querySelectorAll('.wc-retry[data-tid]')).filter(function(x){return !x.disabled});"
                    "if(!list.length){var t0=b.textContent;b.textContent='没有可重试的失败项';setTimeout(function(){b.textContent=t0},1600);return}"
                    "b.disabled=true;b.textContent='已重派 '+list.length+' 项…';var tids=list.map(function(x){x.disabled=true;x.textContent='已重派…';return x.getAttribute('data-tid')});"
                    "try{window.parent.postMessage({type:'wc:retry_all',tids:tids},'*')}catch(err){}return}"
                    "if(b.classList.contains('wc-replan')){b.disabled=true;b.textContent='已转规划…';"
                    "try{window.parent.postMessage({type:'wc:replan',tid:b.getAttribute('data-tid')},'*')}catch(err){}return}"
                    "b.disabled=true;b.textContent='已重派…';"
                    "try{window.parent.postMessage({type:'wc:retry',tid:b.getAttribute('data-tid')},'*')}catch(err){}});})();</script>"
                    "</body></html>")
            fdir = db.conv_files_dir(conv_id)
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / fname).write_text(page, encoding="utf-8")
            _pt_register(tids, conv_id, fname)   # V232: 子任务→画布节点 映射登记
            tree = "\n".join(f"{i + 1}. [{st['kind']}] {st['goal']}" for i, st in enumerate(steps))
            db.create_message(conv_id, "user",
                              f"🤖 多步规划已入队（{len(steps)} 步，电脑端逐个执行）：\n{tree}",
                              files=[{"filename": fname,
                                      "download_url": f"/api/conversations/{conv_id}/download/{fname}"}])
        except Exception:
            try:
                tree = "\n".join(f"{i + 1}. [{st['kind']}] {st['goal']}" for i, st in enumerate(steps))
                db.create_message(conv_id, "user", f"🤖 多步规划已入队（{len(steps)} 步）：\n{tree}")
            except Exception:
                pass
    db.audit(user["uid"], user["sub"], "dispatch_plan", f"{runner}/{len(steps)}steps")
    return {"ok": True, "task_ids": tids, "plan": steps}


@router.post("", summary="派一个任务给指定 runner")
async def create(request: Request):
    user = require_auth(request)
    body = await request.json()
    runner = str(body.get("runner", "default")).strip() or "default"
    kind = str(body.get("kind", "task")).strip() or "task"
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload 必须是对象")
    payload = dict(payload)
    conv_id = str(payload.get("conv_id") or "").strip()
    # V340：派活结果会回写 conv_id；它与消息写入一样必须做对象属主校验，不能因为
    # 经过 desktop runner 就让知道 ID 的用户向别人的 Chat 注入任务/结果。
    if conv_id:
        require_conv_access(request, conv_id)
    # Chat 的功能上下文也可服务桌面浏览器/Computer Use，但不能把客户端对象原样塞进
    # 特权 Agent 的 system。统一复用 Chat API 的白名单、预算、脱敏与不可信边界；任何
    # 客户端自造 workspace_context 都先删除，只有服务端规范化结果可以进入队列。
    raw_feature_contexts = payload.pop("feature_contexts", None)
    payload.pop("workspace_context", None)
    payload.pop("workspace_context_meta", None)
    if raw_feature_contexts:
        from hashmm.agent.feature_context import normalize_feature_contexts
        feature_bundle = normalize_feature_contexts(raw_feature_contexts)
        rendered = feature_bundle.render()
        if rendered:
            payload["workspace_context"] = rendered
            payload["workspace_context_meta"] = feature_bundle.observability()
    if kind == "plan":   # V230/V235: 多步规划（主体在 _do_plan，死信重新规划复用）
        return await _do_plan(user, runner,
                              str(payload.get("goal") or payload.get("task") or "").strip(),
                              conv_id)
    if kind in ("team", "multi_agent"):   # V249: 多智能体协作（服务端并行角色，画布=控制室）
        from hashmm.agent.team import start_team
        return await start_team(user,
                                str(payload.get("goal") or payload.get("task") or "").strip(),
                                conv_id)
    tid = dq.create_task(runner, kind, payload, created_by=user.get("sub", ""))
    work = _admit_dispatch_work(
        user, task_id=tid, kind=kind, payload=payload,
    )
    # 从主 Chat 发起的桌面任务要把用户轮次持久化；面板派活不伪造聊天消息。
    # desktop runner 仍只负责过程/结果回帖，职责边界清晰且不会重复用户消息。
    if conv_id and payload.get("source_chat") is True:
        goal = str(payload.get("goal") or payload.get("task") or "").strip()
        if goal:
            db.create_message(conv_id, "user", goal)
    db.audit(user["uid"], user["sub"], "dispatch_create", f"{runner}/{kind}")
    try:   # V254 总控接入：派活广播（失败静默）
        from hashmm.agent.global_workspace import broadcast as _gwb
        _gwb("dispatch", "task", f"派活 {kind} → {runner}：{str(payload.get('goal') or payload.get('task') or '')[:100]}",
             salience=0.6, user=user.get("sub", ""))
    except Exception:
        pass
    return {"ok": True, "task_id": tid, "work_run_id": str(work.get("id") or "")}


@router.get("/poll", summary="runner 认领一条任务（无任务返回 task=null）")
async def poll(
    request: Request,
    runner: str = "default",
    device_id: str = "",
    device_name: str = "",
    app_version: str = "",
):
    user = require_auth(request)
    if not str(device_id or "").strip():
        # Claiming a side-effecting task without a stable device identity
        # cannot be tied to an execution lease.
        raise HTTPException(428, "桌面端需要升级并提供稳定的 device_id")
    # Admin desktops consume only their own account's queue as well.  Admin
    # visibility must never silently become cross-tenant execution authority.
    task_owner = str(user.get("sub") or "")
    task = dq.poll(
        runner,
        created_by=task_owner,
        presence_owner=str(user.get("uid") or ""),
        device_id=device_id,
        display_name=device_name,
        app_version=app_version,
    )
    if task and device_id:
        from hashmm.agent import work_runtime

        run = work_runtime.get_run_by_source(
            str(task.get("task_id") or ""), str(user.get("uid") or ""),
        )
        if run is not None:
            target = run.get("execution_target") if isinstance(
                run.get("execution_target"), dict
            ) else {}
            selected_id = str(target.get("target_id") or "")
            if target.get("kind") == "remote_device" and selected_id and selected_id != device_id:
                dq.release_claim(
                    str(task.get("task_id") or ""),
                    created_by=task_owner,
                )
                return {"task": None, "waiting_for_device_id": selected_id}
            lease = work_runtime.acquire_execution_lease(
                str(run.get("id") or ""),
                user_id=str(user.get("uid") or ""),
                holder_type="remote_device",
                holder_id=device_id,
                expected_revision=int(run.get("revision") or 0),
                idempotency_key=(
                    f"dispatch-claim:{task['task_id']}:{device_id}:{int(_pt_time.time() * 1000)}"
                )[:120],
                ttl_seconds=120,
            )
            task["work_run_id"] = str(run.get("id") or "")
            task["execution_lease"] = lease.get("lease") if lease.get("ok") else None
            task["execution_authority"] = bool(lease.get("ok"))
            if not lease.get("ok"):
                # The queue claim succeeded but execution authority did not.
                # Immediately return it to the same owner's queue and do not
                # expose executable input to this runner.
                dq.release_claim(
                    str(task.get("task_id") or ""),
                    created_by=task_owner,
                )
                return {
                    "task": None,
                    "authority_error": str(
                        lease.get("error") or "lease_required"
                    ),
                }
    return {"task": task}


@router.get("/runners", summary="runner 心跳（轻量：侧边栏角标用）")
async def runners(request: Request):
    user = require_auth(request)
    return {"runners": dq.runners_status(
        owner=str(user.get("uid") or ""),
        created_by=str(user.get("sub") or ""),
    )}


@router.post("/runners/heartbeat", summary="登记当前账号的一台执行设备在线")
async def runner_heartbeat(request: Request):
    """Presence is independent from task claiming and remote-screen hosting."""
    user = require_auth(request)
    # V1400 clients put the tiny presence payload in the query string.  This
    # avoids Starlette waiting for a request body after a mobile/desktop peer
    # has already disconnected through Cloudflare.  Keep JSON support for older
    # clients, but never try to read a body that was not sent.
    body = dict(request.query_params)
    content_length = str(request.headers.get("content-length") or "0").strip()
    if content_length not in {"", "0"}:
        try:
            legacy_body = await request.json()
            if isinstance(legacy_body, dict):
                body.update(legacy_body)
        except ClientDisconnect:
            # The peer has gone away; do not turn routine Tunnel churn into an
            # unhandled ASGI exception. Query-only V1501 clients never enter
            # this legacy body path.
            if not body.get("device_id"):
                return {"ok": False, "disconnected": True}
    device_id = str(body.get("device_id") or "").strip()
    runner = str(body.get("runner") or "desktop").strip() or "desktop"
    if not device_id:
        raise HTTPException(400, "device_id 不能为空")
    if runner not in {"desktop", "server", "worker"}:
        raise HTTPException(400, "runner 类型不受支持")
    ok = dq.touch_runner(
        owner=str(user.get("uid") or ""), device_id=device_id,
        runner=runner, display_name=str(body.get("device_name") or ""),
        app_version=str(body.get("app_version") or ""),
    )
    if not ok:
        raise HTTPException(503, "设备在线状态暂时无法写入")
    return {
        "ok": True, "device_id": device_id, "runner": runner,
        "online_ttl_seconds": 35, "server_time": _pt_time.time(),
    }


@router.post("/{task_id}/complete", summary="runner 回填结果")
async def complete(task_id: str, request: Request):
    user = require_auth(request)
    body = await request.json()
    task_before = _require_task_access(user, dq.get_task(task_id))
    from hashmm.agent import work_runtime

    run = work_runtime.get_run_by_source(task_id, str(user.get("uid") or ""))
    active = (
        work_runtime.get_active_execution_lease(
            str(run.get("id") or ""), str(user.get("uid") or ""),
        )
        if run is not None else None
    )
    if run is not None:
        lease_id = str(body.get("lease_id") or "")
        device_id = str(body.get("device_id") or "")
        try:
            lease_generation = int(body.get("lease_generation") or 0)
        except (TypeError, ValueError):
            lease_generation = 0
        if (
            active is None
            or lease_id != str(active.get("id") or "")
            or lease_generation != int(active.get("generation") or 0)
            or device_id != str(active.get("holder_id") or "")
        ):
            # A valid account token can view its queue, but cannot finish work
            # currently leased to another device. Do not mutate either ledger.
            raise HTTPException(409, "执行租约已失效或不属于当前电脑")
    ok = dq.complete(task_id, bool(body.get("ok", True)), str(body.get("result", ""))[:20000])
    if not ok:
        raise HTTPException(404, "任务不存在或已收尾")
    _reason = "" if bool(body.get("ok", True)) else str(body.get("result", ""))[:60].replace('"', "'").replace("\n", " ")
    _pt_mark(task_id, bool(body.get("ok", True)), _reason)   # V236: 失败摘要进节点 title（悬停可见）
    # V249 执行回写（借鉴 cognee："agent never repeats the same mistake"）：
    # 任务终态写回长期记忆——失败＝教训（importance 高）、成功＝可复用打法；永不拦截回填。
    try:
        _t0 = dq.get_task(task_id) or {}
        _pl = _t0.get("payload") or {}
        _goal0 = str(_pl.get("goal") or _pl.get("task") or _t0.get("kind") or "")[:120]
        if _goal0:
            from hashmm.memory import hub as _mem_hub
            _mem_hub.record_outcome(user["uid"], _goal0, bool(body.get("ok", True)),
                                    str(body.get("result", ""))[:180], source="派活")
    except Exception:
        pass
    db.audit(user["uid"], user["sub"], "dispatch_complete", task_id)
    try:
        if run is not None:
            if active is not None:
                work_runtime.release_execution_lease(
                    str(run.get("id") or ""),
                    user_id=str(user.get("uid") or ""),
                    lease_id=str(active.get("id") or ""),
                    generation=int(active.get("generation") or 0),
                )
            latest = work_runtime.get_run_by_source(
                task_id, str(user.get("uid") or ""),
            ) or run
            work_runtime.append_event(
                str(run.get("id") or ""),
                user_id=str(user.get("uid") or ""),
                event_type="completed" if bool(body.get("ok", True)) else "failed",
                status="completed" if bool(body.get("ok", True)) else "failed",
                summary=(
                    "在线电脑已完成任务并回写结果"
                    if bool(body.get("ok", True))
                    else "在线电脑回报任务未完成"
                ),
                payload={
                    "task_id": task_id,
                    "runner": str(task_before.get("runner") or ""),
                    "result_persisted_in_dispatch": True,
                },
            )
    except Exception:
        # Dispatch completion remains authoritative if the cross-device
        # projection is temporarily unavailable.
        pass
    return {"ok": True}


@router.post("/{task_id}/heartbeat", summary="runner 续租长任务")
async def heartbeat(task_id: str, request: Request):
    user = require_auth(request)
    _require_task_access(user, dq.get_task(task_id))
    if not dq.heartbeat(task_id):
        raise HTTPException(409, "任务不在可续租的执行状态")
    qp = request.query_params
    dq.touch_runner(
        owner=user["uid"],
        device_id=str(qp.get("device_id") or ""),
        runner=str(qp.get("runner") or "desktop"),
        display_name=str(qp.get("device_name") or ""),
        app_version=str(qp.get("app_version") or ""),
    )
    try:
        from hashmm.agent import work_runtime

        run = work_runtime.get_run_by_source(task_id, str(user.get("uid") or ""))
        lease = (
            work_runtime.get_active_execution_lease(
                str(run.get("id") or ""), str(user.get("uid") or ""),
            ) if run is not None else None
        )
        device_id = str(qp.get("device_id") or "")
        if lease is not None and str(lease.get("holder_id") or "") == device_id:
            work_runtime.heartbeat_execution_lease(
                str(run.get("id") or ""),
                user_id=str(user.get("uid") or ""),
                lease_id=str(lease.get("id") or ""),
                generation=int(lease.get("generation") or 0),
                ttl_seconds=120,
            )
    except Exception:
        pass
    return {"ok": True}


@router.post("/{task_id}/retry", summary="失败子任务一键重试（任务树节点自动复位并继续联动着色）")
async def retry(task_id: str, request: Request):
    """V233: 从队列取原任务重建入队；plan_trees 侧车把节点映射迁移到新 task_id，
    画布节点由「失败 ✗」复位为「重试中…」——新任务完成后照常变绿。"""
    user = require_auth(request)
    t = _require_task_access(user, dq.get_task(task_id))
    # V234 守门：满 3 次重试即死信——拒绝再入队并把画布节点标终态（防手动 API 绕过无限重试）
    with _PT_LOCK:
        _dv = _pt_load().get(task_id)
    if _dv and int(_dv.get("retries", 0)) >= 3:
        try:
            node0 = _dv.get("node", task_id)
            fp0 = db.conv_files_dir(_dv["conv_id"]) / _dv["fname"]
            if fp0.exists():
                h0 = fp0.read_text(encoding="utf-8", errors="ignore")
                import re as _reD
                _m = _reD.search(rf"<span class='st st-fail' id='st-{node0}'[^>]*>.*?</span>", h0)
                if _m:
                    fp0.write_text(h0.replace(_m.group(0), _pt_dead_span(node0), 1), encoding="utf-8")
        except Exception:
            pass
        raise HTTPException(409, "已达重试上限（3 次）：该子任务标记为死信，请人工排查后重新规划")
    new_tid = dq.create_task(t.get("runner") or "desktop", t.get("kind") or "task",
                             t.get("payload") or {}, created_by=user.get("sub", ""))
    _admit_dispatch_work(
        user,
        task_id=new_tid,
        kind=t.get("kind") or "task",
        payload=t.get("payload") or {},
        retry_of=task_id,
    )
    with _PT_LOCK:
        d = _pt_load()
        v = d.pop(task_id, None)
        if v:
            node = v.get("node", task_id)
            d[new_tid] = {"conv_id": v["conv_id"], "fname": v["fname"],
                          "ts": int(_pt_time.time()), "node": node,
                          "retries": int(v.get("retries", 0)) + 1}
            tmp = _pt_store().with_suffix(".tmp")
            tmp.write_text(_pt_json.dumps(d, ensure_ascii=False), encoding="utf-8")
            _pt_os.replace(tmp, _pt_store())
    if v:
        try:   # 画布节点复位（失败态整串 → 重试中…）；找不到即静默
            node = v.get("node", task_id)
            fp = db.conv_files_dir(v["conv_id"]) / v["fname"]
            if fp.exists():
                html = fp.read_text(encoding="utf-8", errors="ignore")
                import re as _reR
                _m = _reR.search(rf"<span class='st st-fail' id='st-{node}'[^>]*>.*?</span>", html)
                if _m:
                    fp.write_text(html.replace(_m.group(0), _pt_retrying_span(node), 1), encoding="utf-8")
        except Exception:
            pass
    db.audit(user["uid"], user["sub"], "dispatch_retry", f"{task_id}->{new_tid}")
    return {"ok": True, "task_id": new_tid}


@router.post("/{task_id}/replan", summary="死信一键重新规划：原目标重发给 plan 拆解，新任务树回同一会话")
async def replan(task_id: str, request: Request):
    user = require_auth(request)
    t = _require_task_access(user, dq.get_task(task_id))
    pl = t.get("payload") or {}
    goal = str(pl.get("goal") or pl.get("task") or "").strip()
    if not goal:
        raise HTTPException(400, "原任务无可规划目标")
    with _PT_LOCK:
        d = _pt_load()
        v = d.pop(task_id, None)
        if v:
            tmp = _pt_store().with_suffix(".tmp")
            tmp.write_text(_pt_json.dumps(d, ensure_ascii=False), encoding="utf-8")
            _pt_os.replace(tmp, _pt_store())
    conv_id = (v or {}).get("conv_id") or str(pl.get("conv_id") or "").strip()
    if v:
        try:   # 旧画布死信节点 → 「已转重新规划 ↗」终态（含按钮整串替换）
            node = v.get("node", task_id)
            fp = db.conv_files_dir(v["conv_id"]) / v["fname"]
            if fp.exists():
                h = fp.read_text(encoding="utf-8", errors="ignore")
                import re as _reP
                _m = _reP.search(rf"<span class='st st-dead' id='st-{node}'[^>]*>.*?</span>", h)
                if _m:
                    fp.write_text(h.replace(_m.group(0), _pt_dead_done_span(node), 1), encoding="utf-8")
        except Exception:
            pass
    res = await _do_plan(user, t.get("runner") or "desktop", goal, conv_id)
    db.audit(user["uid"], user["sub"], "dispatch_replan", f"{task_id}->{len(res.get('task_ids') or [])}steps")
    return res


@router.get("/{task_id}", summary="查询任务状态/结果")
async def status(task_id: str, request: Request):
    user = require_auth(request)
    return _require_task_access(user, dq.get_task(task_id))


@router.get("", summary="最近任务列表（含 runner 心跳）")
async def recent(request: Request, limit: int = 50):
    user = require_auth(request)
    owner = None if user.get("role") == "admin" else str(user.get("sub") or "")
    return {"items": dq.list_tasks(limit, created_by=owner),
            "stats": dq.stats() if owner is None else {},
            "runners": dq.runners_status(
                owner=str(user.get("uid") or ""),
                created_by=str(user.get("sub") or ""),
            )}
