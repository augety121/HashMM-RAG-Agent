"""hashmm/collab/rpc_transport.py —— 交互 Agent 的真实跨机 RPC 传输层 · V320。

让**不同机器**上的两个 HashMM 实例真正互联：alice 机器上的 agent 通过交互 Agent
向 bob 机器上的 agent 发起协作，请求走网络投递到对方，对方也经交互 Agent 入站处理。

━━━━━━━━━━━━━━ 职责边界（关键设计原则）━━━━━━━━━━━━━━
本模块**只做两件事**：① 线路（HTTP 投递）② 机器间身份认证（签名/防重放）。
所有【语义安全】——速率限制、信任门禁、作用域授权、注入检测、出站脱敏、审计——
仍然在交互 Agent（interaction_agent.py）里，一行不动。RPC 只是把交互 Agent 的
`transport` 注入点从"同进程直调"换成"跨网络调用"。这正是交互 Agent 独立成 agent
的意义：换传输不影响任何安全语义。

━━━━━━━━━━━━━━━━━━ 威胁模型与防御 ━━━━━━━━━━━━━━━━━━
对手能力假设：能看到/篡改/重放网络流量，能冒充任意 from_user 字段，能高频轰炸。

  威胁                      防御
  ─────────────────────    ────────────────────────────────────────────
  伪造请求（不是配对方发的） HMAC-SHA256 签名：每个请求用【每对等体独立的共享密钥】
                            签名。收方用该密钥验签 → 证明来自配对的对等体。
  篡改请求体                 签名覆盖规范化请求体 → 任何改动都会验签失败。
  重放旧请求                 ① 时间戳窗口（默认 ±300s，超窗拒绝）
                            ② nonce 缓存（窗口内重复 nonce 拒绝）
  冒充他人身份               from_user 被【限定到已认证的对等体】：来自对等体 P 的请求，
   （对等体 Q 冒充 P 的用户） from_user 一律改写为 `user@P`。Q 发来的只会变成 `user@Q`，
                            与经 P 建立的信任边对不上 → 自动隔离，无法冒充。
  时序侧信道（比对密钥）     hmac.compare_digest 常数时间比较。
  中间人偷看内容             HMAC 只保证真实性+完整性，不加密；机密性靠端点 HTTPS。
                            且请求体已过交互 Agent 出站脱敏，本就不含敏感信息。

配对（一次性）：两台机器交换 (peer_id, endpoint, shared_secret)——像"添加可信远端"。
之后所有协作自动走签名 RPC。密钥只在配对时手工交换一次，进程内不明文回传。

存储：复用 collab.sqlite3（peers 表）。HTTP 用 stdlib urllib（零新依赖）；
实际投递函数可注入 → 协议逻辑（签名/验签/防重放/路由）可在无网络环境完整测试。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

__all__ = [
    "self_peer_id", "PeerRegistry", "get_peer_registry",
    "NonceCache", "RpcClient", "RpcInbound",
    "canonical_json", "sign_payload", "verify_signature",
    "split_remote_user", "is_remote_user",
]

# 签名/防重放参数（可用环境变量调）。
_TS_SKEW_SEC = int(os.environ.get("HASHMM_RPC_TS_SKEW", "300"))     # 时间戳允许偏差
_NONCE_TTL_SEC = int(os.environ.get("HASHMM_RPC_NONCE_TTL", "600")) # nonce 记忆时长
_HTTP_TIMEOUT = int(os.environ.get("HASHMM_RPC_TIMEOUT", "20"))
_SIG_HEADER = "X-HashMM-Sig"
_TS_HEADER = "X-HashMM-Ts"
_NONCE_HEADER = "X-HashMM-Nonce"
_PEER_HEADER = "X-HashMM-Peer"


def _db_path() -> Path:
    try:
        from hashmm.api.database import DATA_ROOT
        base = Path(DATA_ROOT)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HASHMM_DATA_ROOT", "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "collab.sqlite3"


# ════════════════════════════════════════════════════════════════════════
# 本机对等体标识（跨机寻址用）
# ════════════════════════════════════════════════════════════════════════
def self_peer_id() -> str:
    """本 HashMM 实例的稳定标识。优先环境变量，否则从 collab.sqlite3 取/生成一个持久随机值。

    这是本机对外宣告的"我是谁"。配对时告诉对方这个 id，对方用它索引验签密钥。
    """
    env = (os.environ.get("HASHMM_PEER_ID") or "").strip()
    if env:
        return env
    try:
        p = _db_path()
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS peer_self (k TEXT PRIMARY KEY, v TEXT)")
            row = conn.execute("SELECT v FROM peer_self WHERE k='self_id'").fetchone()
            if row and row[0]:
                return str(row[0])
            new_id = "hm_" + secrets.token_hex(6)
            conn.execute("INSERT OR REPLACE INTO peer_self(k, v) VALUES('self_id', ?)", (new_id,))
            conn.commit()
            return new_id
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return "hm_local"


# ════════════════════════════════════════════════════════════════════════
# 远端用户寻址：user@peer 形式
# ════════════════════════════════════════════════════════════════════════
def is_remote_user(user: str) -> bool:
    """带 @ 的用户 id 视为远端（user@peer_id）。本地用户 id 不含 @。"""
    return "@" in str(user or "")


def split_remote_user(user: str) -> tuple[str, str]:
    """拆 `alice@peerB` → ('alice', 'peerB')。非远端返回 (user, '')。"""
    s = str(user or "")
    if "@" not in s:
        return s, ""
    local, _, peer = s.rpartition("@")
    return local, peer


# ════════════════════════════════════════════════════════════════════════
# 签名与验签（HMAC-SHA256）
# ════════════════════════════════════════════════════════════════════════
def canonical_json(payload: dict) -> str:
    """规范化 JSON：键排序 + 紧凑分隔 + 保留非 ASCII。收发两端必须一致，否则签名对不上。"""
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_payload(secret: str, body: str, ts: str, nonce: str) -> str:
    """对 (body + ts + nonce) 用共享密钥做 HMAC-SHA256，返回十六进制签名。"""
    msg = f"{ts}\n{nonce}\n{body}".encode("utf-8")
    return hmac.new(str(secret).encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: str, ts: str, nonce: str, sig: str) -> bool:
    """常数时间验签。仅校验签名本身；时间戳窗口/nonce 重放由 NonceCache 单独把关。"""
    expected = sign_payload(secret, body, ts, nonce)
    return hmac.compare_digest(expected, str(sig or ""))


# ════════════════════════════════════════════════════════════════════════
# 防重放：时间戳窗口 + nonce 缓存
# ════════════════════════════════════════════════════════════════════════
class NonceCache:
    """进程内 nonce 记忆（TTL）。窗口内同一 nonce 只接受一次 → 挡重放。"""

    def __init__(self, ttl: int = _NONCE_TTL_SEC):
        self.ttl = ttl
        self._seen: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        dead = [n for n, exp in self._seen.items() if exp < now]
        for n in dead:
            self._seen.pop(n, None)

    def check_and_remember(self, nonce: str, ts: str,
                           skew: int = _TS_SKEW_SEC) -> tuple[bool, str]:
        """返回 (是否新鲜且未见过, 原因)。时间戳超窗或 nonce 重复都拒绝。"""
        now = time.time()
        try:
            t = float(ts)
        except Exception:  # noqa: BLE001
            return False, "时间戳非法"
        if abs(now - t) > skew:
            return False, f"时间戳超窗（偏差 {int(abs(now - t))}s > {skew}s）——防重放"
        n = str(nonce or "")
        if not n:
            return False, "缺 nonce"
        self._prune(now)
        if n in self._seen:
            return False, "nonce 重复——疑似重放，已拒绝"
        self._seen[n] = now + self.ttl
        return True, ""


# ════════════════════════════════════════════════════════════════════════
# 对等体注册表
# ════════════════════════════════════════════════════════════════════════
class PeerRegistry:
    """已配对的远端 HashMM 实例。peer_id → {endpoint, secret, label}。"""

    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else _db_path()
        self._init_db()

    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS peers (
                peer_id TEXT PRIMARY KEY, endpoint TEXT NOT NULL,
                secret TEXT NOT NULL, label TEXT DEFAULT '',
                created REAL, updated REAL)""")
            conn.commit()
        finally:
            conn.close()

    def pair(self, peer_id: str, endpoint: str, secret: str, label: str = "") -> dict:
        """配对一个远端实例（写入/更新）。secret 是两机手工交换的共享密钥。"""
        peer_id = str(peer_id or "").strip()
        endpoint = str(endpoint or "").strip().rstrip("/")
        secret = str(secret or "").strip()
        if not peer_id or not endpoint or not secret:
            return {"ok": False, "detail": "peer_id / endpoint / secret 都不能为空"}
        if len(secret) < 16:
            return {"ok": False, "detail": "共享密钥太短（至少 16 字符，建议用 gen_secret 生成）"}
        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            return {"ok": False, "detail": "endpoint 必须是 http(s):// 开头"}
        now = time.time()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO peers(peer_id, endpoint, secret, label, created, updated) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(peer_id) DO UPDATE SET "
                "endpoint=excluded.endpoint, secret=excluded.secret, "
                "label=excluded.label, updated=excluded.updated",
                (peer_id, endpoint, secret, label, now, now))
            conn.commit()
        finally:
            conn.close()
        secure = endpoint.startswith("https://")
        return {"ok": True, "peer_id": peer_id, "endpoint": endpoint,
                "secure": secure,
                "detail": ("已配对" + ("" if secure else "（⚠️ 非 HTTPS：内容机密性无保障，"
                                        "仅建议在可信内网/VPN 使用）"))}

    def get(self, peer_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM peers WHERE peer_id=?", (str(peer_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def remove(self, peer_id: str) -> dict:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM peers WHERE peer_id=?", (str(peer_id),))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "detail": f"已移除对等体 {peer_id}"}

    def list_peers(self) -> list[dict]:
        """列出对等体（**不含密钥**——密钥永不出注册表边界）。"""
        conn = self._conn()
        try:
            rows = conn.execute("SELECT peer_id, endpoint, label, created, updated FROM peers "
                                "ORDER BY updated DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def gen_secret() -> str:
        """生成一个强共享密钥（配对时两机各存一份）。"""
        return secrets.token_urlsafe(32)


_REGISTRY: PeerRegistry | None = None


def get_peer_registry(db_path: Path | None = None) -> PeerRegistry:
    global _REGISTRY
    if db_path is not None:
        return PeerRegistry(db_path)
    if _REGISTRY is None:
        _REGISTRY = PeerRegistry()
    return _REGISTRY


# ════════════════════════════════════════════════════════════════════════
# 出站客户端：把交互 Agent 的 transport 挂到网络上
# ════════════════════════════════════════════════════════════════════════
def _default_http_post(url: str, headers: dict, body: str) -> dict:
    """真机上的实际 HTTP 投递（stdlib，零依赖）。沙箱无网络时由注入桩替换。"""
    req = urllib.request.Request(url, data=body.encode("utf-8"),
                                 headers={**headers, "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:  # noqa: S310 scheme 已校验
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"ok": False, "rejected_reason": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "rejected_reason": f"投递异常:{type(e).__name__}"}


class RpcClient:
    """出站 RPC：为交互 Agent 的 send_outbound 提供 transport 回调。"""

    def __init__(self, registry: PeerRegistry | None = None,
                 http_post: Callable[[str, dict, str], dict] | None = None,
                 my_peer_id: str | None = None):
        self.registry = registry or get_peer_registry()
        self._http_post = http_post or _default_http_post
        self.my_peer_id = my_peer_id or self_peer_id()

    def send(self, peer_id: str, request: dict) -> dict:
        """向指定对等体投递一个协作请求（签名 + POST）。"""
        peer = self.registry.get(peer_id)
        if not peer:
            return {"ok": False, "rejected_reason": f"未配对的对等体：{peer_id}（先在两机执行配对）"}
        body = canonical_json(request)
        ts = f"{time.time():.3f}"
        nonce = secrets.token_hex(16)
        sig = sign_payload(peer["secret"], body, ts, nonce)
        headers = {_SIG_HEADER: sig, _TS_HEADER: ts, _NONCE_HEADER: nonce,
                   _PEER_HEADER: self.my_peer_id}
        url = peer["endpoint"].rstrip("/") + "/api/collab/rpc/inbound"
        return self._http_post(url, headers, body)

    def make_transport(self) -> Callable[[dict], dict]:
        """返回可直接传给 InteractionAgent.send_outbound(transport=...) 的回调。

        它按 to_user 里的 @peer 解析目标对等体并投递。to_user 不含 @（本地）时返回错误
        （本地协作不该走 RPC）。
        """
        def _transport(req: dict) -> dict:
            to_user = str(req.get("to_user") or "")
            _local, peer_id = split_remote_user(to_user)
            if not peer_id:
                return {"ok": False, "rejected_reason": "本地用户不应走 RPC 传输"}
            # 线上只传对方本地的 user id（去掉 @peer 后缀），peer 由 header 表明
            wire = dict(req)
            wire["to_user"] = _local
            return self.send(peer_id, wire)
        return _transport


# ════════════════════════════════════════════════════════════════════════
# 入站处理：验签 + 防重放 + 身份限定 → 交互 Agent
# ════════════════════════════════════════════════════════════════════════
class RpcInbound:
    """入站 RPC：验证签名/防重放，把 from_user 限定到已认证对等体，再交给交互 Agent。"""

    def __init__(self, interaction_agent, registry: PeerRegistry | None = None,
                 nonce_cache: NonceCache | None = None):
        self.ia = interaction_agent
        self.registry = registry or get_peer_registry()
        self.nonce = nonce_cache or NonceCache()

    def receive(self, headers: dict, body: str, *,
                agent_executor: Callable[[str], str] | None = None,
                my_user_id: str = "") -> dict:
        """处理一个入站 RPC。headers 需含 Sig/Ts/Nonce/Peer；body 是规范化 JSON 字符串。

        my_user_id: 本机处理该请求的用户身份（作为 to_user；不信任线上字段）。
        """
        # header 大小写无关取值
        h = {str(k).lower(): v for k, v in (headers or {}).items()}
        peer_id = str(h.get(_PEER_HEADER.lower()) or "")
        sig = str(h.get(_SIG_HEADER.lower()) or "")
        ts = str(h.get(_TS_HEADER.lower()) or "")
        nonce = str(h.get(_NONCE_HEADER.lower()) or "")

        peer = self.registry.get(peer_id)
        if not peer:
            return {"ok": False, "rejected_reason": f"未知对等体 {peer_id}（未配对）", "authenticated": False}

        # ① 验签（常数时间）→ 证明来自配对对等体
        if not verify_signature(peer["secret"], body, ts, nonce, sig):
            return {"ok": False, "rejected_reason": "签名验证失败——非配对方或请求被篡改",
                    "authenticated": False}

        # ② 防重放（时间戳窗口 + nonce）
        fresh, why = self.nonce.check_and_remember(nonce, ts)
        if not fresh:
            return {"ok": False, "rejected_reason": why, "authenticated": True, "replay_blocked": True}

        # ③ 解析请求体
        try:
            req = json.loads(body) if body else {}
        except Exception:  # noqa: BLE001
            return {"ok": False, "rejected_reason": "请求体非法 JSON", "authenticated": True}

        # ④ 身份限定：from_user 一律改写为 user@认证对等体 —— 挡冒充。
        #    对等体只能代表"自己域内"的用户；它声称的 from_user 被绑到自己的 peer_id，
        #    对等体 Q 冒充经 P 建信的用户时，会变成 user@Q，与信任边 user@P 对不上。
        raw_from = str(req.get("from_user") or "")
        local_from, _existing_peer = split_remote_user(raw_from)
        req = dict(req)
        req["from_user"] = f"{local_from}@{peer_id}"

        # ⑤ 交给交互 Agent 入站（速率限制 + 五关卡，安全语义原样不动）
        result = self.ia.handle_inbound(req, agent_executor=agent_executor,
                                        my_user_id=my_user_id)
        result["authenticated"] = True
        result["peer_id"] = peer_id
        return result
