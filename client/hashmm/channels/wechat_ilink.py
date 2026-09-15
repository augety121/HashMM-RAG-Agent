"""hashmm/channels/wechat_ilink.py — 微信 iLink 机器人客户端（移植自 fanbox 的做法）。

iLink 是 2026 年腾讯通过 OpenClaw 正式开放的微信个人号官方 Bot 协议（"微信 ClawBot"），
纯 HTTP/JSON，域名 ilinkai.weixin.qq.com，有官方使用条款背书。它是**长轮询客户端**
（不是 webhook）：扫码登录 → getupdates 长轮询收消息 → sendmessage 回复（必带 context_token）。
本模块把 fanbox 的 Node 实现移植为 Python，并把"大脑"换成 HashMM 的 RAG。

合规提示：iLink 是个人号通道、会话约 24h、腾讯可限速/变更，适合内部/轻量场景；企业级核心
业务更稳妥用企业微信（WeCom）官方应用。详见 CHANGELOG。

纯逻辑（UIN/版本号/headers/解析/发送体）与网络分离，前者可单测。零新依赖（stdlib urllib/secrets）。
默认关：HASHMM_WECHAT_ENABLE=1 且已扫码登录（有 bot_token）才工作。永不抛错。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.channels.wechat_ilink")

LOGIN_BASE = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
CHANNEL_VERSION = os.environ.get("HASHMM_WECHAT_CHANNEL_VERSION", "1.0.11")


def enabled() -> bool:
    from hashmm.channels import config
    return config.wechat_enabled()


# ── 纯逻辑：协议原语 ──
def wechat_uin() -> str:
    """X-WECHAT-UIN：随机 uint32 → 十进制字符串 → utf8 → base64。每次请求重算（防重放）。"""
    n = int.from_bytes(secrets.token_bytes(4), "big")
    return base64.b64encode(str(n).encode("utf-8")).decode()


def client_version(v: str = CHANNEL_VERSION) -> str:
    """版本号 'a.b.c' → uint32 (a<<16)|(b<<8)|c → 十进制字符串。"""
    parts = (str(v).split(".") + ["0", "0", "0"])[:3]
    maj, mnr, pat = (int(x) if str(x).isdigit() else 0 for x in parts)
    return str(((maj & 0xFF) << 16) | ((mnr & 0xFF) << 8) | (pat & 0xFF))


def _common_headers() -> dict:
    return {"iLink-App-ClientVersion": client_version()}


def post_headers(token: str = "") -> dict:
    h = dict(_common_headers())
    h["Content-Type"] = "application/json"
    h["AuthorizationType"] = "ilink_bot_token"
    h["X-WECHAT-UIN"] = wechat_uin()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": "HashMM"}


def build_text_send_body(to_user_id: str, context_token: str, text: str) -> dict:
    """sendmessage 文本请求体。
    ⚠️ client_id（每条消息唯一 ID）和顶层 base_info 是 iLink 的必填「幽灵字段」，
       缺任意一个，服务端会返回 200 但把消息静默丢弃（微信端收不到）。"""
    return {
        "msg": {
            "from_user_id": "",                       # 空串，不是不传
            "to_user_id": to_user_id,
            "client_id": f"hashmm-{secrets.token_hex(16)}",  # 每条消息唯一，必填
            "message_type": 2, "message_state": 2,
            "context_token": context_token or "",
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": base_info(),                      # 必填
    }


def content_from_msg(msg: dict) -> dict:
    """从一条 getupdates 消息里抽取文本与媒体。永不抛错。
    返回 {"text": str, "medias": [{"kind","name"}], "from_user_id": str, "context_token": str}。
    type: 1=文本 text_item / 3=语音 voice_item.text / 2=图片 image_item / 4=文件 file_item。"""
    text = ""
    medias = []
    try:
        for it in (msg.get("item_list") or []):
            t = it.get("type")
            if t == 1 and (it.get("text_item") or {}).get("text") is not None:
                text = it["text_item"]["text"]
            elif t == 3 and (it.get("voice_item") or {}).get("text"):
                text = it["voice_item"]["text"]
            elif t == 2 and it.get("image_item"):
                medias.append({"kind": "image", "name": it["image_item"].get("file_name") or "图片"})
            elif t == 4 and it.get("file_item"):
                medias.append({"kind": "file", "name": it["file_item"].get("file_name") or "文件"})
    except Exception as e:
        log_suppressed(logger, e)
    return {
        "text": text or "", "medias": medias,
        "from_user_id": msg.get("from_user_id") or "",
        "context_token": msg.get("context_token") or "",
    }


# ── 网络（urllib，永不抛错）──
def _http_json(url: str, *, method: str = "POST", headers: dict | None = None,
               body: dict | None = None, timeout: int = 35) -> dict:
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except Exception as e:
        log_suppressed(logger, e)
        return {}


def _http_post_ok(url: str, headers: dict, body: dict, timeout: int = 15) -> bool:
    """POST 并判断是否被接受。iLink sendmessage 成功时返回空 {}（无 ret 码），
    故以 HTTP 2xx 为准；若返回体里带非 0 ret 则视为失败。网络/鉴权异常返回 False。"""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            raw = resp.read().decode("utf-8")
        if not (200 <= code < 300):
            return False
        try:
            d = json.loads(raw) if raw else {}
            if isinstance(d, dict) and d.get("ret", 0) not in (0, None):
                logger.warning("微信 sendmessage 返回 ret=%s", d.get("ret"))
                return False
        except Exception:
            pass
        return True
    except Exception as e:
        log_suppressed(logger, e)
        return False


def fetch_qrcode() -> dict:
    """获取登录二维码。返回 {qrcode, qrcode_img_content, baseurl?} 或 {}。"""
    url = f"{LOGIN_BASE}/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}"
    return _http_json(url, method="POST", headers=post_headers(), body={"local_token_list": []}, timeout=15)


def poll_qr_status(qrcode: str, base_url: str = LOGIN_BASE, timeout: int = 35) -> dict:
    """轮询扫码状态；确认后返回含 bot_token/baseurl 的字典。"""
    url = f"{base_url}/ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qrcode)}"
    return _http_json(url, method="GET", headers=_common_headers(), timeout=timeout)


def get_updates(base_url: str, token: str, get_updates_buf: str = "", timeout: int = 35) -> dict:
    """长轮询拉新消息。get_updates_buf 是同步游标，原样保存、原样回传。
    HTTP 失败时带上状态码（401/403=会话失效需重新扫码；5xx=服务端；其它=网络），方便定位。"""
    url = f"{base_url}/ilink/bot/getupdates"
    try:
        data = json.dumps({"get_updates_buf": get_updates_buf or "", "base_info": base_info()}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        for k, v in post_headers(token).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        out = json.loads(raw) if raw else {}
        if not isinstance(out, dict):
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}
        if not out:
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}  # 长轮询无新消息属正常
        return out
    except urllib.error.HTTPError as e:
        return {"ret": -1, "msgs": [], "get_updates_buf": get_updates_buf, "_http_error": True, "_detail": f"HTTP {e.code}"}
    except Exception as e:
        return {"ret": -1, "msgs": [], "get_updates_buf": get_updates_buf, "_http_error": True, "_detail": str(e)[:60]}


def send_text(base_url: str, token: str, to_user_id: str, context_token: str, text: str) -> bool:
    url = f"{base_url}/ilink/bot/sendmessage"
    return _http_post_ok(url, post_headers(token),
                         build_text_send_body(to_user_id, context_token, text), timeout=15)


# ── 会话持久化（bot_token / baseurl / 游标）──
def _session_path() -> str:
    d = os.environ.get("HASHMM_WECHAT_DATA_DIR", "data/wechat")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "session.json")


def load_session() -> dict:
    try:
        with open(_session_path(), "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def save_session(sess: dict) -> bool:
    try:
        with open(_session_path(), "w", encoding="utf-8") as fp:
            json.dump(sess, fp, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_suppressed(logger, e)
        return False
