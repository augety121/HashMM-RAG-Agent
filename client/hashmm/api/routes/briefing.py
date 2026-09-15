"""今日简报端点（V230 主动纪元·简版）：派活队列今日口径的 JSON 聚合。
App 端已本地聚合完整简报；本端点供未来推送/定时任务复用同一数字口径。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from hashmm.api.auth import require_auth
from hashmm.api.routes.dispatch import dq

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


@router.get("/today")
async def today(request: Request):
    require_auth(request)
    day_start = int(time.time()) - (int(time.time()) % 86400)
    items = []
    try:
        items = dq.list_tasks(200) or []
    except Exception:
        pass
    todays = [t for t in items if int(t.get("created", 0)) >= day_start]
    return {"ts": int(time.time()),
            "dispatch_today": len(todays),
            "done_today": sum(1 for t in todays if t.get("status") == "done"),
            "failed_today": sum(1 for t in todays if t.get("status") == "failed")}
