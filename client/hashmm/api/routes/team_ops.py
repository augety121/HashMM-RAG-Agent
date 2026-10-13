"""多智能体操作界面 REST（V254）。

此前"多智能体"只是把示例文字填进输入框，用户回车会当普通消息发出去——
点了没有操作界面（V253 用户实测反馈）。本模块给前端 TeamPanel 提供完整数据链：

  POST /api/team/preview       只拆解不执行：目标 → 2-4 角色分工（用户可改可删）
  POST /api/team/start         按（可能改过的）分工启动并行执行，立即返回 team_id
  GET  /api/team/status/{id}   轮询：角色四态 + 各自产出 + 最终汇总
  POST /api/team/{id}/stop     所有者协作式停止（当前模型调用安全返回后丢弃结果）
  POST /api/team/{id}/retry    终态团队以新 team_id 重新运行，保留 retry_of 审计关系
  GET  /api/team/list          我的最近团队（面板"历史"区）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hashmm.api.auth import require_auth, require_conv_access
from hashmm.agent.feature_context import normalize_feature_contexts
from hashmm.agent import team as team_mod

router = APIRouter(prefix="/api/team", tags=["team"])

_PUBLIC_TEAM_FIELDS = {
    "team_id", "goal", "conv_id", "file", "created", "finished", "status",
    "final", "mode", "retry_of", "stop_requested", "stop_requested_at",
    "recovery_reason", "attached_context", "evidence", "trace", "roles",
    "work_run_id", "root_session_id", "agent_mesh", "agent_session_tree",
    "mailbox", "independent_verification", "evidence_graph",
    "causal_work_graph", "execution_receipts", "execution_frontier",
    "completion_gate", "mesh_decision", "ok_n", "total",
    "task_contract",
}


def _public_team(team: dict) -> dict:
    """Fail closed when new private control-plane fields are introduced."""
    return {
        key: team[key] for key in _PUBLIC_TEAM_FIELDS
        if key in team
    }


@router.get("/agents", summary="预置 Agent 库（智能体工坊选人用）")
async def team_agents(request: Request):
    require_auth(request)
    public_fields = ("id", "name", "skill", "category", "source", "license")
    return {
        "agents": [
            {key: value for key in public_fields if (value := a.get(key)) is not None}
            for a in team_mod.AGENT_LIBRARY
        ],
        "count": len(team_mod.AGENT_LIBRARY),
    }


@router.get("/route", summary="问答智能调度设置（V256）")
async def agent_route_get(request: Request):
    require_auth(request)
    from hashmm.api.settings_store import get_setting
    return {"mode": get_setting("chat_agent_mode", "auto") or "auto"}


@router.post("/route", summary="设置问答智能调度：auto / off / <agent_id>")
async def agent_route_set(request: Request):
    user = require_auth(request)
    body = await request.json()
    mode = str(body.get("mode") or "auto").strip()
    valid = {"auto", "off"} | {a["id"] for a in team_mod.AGENT_LIBRARY}
    if mode not in valid:
        raise HTTPException(400, "mode 需为 auto / off / 有效 agent id")
    from hashmm.api.settings_store import set_setting
    set_setting("chat_agent_mode", mode)
    try:
        from hashmm.agent.global_workspace import broadcast
        label = {"auto": "智能调度", "off": "关闭"}.get(mode) or f"固定「{mode}」"
        broadcast("team", "route_cfg", f"问答智能调度已设为：{label}", salience=0.5,
                  user=user.get("sub", ""))
    except Exception:
        pass
    return {"ok": True, "mode": mode}


@router.post("/preview", summary="预览分工（智能编队或手动编队，只拆解不执行）")
async def team_preview(request: Request):
    user = require_auth(request)
    body = await request.json()
    goal = str(body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(400, "需要 goal")
    agent_ids = body.get("agent_ids") if isinstance(body.get("agent_ids"), list) else None
    roles = await run_in_threadpool(team_mod.decompose_preview, goal, agent_ids)
    try:
        from hashmm.agent.global_workspace import broadcast
        broadcast("team", "preview",
                  ("手动编队" if agent_ids else "智能编队") + f"预览：{goal[:100]}",
                  salience=0.4, user=user.get("sub", ""))
    except Exception:
        pass
    return {"ok": True, "roles": roles, "mode": "manual" if agent_ids else "auto"}


@router.post("/start", summary="按分工启动多智能体")
async def team_start(request: Request):
    user = require_auth(request)
    body = await request.json()
    goal = str(body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(400, "需要 goal")
    conv_id = str(body.get("conv_id") or "").strip()
    if conv_id:
        # The team writes a control-room Artifact and the final answer into the
        # conversation.  Bind that side effect to the authenticated owner before
        # any model work or background task is created.
        require_conv_access(request, conv_id)
    roles = body.get("roles") if isinstance(body.get("roles"), list) else None
    mode = str(body.get("mode") or "auto").strip()
    context_bundle = normalize_feature_contexts(body.get("feature_contexts"))
    allowed_tools = [str(item)[:120] for item in (body.get("allowed_tools") or []) if str(item).strip()][:64]
    workspace_id = str(body.get("workspace_id") or "").strip()[:160]
    write_markers = {"execute_code", "create_file", "write_file", "shell", "git_apply_patch", "computer_use"}
    if write_markers.intersection(allowed_tools) and not workspace_id:
        raise HTTPException(400, "包含写入工具的 Team Run 必须绑定受管 workspace_id")
    task_contract = {
        "schema": "hashmm.agent-task.v1",
        "objective": goal[:2000],
        "scope": str(body.get("scope") or "conversation")[:120],
        "inputs": [str(item)[:300] for item in (body.get("inputs") or [])[:64]],
        "workspace_id": workspace_id,
        "allowed_tools": allowed_tools,
        "budget": body.get("budget") if isinstance(body.get("budget"), dict) else {},
        "acceptance": str(body.get("acceptance") or "")[:2000],
        "deadline": str(body.get("deadline") or "")[:80],
        "parent_run_id": str(body.get("parent_run_id") or "")[:160],
        "result_schema": body.get("result_schema") if isinstance(body.get("result_schema"), dict) else {},
        "reasoning_policy": "decision_summary_action_evidence_only",
    }
    return await team_mod.start_team(
        user, goal, conv_id, roles_override=roles, mode=mode,
        feature_context=context_bundle.render(),
        feature_context_meta=context_bundle.observability(),
        task_contract=task_contract,
    )


@router.get("/status/{team_id}", summary="团队实时状态")
async def team_status(team_id: str, request: Request):
    user = require_auth(request)
    # 所有者校验与“不存在”共用 404，避免通过枚举 team_id 探测他人的团队任务。
    t = team_mod.get_team(team_id, user.get("uid", ""))
    if not t:
        raise HTTPException(404, "团队不存在或已过期（重启后内存态清空，历史看会话回帖）")
    return _public_team(t)


@router.post("/{team_id}/stop", summary="请求停止团队（协作式、幂等）")
async def team_stop(team_id: str, request: Request):
    user = require_auth(request)
    t = team_mod.request_team_stop(team_id, user.get("uid", ""))
    if not t:
        raise HTTPException(404, "团队不存在或已过期")
    return {"ok": True, "team": _public_team(t)}


@router.post("/{team_id}/retry", summary="以新任务重试一个已结束团队")
async def team_retry(team_id: str, request: Request):
    user = require_auth(request)
    result = await team_mod.retry_team(user, team_id)
    if result is None:
        raise HTTPException(404, "团队不存在或已过期")
    if result.get("conflict"):
        raise HTTPException(409, "团队仍在运行或停止中，不能重复派发")
    return result


@router.get("/{team_id}/tree", summary="读取持久 Agent 树、任务 DAG 与邮箱状态")
async def team_tree(team_id: str, request: Request):
    user = require_auth(request)
    team = team_mod.get_team(team_id, user.get("uid", ""))
    if not team:
        raise HTTPException(404, "团队不存在或无权访问")
    return {
        "schema": "hashmm.agent-team-control.v1",
        "team_id": team_id,
        "root_session_id": team.get("root_session_id", ""),
        "session_tree": team.get("agent_session_tree") or {},
        "task_graph": team.get("agent_mesh") or {},
        "mailbox": team.get("mailbox") or {},
        "independent_verification": team.get("independent_verification") or {},
    }


@router.post("/{team_id}/agents/{session_id}/message", summary="向运行中的子 Agent 发送纠正")
async def team_agent_message(team_id: str, session_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    team = team_mod.get_team(team_id, owner)
    if not team:
        raise HTTPException(404, "团队不存在或无权访问")
    body = await request.json()
    content = str(body.get("content") or "").strip()
    client_message_id = str(body.get("client_message_id") or "").strip()[:160]
    if not content or len(content) > 4000:
        raise HTTPException(400, "纠正内容需为 1-4000 字")
    tree = team.get("agent_session_tree") or {}
    nodes = {
        str(node.get("session_id") or ""): node
        for node in (tree.get("nodes") or [])
        if isinstance(node, dict)
    }
    target = nodes.get(session_id)
    if not target or session_id == str(team.get("root_session_id") or ""):
        raise HTTPException(404, "子 Agent 不存在或无权访问")
    if str(target.get("status") or "") in {
        "completed", "failed", "stopped", "interrupted", "orphaned",
    }:
        raise HTTPException(409, "该子 Agent 已结束，不能再接收纠正")
    try:
        from hashmm.agent import mesh
        message = mesh.send_message(
            owner_id=owner, team_id=team_id,
            recipient_session_id=session_id,
            sender_kind="user", message_type="correction",
            body=content,
            idempotency_key=client_message_id or "",
        )
        mailbox = mesh.mailbox_summary(owner_id=owner, team_id=team_id)
    except LookupError:
        raise HTTPException(404, "子 Agent 不存在或无权访问")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        from hashmm.api import database as db
        db.audit(owner, user.get("sub", ""), "team_agent_message",
                 f"{team_id}:{session_id}:{message.get('message_id', '')}")
    except Exception:
        pass
    return {"ok": True, "message": message, "mailbox": mailbox}


@router.post("/{team_id}/agents/{session_id}/stop", summary="协作式停止单个子 Agent")
async def team_agent_stop(team_id: str, session_id: str, request: Request):
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    team = team_mod.get_team(team_id, owner)
    if not team:
        raise HTTPException(404, "团队不存在或无权访问")
    tree = team.get("agent_session_tree") or {}
    member_ids = {
        str(node.get("session_id") or "")
        for node in (tree.get("nodes") or [])
        if isinstance(node, dict)
    }
    if session_id not in member_ids or session_id == str(team.get("root_session_id") or ""):
        raise HTTPException(404, "子 Agent 不存在或无权访问")
    from hashmm.agent.session import get_session_registry
    sessions = get_session_registry()
    if not sessions.request_interrupt(session_id, owner_id=owner):
        current = sessions.get(session_id, owner_id=owner)
        if not current:
            raise HTTPException(404, "子 Agent 不存在或无权访问")
        raise HTTPException(409, "子 Agent 已结束或已在停止")
    try:
        from hashmm.api import database as db
        db.audit(owner, user.get("sub", ""), "team_agent_stop", f"{team_id}:{session_id}")
    except Exception:
        pass
    return {"ok": True, "session": sessions.get(session_id, owner_id=owner)}


@router.get("/list", summary="我的最近团队")
async def team_list(request: Request):
    user = require_auth(request)
    return {
        "items": [
            _public_team(team)
            for team in team_mod.list_teams(user.get("uid", ""))[:10]
        ],
    }
