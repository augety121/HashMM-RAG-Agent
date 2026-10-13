"""hashmm/channels/feishu.py — 飞书（Lark）应用机器人适配。

按飞书开放平台官方做法接入"应用机器人"（企业自建应用 + 事件订阅 webhook），
让员工在飞书单聊/群聊 @机器人 直接问 HashMM 知识库。

收：事件订阅 webhook → ① url_verification 返回 challenge ② 签名校验
   X-Lark-Signature = sha256(timestamp+nonce+encrypt_key+body) ③ 配了 Encrypt Key 则
   AES-256-CBC 解密 ④ 按 event_id 去重 ⑤ 取消息文本 → 交 RAG。
发：tenant_access_token（缓存）→ POST im/v1/messages。

纯逻辑（签名/解密/解析/去重）与网络（取 token / 发消息）分离，前者可单测。
零新依赖：AES 复用 cryptography（项目既有），签名用 stdlib，HTTP 用 stdlib urllib。
默认关：HASHMM_FEISHU_ENABLE=1 且配齐 APP_ID/APP_SECRET 才启用。永不抛错。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.request
from typing import Optional

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.channels.feishu")

_BASE = "https://open.feishu.cn/open-apis"


# ── 配置（DB → 环境变量 → 默认；可在客户端 UI 配置）──
def enabled() -> bool:
    from hashmm.channels import config
    return config.feishu_enabled()


def _cfg(name: str, default: str = "") -> str:
    from hashmm.channels import config
    return config.feishu(name) or default


# ── 安全校验（纯逻辑）──
def compute_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    """飞书加密事件签名：sha256(timestamp + nonce + encrypt_key + body)。
    body 必须是**原始请求体字节**（反序列化前）。返回十六进制摘要。"""
    h = hashlib.sha256()
    h.update(timestamp.encode("utf-8"))
    h.update(nonce.encode("utf-8"))
    h.update(encrypt_key.encode("utf-8"))
    h.update(body)
    return h.hexdigest()


def verify_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes, signature: str) -> bool:
    """常数时间比较签名。未配 encrypt_key（明文事件无签名）时调用方不应走这里。"""
    try:
        import hmac as _hmac
        expected = compute_signature(timestamp, nonce, encrypt_key, body)
        return _hmac.compare_digest(expected, signature or "")
    except Exception:
        return False


def decrypt_event(encrypt_b64: str, encrypt_key: str) -> str:
    """飞书 AES-256-CBC 解密：key=sha256(encrypt_key)；密文 base64 解出后前 16 字节为 IV，
    其余为密文；解密后去 PKCS7 padding。返回明文 JSON 字符串。永不抛错（失败返回 ""）。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        data = base64.b64decode(encrypt_b64)
        if len(data) <= 16:
            return ""
        iv, ciphertext = data[:16], data[16:]
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = dec.update(ciphertext) + dec.finalize()
        if not plain:
            return ""
        pad = plain[-1]
        if isinstance(pad, str):
            pad = ord(pad)
        if 1 <= pad <= 16:
            plain = plain[:-pad]
        return plain.decode("utf-8", errors="replace")
    except Exception as e:
        log_suppressed(logger, e)
        return ""


# ── 入站解析（纯逻辑）──
def parse_inbound(payload: dict) -> dict:
    """把已解密的飞书事件 payload 归一化。永不抛错。返回：
      {"kind": "challenge", "challenge": str}                — URL 验证
      {"kind": "message", "text", "chat_id", "open_id",
       "event_id", "chat_type", "mentioned": bool}          — 收到消息事件
      {"kind": "ignore"}                                     — 其它/无关
    兼容 schema 1.0（顶层 type/event）与 2.0（header/event）。"""
    try:
        if not isinstance(payload, dict):
            return {"kind": "ignore"}
        # URL 验证（明文或解密后均为此形）
        if payload.get("type") == "url_verification" and payload.get("challenge"):
            return {"kind": "challenge", "challenge": payload["challenge"]}

        header = payload.get("header") or {}
        event = payload.get("event") or {}
        event_type = header.get("event_type") or payload.get("type") or ""
        event_id = header.get("event_id") or payload.get("uuid") or ""

        if event_type not in ("im.message.receive_v1", "message"):
            return {"kind": "ignore"}

        msg = event.get("message") or {}
        chat_id = msg.get("chat_id") or ""
        chat_type = msg.get("chat_type") or ""  # "p2p" 单聊 | "group" 群聊
        sender = event.get("sender") or {}
        open_id = ((sender.get("sender_id") or {}).get("open_id")) or ""
        mentions = msg.get("mentions") or []
        mentioned = bool(mentions)

        # content 是 JSON 字符串；文本消息形如 {"text": "..."}
        text = ""
        try:
            content = json.loads(msg.get("content") or "{}")
            text = (content.get("text") or "").strip()
        except Exception:
            text = ""
        # 群聊 @机器人 文本里会带 "@_user_1" 占位，去掉
        if text:
            import re as _re
            text = _re.sub(r"@_user_\d+", "", text).strip()

        return {
            "kind": "message", "text": text, "chat_id": chat_id,
            "open_id": open_id, "event_id": event_id, "chat_type": chat_type,
            "mentioned": mentioned,
        }
    except Exception as e:
        log_suppressed(logger, e)
        return {"kind": "ignore"}


def should_reply(parsed: dict) -> bool:
    """是否应该回复：单聊一律回；群聊仅在 @机器人 时回（避免刷屏）。空文本不回。"""
    if parsed.get("kind") != "message" or not parsed.get("text"):
        return False
    if parsed.get("chat_type") == "group":
        return bool(parsed.get("mentioned"))
    return True


# ── 事件去重（有界 TTL）──
class EventDedup:
    """按 event_id 去重（飞书会在 ~7.5h 内最多重推 4 次）。有界、自动过期。"""

    def __init__(self, ttl: float = 600.0, cap: int = 4096):
        self._seen: dict[str, float] = {}
        self._ttl = ttl
        self._cap = cap

    def seen_before(self, event_id: str) -> bool:
        if not event_id:
            return False
        now = time.time()
        # 惰性清理
        if len(self._seen) > self._cap:
            for k, t in list(self._seen.items()):
                if now - t > self._ttl:
                    self._seen.pop(k, None)
        if event_id in self._seen and now - self._seen[event_id] <= self._ttl:
            return True
        self._seen[event_id] = now
        return False


# ── 网络（取 token / 发消息）──
class FeishuClient:
    """tenant_access_token 缓存 + 发文本消息。urllib 实现，永不抛错。"""

    def __init__(self, app_id: str = "", app_secret: str = ""):
        self.app_id = app_id or _cfg("APP_ID")
        self.app_secret = app_secret or _cfg("APP_SECRET")
        self._token = ""
        self._token_exp = 0.0

    def _post_json(self, url: str, body: dict, headers: dict | None = None, timeout: int = 10) -> dict:
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log_suppressed(logger, e)
            return {}

    def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        out = self._post_json(
            f"{_BASE}/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = out.get("tenant_access_token", "")
        if token:
            self._token = token
            self._token_exp = now + float(out.get("expire", 7200))
        return token

    def send_text(self, chat_id: str, text: str) -> bool:
        if not (chat_id and text):
            return False
        token = self.get_tenant_access_token()
        if not token:
            return False
        out = self._post_json(
            f"{_BASE}/im/v1/messages?receive_id_type=chat_id",
            {"receive_id": chat_id, "msg_type": "text",
             "content": json.dumps({"text": text}, ensure_ascii=False)},
            headers={"Authorization": f"Bearer {token}"},
        )
        return out.get("code", -1) == 0

    # ── 文件/图片发送（飞书开放平台真实 API：先上传拿 key，再发消息）──
    def _post_multipart(self, url: str, fields: dict, file_field: str, file_name: str,
                        file_bytes: bytes, headers: dict, timeout: int = 30) -> dict:
        try:
            boundary = "----hashmm" + secrets.token_hex(8)
            body = bytearray()
            for k, v in fields.items():
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                body += f"{v}\r\n".encode("utf-8")
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode("utf-8")
            body += b"Content-Type: application/octet-stream\r\n\r\n"
            body += file_bytes + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, data=bytes(body), method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log_suppressed(logger, e)
            return {}

    def upload_image(self, image_bytes: bytes) -> str:
        token = self.get_tenant_access_token()
        if not token:
            return ""
        out = self._post_multipart(f"{_BASE}/im/v1/images", {"image_type": "message"},
                                   "image", "img", image_bytes, {"Authorization": f"Bearer {token}"})
        return (out.get("data") or {}).get("image_key", "")

    def upload_file(self, file_name: str, file_bytes: bytes) -> str:
        token = self.get_tenant_access_token()
        if not token:
            return ""
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        ftype = {"doc": "doc", "docx": "doc", "xls": "xls", "xlsx": "xls",
                 "ppt": "ppt", "pptx": "ppt", "pdf": "pdf", "mp4": "mp4"}.get(ext, "stream")
        out = self._post_multipart(f"{_BASE}/im/v1/files", {"file_type": ftype, "file_name": file_name},
                                   "file", file_name, file_bytes, {"Authorization": f"Bearer {token}"})
        return (out.get("data") or {}).get("file_key", "")

    def send_image(self, chat_id: str, image_key: str) -> bool:
        token = self.get_tenant_access_token()
        if not (token and chat_id and image_key):
            return False
        out = self._post_json(f"{_BASE}/im/v1/messages?receive_id_type=chat_id",
                              {"receive_id": chat_id, "msg_type": "image",
                               "content": json.dumps({"image_key": image_key})},
                              headers={"Authorization": f"Bearer {token}"})
        return out.get("code", -1) == 0

    def send_file_key(self, chat_id: str, file_key: str) -> bool:
        token = self.get_tenant_access_token()
        if not (token and chat_id and file_key):
            return False
        out = self._post_json(f"{_BASE}/im/v1/messages?receive_id_type=chat_id",
                              {"receive_id": chat_id, "msg_type": "file",
                               "content": json.dumps({"file_key": file_key})},
                              headers={"Authorization": f"Bearer {token}"})
        return out.get("code", -1) == 0

    def send_local_file(self, chat_id: str, path: str) -> bool:
        """上传本地文件并发到飞书会话：图片走 image、其余走 file。永不抛错。"""
        try:
            name = os.path.basename(path)
            with open(path, "rb") as fp:
                data = fp.read()
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in ("png", "jpg", "jpeg", "gif", "bmp", "webp"):
                key = self.upload_image(data)
                return self.send_image(chat_id, key) if key else False
            key = self.upload_file(name, data)
            return self.send_file_key(chat_id, key) if key else False
        except Exception as e:
            log_suppressed(logger, e)
            return False
