"""画布编辑互斥锁（V230 协作纪元）：解决"两处同时编辑互相覆盖"的真问题。

先到先得：进入编辑 acquire；编辑中每 10s beat；退出/关闭 release。
TTL 20s——持锁端崩溃/断网，20s 后任何人可接管。存 data/canvas_locks.json。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from hashmm.api.auth import require_conv_access
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.canvas_lock")
router = APIRouter(tags=["canvas"])

_LOCK = threading.Lock()
_TTL = 20


def _store() -> Path:
    p = Path(os.environ.get("HASHMM_DATA_DIR", "data")) / "canvas_locks.json"
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
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def require_canvas_lock(conv_id: str, filename: str, session: str) -> None:
    """Fail closed unless ``session`` currently owns the live canvas lock.

    File ownership is checked by the calling route.  This helper deliberately
    exposes only a boolean authority decision: callers cannot use it to inspect
    another editor's session identifier or lock metadata.
    """
    fname = os.path.basename(
        urllib.parse.unquote(str(filename or "")).split("?")[0].split("#")[0]
    )
    session = str(session or "").strip()[:32]
    if not conv_id or not fname or not session:
        raise HTTPException(409, "画布编辑租约不存在，请重新进入编辑模式")
    key = f"{conv_id}::{fname}"
    now = int(time.time())
    with _LOCK:
        cur = _load().get(key)
        alive = bool(cur) and (now - int(cur.get("ts", 0))) < _TTL
        if not alive or cur.get("session") != session:
            raise HTTPException(409, "画布编辑租约已失效，请刷新后再修改")


@router.post("/api/canvas/lock")
async def canvas_lock(request: Request):
    body = await request.json()
    conv_id = str((body or {}).get("conv_id") or "").strip()
    fname = os.path.basename(
        urllib.parse.unquote(str((body or {}).get("filename") or ""))
        .split("?")[0].split("#")[0]
    )
    session = str((body or {}).get("session") or "").strip()[:32]
    action = str((body or {}).get("action") or "acquire").strip()
    if not conv_id or not fname or not session:
        raise HTTPException(400, "conv_id / filename / session 必填")
    # Locks are write authority, not cosmetic presence.  Foreign and missing
    # conversations deliberately share the same non-enumerable 404.
    require_conv_access(request, conv_id)
    key = f"{conv_id}::{fname}"
    now = int(time.time())
    with _LOCK:
        d = _load()
        cur = d.get(key)
        alive = bool(cur) and (now - int(cur.get("ts", 0))) < _TTL
        if action == "release":
            if cur and cur.get("session") == session:
                d.pop(key, None)
                _save(d)
            return {"ok": True}
        if action == "beat":
            if cur and cur.get("session") == session:
                cur["ts"] = now
                _save(d)
                return {"ok": True, "held": True}
            return {"ok": True, "held": False}   # 锁已易主/过期被清
        # acquire
        if alive and cur.get("session") != session:
            return {"ok": True, "held": False, "age": now - int(cur.get("ts", 0))}
        d[key] = {"session": session, "ts": now}
        _save(d)
        return {"ok": True, "held": True}
