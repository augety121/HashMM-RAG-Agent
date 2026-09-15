"""Durable background-agent API: create, inspect, pause, resume and stop."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth, require_conv_access

router = APIRouter(prefix="/api/loops", tags=["loops"])


def _principal(user: dict[str, Any]) -> str:
    # uid is immutable and therefore preferable to the display/login name.
    return str(user.get("uid") or user.get("sub") or "")


def _is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"


def _body_int(body: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(body.get(key) if body.get(key) is not None else default)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} 必须是整数") from None


def _body_bool(body: dict[str, Any], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value
    raise HTTPException(400, f"{key} 必须是布尔值")


def _body_origins(body: dict[str, Any]) -> list[str]:
    value = body.get("allowed_origins", [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(400, "allowed_origins 必须是字符串数组")
    return value[:32]


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是合法 JSON 对象") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return body


@router.get("")
async def loops_list(request: Request):
    user = require_auth(request)
    from hashmm.agent.loop_engine import list_loops
    return {"loops": list_loops(_principal(user), is_admin=_is_admin(user))}


@router.post("/goal")
async def loops_goal(request: Request):
    user = require_auth(request)
    body = await _json_body(request)
    goal = str(body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(400, "需要 goal")
    conv_id = str(body.get("conv_id") or "").strip()
    if conv_id:
        require_conv_access(request, conv_id)
    from hashmm.agent.loop_engine import start_goal_loop
    result = start_goal_loop(
        goal,
        max_rounds=_body_int(body, "max_rounds", 4),
        threshold=_body_int(body, "threshold", 85),
        conv_id=conv_id,
        user=_principal(user),
        acceptance=str(body.get("acceptance") or ""),
        approval_mode=str(body.get("approval_mode") or "read_only"),
        max_tokens=_body_int(body, "max_tokens", 50_000),
        max_seconds=_body_int(body, "max_seconds", 3_600),
        network_mode=str(body.get("network_mode") or "deny"),
        allowed_origins=_body_origins(body),
        allow_subagents=_body_bool(body, "allow_subagents"),
    )
    if not result.get("ok"):
        raise HTTPException(429, result.get("error", "创建失败"))
    return result


@router.post("/interval")
async def loops_interval(request: Request):
    user = require_auth(request)
    body = await _json_body(request)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "需要 prompt")
    conv_id = str(body.get("conv_id") or "").strip()
    if conv_id:
        require_conv_access(request, conv_id)
    from hashmm.agent.loop_engine import start_interval_loop
    result = start_interval_loop(
        prompt,
        interval_min=_body_int(body, "interval_min", 30),
        max_runs=_body_int(body, "max_runs", 12),
        user=_principal(user),
        conv_id=conv_id,
        approval_mode=str(body.get("approval_mode") or "read_only"),
        max_tokens=_body_int(body, "max_tokens", 100_000),
        max_seconds=_body_int(body, "max_seconds", 28_800),
        network_mode=str(body.get("network_mode") or "deny"),
        allowed_origins=_body_origins(body),
        allow_subagents=_body_bool(body, "allow_subagents"),
    )
    if not result.get("ok"):
        raise HTTPException(429, result.get("error", "创建失败"))
    return result


@router.get("/{loop_id}")
async def loops_detail(loop_id: str, request: Request):
    user = require_auth(request)
    from hashmm.agent.loop_engine import get_loop
    loop = get_loop(loop_id, _principal(user), is_admin=_is_admin(user))
    if loop is None:
        # Missing and forbidden intentionally share a response to prevent ID probing.
        raise HTTPException(404, "没有这个循环")
    return {"loop": loop}


@router.post("/{loop_id}/pause")
async def loops_pause(loop_id: str, request: Request):
    user = require_auth(request)
    from hashmm.agent.loop_engine import pause_loop
    if not pause_loop(loop_id, _principal(user), is_admin=_is_admin(user)):
        raise HTTPException(404, "循环不存在、无权访问或当前状态不可暂停")
    return {"ok": True}


@router.post("/{loop_id}/resume")
async def loops_resume(loop_id: str, request: Request):
    user = require_auth(request)
    from hashmm.agent.loop_engine import resume_loop
    if not resume_loop(loop_id, _principal(user), is_admin=_is_admin(user)):
        raise HTTPException(409, "循环不存在、无权访问、预算已耗尽或当前不可恢复")
    return {"ok": True}


@router.post("/{loop_id}/stop")
async def loops_stop(loop_id: str, request: Request):
    user = require_auth(request)
    from hashmm.agent.loop_engine import stop_loop
    if not stop_loop(loop_id, _principal(user), is_admin=_is_admin(user)):
        raise HTTPException(404, "没有这个循环")
    return {"ok": True}


@router.post("/{loop_id}/acceptance")
async def loops_acceptance(loop_id: str, request: Request):
    """Record the task owner's explicit acceptance or rejection."""
    user = require_auth(request)
    body = await _json_body(request)
    accepted = _body_bool(body, "accepted")
    note = str(body.get("note") or "").strip()[:500]
    from hashmm.agent.loop_engine import record_user_acceptance
    loop = record_user_acceptance(
        loop_id,
        accepted,
        _principal(user),
        note=note,
        is_admin=_is_admin(user),
    )
    if loop is None:
        # Missing, forbidden and ineligible loops intentionally share a response.
        raise HTTPException(404, "任务不存在、无权访问或尚未到可验收状态")
    return {"ok": True, "loop": loop}
