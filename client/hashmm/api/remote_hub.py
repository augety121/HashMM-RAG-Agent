"""hashmm/api/remote_hub.py — 账号级远程信令中继（纯逻辑，不依赖 FastAPI，便于单测）。

同一账号（uid）的设备连进同一个「房间」。被控端注册为 host，控制端为 viewer。
本中继只在同账号的 viewer ⇄ host 之间转发 WebRTC 信令(offer/answer/ICE)与输入事件——
真正的屏幕视频走 viewer↔host 的 WebRTC P2P 直连，不经过本服务器（省带宽、低延迟）。
跨网络之所以无需用户自己的服务器：用你**已有的公网账号后端**当这个「碰头点」即可。

设计要点：on_message / add / remove 都**返回待发送动作列表** [(conn, message), ...]，
不直接做 IO，因此与 asyncio/FastAPI 解耦、可用假连接对象单测（见 tests/test_remote_hub.py）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Tuple, Optional


DEFAULT_ICE = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
]


class Conn:
    """一个已认证连接。adapter 会把真实 WebSocket 挂到 .ws 上；hub 只认 .send 抽象。"""

    def __init__(self, uid: str, role: str, name: str = "", platform: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.uid = str(uid)
        self.role = "host" if role == "host" else "viewer"
        self.name = name or ("被控端" if self.role == "host" else "查看端")
        self.platform = platform or ""
        self.peer: Optional[str] = None      # viewer：所连 host 的 conn id
        self.viewers: set = set()            # host：已连入的 viewer conn id 集合
        self.ts = time.time()
        self.ws: Any = None                  # adapter 填充（真实 WebSocket）


Action = Tuple[Conn, Dict[str, Any]]


class SignalHub:
    def __init__(self, ice_servers: Optional[List[dict]] = None):
        self.accounts: Dict[str, Dict[str, Conn]] = {}    # uid -> {conn_id -> Conn}
        self.ice_servers = ice_servers or list(DEFAULT_ICE)

    # ── 注册 / 注销 ──────────────────────────────────────────
    def _room(self, uid: str) -> Dict[str, Conn]:
        return self.accounts.get(str(uid), {})

    def add(self, conn: Conn) -> List[Action]:
        self.accounts.setdefault(conn.uid, {})[conn.id] = conn
        return [(conn, {"type": "authOk", "iceServers": self.ice_servers, "id": conn.id})]

    def remove(self, conn: Conn) -> List[Action]:
        out: List[Action] = []
        room = self._room(conn.uid)
        room.pop(conn.id, None)
        if conn.role == "viewer" and conn.peer:
            host = room.get(conn.peer)
            if host:
                host.viewers.discard(conn.id)
                out.append((host, {"type": "viewerLeft", "vid": conn.id}))
        elif conn.role == "host":
            for vid in list(conn.viewers):
                v = room.get(vid)
                if v:
                    v.peer = None
                    out.append((v, {"type": "pairFail", "reason": "offline"}))
        if not room:
            self.accounts.pop(conn.uid, None)
        return out

    def hosts(self, uid: str) -> List[Conn]:
        return [c for c in self._room(uid).values() if c.role == "host"]

    # ── 消息处理（同账号内 viewer ⇄ host 中继）──────────────
    def on_message(self, conn: Conn, msg: Dict[str, Any]) -> List[Action]:
        t = msg.get("type")
        if t == "listDevices":
            lst = [{"id": h.id, "name": h.name, "platform": h.platform}
                   for h in self.hosts(conn.uid) if h.id != conn.id]
            return [(conn, {"type": "devices", "list": lst})]

        if t == "connect":                                    # viewer 选择某台 host
            host = self._room(conn.uid).get(msg.get("target"))
            if not host or host.role != "host":
                return [(conn, {"type": "pairFail", "reason": "offline"})]
            conn.peer = host.id
            host.viewers.add(conn.id)
            return [
                (host, {"type": "viewerJoined", "vid": conn.id}),
                (conn, {"type": "ready"}),
            ]

        if t == "rtcSignal":
            if conn.role == "host":                           # host → 指定 viewer
                v = self._room(conn.uid).get(msg.get("vid"))
                if v and v.peer == conn.id:
                    return [(v, {"type": "rtcSignal", "kind": msg.get("kind"), "data": msg.get("data")})]
                return []
            # viewer → 它所连的 host
            host = self._room(conn.uid).get(conn.peer) if conn.peer else None
            if host:
                return [(host, {"type": "rtcSignal", "vid": conn.id, "kind": msg.get("kind"), "data": msg.get("data")})]
            return []

        if t in ("input", "rtcOn", "rtcOff"):                 # viewer → host（host 渲染进程转给主进程注入）
            if conn.role == "viewer" and conn.peer:
                host = self._room(conn.uid).get(conn.peer)
                if host:
                    fwd = dict(msg)
                    fwd["vid"] = conn.id
                    return [(host, fwd)]
            return []

        # V103.51: 对标 UU 远程的扩展能力。这些都是**小控制消息**，在已配对的
        # viewer ⇄ host 之间转发；真正的文件字节走 P2P WebRTC DataChannel（不经本服务器，
        # 故文件大小无限制、省带宽）。
        #   fileOffer/fileAccept/fileReject/fileProgress/fileDone — 文件传输握手与进度
        #   selectMonitor/monitorList                            — 多屏选择（多屏协作/被控端选屏）
        #   setQuality                                           — 画质（fps / jpeg 质量 / 真彩）
        #   setPrivacy                                           — 隐私防护（被控端黑屏 / 锁输入）
        # 双向转发：viewer→host 与 host→viewer 都允许（如 host 回 monitorList / fileProgress）。
        _VIEWER_TO_HOST = {"fileOffer", "fileAccept", "fileReject", "fileProgress",
                           "fileDone", "fileCancel", "selectMonitor", "setQuality", "setPrivacy",
                           "control"}
        _HOST_TO_VIEWER = {"fileOffer", "fileAccept", "fileReject", "fileProgress",
                           "fileDone", "fileCancel", "monitorList", "privacyState"}
        if t in _VIEWER_TO_HOST or t in _HOST_TO_VIEWER:
            if conn.role == "viewer" and conn.peer and t in _VIEWER_TO_HOST:
                host = self._room(conn.uid).get(conn.peer)
                if host:
                    fwd = dict(msg)
                    fwd["vid"] = conn.id
                    return [(host, fwd)]
                return []
            if conn.role == "host" and t in _HOST_TO_VIEWER:
                # host 指定某个 viewer（msg["vid"]）；不指定则广播给它所有 viewer。
                vid = msg.get("vid")
                if vid:
                    v = self._room(conn.uid).get(vid)
                    if v and v.peer == conn.id:
                        return [(v, {k: val for k, val in msg.items() if k != "vid"} | {"type": t})]
                    return []
                out: List[Action] = []
                for vid2 in list(conn.viewers):
                    v = self._room(conn.uid).get(vid2)
                    if v:
                        out.append((v, {k: val for k, val in msg.items() if k != "vid"} | {"type": t}))
                return out
            return []

        return []
