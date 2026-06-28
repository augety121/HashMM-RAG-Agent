"""Conversation routes — CRUD, files, export, execute, fork, tags.

v17 Phase 63 (security): every /conversations/{conv_id} endpoint now goes
through require_conv_access(...) before touching data. Previously these
endpoints operated purely by id with no ownership check, so anyone who knew a
conv_id could read/modify/delete/export another user's conversation or run code
in their workspace (OWASP API #1 — Broken Object Level Authorization). The
guard returns the conversation row (admins bypass ownership) or raises 404.
"""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)
import json, os, time, uuid
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse, Response, HTMLResponse
from pydantic import BaseModel

from hashmm.api import database as db
from hashmm.api import app_state
from hashmm.api.auth import (
    get_current_user, require_auth, require_conv_access, resolve_user_id,
)
# Phase 63 fix: execute_tool was used below (/execute, /files/create) but never
# imported — those endpoints raised NameError at call time. Import from
# tool_registry (its source module) NOT server: server imports the routes
# package, so importing from server here would create a circular import.
from hashmm.api.tool_registry import execute_tool

router = APIRouter(prefix="/api", tags=["conversations"])


class TitleRequest(BaseModel):
    query: str
    answer: str = ""


# V50: 标题生成的云端硬超时（秒）。超过即回退本地截取。
_TITLE_TIMEOUT_S = 8.0


@router.post("/title")
async def generate_title(req: TitleRequest):
    """Generate a short conversation title from query+answer.

    The LLM call is synchronous and can take many seconds (slow reasoning
    models). Running it directly in this async handler would block the whole
    event loop — starving every other concurrent request (observed: a 29s title
    call stalling unrelated /corpus/stats requests). So we offload it to a
    thread, keeping the loop responsive.
    """
    llm = app_state.llm_fn
    if not llm:
        return {"title": req.query[:30]}
    import asyncio
    # Cloud/local routing: title generation is a cheap, high-volume task → prefer
    # the local model when routing is enabled (cost + latency). Falls back to the
    # cloud fn transparently.
    try:
        from hashmm.llm_router import route_llm, record_routing
        routed, backend = route_llm("title", llm, user_id=getattr(req, "user_id", None))
        llm = routed or llm
        record_routing("title", backend)
    except Exception:
        pass  # nosem: observability-fallback
    try:
        prompt = (f"为以下对话生成一个简短标题（10字以内，不要引号）：\n"
                  f"问：{req.query[:100]}\n答：{req.answer[:100]}\n标题：")
        # Titles are tiny; cap local generation hard so a 7B model doesn't spend
        # seconds emitting tokens we'll throw away (observed 15s otherwise).
        def _call(p):
            try:
                return llm(p, max_new_tokens=24)  # local fn supports the kwarg
            except TypeError:
                return llm(p)                      # cloud fn: no kwarg
        # V50: 云端推理模型在这种 5 字小活上可能跑 19s+（真机日志 SLOW 19441ms）。
        # 超时直接回退本地截取——标题不值得让用户等；后台线程跑完即作废，无副作用。
        t = await asyncio.wait_for(asyncio.to_thread(_call, prompt), timeout=_TITLE_TIMEOUT_S)
        title = t.strip().strip('"\'「」').split('\n')[0][:30]
        return {"title": title if title else req.query[:30]}
    except asyncio.TimeoutError:
        return {"title": req.query[:30]}
    except Exception:
        return {"title": req.query[:30]}



@router.get("/conversations")
async def list_convs(request: Request, since: str = ""):
    """List user's conversations. 按账号双向同步：先把 Supabase 上该账号的对话补建到本地，
    这样 App 建的对话桌面端也能看到（之前只把本地推到云端，是单向的）。
    增量同步：客户端可带 ?since=<上次同步的ISO时间戳>，则只从云端拉变化过的会话，
    没变的不重复下载（大厂客户端同步的标准做法：时间戳游标增量）。不带 since 时行为不变。"""
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    convs = db.list_conversations(user_id, limit=50)
    try:
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            have = {c.get("id") for c in convs}
            remote = supabase_sync.pull_conversations(user_id, since=since or None)
            added = False
            for sc in remote:
                sid = sc.get("id")
                if sid and sid not in have:
                    db.create_conversation(sid, user_id, sc.get("title") or "对话")
                    added = True
            if added:
                convs = db.list_conversations(user_id, limit=50)
            supabase_sync.backfill_conversations(convs)   # 反向：本地新对话补推云端
    except Exception:
        pass
    return {"conversations": convs}


@router.get("/activity")
async def get_activity(request: Request):
    """当前用户客户端正在进行的任务：流式生成中的对话 + 运行中的后台任务(RAG解析/索引)。

    供 App 显示「客户端任务进度」并一键接管(打开对应会话——内容随客户端流式实时同步)。
    流式对话按用户过滤；后台任务为系统级，仅管理员可见，避免多用户泄露。
    """
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    active_chats = db.get_active_chats(user_id)
    jobs_running = []
    if user and user.get("role") == "admin":
        try:
            from hashmm.api import jobs as _jobs
            jobs_running = [
                {"id": j.get("id"), "kind": j.get("kind"), "status": j.get("status"),
                 "done": j.get("done", 0), "total": j.get("total", 0), "message": j.get("message", "")}
                for j in _jobs.list_jobs(limit=50)
                if j.get("status") in ("pending", "running")
            ]
        except Exception:
            jobs_running = []
    return {"active_chats": active_chats, "jobs": jobs_running}


@router.post("/conversations")
async def create_conv(request: Request):
    """Create a new conversation."""
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    body = await request.json()
    conv_id = body.get("id") or str(uuid.uuid4())
    title = body.get("title", "新对话")
    db.create_conversation(conv_id, user_id, title)
    return {"id": conv_id, "title": title}


@router.get("/conversations/{conv_id}")
async def get_conv(conv_id: str, request: Request):
    """Get conversation with messages. 含按账号双向同步：本地没有的消息从 Supabase 补回
    （桌面端打开 App 建/早期的对话也能看到内容；失败安全，不影响返回本地结果）。"""
    conv = require_conv_access(request, conv_id)
    msgs = db.get_messages(conv_id, limit=100)
    try:
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            uid = conv.get("user_id")
            remote = supabase_sync.pull_messages(conv_id, uid)
            if remote and len(remote) > len(msgs):
                db.import_messages_local(conv_id, remote)
                msgs = db.get_messages(conv_id, limit=100)
    except Exception:
        pass
    return {"conversation": conv, "messages": msgs}


@router.delete("/conversations/{conv_id}")
async def delete_conv(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    db.delete_conversation(conv_id)
    return {"ok": True}


@router.patch("/conversations/{conv_id}")
async def update_conv(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    body = await request.json()
    db.update_conversation(conv_id, **body)
    return {"ok": True}


@router.get("/conversations/{conv_id}/messages")
async def get_conv_messages(conv_id: str, request: Request, limit: int = 100):
    require_conv_access(request, conv_id)
    msgs = db.get_messages(conv_id, limit=limit)
    # 云同步：本地↔Supabase 双向。先把云端这条对话的消息补到本地（App 端历史桌面也能看到），
    # 再把本地消息补推回云端。任一步失败都不影响返回本地结果。
    try:
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            conv = db.get_conversation(conv_id)
            uid = conv.get("user_id") if conv else None
            remote = supabase_sync.pull_messages(conv_id, uid)
            if remote and len(remote) > len(msgs):
                db.import_messages_local(conv_id, remote)
                msgs = db.get_messages(conv_id, limit=limit)
            if conv:
                supabase_sync.backfill_messages(msgs, uid)
    except Exception:
        pass
    return {"messages": msgs}


@router.patch("/conversations/{conv_id}/messages/{msg_id}")
async def update_msg(conv_id: str, msg_id: str, request: Request):
    """Update a streaming message (intermediate save)."""
    require_conv_access(request, conv_id)
    body = await request.json()
    db.update_message(msg_id, **body)
    return {"ok": True}


@router.get("/conversations/{conv_id}/files")
async def list_conv_files(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    files = db.list_conversation_files(conv_id)
    # Also check filesystem for files not yet in DB
    fdir = db.conv_files_dir(conv_id)
    if fdir.exists():
        for f in sorted(fdir.iterdir()):
            if f.is_file() and not any(cf["filename"] == f.name for cf in files):
                st = f.stat()
                files.append({"filename": f.name, "path": str(f), "size": st.st_size,
                              "mtime": st.st_mtime,
                              "download_url": f"/api/conversations/{conv_id}/download/{f.name}"})
    for f in files:
        f["download_url"] = f"/api/conversations/{conv_id}/download/{f['filename']}"
        f["size_str"] = f"{f.get('size',0)/1024:.1f}K" if f.get("size",0) > 1024 else f"{f.get('size',0)}B"
        # 补充生成时间（文件实际 mtime），供前端显示正确日期，而非 DB 里可能过期的时间
        if not f.get("mtime"):
            try:
                _fp = fdir / f["filename"]
                if _fp.exists():
                    f["mtime"] = _fp.stat().st_mtime
            except Exception:
                pass
    return {"files": files}


@router.get("/conversations/{conv_id}/files/{filename}")
async def get_conv_file(conv_id: str, filename: str, request: Request):
    return await _serve_conv_file(conv_id, filename, request)


@router.get("/conversations/{conv_id}/download/{filename}")
async def download_conv_file(conv_id: str, filename: str, request: Request):
    """专用下载路由（路径段 /download/ 与 /files/ 区分，避免任何路由匹配歧义）。"""
    return await _serve_conv_file(conv_id, filename, request)


async def _serve_conv_file(conv_id: str, filename: str, request: Request):
    import urllib.parse, os.path as _osp
    decoded = _osp.basename(urllib.parse.unquote(filename).split("?")[0].split("#")[0])
    fdir = db.conv_files_dir(conv_id)
    try:
        existing = [p.name for p in fdir.iterdir()] if fdir.exists() else []
    except Exception:
        existing = ["<iterdir失败>"]
    # 诊断在最前面（鉴权之前），确保一定打印，能区分"鉴权挂"还是"文件没找到"
    _obs_logger.error(f"[下载诊断] conv={conv_id} 文件='{filename}' 解码='{decoded}' "
                 f"目录={fdir}（绝对={fdir.resolve()}）目录存在={fdir.exists()} "
                 f"目录内文件={existing}")
    # 鉴权：失败单独记录，便于区分 404 来源
    try:
        require_conv_access(request, conv_id)
    except HTTPException as _e:
        _obs_logger.error(f"[下载诊断] 鉴权失败 status={_e.status_code} detail={_e.detail} "
                     f"—— 404来自鉴权而非文件查找")
        raise
    for cand in (filename, decoded):
        fp = fdir / cand
        if fp.exists() and fp.is_file():
            return FileResponse(fp, filename=cand)
    try:
        if fdir.exists():
            targets = {filename.strip(), decoded.strip()}
            for p in fdir.iterdir():
                if p.is_file() and p.name.strip() in targets:
                    return FileResponse(p, filename=p.name)
    except Exception:
        pass
    _obs_logger.error(f"[下载诊断] 文件查找失败：'{filename}' 不在 {fdir}")
    raise HTTPException(404, "文件不存在")



@router.get("/conversations/{conv_id}/export")
async def export_conversation(conv_id: str, request: Request, format: str = "markdown"):
    """Export conversation as Markdown or JSON."""
    conv = require_conv_access(request, conv_id)
    msgs = db.get_messages(conv_id, limit=500)
    files = db.list_conversation_files(conv_id)

    if format == "json":
        return {"conversation": conv, "messages": msgs, "files": files}

    # Markdown export
    lines = [f"# {conv.get('title', '对话')}\n"]
    lines.append(f"*导出时间: {time.strftime('%Y-%m-%d %H:%M')}*\n\n---\n")
    for m in msgs:
        role = "👤 用户" if m["role"] == "user" else "🤖 助手"
        lines.append(f"\n### {role}\n\n{m.get('content', '')}\n")
        if m.get("files"):
            for f in (m["files"] if isinstance(m["files"], list) else []):
                fname = f.get("filename", "") if isinstance(f, dict) else str(f)
                if fname:
                    lines.append(f"\n📎 {fname}\n")
        lines.append("\n---\n")

    md_content = "\n".join(lines)
    return PlainTextResponse(md_content, media_type="text/markdown",
                             headers={"Content-Disposition": f"attachment; filename={conv_id}.md"})



@router.post("/conversations/{conv_id}/upload-zip")
async def upload_zip_endpoint(conv_id: str, request: Request, file: UploadFile = File(...)):
    """Upload and extract ZIP to conversation workspace."""
    require_conv_access(request, conv_id)
    from hashmm.api.workspace import upload_zip
    import tempfile
    # Save upload to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    result = upload_zip(tmp_path, conv_id)
    os.unlink(tmp_path)
    return result


@router.get("/conversations/{conv_id}/tree")
async def get_file_tree(conv_id: str, request: Request):
    """Get file tree for conversation workspace."""
    require_conv_access(request, conv_id)
    from hashmm.api.workspace import file_tree, scan_project
    return {
        "tree_text": file_tree(conv_id),
        "project": scan_project(conv_id),
    }


@router.get("/conversations/{conv_id}/search")
async def search_workspace(conv_id: str, request: Request, q: str = ""):
    """Search text in workspace files."""
    require_conv_access(request, conv_id)
    from hashmm.api.workspace import search_in_workspace
    return {"results": search_in_workspace(conv_id, q)}


@router.post("/conversations/{conv_id}/assistant-message")
async def add_assistant_message(conv_id: str, request: Request):
    """桌面文件投送完成后由桌面客户端调用：往对话写一条助手消息（自动同步 Supabase，App 实时可见）。

    body: {"content": "…", "files": [可选附件元数据]}。
    """
    require_conv_access(request, conv_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = (str(body.get("content") or "")).strip()
    if not content:
        raise HTTPException(400, "content 不能为空")
    files = body.get("files") if isinstance(body.get("files"), list) else None
    mid = db.create_message(conv_id, "assistant", content, files=files)
    return {"ok": True, "id": mid}


@router.post("/conversations/migrate")
async def migrate_conversations(request: Request):
    """把旧身份名下的对话过户到当前登录账号（Supabase 身份）。

    场景：以前匿名/本地账号建的对话，换成 Supabase 账号登录后看不到了——调用本接口一键认领。
    body 可选 {"from": "anonymous" 或 "<旧uid>"}，默认 anonymous。
    """
    user = get_current_user(request)
    if not user or not user.get("uid"):
        raise HTTPException(401, "请先登录账号再迁移")
    to_uid = user["uid"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    from_uid = (str(body.get("from") or "anonymous")).strip()
    if from_uid == to_uid:
        return {"ok": True, "moved": 0, "from": from_uid, "to": to_uid, "note": "源与目标相同"}
    moved = db.reassign_conversations(from_uid, to_uid)
    return {"ok": True, "moved": moved, "from": from_uid, "to": to_uid}


@router.get("/conversations/migrate/preview")
async def migrate_conversations_preview(request: Request):
    """预览：当前账号有多少对话、anonymous 旧身份下还有多少可迁移。"""
    user = get_current_user(request)
    if not user or not user.get("uid"):
        raise HTTPException(401, "请先登录账号")
    to_uid = user["uid"]
    return {
        "current_uid": to_uid,
        "mine": db.count_conversations(to_uid),
        "anonymous": db.count_conversations("anonymous"),
    }


def _office_rich(fpath, ext: str) -> dict | None:
    """把 office/pdf 渲染成可在右栏直接预览的结构（docx→HTML、xlsx→表、pptx→分页文本、pdf→交给前端 iframe）。
    任一步失败返回 None，前端回退到「下载」。仅依赖后端本就装好的 python-docx/openpyxl/python-pptx。"""
    try:
        p = str(fpath)
        if ext == ".docx":
            from hashmm.api.doc_preview import docx_to_html
            return {"kind": "html", "html": docx_to_html(p)}
        if ext == ".xlsx":
            from hashmm.api.doc_preview import xlsx_to_json
            return {"kind": "sheets", "sheets": (xlsx_to_json(p) or {}).get("sheets", [])}
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(p)
            slides = []
            for slide in prs.slides:
                texts = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        t = shape.text_frame.text.strip()
                        if t:
                            texts.append(t)
                slides.append("\n".join(texts))
            return {"kind": "slides", "slides": slides, "pages": len(slides)}
        if ext == ".pdf":
            return {"kind": "pdf"}
    except Exception:
        return None
    return None


@router.get("/conversations/{conv_id}/files/{filename}/preview")
async def preview_conv_file(conv_id: str, filename: str, request: Request):
    """Preview a file in conversation workspace (text + office/pdf rich preview)."""
    require_conv_access(request, conv_id)
    import urllib.parse as _up, os.path as _osp
    # 防御：有的客户端会把 ?token=... 拼进文件名段（→ 文件名超长 OSError）。一律剥掉查询/锚点+目录。
    filename = _osp.basename(_up.unquote(filename).split("?")[0].split("#")[0])
    fdir = db.conv_files_dir(conv_id)
    fpath = fdir / filename
    if not fpath.exists():
        raise HTTPException(404, "文件不存在")

    ext = fpath.suffix.lower()
    size = fpath.stat().st_size
    binary_exts = {".pptx", ".docx", ".xlsx", ".pdf", ".zip", ".gz", ".tar",
                   ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico"}

    if ext in binary_exts or size > 1000000:
        # office/pdf 给出富预览结构；其余二进制仍只给下载。
        rich = _office_rich(fpath, ext) if ext in {".docx", ".xlsx", ".pptx", ".pdf"} else None
        return {
            "filename": filename, "ext": ext[1:], "size": size,
            "binary": True, "content": None, "lines": None,
            "rich": rich,
            "download_url": f"/api/conversations/{conv_id}/download/{filename}"
        }

    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        lang_map = {".py": "python", ".js": "javascript", ".ts": "typescript",
                    ".java": "java", ".cpp": "cpp", ".c": "c", ".go": "go",
                    ".rs": "rust", ".html": "html", ".css": "css", ".json": "json",
                    ".md": "markdown", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml"}
        return {
            "filename": filename, "ext": ext[1:], "size": size,
            "language": lang_map.get(ext, "text"),
            "content": content[:100000], "lines": len(content.splitlines()),
            "binary": False,
            "download_url": f"/api/conversations/{conv_id}/download/{filename}"
        }
    except Exception as e:
        return {"filename": filename, "ext": ext[1:], "size": size,
                "binary": True, "content": None, "error": str(e),
                "download_url": f"/api/conversations/{conv_id}/download/{filename}"}


@router.get("/conversations/{conv_id}/files/{filename}/view", response_class=HTMLResponse)
async def view_conv_file(conv_id: str, filename: str, request: Request):
    """返回适配手机的完整 HTML 预览页：App 内置 WebView 直接打开、不跳浏览器。
    docx→HTML、xlsx→表格、pptx→分页、txt/code→等宽文本、pdf→pdf.js 渲染、图片→<img>。"""
    require_conv_access(request, conv_id)
    import urllib.parse, html as _html, os.path as _osp
    fname = _osp.basename(urllib.parse.unquote(filename).split("?")[0].split("#")[0])
    fdir = db.conv_files_dir(conv_id)
    fpath = fdir / fname
    if not fpath.exists():
        return HTMLResponse("<h3 style='font-family:sans-serif;padding:24px'>文件不存在</h3>", status_code=404)
    ext = fpath.suffix.lower()
    tok = request.query_params.get("token", "")
    dl = f"/api/conversations/{conv_id}/download/{urllib.parse.quote(fname)}"
    dl_tok = dl + (("?token=" + urllib.parse.quote(tok)) if tok else "")
    head = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=5'>"
            "<style>body{margin:0;padding:16px;font-family:system-ui,-apple-system,sans-serif;"
            "color:#1a1a1a;background:#fff;line-height:1.7;font-size:15px;-webkit-text-size-adjust:100%}"
            "table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;display:block;overflow-x:auto}"
            "td,th{border:1px solid #e5e7eb;padding:6px 10px;text-align:left;white-space:nowrap}"
            "th{background:#f9fafb;font-weight:600}"
            "pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fa;padding:12px;border-radius:8px;font-size:13px}"
            ".slide{border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin:10px 0}"
            ".slide .n{font-size:11px;color:#888;margin-bottom:6px}img{max-width:100%}"
            "canvas{max-width:100%;margin-bottom:8px;box-shadow:0 1px 4px rgba(0,0,0,.12)}"
            "h1,h2,h3{margin:.6em 0 .3em}a{color:#2563eb}</style></head><body>")
    tail = "</body></html>"
    try:
        if ext == ".docx":
            from hashmm.api.doc_preview import docx_to_html
            body = docx_to_html(str(fpath))
        elif ext == ".xlsx":
            from hashmm.api.doc_preview import xlsx_to_json
            sheets = (xlsx_to_json(str(fpath)) or {}).get("sheets", [])
            parts = []
            for sh in sheets:
                parts.append(f"<h3>{_html.escape(str(sh.get('name', '')))}</h3><table>")
                headers = sh.get("headers") or []
                if headers:
                    parts.append("<tr>" + "".join(f"<th>{_html.escape(str(h))}</th>" for h in headers) + "</tr>")
                for row in (sh.get("rows") or []):
                    parts.append("<tr>" + "".join(f"<td>{_html.escape('' if c is None else str(c))}</td>" for c in row) + "</tr>")
                parts.append("</table>")
            body = "".join(parts) or "<p>空表格</p>"
        elif ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(str(fpath))
            parts = []
            for i, slide in enumerate(prs.slides):
                txt = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        t = shape.text_frame.text.strip()
                        if t:
                            txt.append(_html.escape(t))
                parts.append(f"<div class='slide'><div class='n'>第 {i + 1} 页</div>" + "<br>".join(txt) + "</div>")
            body = "".join(parts) or "<p>空演示文稿</p>"
        elif ext == ".pdf":
            body = (f"<div id='pdf'></div><p id='fb' style='display:none'>无法在线渲染，<a href='{dl_tok}'>点此下载查看</a></p>"
                    "<script src='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'></script>"
                    "<script>try{pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';"
                    f"pdfjsLib.getDocument('{dl_tok}').promise.then(function(pdf){{"
                    "for(var i=1;i<=pdf.numPages;i++){(function(n){pdf.getPage(n).then(function(p){"
                    "var vp=p.getViewport({scale:1.5});var c=document.createElement('canvas');var ctx=c.getContext('2d');"
                    "c.width=vp.width;c.height=vp.height;document.getElementById('pdf').appendChild(c);"
                    "p.render({canvasContext:ctx,viewport:vp});});})(i);}"
                    "}).catch(function(){document.getElementById('fb').style.display='block';});}"
                    "catch(e){document.getElementById('fb').style.display='block';}</script>")
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            body = f"<img src='{dl_tok}'>"
        else:
            content = fpath.read_text(encoding="utf-8", errors="replace")[:200000]
            body = f"<pre>{_html.escape(content)}</pre>"
    except Exception as e:
        body = f"<p>预览失败：{_html.escape(str(e)[:120])}</p><p><a href='{dl_tok}'>点此下载查看</a></p>"
    return HTMLResponse(head + body + tail)




@router.get("/conversations/{conv_id}/export/jupyter")
async def export_jupyter(conv_id: str, request: Request):
    """Export conversation as Jupyter Notebook."""
    require_conv_access(request, conv_id)
    nb = db.export_as_jupyter(conv_id)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ipynb', delete=False, encoding='utf-8') as f:
        json.dump(nb, f, ensure_ascii=False)
        tmp = f.name
    return FileResponse(tmp, filename=f"{conv_id[:8]}_notebook.ipynb",
                       media_type="application/x-ipynb+json")


@router.get("/conversations/{conv_id}/export/markdown")
async def export_markdown(conv_id: str, request: Request):
    """Export conversation as Markdown."""
    require_conv_access(request, conv_id)
    md = db.export_as_markdown(conv_id)
    return Response(content=md, media_type="text/markdown",
                   headers={"Content-Disposition": f"attachment; filename={conv_id[:8]}.md"})


@router.get("/conversations/{conv_id}/export/html")
async def export_html(conv_id: str, request: Request):
    """Export conversation as standalone HTML."""
    require_conv_access(request, conv_id)
    html = db.export_as_html(conv_id)
    return Response(content=html, media_type="text/html",
                   headers={"Content-Disposition": f"attachment; filename={conv_id[:8]}.html"})




@router.get("/conversations/{conv_id}/dependencies")
async def analyze_deps(conv_id: str, request: Request):
    """Analyze project dependencies in workspace."""
    require_conv_access(request, conv_id)
    from hashmm.api.workspace import analyze_dependencies
    return analyze_dependencies(conv_id)



@router.post("/conversations/{conv_id}/share")
async def share_conversation(conv_id: str, request: Request):
    """Create a share link for a conversation."""
    require_conv_access(request, conv_id)
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    code = db.create_share(conv_id, user_id,
                          password=body.get("password"),
                          expires_hours=body.get("expires_hours", 72))
    return {"share_code": code, "url": f"/shared/{code}"}


@router.post("/conversations/{conv_id}/execute")
async def execute_code_in_conv(conv_id: str, request: Request):
    """Execute code within conversation workspace."""
    require_conv_access(request, conv_id)
    body = await request.json()
    code_text = body.get("code", "")
    if not code_text:
        return {"error": "代码为空"}
    result = execute_tool("execute_code", {"code": code_text, "timeout": 15}, {"session_id": conv_id})
    return {"output": result}




@router.patch("/conversations/{conv_id}/messages/{msg_id}/edit")
async def edit_message(conv_id: str, msg_id: str, request: Request):
    """Edit a user message (deletes all subsequent messages)."""
    require_conv_access(request, conv_id)
    body = await request.json()
    new_content = body.get("content", "")
    if not new_content:
        raise HTTPException(400, "内容不能为空")
    
    # Update the message
    db.update_message(msg_id, content=new_content)
    
    # Delete all messages after this one
    with db._conn() as c:
        msg = c.execute("SELECT created_at FROM messages WHERE id=?", (msg_id,)).fetchone()
        if msg:
            c.execute("DELETE FROM messages WHERE conv_id=? AND created_at > ?",
                      (conv_id, msg["created_at"]))
    
    return {"ok": True, "message_id": msg_id}


@router.post("/conversations/{conv_id}/messages/{msg_id}/regenerate")
async def regenerate_message(conv_id: str, msg_id: str, request: Request):
    """Regenerate response from a specific message point."""
    require_conv_access(request, conv_id)
    with db._conn() as c:
        # Find the user message before this assistant message
        msg = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "消息不存在")
        
        # Delete this message and all after it
        c.execute("DELETE FROM messages WHERE conv_id=? AND created_at >= ?",
                  (conv_id, msg["created_at"]))
    
    return {"ok": True, "conv_id": conv_id}




@router.post("/conversations/{cid}/assign-project")
async def assign_project(cid: str, request: Request):
    require_conv_access(request, cid)
    body = await request.json()
    db.assign_conv_to_project(cid, body.get("project_id"))
    return {"ok": True}




@router.post("/conversations/{conv_id}/tags")
async def add_conv_tag(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        raise HTTPException(400, "标签不能为空")
    db.add_tag(conv_id, tag)
    return {"ok": True, "tags": db.get_tags(conv_id)}


@router.delete("/conversations/{conv_id}/tags/{tag}")
async def remove_conv_tag(conv_id: str, tag: str, request: Request):
    require_conv_access(request, conv_id)
    db.remove_tag(conv_id, tag)
    return {"ok": True}


@router.post("/conversations/{conv_id}/fork/{msg_id}")
async def fork_conversation(conv_id: str, msg_id: str, request: Request):
    """Fork conversation from a specific message. Creates a new conversation
    with all messages up to and including msg_id, owned by the caller."""
    require_conv_access(request, conv_id)
    uid = resolve_user_id(request)

    # v17 Phase 67: the old body called db.create_conversation(uid, title)
    # (argument order is (conv_id, user_id, title) → it built a broken row keyed
    # by the user id) and db.add_message (which doesn't exist). Delegate to the
    # correct, already-tested helper instead.
    with db._conn() as c:
        exists = c.execute("SELECT 1 FROM messages WHERE id=? AND conv_id=?",
                           (msg_id, conv_id)).fetchone()
    if not exists:
        raise HTTPException(404, "消息不存在")

    new_id = db.fork_conversation(conv_id, msg_id, uid)
    if not new_id:
        raise HTTPException(404, "对话不存在")
    copied = len(db.get_messages(new_id, limit=500))
    return {"ok": True, "new_conv_id": new_id, "messages_copied": copied}


@router.post("/conversations/{conv_id}/files/create")
async def create_file_in_conv(conv_id: str, request: Request):
    """Create a file in conversation workspace (from CodeBlock button)."""
    require_conv_access(request, conv_id)
    body = await request.json()
    filename = body.get("filename", "untitled.py")
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "内容为空")
    result = execute_tool("create_file", {"filename": filename, "content": content}, {"session_id": conv_id})
    return {"ok": "OK" in result, "result": result, "download_url": f"/api/conversations/{conv_id}/download/{filename}"}


@router.post("/conversations/{conv_id}/upload")
async def upload_file_to_conv(conv_id: str, request: Request, file: UploadFile = File(...)):
    """Upload any file to conversation workspace."""
    require_conv_access(request, conv_id)
    fdir = db.conv_files_dir(conv_id)
    fdir.mkdir(parents=True, exist_ok=True)
    fpath = fdir / (file.filename or "uploaded_file")
    content = await file.read()
    fpath.write_bytes(content)
    
    # Check if it's an image for vision analysis
    ext = fpath.suffix.lower()
    is_image = ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp')
    
    return {
        "ok": True,
        "filename": fpath.name,
        "size": len(content),
        "is_image": is_image,
        "download_url": f"/api/conversations/{conv_id}/download/{fpath.name}",
    }


@router.get("/phone-file-requests")
async def list_phone_file_requests(request: Request):
    """App 轮询：列出"电脑端向手机要照片"的待办请求（pending, target=phone）。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    from hashmm.api import supabase_sync as _sbs
    rows = _sbs.list_phone_requests(user.get("uid"))
    out = [{"id": r.get("id"), "conv_id": r.get("conv_id"), "query": r.get("query", "")}
           for r in rows if isinstance(r, dict) and r.get("id") and r.get("conv_id")]
    return {"requests": out}


@router.post("/phone-file-requests/{req_id}/status")
async def set_phone_file_request_status(req_id: str, request: Request):
    """App 回写：将某条手机照片请求标记为 processing/done/denied/error。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    try:
        body = await request.json()
    except Exception:
        body = {}
    status = (body.get("status") or "done")[:20]
    from hashmm.api import supabase_sync as _sbs
    _sbs.set_request_status(req_id, status)
    return {"ok": True}




@router.post("/conversations/{conv_id}/fork")
async def fork_conversation_api(conv_id: str, request: Request):
    """Fork conversation from a specific message."""
    require_conv_access(request, conv_id)
    uid = resolve_user_id(request)
    body = await request.json()
    msg_id = body.get("message_id", "")
    new_id = db.fork_conversation(conv_id, msg_id, uid)
    if not new_id:
        raise HTTPException(400, "分支失败")
    return {"ok": True, "conv_id": new_id, "url": f"/chat/{new_id}"}


@router.patch("/conversations/{conv_id}/prompt")
async def set_conv_custom_prompt(conv_id: str, request: Request):
    """Set per-conversation custom system prompt."""
    require_conv_access(request, conv_id)
    body = await request.json()
    prompt = body.get("prompt", "")
    with db._conn() as c:
        try:
            c.execute("ALTER TABLE conversations ADD COLUMN custom_prompt TEXT DEFAULT ''")
        except Exception: pass
        c.execute("UPDATE conversations SET custom_prompt=? WHERE id=?", (prompt, conv_id))
    return {"ok": True}



# ── v10.0: Message feedback (triggers skill creation) ──

class FeedbackRequest(BaseModel):
    feedback: str  # "up" or "down"
    message_content: str = ""
    query: str = ""


@router.post("/conversations/{conv_id}/messages/{msg_idx}/feedback")
async def message_feedback(conv_id: str, msg_idx: int, req: FeedbackRequest, request: Request):
    """Record message feedback and optionally trigger skill creation."""
    require_conv_access(request, conv_id)
    uid = resolve_user_id(request)

    # Save feedback to message
    try:
        with db._conn() as c:
            msgs = c.execute(
                "SELECT id, content FROM messages WHERE conv_id=? ORDER BY created_at",
                (conv_id,)
            ).fetchall()
            if msg_idx < len(msgs):
                c.execute(
                    "UPDATE messages SET feedback=? WHERE id=?",
                    (req.feedback, msgs[msg_idx]["id"])
                )
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    # v10.0: Trigger skill creation on positive feedback
    if req.feedback == "up" and req.message_content and len(req.message_content) > 300:
        try:
            from hashmm.evolution.skill_manager import get_skill_manager
            mgr = get_skill_manager()
            if mgr.should_create_skill(
                query=req.query, answer=req.message_content,
                feedback="up", strategy="grounded",
            ):
                mgr.create_from_conversation(
                    query=req.query, answer=req.message_content,
                    llm_fn=app_state.llm_fn,
                )
        except Exception as _e:
            log_suppressed(_obs_logger, _e)

    # v10.0: Record for prompt optimizer
    try:
        from hashmm.evolution.prompt_optimizer import get_prompt_optimizer
        get_prompt_optimizer().record_feedback(
            prompt_template="", task_type="", feedback=req.feedback, query=req.query,
        )
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    # v10.0: Update user model
    try:
        from hashmm.evolution.user_model import get_user_model
        get_user_model().update_from_conversation(
            user_id=uid, query=req.query, answer=req.message_content,
            feedback=req.feedback,
        )
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    return {"ok": True}
