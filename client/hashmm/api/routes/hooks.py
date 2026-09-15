"""事件触发器（V230 主动纪元）：外部系统 POST 一个 URL → 自动入派活队列。

创建：POST /api/hooks {name, runner, kind, payload} → 返回触发 URL（含 secret）。
触发：POST /api/hooks/t/{hook_id}?secret=...（无需登录；body 可覆盖 payload 字段）。
存 data/hooks.json；触发计数与最近触发时间留痕。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_auth
from hashmm.api.routes.dispatch import dq
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.hooks")
router = APIRouter(tags=["hooks"])

_LOCK = threading.Lock()


def _store() -> Path:
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "hooks.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        p = _store()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception as e:
        log_suppressed(logger, e)
    return {}


def _save(d: dict) -> None:
    p = _store()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


@router.get("/api/hooks")
async def list_hooks(request: Request):
    user = require_auth(request)
    d = _load()
    return {"items": [{"id": k, "name": v.get("name"), "kind": v.get("kind"),
                       "runner": v.get("runner"), "hits": v.get("hits", 0),
                       "last_hit": v.get("last_hit", 0),
                       "recent": (v.get("recent") or [])[-20:],
                       "enabled": bool(v.get("enabled", True)),
                       "url": f"/api/hooks/t/{k}?secret={v.get('secret')}"}
                      for k, v in d.items() if v.get("owner") == user.get("uid")]}


@router.post("/api/hooks")
async def create_hook(request: Request):
    user = require_auth(request)
    body = await request.json()
    name = str((body or {}).get("name") or "外部触发")[:40]
    runner = str((body or {}).get("runner") or "desktop")[:20]
    kind = str((body or {}).get("kind") or "browser_use")[:20]
    payload = (body or {}).get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload 必须是对象")
    hid = secrets.token_hex(5)
    sec = secrets.token_hex(8)
    with _LOCK:
        d = _load()
        d[hid] = {"name": name, "runner": runner, "kind": kind, "payload": payload,
                  "secret": sec, "owner": user.get("uid", ""), "created": int(time.time()),
                  "hits": 0, "last_hit": 0, "enabled": True}
        _save(d)
    return {"ok": True, "id": hid, "url": f"/api/hooks/t/{hid}?secret={sec}"}


@router.post("/api/hooks/{hook_id}/toggle")
async def toggle_hook(hook_id: str, request: Request):
    """V235: 暂停/启用触发器——外部乱轰或维护窗口时一键静音，URL 不作废。"""
    user = require_auth(request)
    with _LOCK:
        d = _load()
        v = d.get(hook_id)
        if not v or v.get("owner") != user.get("uid"):
            raise HTTPException(404, "触发器不存在")
        v["enabled"] = not bool(v.get("enabled", True))
        _save(d)
    return {"ok": True, "enabled": v["enabled"]}


@router.delete("/api/hooks/{hook_id}")
async def delete_hook(hook_id: str, request: Request):
    user = require_auth(request)
    with _LOCK:
        d = _load()
        v = d.get(hook_id)
        if v and v.get("owner") == user.get("uid"):
            d.pop(hook_id, None)
            _save(d)
    return {"ok": True}


@router.post("/api/hooks/t/{hook_id}")
async def trigger_hook(hook_id: str, request: Request, secret: str = ""):
    """外部触发：无需登录，凭 secret；body（JSON，可选）浅覆盖预设 payload。"""
    with _LOCK:
        d = _load()
        v = d.get(hook_id)
        if not v or secret != v.get("secret"):
            raise HTTPException(403, "无效触发地址")
        if not v.get("enabled", True):   # V235 暂停闸：URL 仍有效，随时可在桌面重新启用
            raise HTTPException(403, "触发器已暂停（在桌面「高级能力·触发器」可重新启用）")
        v["hits"] = int(v.get("hits", 0)) + 1
        v["last_hit"] = int(time.time())
        # V234 命中明细：只记时间与覆盖字段名（不存值——哨兵送来的文件路径等可能敏感）
        try:
            _keys = []
            try:
                _peek = await request.json()
                if isinstance(_peek, dict):
                    _keys = [str(k)[:24] for k in list(_peek)[:8]]
                request._json_cache = _peek   # 供下方复用，避免二次读 body
            except Exception:
                request._json_cache = None
            v.setdefault("recent", []).append({"ts": int(time.time()), "keys": _keys})
            v["recent"] = v["recent"][-20:]
        except Exception:
            pass
        _save(d)
    payload = dict(v.get("payload") or {})
    try:
        extra = getattr(request, "_json_cache", None)
        if isinstance(extra, dict):
            payload.update({k: extra[k] for k in list(extra)[:10]})
    except Exception:
        pass
    tid = dq.create_task(v.get("runner", "desktop"), v.get("kind", "browser_use"),
                         payload, created_by=f"hook:{hook_id}")
    return {"ok": True, "task_id": tid}
