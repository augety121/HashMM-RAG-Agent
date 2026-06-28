"""hashmm/api/supabase_sync.py — 把用户可见数据(会话/消息/设置/记忆)同步写入 Supabase。

让 App 登录后能从 Supabase 读到与桌面客户端一致的数据。

设计要点：
  · 本模块只发 HTTP（Supabase PostgREST），不碰 SQLite —— 调用方(database.py 的钩子)在自己线程里
    同步读好数据后把字典传进来，本模块只在后台守护线程里发请求。绝不阻塞写入路径、绝不抛错。
  · 用 service_role key 写入（服务端可信，绕过 RLS）；只同步 Supabase 账号用户(user_id 形如 "sb_<uuid>")，
    去掉 "sb_" 前缀即 Supabase auth.users 的 uuid。本地原生用户无对应 Supabase 身份 → 跳过。
  · 未配置 supabase_url 或 service_key → enabled()=False → 全部 no-op，零变化。
  · RAG 大文件 / 向量不在此 —— 由 AutoDL 私有云直连。
零新依赖：标准库 urllib。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from datetime import datetime, timezone

from hashmm.utils import get_logger, log_suppressed
from hashmm.api.supabase_auth import supabase_url

logger = get_logger("hashmm.api.supabase_sync")


def _service_key() -> str:
    try:
        from hashmm.api import settings_store
        v = settings_store.get_setting("supabase_service_key", "")
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("HASHMM_SUPABASE_SERVICE_KEY", "")


def enabled() -> bool:
    return bool(supabase_url() and _service_key())


def _uid(user_id) -> str | None:
    """SQLite 的 user_id → Supabase uuid。仅 "sb_<uuid>" 同步；其余(本地原生用户)跳过。"""
    if isinstance(user_id, str) and user_id.startswith("sb_"):
        return user_id[3:]
    return None


def _iso(ts):
    """epoch 浮点 → ISO timestamptz。失败返回 None（让 Supabase 用默认值）。"""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _jsonify(v):
    """SQLite 里 JSON 字段是字符串 → 解析成对象供 jsonb。已是对象则原样。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except Exception:
            return None
    return None


def _req(method: str, path: str, body=None):
    """对 PostgREST 发请求。永不抛错。"""
    try:
        key = _service_key()
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(supabase_url() + path, data=data, method=method)
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Prefer", "resolution=merge-duplicates")  # upsert（按主键合并）
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        log_suppressed(logger, e)


def _bg(fn, *args):
    """后台守护线程执行；未启用则直接返回。"""
    if not enabled():
        return
    try:
        threading.Thread(target=fn, args=args, daemon=True).start()
    except Exception as e:
        log_suppressed(logger, e)


def _get_json(path: str):
    """GET PostgREST，返回解析后的对象（通常是 list）。任何失败返回 None
    （调用方据此跳过——本拉取是"锦上添花"，绝不能因 Supabase 抖动而破坏本地数据/接口）。"""
    try:
        key = _service_key()
        req = urllib.request.Request(supabase_url() + path, method="GET")
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        log_suppressed(logger, e)
        return None


def pull_conversations(user_id, since: str | None = None):
    """从 Supabase 拉取该账号的对话列表（按账号双向同步用）。返回 list（失败为空）。
    增量同步：给定 since（ISO 时间戳）时只拉 updated_at 晚于它的——客户端记住上次同步时间，
    每次只下"变化过的"会话，没变的不重复下载（这是大厂做客户端同步的标准做法：增量/时间戳游标）。"""
    uid = _uid(user_id)
    if not uid:
        return []
    q = (f"/rest/v1/chat_conversations?user_id=eq.{uid}"
         f"&select=id,title,pinned,created_at,updated_at&order=updated_at.desc&limit=200")
    if since:
        from urllib.parse import quote
        q += f"&updated_at=gt.{quote(str(since), safe='')}"
    rows = _get_json(q)
    return rows if isinstance(rows, list) else []


def pull_messages(conv_id, user_id, since: str | None = None):
    """从 Supabase 拉取某对话的消息（按账号双向同步用）。返回 list（失败为空）。
    增量同步：给定 since 时只拉 created_at 晚于它的新消息（消息基本只增不改，按时间游标增量最省）。"""
    uid = _uid(user_id)
    if not uid:
        return []
    q = (f"/rest/v1/chat_messages?conv_id=eq.{conv_id}"
         f"&select=id,conv_id,role,content,thinking,tool_calls,files,sources,suggestions,status,created_at"
         f"&order=created_at.asc&limit=500")
    if since:
        from urllib.parse import quote
        q += f"&created_at=gt.{quote(str(since), safe='')}"
    rows = _get_json(q)
    return rows if isinstance(rows, list) else []


# ─────────────────────────── 会话 ───────────────────────────
def push_conversation(conv: dict):
    _bg(_do_push_conversation, conv)


def _do_push_conversation(conv: dict):
    uid = _uid(conv.get("user_id"))
    if not uid or not conv.get("id"):
        return
    row = {
        "id": conv.get("id"),
        "user_id": uid,
        "title": conv.get("title", "新对话"),
        "pinned": bool(conv.get("pinned", 0)),
        "metadata": _jsonify(conv.get("metadata")) or {},
    }
    for k in ("created_at", "updated_at"):
        iso = _iso(conv.get(k))
        if iso:
            row[k] = iso
    _req("POST", "/rest/v1/chat_conversations", [row])


def remove_conversation(conv_id: str, user_id):
    _bg(_do_remove_conversation, conv_id, user_id)


def _do_remove_conversation(conv_id: str, user_id):
    if not _uid(user_id) or not conv_id:
        return
    _req("DELETE", f"/rest/v1/chat_conversations?id=eq.{conv_id}", None)  # 级联删消息(外键)


# ─────────────────────────── 消息 ───────────────────────────
def push_message(msg: dict, user_id):
    _bg(_do_push_message, msg, user_id)

def _do_push_message(msg: dict, user_id):
    uid = _uid(user_id)
    if not uid or not msg.get("id"):
        return
    row = {
        "id": msg.get("id"),
        "conv_id": msg.get("conv_id"),
        "user_id": uid,
        "role": msg.get("role", "user"),
        "content": msg.get("content", ""),
        "thinking": msg.get("thinking", ""),
        "tool_calls": _jsonify(msg.get("tool_calls")) or [],
        "files": _jsonify(msg.get("files")) or [],
        "sources": _jsonify(msg.get("sources")) or [],
        "suggestions": _jsonify(msg.get("suggestions")) or [],
        "status": msg.get("status", "complete"),
        "tokens_in": msg.get("tokens_in", 0),
        "tokens_out": msg.get("tokens_out", 0),
    }
    iso = _iso(msg.get("created_at"))
    if iso:
        row["created_at"] = iso
    _req("POST", "/rest/v1/chat_messages", [row])


def push_file_request(user_id, conv_id: str, query: str, target: str = "desktop"):
    """写一条"文件投送"请求到 Supabase file_requests（消费方按 target 决定）。
    target='desktop'（默认）→ 桌面客户端常驻轮询消费；target='phone' → 手机 App 消费。
    同步阻塞写（要尽快让消费方轮询到），失败静默。"""
    uid = _uid(user_id)
    if not uid or not conv_id:
        return
    import uuid as _uuid
    row = {
        "id": str(_uuid.uuid4()),
        "user_id": uid,
        "conv_id": conv_id,
        "query": (query or "")[:500],
        "status": "pending",
        "target": target,
    }
    try:
        _req("POST", "/rest/v1/file_requests", [row])
    except Exception:
        pass


def list_phone_requests(user_id) -> list:
    """列出该用户待手机处理的照片请求（status=pending, target=phone, 取最近的）。供 App 轮询。"""
    uid = _uid(user_id)
    if not uid:
        return []
    try:
        q = (f"/rest/v1/file_requests?user_id=eq.{uid}&status=eq.pending"
             f"&target=eq.phone&order=created_at.asc&limit=5")
        rows = _get_json(q)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def set_request_status(req_id: str, status: str) -> None:
    """回写某条 file_request 的状态（processing/done/denied/error）。供 App/桌面调用。"""
    if not req_id:
        return
    try:
        _req("PATCH", f"/rest/v1/file_requests?id=eq.{req_id}", {"status": status})
    except Exception:
        pass


# ─────────────────── Backfill：把已有对话/消息补推到 Supabase ───────────────────
# 由 GET /conversations、GET messages 端点在请求线程读完 DB 后调用（线程安全），
# HTTP 在后台线程批量 upsert。让 App 能看到「设 service key 之前就存在」的历史数据。
def _conv_row(cv: dict):
    uid = _uid(cv.get("user_id"))
    if not uid or not cv.get("id"):
        return None
    return {
        "id": cv.get("id"),
        "user_id": uid,
        "title": cv.get("title", "新对话"),
        "pinned": bool(cv.get("pinned", 0)),
        "metadata": _jsonify(cv.get("metadata")) or {},
    }


def _msg_row(msg: dict, uid: str):
    if not msg.get("id"):
        return None
    row = {
        "id": msg.get("id"), "conv_id": msg.get("conv_id"), "user_id": uid,
        "role": msg.get("role", "user"), "content": msg.get("content", ""),
        "thinking": msg.get("thinking", ""),
        "tool_calls": _jsonify(msg.get("tool_calls")) or [],
        "files": _jsonify(msg.get("files")) or [],
        "sources": _jsonify(msg.get("sources")) or [],
        "suggestions": _jsonify(msg.get("suggestions")) or [],
        "status": msg.get("status", "complete"),
        "tokens_in": msg.get("tokens_in", 0), "tokens_out": msg.get("tokens_out", 0),
    }
    iso = _iso(msg.get("created_at"))
    if iso:
        row["created_at"] = iso
    return row


def backfill_conversations(convs):
    """把一批已有对话 upsert 到 Supabase（仅 sb_ 用户）。"""
    _bg(_do_backfill_conversations, list(convs or []))


def _do_backfill_conversations(convs):
    rows = []
    for cv in convs:
        r = _conv_row(cv)
        if r:
            rows.append(r)
    if rows:
        _req("POST", "/rest/v1/chat_conversations", rows)


def backfill_messages(msgs, user_id):
    """把某会话的一批已有消息 upsert 到 Supabase。"""
    _bg(_do_backfill_messages, list(msgs or []), user_id)


def _do_backfill_messages(msgs, user_id):
    uid = _uid(user_id)
    if not uid:
        return
    rows = []
    for m in msgs:
        r = _msg_row(m, uid)
        if r:
            rows.append(r)
    if rows:
        _req("POST", "/rest/v1/chat_messages", rows)


# ─────────────────────────── 记忆 ───────────────────────────
def push_memory(mem: dict):
    _bg(_do_push_memory, mem)


def _do_push_memory(mem: dict):
    uid = _uid(mem.get("user_id"))
    if not uid or not mem.get("id"):
        return
    row = {
        "id": mem.get("id"),
        "user_id": uid,
        "category": mem.get("category", ""),
        "key": mem.get("key", ""),
        "value": mem.get("value", ""),
        "confidence": mem.get("confidence", 0.8),
    }
    _req("POST", "/rest/v1/user_memory", [row])


# ─────────────────────────── 设置 ───────────────────────────
def push_settings(user_id, profile):
    _bg(_do_push_settings, user_id, profile)


def _do_push_settings(user_id, profile):
    uid = _uid(user_id)
    if not uid:
        return
    row = {"user_id": uid, "profile": _jsonify(profile) or {}}
    _req("POST", "/rest/v1/user_settings", [row])


# ─────────────────────── 零配置：后端公网地址 ───────────────────────
def push_backend_url(url: str):
    """把后端公网地址写入 Supabase app_config（key=backend_url），供 App 零配置读取。
    App 登录后从 app_config 读到该地址，自动作为客户端地址连接，无需手动填写。"""
    if not url:
        return
    _bg(_do_push_backend_url, str(url).strip().rstrip("/"))


def _do_push_backend_url(url: str):
    _req("POST", "/rest/v1/app_config", [{"key": "backend_url", "value": url}])


# ─────────────────────── 实时任务活动（Realtime）───────────────────────
def push_activity(item: dict):
    """写入/更新一条进行中的任务（对话生成中 / RAG 解析中），供 App 实时显示。"""
    _bg(_do_push_activity, item)


def _do_push_activity(item: dict):
    uid = _uid(item.get("user_id"))
    if not uid or not item.get("id"):
        return
    row = {
        "id": item.get("id"),
        "user_id": uid,
        "kind": item.get("kind", "chat"),
        "title": item.get("title", ""),
        "status": item.get("status", "active"),
        "done": item.get("done", 0),
        "total": item.get("total", 0),
    }
    _req("POST", "/rest/v1/client_activity", [row])


def remove_activity(activity_id, user_id):
    """任务结束 → 删除该活动行（App 实时移除）。"""
    _bg(_do_remove_activity, activity_id, user_id)


def _do_remove_activity(activity_id, user_id):
    if not _uid(user_id) or not activity_id:
        return
    _req("DELETE", f"/rest/v1/client_activity?id=eq.{activity_id}", None)
