"""hashmm/api/routes/remote_relay.py — 远程「帧中继」HTTP 端点。

为什么需要：远程屏幕默认走 WebRTC P2P 直连，在对称 NAT（校园网/公司网）下、手机 5G ⇄
电脑校园网这类跨网络场景常常打不通，而用户又没有自建 TURN。本中继让画面改走 HTTP——
被控端把最新一帧 JPEG POST 到后端，查看端从后端 GET 最新帧或走 MJPEG 流。后端有公网 IP，
手机与电脑都连得到它，因此任何网络都能通（代价是画质/帧率比 WebRTC 低一档）。

信令与输入走统一 WebSocket（/api/remote/ws）；只有「视频」这一路改走本中继。

room 约定：已批准的远程 session id。鉴权使用会话签发的短时 Remote ticket，
不再把账号 access token 放进图片 URL。ticket 绑定 owner/session/role/device/scopes/generation。
内存保存最新帧，最多 _MAX_ROOMS 个房间，超过 _FRAME_TTL 秒无更新即视为离线并回收。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from hashmm.api.auth import require_auth
from hashmm.api.remote_sessions import remote_session_registry
from hashmm.api.remote_transport import request_is_secure, secure_remote_required

router = APIRouter(prefix="/api/remote/relay", tags=["remote-relay"])

_diag_logger = logging.getLogger("hashmm.remote.host")


def _allow_legacy_auth() -> bool:
    return os.environ.get("HASHMM_REMOTE_LEGACY_RELAY_AUTH", "").lower() in ("1", "true", "yes", "on")


def _remote_ticket(request: Request, room: str, role: str, scope: str) -> dict:
    if secure_remote_required() and not request_is_secure(request):
        raise HTTPException(status_code=403, detail="远程控制必须使用 HTTPS/WSS")
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Remote ") else request.query_params.get("ticket", "")
    payload = remote_session_registry.verify_ticket(token, required_scope=scope, role=role, session_id=room)
    if payload:
        return payload
    # Explicit migration switch only.  Even in compatibility mode, bind a
    # legacy room to the authenticated owner instead of accepting arbitrary IDs.
    if _allow_legacy_auth():
        user = require_auth(request)
        if str(room) == str(user.get("uid", "")):
            return {"uid": user["uid"], "sid": room, "role": role, "legacy": True}
    raise HTTPException(status_code=401, detail="远程授权已失效")


@router.post("/hostlog")
async def host_diag_log(request: Request):
    """被控端投屏窗（remote-host.html）把抓屏/推流诊断一行 POST 到这里，落进后端日志。
    用途：黑屏排障——用户分享后端日志即可看到被控端到底有没有抓到屏、轨道几条、是否在推流，
    无需打开隐藏投屏窗的 DevTools。纯诊断，无副作用。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    msg = str(body.get("msg") or "")[:300]
    room = str(body.get("room") or "")[:64]
    _remote_ticket(request, room, "host", "view")
    _diag_logger.warning(f"[被控端诊断] room={room} {msg}")
    return {"ok": True}


_FRAMES: dict[str, dict] = {}     # room -> {"data": bytes, "ts": float, "seq": int}
_LAST_PULL: dict[str, float] = {}  # room -> 查看端最近一次拉帧时间（用于"有没有人在看"判断）
_WATCH_WINDOW = 5.0               # 秒；这段时间内有人拉过帧就算"有人在看"
_MAX_ROOMS = 64
_FRAME_TTL = 30.0                 # 秒；超过则视为离线并回收
_MAX_FRAME_BYTES = 3_000_000      # 单帧上限 3MB，防滥用/内存爆


def _mark_pull(room: str) -> None:
    """记录查看端拉帧时刻（被控端据此决定是否推帧，没人看就别推，省得日志/上行刷屏）。"""
    _LAST_PULL[room] = time.time()


def _is_watching(room: str, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    return (now - _LAST_PULL.get(room, 0.0)) < _WATCH_WINDOW


def _gc(now: float | None = None) -> None:
    """回收过期房间，并在超过上限时丢弃最旧的，防止内存无界增长。"""
    now = now if now is not None else time.time()
    for k in [k for k, v in _FRAMES.items() if now - v["ts"] > _FRAME_TTL]:
        _FRAMES.pop(k, None)
    if len(_FRAMES) > _MAX_ROOMS:
        for k in sorted(_FRAMES, key=lambda kk: _FRAMES[kk]["ts"])[: len(_FRAMES) - _MAX_ROOMS]:
            _FRAMES.pop(k, None)


def _put_frame(room: str, data: bytes) -> int:
    """存入一帧，返回递增序号（供查看端判断是否有新帧）。纯逻辑，便于单测。"""
    _gc()
    prev = _FRAMES.get(room)
    seq = (prev["seq"] + 1) if prev else 1
    _FRAMES[room] = {"data": data, "ts": time.time(), "seq": seq}
    return seq


def _get_frame(room: str, now: float | None = None):
    """取一帧（已过期则视为不存在）。纯逻辑，便于单测。"""
    now = now if now is not None else time.time()
    rec = _FRAMES.get(room)
    if not rec or now - rec["ts"] > _FRAME_TTL:
        return None
    return rec


@router.post("/{room}/push")
async def push_frame(room: str, request: Request):
    """被控端推送最新一帧（请求体为 raw JPEG 字节）。"""
    _remote_ticket(request, room, "host", "view")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="空帧")
    if len(body) > _MAX_FRAME_BYTES:
        raise HTTPException(status_code=413, detail="单帧过大（上限 3MB）")
    seq = _put_frame(room, body)
    # 告诉被控端「现在有没有人在看」：没人看就让它把推帧降到很低的探测频率，避免日志/上行刷屏。
    return {"ok": True, "seq": seq, "bytes": len(body), "watching": _is_watching(room)}


@router.get("/{room}/meta")
async def frame_meta(room: str, request: Request):
    """查看端用：该房间是否有活跃画面、最新序号/时间。"""
    _remote_ticket(request, room, "viewer", "view")
    rec = _get_frame(room)
    if not rec:
        return {"online": False, "seq": 0}
    return {"online": True, "seq": rec["seq"], "age_ms": int((time.time() - rec["ts"]) * 1000)}


@router.get("/{room}/frame")
async def get_frame(room: str, request: Request):
    """查看端轮询用：返回该房间最新一帧 JPEG。"""
    _remote_ticket(request, room, "viewer", "view")
    _mark_pull(room)                 # 登记"有人在看"（即使暂时 404 也算，好让被控端开始推）
    rec = _get_frame(room)
    if not rec:
        raise HTTPException(status_code=404, detail="暂无画面")
    return Response(
        content=rec["data"],
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Frame-Seq": str(rec["seq"])},
    )


@router.get("/{room}/mjpeg")
async def mjpeg_stream(room: str, request: Request):
    """查看端流式用：multipart/x-mixed-replace，浏览器 <img src> 可直接显示。
    通过 ?ticket= 携带仅能读取该会话画面的短时能力票据。"""
    _remote_ticket(request, room, "viewer", "view")
    boundary = "hashmmframe"

    async def gen():
        last = -1
        idle = 0
        while True:
            _mark_pull(room)         # 只要这个 MJPEG 流还连着，就持续登记"有人在看"
            rec = _get_frame(room)
            if rec and rec["seq"] != last:
                last = rec["seq"]
                idle = 0
                head = (
                    b"--" + boundary.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(rec["data"])).encode() + b"\r\n\r\n"
                )
                yield head + rec["data"] + b"\r\n"
            else:
                idle += 1
                if idle > 600:   # ~60s 无新帧则结束流，让查看端重连
                    break
            await asyncio.sleep(0.1)

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=" + boundary,
        headers={"Cache-Control": "no-store"},
    )
