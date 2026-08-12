"""画布发布与组织内查看（V226，Artifacts 化核心）。

对标 Claude Code Artifacts 的协作可见性模型（用户提供的架构图）：
  执行在会话侧（生成/编辑/版本都在画布），共享在 Viewer 侧（认证访问、只读、
  同一链接永远最新版）。我们的实现比"republish 才更新"更进一步：Viewer 直读
  latest 文件与版本侧车，**编辑保存即全网最新**，republish 动作本身被消灭。

端点：
  POST /api/canvas/publish {conv_id, filename, visibility}   属主发布/改可见性，幂等同 id
  GET  /api/canvas/shares?conv_id=&filename=                  属主查发布状态（前端按钮态）
  DELETE /api/canvas/shares/{share_id}                        属主停止分享
  GET  /canvas/{share_id}                                     Viewer 页（HTML）：
        org=任何已登录用户可看；private=仅属主。?token= 与 /view 页同口径。

存储：data/canvas_shares.json 侧车（share_id → 记录），进程锁 + 原子写；
      访问计数/last_view 记入同文件（审计轻量版，v2 迁审计日志表）。
安全红线：Viewer iframe sandbox=allow-scripts 且壳不监听任何 wc:* 消息——
      编辑/保存/小问答在 Viewer 侧天然失效，只读由架构保证而非样式伪装。
"""
from __future__ import annotations

import html as _html
import json
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.api.routes.conversations import require_conv_access
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.canvas_share")
router = APIRouter(tags=["canvas"])

_LOCK = threading.Lock()
_VIS = {"org", "private", "link"}   # V228: link=链接+口令，对外无需登录

# ── V234 在线人数（内存态，TTL 45s；重启清零——只是"氛围气泡"，不做持久） ──
_PRESENCE: dict = {}
_PRES_LOCK = threading.Lock()


def _presence_touch(sid: str, vid: str, who: str = "") -> tuple:
    now = int(time.time())
    with _PRES_LOCK:
        m = _PRESENCE.setdefault(sid, {})
        m[vid[:24]] = {"ts": now, "who": (who or "访客")[:20]}
        stale = [k for k, e in m.items() if now - int(e.get("ts", 0)) > 45]
        for k in stale:
            m.pop(k, None)
        uniq = sorted({e.get("who", "访客") for e in m.values()})[:10]
        names = [{"name": w, "initial": (w[:1].upper() if w and w[0].isascii() else w[:1])} for w in uniq]
        return len(m), names


def _store_path() -> Path:
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "canvas_shares.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _store_path()
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception as e:
        log_suppressed(logger, e)
    return {}


def _save(d: dict) -> None:
    p = _store_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _fname(filename: str) -> str:
    import os.path as _osp
    return _osp.basename(urllib.parse.unquote(filename).split("?")[0].split("#")[0])


@router.post("/api/canvas/publish")
async def publish(request: Request):
    user = require_auth(request)
    body = await request.json()
    conv_id = str((body or {}).get("conv_id") or "").strip()
    fname = _fname(str((body or {}).get("filename") or ""))
    vis = str((body or {}).get("visibility") or "org").strip()
    if vis not in _VIS:
        vis = "org"
    if not conv_id or not fname:
        raise HTTPException(400, "conv_id 与 filename 必填")
    require_conv_access(request, conv_id)
    fp = db.conv_files_dir(conv_id) / fname
    if not fp.exists():
        raise HTTPException(404, "画布文件不存在")
    # Evidence-backed canvases may only be published when every linked browser
    # quote still matches its latest isolated-browser observation.  Creative
    # canvases with no EvidenceRef remain publishable.
    from hashmm.api.routes.canvas_evidence import canvas_evidence_summary
    evidence = canvas_evidence_summary(conv_id, fname)
    if evidence["linked"] and not evidence["ready"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "画布引用的网页证据已经变化或无法定位，请重新验证后再发布。"
                f"（已变化 {evidence['stale']}，无法定位 {evidence['unavailable']}）"
            ),
        )
    uid = user.get("uid", "")
    with _LOCK:
        d = _load()
        # 幂等：同 (owner, conv, file) 复用同一 share_id —— 同链接永远最新版
        sid = next((k for k, v in d.items()
                    if v.get("owner") == uid and v.get("conv_id") == conv_id and v.get("filename") == fname), None)
        now = int(time.time())
        if sid is None:
            sid = secrets.token_hex(4)
            d[sid] = {"conv_id": conv_id, "filename": fname, "owner": uid,
                      "visibility": vis, "created": now, "updated": now,
                      "views": 0, "last_view": 0, "comments": []}
        else:
            d[sid]["visibility"] = vis
            d[sid]["updated"] = now
        if vis == "link" and not d[sid].get("passcode"):
            d[sid]["passcode"] = f"{secrets.randbelow(900000) + 100000}"   # 6 位口令，发布者可见
        _save(d)
        rec = d[sid]
    return {"ok": True, "share_id": sid, "url": f"/canvas/{sid}", "visibility": vis,
            "passcode": rec.get("passcode", "") if vis == "link" else "",
            "evidence": evidence}


@router.get("/api/canvas/shares")
async def share_status(request: Request, conv_id: str = "", filename: str = ""):
    user = require_auth(request)
    uid = user.get("uid", "")
    fname = _fname(filename)
    evidence = None
    if conv_id and fname:
        require_conv_access(request, conv_id)
        from hashmm.api.routes.canvas_evidence import canvas_evidence_summary
        evidence = canvas_evidence_summary(conv_id, fname)
    d = _load()
    # V250：空参＝列出本人全部分享（设置·数据管理「共享链接管理」的数据源——此前该按钮是
    # alert 占位；后端本就存着全量分享，这里补一个 list 视图即成真功能）。
    if not conv_id and not filename:
        items = []
        for sid, v in d.items():
            if v.get("owner") != uid:
                continue
            items.append({
                "share_id": sid, "url": f"/canvas/{sid}",
                "conv_id": v.get("conv_id", ""), "filename": v.get("filename", ""),
                "visibility": v.get("visibility", "org"), "views": int(v.get("views", 0)),
                "comments_count": len(v.get("comments") or []),
                "created": int(v.get("created", 0) or v.get("ts", 0) or 0),
            })
        items.sort(key=lambda x: x["created"], reverse=True)
        return {"published": len(items) > 0, "items": items, "total": len(items)}
    for sid, v in d.items():
        if v.get("owner") == uid and v.get("conv_id") == conv_id and v.get("filename") == fname:
            return {"published": True, "share_id": sid, "url": f"/canvas/{sid}",
                    "visibility": v.get("visibility", "org"), "views": v.get("views", 0),
                    "passcode": v.get("passcode", "") if v.get("visibility") == "link" else "",
                    "comments_count": len(v.get("comments") or []),
                    "comments_new": sum(1 for c in (v.get("comments") or [])
                                        if int(c.get("ts", 0)) > int(v.get("comments_read_ts", 0))),
                    "evidence": evidence}
    return {"published": False, "evidence": evidence}


@router.delete("/api/canvas/shares/{share_id}")
async def unpublish(share_id: str, request: Request):
    user = require_auth(request)
    with _LOCK:
        d = _load()
        v = d.get(share_id)
        if not v:
            return {"ok": True}
        if v.get("owner") != user.get("uid"):
            raise HTTPException(403, "只有发布者能停止分享")
        d.pop(share_id, None)
        _save(d)
    return {"ok": True}


@router.post("/api/canvas/comments/read")
async def mark_comments_read(request: Request):
    """V229 已读回执：发布者查看评论后打点；Viewer 端在该时点前的评论显示「发布者已读 ✓」。"""
    user = require_auth(request)
    body = await request.json()
    sid = str((body or {}).get("share_id") or "").strip()
    with _LOCK:
        d = _load()
        v = d.get(sid)
        if not v:
            raise HTTPException(404, "分享不存在")
        if v.get("owner") != user.get("uid"):
            raise HTTPException(403, "只有发布者能标记已读")
        v["comments_read_ts"] = int(time.time())
        _save(d)
    return {"ok": True, "read_ts": v["comments_read_ts"]}


@router.post("/api/canvas/passcode/reset")
async def reset_passcode(request: Request):
    """V229 口令重置：旧口令即刻失效，返回新 6 位口令（仅 link 档、仅发布者）。"""
    user = require_auth(request)
    body = await request.json()
    sid = str((body or {}).get("share_id") or "").strip()
    with _LOCK:
        d = _load()
        v = d.get(sid)
        if not v:
            raise HTTPException(404, "分享不存在")
        if v.get("owner") != user.get("uid"):
            raise HTTPException(403, "只有发布者能重置口令")
        if v.get("visibility") != "link":
            raise HTTPException(400, "仅「链接 + 口令」档支持重置口令")
        v["passcode"] = f"{secrets.randbelow(900000) + 100000}"
        _save(d)
    return {"ok": True, "passcode": v["passcode"]}


@router.post("/api/canvas/comment")
async def add_comment(request: Request):
    """V228 评论回流：组织内同事在 Viewer 留言 → 同步写进发布者的那条会话，
    发布者在主对话里直接看到并可让 agent 继续处理——Viewer 从只读窗口变协作回路。
    link（口令访客）模式不支持评论（匿名不回流）。"""
    user = require_auth(request)
    body = await request.json()
    sid = str((body or {}).get("share_id") or "").strip()
    text = str((body or {}).get("text") or "").strip()[:500]
    if not sid or not text:
        raise HTTPException(400, "share_id 与 text 必填")
    with _LOCK:
        d = _load()
        v = d.get(sid)
        if not v:
            raise HTTPException(404, "分享不存在")
        if v.get("visibility") == "private" and v.get("owner") != user.get("uid"):
            raise HTTPException(403, "该画布仅发布者可见")
        by = (user.get("sub") or user.get("uid") or "同事").split("@")[0][:20]
        import re as _re
        mentions = _re.findall(r"@([\w.\-]{1,24})", text)[:8]
        parent_id = str((body or {}).get("parent_id") or "")[:12]   # V230 线程化：回复挂父评论
        c = {"id": secrets.token_hex(3), "by": by, "text": text, "ts": int(time.time()),
             "mentions": mentions, "parent_id": parent_id}
        v.setdefault("comments", []).append(c)
        v["comments"] = v["comments"][-50:]
        _save(d)
    try:   # V230 站内通知：@到谁通知谁；发布者收"新评论"（自己评自己不通知）
        from hashmm.api.routes.notifications import push_notification
        for m in mentions:
            if m != by:
                push_notification(m, "mention", f"{by} 在《{v['filename']}》评论里提到了你：{text[:80]}", by, sid)
        if v.get("owner") and by != (user.get("sub") or "").split("@")[0]:
            push_notification(v["owner"], "comment", f"{by} 评论了你发布的《{v['filename']}》：{text[:80]}", by, sid)
        if parent_id:   # V233: 回复通知——被回复的评论作者也能收到（自己回自己不通知）
            parent = next((x for x in (v.get("comments") or []) if x.get("id") == parent_id), None)
            if parent and parent.get("by") and parent.get("by") != by:
                push_notification(parent["by"], "reply",
                                  f"{by} 回复了你在《{v['filename']}》的评论：{text[:80]}", by, sid)
    except Exception as e:
        log_suppressed(logger, e)
    try:
        db.create_message(v["conv_id"], "user",
                          f"【画布评论 · {by}】{text}\n（来自组织内 Viewer《{v['filename']}》，可直接让我处理）")
    except Exception as e:
        log_suppressed(logger, e)
    return {"ok": True, "by": by, "ts": c["ts"], "id": c["id"]}


def _pass_page(share_id: str, wrong: bool) -> HTMLResponse:
    tip = "口令不正确，再试一次" if wrong else "此画布由发布者以口令方式对外分享"
    return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>输入口令 · HashMM 画布</title>
<style>body{{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f6f7f9;font:14px system-ui,'Noto Sans SC',sans-serif}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:28px 26px;width:min(340px,88vw);text-align:center}}
input{{width:100%;box-sizing:border-box;margin:14px 0 12px;padding:10px 12px;font-size:16px;letter-spacing:4px;text-align:center;border:1px solid #e5e7eb;border-radius:10px}}
button{{width:100%;padding:10px;border:0;border-radius:10px;background:#2563eb;color:#fff;font-size:14px;cursor:pointer}}
p{{color:#6b7280;font-size:12.5px;margin:6px 0 0}}</style></head><body>
<form class="card" method="get"><div style="font-size:28px">🔒</div>
<b>输入访问口令</b><p>{tip}</p>
<input name="pass" inputmode="numeric" autocomplete="off" placeholder="6 位数字" autofocus>
<button type="submit">查看画布</button></form></body></html>""", status_code=401 if wrong else 200)


@router.post("/api/canvas/presence")
async def presence(request: Request):
    """V234 在线心跳：Viewer 每 20s 上报。org/private 需登录；link 档口令访客亦可计数。"""
    body = await request.json()
    sid = str((body or {}).get("share_id") or "").strip()
    vid = str((body or {}).get("vid") or "").strip()
    if not sid or not vid:
        raise HTTPException(400, "share_id / vid 必填")
    v = _load().get(sid)
    if not v:
        raise HTTPException(404, "分享不存在")
    who = ""
    if v.get("visibility") != "link":
        user = require_auth(request)
        if v.get("visibility") == "private" and v.get("owner") != user.get("uid"):
            raise HTTPException(403, "无权访问")
        who = (user.get("sub") or user.get("uid") or "").split("@")[0]
    n, names = _presence_touch(sid, vid, who)
    # V235/236: 名单仅 org/private（登录可溯）返回头像籽（name+initial）；link 访客只给数字
    return {"ok": True, "online": n,
            "names": names if v.get("visibility") != "link" else []}


@router.get("/canvas/{share_id}", response_class=HTMLResponse)
async def viewer(share_id: str, request: Request):
    """Viewer：org/private=认证只读；link=口令访问（对外，无需登录）。同链接最新、可切近版。"""
    d = _load()
    v = d.get(share_id)
    if not v:
        return HTMLResponse("<h3 style='font-family:sans-serif;padding:24px'>分享不存在或已停止</h3>", status_code=404)
    vis = v.get("visibility", "org")
    user: dict = {}
    if vis == "link":
        got = str(request.query_params.get("pass") or "").strip()
        if got != str(v.get("passcode") or "!"):
            return _pass_page(share_id, wrong=bool(got))
        try:
            user = require_auth(request)   # 带 token 也认（组织内成员点外链）
        except Exception:
            user = {}
    else:
        user = require_auth(request)
        if vis == "private" and v.get("owner") != user.get("uid"):
            raise HTTPException(403, "该画布仅发布者可见")
    conv_id, fname = v["conv_id"], v["filename"]
    fp = db.conv_files_dir(conv_id) / fname
    if not fp.exists():
        return HTMLResponse("<h3 style='font-family:sans-serif;padding:24px'>源画布已被删除</h3>", status_code=404)
    latest = fp.read_text(encoding="utf-8", errors="ignore")
    # 版本（直读侧车，最多 5 个近版；坏档回空，绝不拦 Viewer）
    vers: list = []
    try:
        vp = db.conv_files_dir(conv_id) / ".wc-versions" / f"{fname}.json"
        if vp.exists():
            raw = json.loads(vp.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for it in raw[-5:]:
                    if isinstance(it, dict) and it.get("html"):
                        vers.append({"ts": int(it.get("ts") or 0), "html": str(it.get("html"))[:2_000_000]})
    except Exception as e:
        log_suppressed(logger, e)
    # 访问审计（轻量）：计数 + 最近访问
    try:
        with _LOCK:
            d2 = _load()
            if share_id in d2:
                d2[share_id]["views"] = int(d2[share_id].get("views", 0)) + 1
                d2[share_id]["last_view"] = int(time.time())
                _save(d2)
    except Exception as e:
        log_suppressed(logger, e)

    docs = [{"label": "最新", "html": latest, "ts": v.get("updated", 0)}] + [
        {"label": f"v{i + 1}", "html": x["html"], "ts": x.get("ts", 0)} for i, x in enumerate(vers)
    ]
    payload = json.dumps(docs, ensure_ascii=False).replace("</", "<\\/")
    sid_js = json.dumps(share_id)
    comments_js = json.dumps((v.get("comments") or [])[-10:], ensure_ascii=False).replace("</", "<\\/")
    can_comment_js = "true" if (user and vis != "link") else "false"
    read_ts_js = str(int(v.get("comments_read_ts", 0)))
    title = _html.escape(fname)
    owner_disp = _html.escape((v.get("owner") or "")[:14] + ("…" if len(v.get("owner") or "") > 14 else ""))
    upd = time.strftime("%m-%d %H:%M", time.localtime(v.get("updated", 0))) if v.get("updated") else ""
    views = int(v.get("views", 0)) + 1
    # V227 Viewer 大厂化：暗色自适应 · 精致头部（标题/发布者/更新/浏览） · 版本 pills ·
    # 复制链接 · 品牌尾注 · 移动端友好。只读铁律不变：壳不监听任何 wc:* 消息。
    return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · HashMM 画布</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#16181d;--muted:#6b7280;--bd:#e5e7eb;--ac:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0b0b0d;--card:#141418;--fg:#ececf1;--muted:#9ca3af;--bd:#26262b;--ac:#5b8cff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,-apple-system,'Noto Sans SC',sans-serif}}
header{{display:flex;align-items:center;gap:12px;padding:12px 18px;background:var(--card);border-bottom:1px solid var(--bd);position:sticky;top:0;z-index:2}}
.logo{{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--ac),#7c3aed);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:13px;flex:none}}
.tt{{min-width:0}}.tt b{{display:block;font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:52vw}}
.tt span{{font-size:11px;color:var(--muted)}}
.badge{{font-size:11px;padding:3px 9px;border-radius:99px;background:color-mix(in srgb,var(--ac) 12%,transparent);color:var(--ac);white-space:nowrap}}
.sp{{flex:1}}
.copy{{font-size:12px;padding:5px 12px;border-radius:9px;border:1px solid var(--bd);background:var(--card);color:var(--fg);cursor:pointer}}
.copy:hover{{border-color:var(--ac);color:var(--ac)}}
nav{{display:flex;gap:6px;align-items:center;padding:8px 18px;background:var(--card);border-bottom:1px solid var(--bd);overflow-x:auto}}
nav b{{font-size:11px;color:var(--muted);font-weight:600;flex:none}}
.pill{{font-size:12px;padding:4px 12px;border-radius:99px;border:1px solid var(--bd);background:transparent;color:var(--fg);cursor:pointer;flex:none}}
.pill.on{{background:var(--ac);border-color:var(--ac);color:#fff}}
iframe{{display:block;width:100%;height:calc(100vh - 132px);border:0;background:#fff}}
footer{{padding:9px 18px;font-size:11px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap;align-items:center}}
footer .dot{{width:6px;height:6px;border-radius:99px;background:#22a06b;display:inline-block;margin-right:5px}}
</style></head><body>
<header>
  <div class="logo">画</div>
  <div class="tt"><b>{title}</b><span>发布者 {owner_disp} · 更新 {upd} · 浏览 {views} 次</span></div>
  <span class="badge">只读 · 已认证</span><span class="badge" id="wc-online" style="display:none"></span><span id="wc-online-avatars" style="display:none;align-items:center;margin-left:8px"></span><span class="sp"></span>
  <button class="copy" onclick="navigator.clipboard&&navigator.clipboard.writeText(location.href).then(()=>{{this.textContent='已复制';setTimeout(()=>this.textContent='复制链接',1400)}})">复制链接</button>
</header>
<nav><b>版本</b><span id="pills"></span></nav>
<iframe id="doc" sandbox="allow-scripts"></iframe>
<section id="cmt" style="max-width:860px;margin:0 auto;padding:12px 18px 4px;display:none">
  <div style="font-size:12px;color:var(--muted);font-weight:600;margin-bottom:8px">评论 · 会同步到发布者的对话</div>
  <div id="cmt-list"></div>
  <div id="cmt-input" style="display:none;gap:8px;margin-top:8px">
    <input id="cmt-text" maxlength="500" placeholder="留个建议，发布者在会话里直接看到…" style="flex:1;padding:8px 12px;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg);font-size:13px">
    <button class="copy" id="cmt-send">发送</button>
  </div>
  <div id="cmt-tip" style="font-size:11px;color:var(--muted);margin-top:6px"></div>
</section>
<footer><span><span class="dot"></span>同一链接始终最新（发布者编辑保存即生效）</span><span>HashMM 工作画布 · 组织内分享</span></footer>
<script>
var DOCS = {payload};
var pills = document.getElementById("pills"), fr = document.getElementById("doc");
DOCS.forEach(function(d, i) {{
  var b = document.createElement("button"); b.className = "pill" + (i === 0 ? " on" : "");
  b.textContent = d.label; b.onclick = function() {{
    document.querySelectorAll(".pill").forEach(function(x) {{ x.className = "pill"; }});
    b.className = "pill on"; fr.srcdoc = DOCS[i].html;
  }}; pills.appendChild(b);
}});
fr.srcdoc = DOCS[0].html;
/* ── V228 评论：org/private 登录用户可发；link 口令访客只读 ── */
var SID = {sid_js}, COMMENTS = {comments_js}, CAN_COMMENT = {can_comment_js}, READ_TS = {read_ts_js};
var TOKEN = new URLSearchParams(location.search).get("token") || "";
function esc(t) {{ var d = document.createElement("div"); d.textContent = t; return d.innerHTML; }}
function ago(ts) {{ var s = Math.max(0, Date.now()/1000 - ts); return s<60?"刚刚":s<3600?Math.floor(s/60)+"分钟前":s<86400?Math.floor(s/3600)+"小时前":Math.floor(s/86400)+"天前"; }}
function hiAt(h) {{ return h.replace(/@([\\w.\\-]{{1,24}})/g, '<b style="color:var(--ac)">@$1</b>'); }}
var REPLY_TO = "";
function cmtCard(c, indent) {{
  var seen = (READ_TS > 0 && c.ts <= READ_TS) ? ' · <span style="color:#22a06b">发布者已读 ✓</span>' : '';
  var rep = CAN_COMMENT ? ' · <a href="javascript:void(0)" data-rep="' + (c.id || '') + '" data-by="' + esc(c.by) + '" style="color:var(--ac);text-decoration:none">回复</a>' : '';
  return '<div style="padding:8px 12px;margin:6px 0 6px ' + (indent ? '20px' : '0') + ';background:var(--card);border:1px solid var(--bd);border-radius:12px">'
    + '<div style="font-size:11px;color:var(--muted)">' + esc(c.by) + ' · ' + ago(c.ts) + seen + rep + '</div>'
    + '<div style="font-size:13px;margin-top:2px;white-space:pre-wrap">' + hiAt(esc(c.text)) + '</div></div>';
}}
function renderCmts() {{
  var el = document.getElementById("cmt-list");
  var roots = COMMENTS.filter(function(c) {{ return !c.parent_id; }});
  var html = roots.map(function(c) {{
    var kids = COMMENTS.filter(function(k) {{ return k.parent_id && k.parent_id === c.id; }});
    return cmtCard(c, false) + kids.map(function(k) {{ return cmtCard(k, true); }}).join("");
  }}).join("");
  el.innerHTML = html || '<div style="font-size:12px;color:var(--muted)">还没有评论 · 试试 @同事名 提及 TA</div>';
  el.querySelectorAll("[data-rep]").forEach(function(a) {{
    a.onclick = function() {{
      REPLY_TO = a.getAttribute("data-rep") || "";
      var inp = document.getElementById("cmt-text");
      inp.value = "@" + a.getAttribute("data-by") + " "; inp.focus();
      document.getElementById("cmt-tip").textContent = "正在回复 " + a.getAttribute("data-by") + "（发送后自动挂到该评论下）";
    }};
  }});
}}
(function() {{
  var box = document.getElementById("cmt");
  box.style.display = "block"; renderCmts();
  if (!CAN_COMMENT) {{ document.getElementById("cmt-tip").textContent = "口令访客为只读模式；组织内成员登录后可评论"; return; }}
  var row = document.getElementById("cmt-input"); row.style.display = "flex";
  document.getElementById("cmt-send").onclick = function() {{
    var t = document.getElementById("cmt-text").value.trim(); if (!t) return;
    var btn = this; btn.disabled = true;
    fetch("/api/canvas/comment", {{ method: "POST",
      headers: {{ "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN }},
      body: JSON.stringify({{ share_id: SID, text: t, parent_id: REPLY_TO }}) }})
      .then(function(r) {{ if (!r.ok) throw 0; return r.json(); }})
      .then(function(r) {{ COMMENTS.push({{ id: r.id || "", by: r.by, text: t, ts: r.ts, parent_id: REPLY_TO }}); REPLY_TO = ""; renderCmts();
        document.getElementById("cmt-text").value = "";
        document.getElementById("cmt-tip").textContent = "已同步到发布者会话 ✓"; }})
      .catch(function() {{ document.getElementById("cmt-tip").textContent = "发送失败（登录态失效？）"; }})
      .finally(function() {{ btn.disabled = false; }});
  }};
}})();
/* ── V234 在线人数气泡：20s 心跳，TTL 45s；失败静默隐身 ── */
(function() {{
  var VID = Math.random().toString(36).slice(2, 12);
  function beat() {{
    var h = {{ "Content-Type": "application/json" }};
    if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
    fetch("/api/canvas/presence", {{ method: "POST", headers: h,
      body: JSON.stringify({{ share_id: SID, vid: VID }}) }})
      .then(function(r) {{ if (!r.ok) throw 0; return r.json(); }})
      .then(function(r) {{
        var el = document.getElementById("wc-online");
        var av = document.getElementById("wc-online-avatars");
        if (r.online >= 1) {{
          el.style.display = ""; el.textContent = "👀 " + r.online + " 人在看";
          if (r.names && r.names.length && av) {{
            var pal = ["#2563eb","#16a34a","#d97706","#db2777","#7c3aed","#0891b2"];
            av.innerHTML = r.names.slice(0, 6).map(function(x, i) {{
              return '<span title="' + x.name + '" style="display:inline-flex;align-items:center;justify-content:center;'
                + 'width:22px;height:22px;margin-left:-6px;border-radius:50%;font-size:10px;font-weight:700;color:#fff;'
                + 'border:2px solid var(--bg);background:' + pal[i % pal.length] + '">' + (x.initial || "·") + '</span>';
            }}).join("") + (r.online > 6 ? '<span style="margin-left:4px;font-size:11px;color:var(--muted)">+' + (r.online - 6) + '</span>' : "");
            av.style.display = "inline-flex";
            av.style.cursor = "pointer";
            av.onclick = function() {{   // V237: 点击头像圈 → 完整名单浮层
              var pop = document.getElementById("wc-online-pop");
              if (pop) {{ pop.remove(); return; }}
              pop = document.createElement("div");
              pop.id = "wc-online-pop";
              pop.style.cssText = "position:absolute;top:100%;right:0;margin-top:8px;z-index:50;"
                + "background:var(--card);border:1px solid var(--bd);border-radius:12px;"
                + "padding:10px 14px;box-shadow:0 8px 24px rgba(0,0,0,.12);min-width:150px";
              pop.innerHTML = '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">正在看（' + r.online + '）</div>'
                + r.names.map(function(x) {{ return '<div style="font-size:12.5px;padding:2px 0">' + x.name + '</div>'; }}).join("");
              av.style.position = "relative";
              av.appendChild(pop);
            }};
          }}
        }}
      }})
      .catch(function() {{ /* 老后端/未授权：气泡隐身 */ }});
  }}
  beat(); setInterval(beat, 20000);
}})();
/* Viewer 壳不监听任何 wc:* 消息：编辑/保存/小问答在此天然失效，只读由架构保证 */
</script></body></html>""")
