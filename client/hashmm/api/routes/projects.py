"""Project, tag, template, and sharing routes."""
from __future__ import annotations
import json, uuid
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from hashmm.api import database as db
from hashmm.api.auth import get_current_user, require_auth

router = APIRouter(prefix="/api", tags=["projects"])


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
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    return {"projects": db.list_projects(uid)}


@router.post("/projects")
async def create_project(request: Request):
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    body = await request.json()
    pid = db.create_project(uid, body.get("name", "新项目"), body.get("description", ""))
    return {"id": pid, "ok": True}


@router.patch("/projects/{pid}")
async def update_project(pid: str, request: Request):
    body = await request.json()
    db.update_project(pid, **body)
    return {"ok": True}


# ── Tags ──

@router.post("/conversations/{conv_id}/tags")
async def add_tag(conv_id: str, request: Request):
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        raise HTTPException(400, "tag is required")
    db.add_conversation_tag(conv_id, tag)
    return {"ok": True}


@router.delete("/conversations/{conv_id}/tags/{tag}")
async def remove_tag(conv_id: str, tag: str):
    db.remove_conversation_tag(conv_id, tag)
    return {"ok": True}


@router.get("/tags")
async def list_tags(request: Request):
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    return {"tags": db.list_all_tags(uid)}


@router.get("/tags/{tag}/conversations")
async def get_convs_by_tag(tag: str, request: Request):
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    return {"conversations": db.get_conversations_by_tag(uid, tag)}
