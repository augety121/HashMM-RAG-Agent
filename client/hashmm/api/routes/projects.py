"""Project, tag, template, and sharing routes."""
from __future__ import annotations
import json, uuid
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from hashmm.api import database as db
from hashmm.api.auth import require_auth, require_conv_access

router = APIRouter(prefix="/api", tags=["projects"])
_PERMISSION_MODES = {"ask", "read_only", "trusted_workspace"}
_PROJECT_STATUSES = {"active", "paused", "completed"}


def _success_criteria(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()[:240] for item in value
        if isinstance(item, str) and str(item).strip()
    ][:12]


def _project_payload(body: dict, *, partial: bool = False) -> dict:
    """Validate the user-authored project brief without model inference."""
    clean: dict = {}
    text_limits = {
        "name": 120,
        "description": 1000,
        "custom_prompt": 8000,
        "goal": 2000,
        "deliverable": 1200,
    }
    for field, limit in text_limits.items():
        if field in body or not partial:
            clean[field] = str(body.get(field) or "").strip()[:limit]
    if "success_criteria" in body or not partial:
        clean["success_criteria"] = _success_criteria(
            body.get("success_criteria")
        )
    if "permission_mode" in body or not partial:
        mode = str(body.get("permission_mode") or "ask").strip()
        if mode not in _PERMISSION_MODES:
            raise HTTPException(422, "未知的工作权限方式")
        clean["permission_mode"] = mode
    if "status" in body:
        status = str(body.get("status") or "").strip()
        if status not in _PROJECT_STATUSES:
            raise HTTPException(422, "未知的项目状态")
        clean["status"] = status
    if "archived" in body:
        clean["archived"] = bool(body.get("archived"))
    return clean


# ── Sharing ──

@router.get("/shared/{share_code}")
async def get_shared_conversation(share_code: str, request: Request):
    """Get a shared conversation."""
    share = db.get_shared(share_code)
    if not share:
        raise HTTPException(404, "分享链接不存在或已过期")
    conv = db.get_conversation(share["conv_id"])
    messages = db.get_messages(share["conv_id"])

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        title = conv.get("title", "对话") if conv else "对话"
        html_msgs = []
        for m in messages:
            role_label = "用户" if m["role"] == "user" else "助手"
            content = (m.get("content", "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br>"))
            html_msgs.append(f'<div class="msg"><b>{role_label}</b><br>{content}</div>')
        body = "\n".join(html_msgs)
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{body}</body></html>"
        return HTMLResponse(html)

    return {"share": share, "conversation": conv, "messages": messages}


# ── Templates ──

@router.get("/templates")
async def get_templates(category: str = None):
    """List prompt templates."""
    return {"templates": db.list_templates(category)}


@router.post("/templates")
async def create_template(request: Request):
    """Create a new template."""
    body = await request.json()
    tid = db.create_template(
        name=body.get("name", ""), category=body.get("category", ""),
        prompt=body.get("prompt", ""), description=body.get("description", ""),
    )
    return {"id": tid, "ok": True}


@router.post("/templates/{tid}/use")
async def use_template(tid: str):
    """Use a template (increment counter, return prompt)."""
    t = db.use_template(tid)
    if not t:
        raise HTTPException(404, "模板不存在")
    return t


# ── Projects ──

@router.get("/projects")
async def list_projects(request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    return {"projects": db.list_projects(uid)}


@router.post("/projects")
async def create_project(request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    body = await request.json()
    clean = _project_payload(body)
    clean["name"] = clean["name"] or "新项目"
    pid = db.create_project(
        uid,
        clean["name"],
        clean["description"],
        clean["custom_prompt"],
        goal=clean["goal"],
        deliverable=clean["deliverable"],
        success_criteria=clean["success_criteria"],
        permission_mode=clean["permission_mode"],
    )
    return {"id": pid, "ok": True, "project": db.get_project_for_user(pid, uid)}


@router.patch("/projects/{pid}")
async def update_project(pid: str, request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    body = await request.json()
    if db.get_project_for_user(pid, uid) is None:
        raise HTTPException(404, "项目不存在")
    clean = _project_payload(body, partial=True)
    if "name" in clean and not clean["name"]:
        raise HTTPException(422, "项目名称不能为空")
    if not clean or not db.update_project(pid, uid, **clean):
        raise HTTPException(400, "没有可保存的项目设置")
    return {"ok": True, "project": db.get_project_for_user(pid, uid)}


@router.get("/projects/{pid}")
async def get_project(pid: str, request: Request):
    """Return one owner-scoped project goal brief."""
    user = require_auth(request)
    project = db.get_project_for_user(pid, str(user.get("uid") or ""))
    if project is None:
        # Do not reveal whether another account owns this id.
        raise HTTPException(404, "项目不存在")
    return {"project": project}


@router.get("/projects/{pid}/conversations")
async def list_project_conversations(
    pid: str,
    request: Request,
    limit: int = 50,
    cursor: str = "",
):
    """Stable, owner-scoped project Chat view used by large sidebars."""
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    if db.get_project_for_user(pid, uid) is None:
        raise HTTPException(404, "project_not_found")
    from hashmm.api.routes.conversations import (
        _decode_conversation_cursor,
        _encode_conversation_cursor,
    )
    cursor_key = _decode_conversation_cursor(cursor, scope="project", project_id=pid)
    page_limit = max(1, min(int(limit or 50), 100))
    rows = db.list_conversations(
        uid,
        limit=page_limit + 1,
        archived=0,
        meaningful_only=True,
        scope="project",
        project_id=pid,
        cursor=cursor_key,
    )
    has_more = len(rows) > page_limit
    items = rows[:page_limit]
    return {
        "project_id": pid,
        "conversations": items,
        "page": {
            "limit": page_limit,
            "has_more": has_more,
            "next_cursor": _encode_conversation_cursor(
                items[-1], scope="project", project_id=pid,
            ) if has_more and items else None,
        },
    }


@router.get("/projects/{pid}/resources")
async def list_project_resources(pid: str, request: Request):
    """List server-authoritative resource memberships for one project."""
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    if db.get_project_for_user(pid, uid) is None:
        raise HTTPException(404, "project_not_found")
    return {
        "schema": "hashmm.project-resources.v1",
        "project_id": pid,
        "items": db.list_project_resources(pid, uid),
    }


@router.post("/projects/{pid}/resources")
async def add_project_resource(pid: str, request: Request):
    """Attach exact bytes from an owner-scoped conversation to a project."""
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    if db.get_project_for_user(pid, uid) is None:
        raise HTTPException(404, "project_not_found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    conv_id = str(body.get("conversation_id") or "")[:160]
    filename = str(body.get("filename") or "")[:240]
    safe_name = Path(filename).name
    conversation = db.get_conversation(conv_id)
    if (
        not conversation
        or str(conversation.get("user_id") or "") != uid
        or not safe_name
        or safe_name != filename
    ):
        raise HTTPException(404, "resource_not_found")
    source_path = db.get_conversation_file_path(conv_id, safe_name)
    if not source_path or not Path(source_path).is_file():
        raise HTTPException(404, "resource_not_found")
    from hashmm.pipeline.resource_pipeline import parse_resource, resource_summary
    resource = parse_resource(source_path, display_name=safe_name)
    row = db.add_project_resource(
        pid,
        uid,
        resource_id=str(resource.get("resource_id") or ""),
        conv_id=conv_id,
        filename=safe_name,
        sha256=str(resource.get("sha256") or ""),
        role=str(body.get("role") or "reference"),
    )
    if row is None:
        raise HTTPException(404, "resource_not_found")
    return {"ok": True, "membership": row, "resource": resource_summary(resource)}


@router.delete("/projects/{pid}/resources/{resource_id}")
async def delete_project_resource(pid: str, resource_id: str, request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    if db.get_project_for_user(pid, uid) is None:
        raise HTTPException(404, "project_not_found")
    if not db.remove_project_resource(pid, uid, resource_id):
        raise HTTPException(404, "resource_not_found")
    return {"ok": True}


# ── Tags ──

@router.post("/conversations/{conv_id}/tags")
async def add_tag(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        raise HTTPException(400, "tag is required")
    db.add_conversation_tag(conv_id, tag)
    return {"ok": True}


@router.delete("/conversations/{conv_id}/tags/{tag}")
async def remove_tag(conv_id: str, tag: str, request: Request):
    require_conv_access(request, conv_id)
    db.remove_conversation_tag(conv_id, tag)
    return {"ok": True}


@router.get("/tags")
async def list_tags(request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    return {"tags": db.list_all_tags(uid)}


@router.get("/tags/{tag}/conversations")
async def get_convs_by_tag(tag: str, request: Request):
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    return {"conversations": db.get_conversations_by_tag(uid, tag)}
