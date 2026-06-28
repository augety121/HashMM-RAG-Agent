"""hashmm/api/routes/remote_signal.py — 账号级远程信令 WebSocket 端点（薄适配层）。

端点：WS /api/remote/ws
首条消息必须是 {type:"auth", token, role:"host"|"viewer", name, platform}；token 用账号登录
令牌（与其它 API 同一套 JWT）。认证通过后加入该账号房间，由 SignalHub 在同账号 viewer⇄host
之间中继信令与输入。真实屏幕视频走 WebRTC P2P 直连，不经此服务器。

ICE 服务器可经环境变量 HASHMM_ICE_SERVERS（JSON 数组）配置，默认免费公共 STUN。
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from hashmm.api.auth import verify_any_token, get_current_user
from hashmm.api.remote_hub import SignalHub, Conn, DEFAULT_ICE

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _load_ice():
    raw = os.environ.get("HASHMM_ICE_SERVERS", "").strip()
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list) and v:
                return v
        except Exception:
            pass
    return list(DEFAULT_ICE)


hub = SignalHub(ice_servers=_load_ice())


@router.get("/status")
async def remote_status(request: Request):
    """当前账号在线的被控端（桌面客户端）状态，供 App 显示「客户端在线」并启用远程控制。

    同账号校验：只统计与当前登录用户同一 uid 的 host，跨账号不可见。
    """
    user = get_current_user(request)
    if not user or not user.get("uid"):
        return {"online": False, "count": 0, "hosts": []}
    hosts = hub.hosts(user["uid"])
    return {
        "online": len(hosts) > 0,
        "count": len(hosts),
        "hosts": [{"id": h.id, "name": h.name, "platform": h.platform} for h in hosts],
    }


async def _dispatch(actions):
    for c, msg in actions:
        try:
            if c.ws is not None:
                await c.ws.send_text(json.dumps(msg))
        except Exception:
            pass


@router.websocket("/ws")
async def remote_ws(ws: WebSocket):
    await ws.accept()
    conn = None
    try:
        first = await ws.receive_text()
        try:
            msg = json.loads(first)
        except Exception:
            await ws.send_text(json.dumps({"type": "authFail", "reason": "bad_auth"}))
            await ws.close()
            return
        if msg.get("type") != "auth":
            await ws.send_text(json.dumps({"type": "authFail", "reason": "need_auth"}))
            await ws.close()
            return
        data = verify_any_token(msg.get("token", ""))
        if not data or not data.get("uid"):
            await ws.send_text(json.dumps({"type": "authFail", "reason": "invalid_token"}))
            await ws.close()
            return

        conn = Conn(uid=data["uid"], role=msg.get("role", "viewer"),
                    name=msg.get("name", ""), platform=msg.get("platform", ""))
        conn.ws = ws
        await _dispatch(hub.add(conn))

        while True:
            txt = await ws.receive_text()
            try:
                m = json.loads(txt)
            except Exception:
                continue
            if not isinstance(m, dict):
                continue
            await _dispatch(hub.on_message(conn, m))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if conn is not None:
            await _dispatch(hub.remove(conn))
