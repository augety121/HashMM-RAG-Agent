"""站内通知（V230 协作纪元）：@提及、我发布的画布有新评论。

存储 data/notifications/{key}.json（key=uid 或 username，写读同法双 key 兼容），
cap 100/人，原子写。供 canvas_share 等模块 import push_notification 写入。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.notify")
router = APIRouter(prefix="/api/notifications", tags=["notify"])

_LOCK = threading.Lock()
_CAP = 100


def _path(key: str) -> Path:
    safe = "".join(c for c in (key or "") if c.isalnum() or c in "-_.")[:64] or "anon"
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "notifications" / f"{safe}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(key: str) -> list:
    try:
        p = _path(key)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
    except Exception as e:
        log_suppressed(logger, e)
    return []


def _save(key: str, items: list) -> None:
    p = _path(key)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-_CAP:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def push_notification(key: str, ntype: str, text: str, by: str = "", share_id: str = "") -> None:
    """写一条通知（key=uid 或 username 均可；失败静默，绝不拦主流程）。"""
    try:
        with _LOCK:
            items = _load(key)
            items.append({"id": secrets.token_hex(3), "type": ntype, "text": text[:200],
                          "by": by[:20], "share_id": share_id, "ts": int(time.time()), "read": False})
            _save(key, items)
    except Exception as e:
        log_suppressed(logger, e)


@router.get("")
async def list_notifications(request: Request):
    user = require_auth(request)
    uid = user.get("uid", "")
    uname = (user.get("sub") or "").split("@")[0]
    merged = _load(uid) + ([] if uname == uid else _load(uname))
    merged.sort(key=lambda x: x.get("ts", 0))
    items = merged[-30:]
    return {"items": list(reversed(items)),
            "unread": sum(1 for x in merged if not x.get("read"))}


@router.post("/read")
async def mark_all_read(request: Request):
    user = require_auth(request)
    with _LOCK:
        for key in {user.get("uid", ""), (user.get("sub") or "").split("@")[0]}:
            if not key:
                continue
            items = _load(key)
            for x in items:
                x["read"] = True
            _save(key, items)
    return {"ok": True}
