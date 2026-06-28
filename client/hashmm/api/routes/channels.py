"""hashmm/api/routes/channels.py — IM 渠道路由（飞书 webhook + 微信 iLink 登录/worker）。

飞书：应用机器人事件订阅 webhook（公开，靠签名/Verification Token 保护）。收到消息后
**先秒回 200**（飞书要求 1 秒内响应），再用后台任务跑 RAG 并发回——避免超时重推。
微信：iLink 是长轮询客户端，靠后台 worker 拉消息；登录/状态走管理员路由（需扫码）。

默认关：路由始终注册，但 enabled() 为假时收到即忽略（关闭零变化）。永不抛错。
"""
from __future__ import annotations

import asyncio
import json

import time
import secrets
from fastapi import APIRouter, Request, HTTPException

from hashmm.utils import get_logger, log_suppressed
from hashmm.channels import feishu, wechat_ilink as wx, rag_bridge
from hashmm.channels.replies import chunk_for_im, format_sources

logger = get_logger("hashmm.channels.routes")

router = APIRouter(prefix="/api/channels", tags=["channels"])

_feishu_dedup = feishu.EventDedup()
_feishu_client = None
_FEISHU_LIMIT = 1800
_WECHAT_LIMIT = 1500


# ══════════════ 渠道文件投送（用户在微信/飞书里说"把电脑某文件发我"）══════════════
# 复用桌面投送管线：建合成会话 → 写 file_request（桌面常驻轮询会把文件上传到该会话目录）
# → 轮询目录拿到文件 → 飞书直接发文件 / 微信发带签名的下载链接。
# 仅在已配置 channel_owner_uid（你的 Supabase uid，桌面用同账号轮询）时启用；否则退回普通问答（零回归）。
import hashlib as _hashlib
import hmac as _hmac
import os as _os


def _owner_uid() -> str:
    try:
        from hashmm.api import settings_store
        v = (settings_store.get_setting("channel_owner_uid", "") or "").strip()
        if v:
            return v if v.startswith("sb_") else f"sb_{v}"
    except Exception:
        pass
    return ""


def _public_base() -> str:
    try:
        b = (_os.environ.get("HASHMM_PUBLIC_URL", "") or "").strip()
        if not b:
            from hashmm.api import settings_store
            b = (settings_store.get_setting("channel_public_base", "") or "").strip()
        return b.rstrip("/")
    except Exception:
        return ""


def _link_secret() -> bytes:
    try:
        from hashmm.api import settings_store
        s = settings_store.get_setting("channel_link_secret", "")
        if not s:
            s = secrets.token_hex(16)
            settings_store.set_setting("channel_link_secret", s)
        return s.encode()
    except Exception:
        return b"hashmm-channel-fallback-secret"


def _sign_file(conv_id: str, filename: str, exp: int) -> str:
    msg = f"{conv_id}|{filename}|{exp}".encode("utf-8")
    return _hmac.new(_link_secret(), msg, _hashlib.sha256).hexdigest()[:32]


def _file_link(conv_id: str, filename: str) -> str:
    import urllib.parse as _up
    base = _public_base()
    if not base:
        return ""
    exp = int(time.time()) + 7 * 86400
    sig = _sign_file(conv_id, filename, exp)
    q = _up.urlencode({"c": conv_id, "f": filename, "e": exp, "s": sig})
    return f"{base}/api/channels/ch-file?{q}"


def _is_file_request(text: str) -> bool:
    try:
        from hashmm.api.server import _is_desktop_file_request
        return _is_desktop_file_request(text)
    except Exception:
        return False


async def _await_channel_file(query: str, deliver) -> None:
    """建合成会话+file_request，等桌面把文件投送到会话目录（约 90s），到了调 deliver(conv_id, path)。永不抛错。"""
    owner = _owner_uid()
    if not owner:
        return
    try:
        from hashmm.api import database as db, supabase_sync as sbs
        conv_id = f"ch-file-{int(time.time())}-{secrets.token_hex(4)}"
        try:
            db.create_conversation(conv_id, user_id=owner, title="[渠道]文件投送")
        except Exception:
            pass
        await asyncio.to_thread(sbs.push_file_request, owner, conv_id, query)
        fdir = db.conv_files_dir(conv_id)
        for _ in range(45):                      # 45 × 2s ≈ 90s
            await asyncio.sleep(2)
            try:
                files = [p for p in fdir.iterdir() if p.is_file()] if fdir.exists() else []
            except Exception:
                files = []
            if files:
                newest = max(files, key=lambda p: p.stat().st_mtime)
                await asyncio.to_thread(deliver, conv_id, str(newest))
                return
    except Exception as e:
        log_suppressed(logger, e)


@router.get("/ch-file")
async def channel_file_download(request: Request):
    """渠道文件下载（签名链接，无需登录；HMAC 防伪、7 天过期）。供微信里点链接下载。"""
    from fastapi.responses import FileResponse
    p = request.query_params
    conv_id = p.get("c", ""); filename = p.get("f", ""); exp = p.get("e", ""); sig = p.get("s", "")
    try:
        exp_i = int(exp)
    except Exception:
        raise HTTPException(status_code=400, detail="链接无效")
    if exp_i < int(time.time()):
        raise HTTPException(status_code=410, detail="链接已过期")
    if not (conv_id and filename and sig) or not _hmac.compare_digest(_sign_file(conv_id, filename, exp_i), sig):
        raise HTTPException(status_code=403, detail="签名无效")
    from hashmm.api import database as db
    import os.path as _osp
    safe = _osp.basename(filename)
    fpath = db.conv_files_dir(conv_id) / safe
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(fpath), filename=safe)


def _get_feishu_client():
    global _feishu_client
    if _feishu_client is None:
        _feishu_client = feishu.FeishuClient()
    return _feishu_client


# ══════════════ 飞书 ══════════════
@router.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅入口。处理 url_verification / 签名校验 / 解密 / 去重 / 转 RAG。"""
    try:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return {"code": 0}

        encrypt_key = feishu._cfg("ENCRYPT_KEY")

        # 加密事件：先验签（有头时）再解密
        if isinstance(payload, dict) and "encrypt" in payload:
            ts = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            sig = request.headers.get("X-Lark-Signature", "")
            if encrypt_key and ts and nonce and sig:
                if not feishu.verify_signature(ts, nonce, encrypt_key, raw, sig):
                    raise HTTPException(status_code=401, detail="bad signature")
            plain = feishu.decrypt_event(payload.get("encrypt", ""), encrypt_key)
            try:
                payload = json.loads(plain)
            except Exception:
                return {"code": 0}

        parsed = feishu.parse_inbound(payload)

        # URL 验证：原样返回 challenge
        if parsed.get("kind") == "challenge":
            return {"challenge": parsed["challenge"]}

        # 消息事件 → 满足条件则秒回 200、后台处理
        if parsed.get("kind") == "message" and feishu.should_reply(parsed):
            if not feishu.enabled():
                return {"code": 0}
            if _feishu_dedup.seen_before(parsed.get("event_id", "")):
                return {"code": 0}
            asyncio.create_task(_handle_feishu_message(parsed))
        return {"code": 0}
    except HTTPException:
        raise
    except Exception as e:
        log_suppressed(logger, e)
        return {"code": 0}


async def _handle_feishu_message(parsed: dict):
    try:
        peer = parsed.get("open_id") or parsed.get("chat_id") or ""
        chat_id = parsed.get("chat_id", "")
        logger.info("飞书 消息 chat=%s… text=%s", (chat_id or "")[:8], parsed["text"][:30])
        # 文件投送：用户说"把电脑某文件发我" → 走桌面投送管线，飞书直接发文件。
        if _is_file_request(parsed["text"]):
            if _owner_uid():
                client = _get_feishu_client()
                await asyncio.to_thread(client.send_text, chat_id,
                                        "好的，正在从你的电脑里找这个文件——请确保桌面客户端在运行，找到后发给你。")

                def _deliver(conv_id, path):
                    try:
                        import os.path as _osp
                        if not client.send_local_file(chat_id, path):
                            link = _file_link(conv_id, _osp.basename(path))
                            if link:
                                client.send_text(chat_id, f"文件已找到：{_osp.basename(path)}\n下载：{link}")
                    except Exception:
                        pass
                asyncio.create_task(_await_channel_file(parsed["text"], _deliver))
                return
            # 识别为"取电脑文件"但还没绑定到你的桌面账号 → 给清晰指引，别退回成误导性的普通问答。
            _c = _get_feishu_client()
            await asyncio.to_thread(_c.send_text, chat_id,
                "你想让我把电脑/桌面上的文件发给你。这个功能要满足两点：\n"
                "① 用同一个账号登录过 App 或桌面客户端（登录后系统会自动完成绑定）；\n"
                "② 桌面客户端正在你电脑上运行（由它去 桌面/下载/文档 里找文件并发出来）。\n"
                "确认这两点后，再说一次「把桌面上的XX文件发我」就能收到了。")
            return
        out = await rag_bridge.answer(parsed["text"], channel="feishu", peer_id=peer,
                                      user_id=parsed.get("open_id", ""))
        reply = out.get("text") or ""
        if not reply:
            logger.warning("飞书 RAG 未产出回复，原文=%s", parsed["text"][:30])
            return
        src = format_sources(out.get("sources"))
        if src:
            reply = (reply + "\n\n" + src).strip()
        client = _get_feishu_client()
        sent_ok = True
        for chunk in chunk_for_im(reply, limit=_FEISHU_LIMIT):
            ok = await asyncio.to_thread(client.send_text, parsed.get("chat_id", ""), chunk)
            sent_ok = sent_ok and ok
        logger.info("飞书 已回复 chat=%s… 发送%s", (parsed.get("chat_id", "") or "")[:8],
                    "成功" if sent_ok else "失败(code!=0，检查 app_id/secret 或机器人发消息权限)")
    except Exception as e:
        logger.warning("飞书 处理消息异常: %s", e)
        log_suppressed(logger, e)


# ══════════════ 微信 iLink ══════════════
_wechat_task = None
_wechat_health = {"last_poll": 0.0, "last_msg": 0.0, "last_reply": 0.0, "msgs": 0, "replies": 0, "errors": 0, "last_error": ""}


def _require_admin(request: Request):
    """登录/管理路由需管理员。失败抛 403。"""
    try:
        from hashmm.api.auth import get_current_user
        user = get_current_user(request)
        if not user or user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="需要管理员权限")


def start_wechat_worker() -> bool:
    """启动微信长轮询 worker（幂等）。enabled() 为假或未登录则不启动。"""
    global _wechat_task
    if not wx.enabled():
        return False
    if _wechat_task is not None and not _wechat_task.done():
        return True
    sess = wx.load_session()
    if not sess.get("bot_token"):
        return False
    try:
        _wechat_task = asyncio.create_task(_wechat_worker_loop())
        return True
    except Exception as e:
        log_suppressed(logger, e)
        return False


def restart_wechat_worker() -> bool:
    """强制重启 worker：先取消正在跑的旧任务，再起新的，确保用上最新 token。
    重新扫码后必须用它——否则旧 worker 仍拿着旧 token 在轮询旧会话，你的新消息永远收不到。"""
    global _wechat_task
    try:
        if _wechat_task is not None and not _wechat_task.done():
            _wechat_task.cancel()
        _wechat_task = None
        logger.info("微信 iLink worker 强制重启（重新扫码后切换到新 token）")
    except Exception as e:
        log_suppressed(logger, e)
        _wechat_task = None
    return start_wechat_worker()


async def _wechat_worker_loop():
    """长轮询：getupdates → 每条消息 → RAG → sendmessage。会话过期/禁用则退出。"""
    sess = wx.load_session()
    base = sess.get("base_url") or wx.LOGIN_BASE
    token = sess.get("bot_token", "")
    buf = sess.get("buf", "")
    logger.info("微信 iLink worker 启动 base=%s token=%s", base, (token[:8] + "…") if token else "(空)")
    consecutive_errors = 0
    while wx.enabled() and token:
        out = await asyncio.to_thread(wx.get_updates, base, token, buf, 35)
        _wechat_health["last_poll"] = time.time()
        if out.get("_http_error"):
            consecutive_errors += 1
            _wechat_health["errors"] += 1
            detail = out.get("_detail", "")
            _wechat_health["last_error"] = f"getupdates 失败：{detail}"
            logger.warning("微信 getupdates 失败（第 %d 次）：%s", consecutive_errors, detail)
            # 401/403 → 会话失效，清掉旧 token 直接退出，提示重新扫码（别再拿死 token 空转）
            if "HTTP 401" in detail or "HTTP 403" in detail:
                logger.warning("微信 会话已失效（%s），清除旧 token 并退出，请重新扫码登录", detail)
                try:
                    sess["bot_token"] = ""
                    wx.save_session(sess)
                except Exception:
                    pass
                break
            if consecutive_errors >= 3:
                logger.warning("微信 连续失败 3 次，worker 退出（请检查网络能否访问 ilinkai.weixin.qq.com 或重新扫码）")
                break
            await asyncio.sleep(3)
            continue
        consecutive_errors = 0
        ret = out.get("ret", 0)
        if ret not in (0, None):
            logger.warning("微信 getupdates ret=%s，会话已过期，worker 退出并清除旧 token（请重新扫码）", ret)
            try:
                sess["bot_token"] = ""        # 清掉过期 token，避免配置变更时拿它一直空转重启
                wx.save_session(sess)
            except Exception:
                pass
            break
        buf = out.get("get_updates_buf", buf)
        msgs = out.get("msgs") or []
        if msgs:
            logger.info("微信 收到 %d 条更新", len(msgs))
        for msg in msgs:
            c = wx.content_from_msg(msg)
            if c.get("text") and c.get("from_user_id"):
                _wechat_health["last_msg"] = time.time()
                _wechat_health["msgs"] += 1
                logger.info("微信 消息 from=%s… text=%s", c["from_user_id"][:8], c["text"][:30])
                asyncio.create_task(_handle_wechat_message(base, token, c))
            elif c.get("medias"):
                logger.info("微信 收到媒体消息（暂仅回文本）from=%s…", c.get("from_user_id", "")[:8])
        sess["buf"] = buf
        wx.save_session(sess)
    logger.info("微信 iLink worker 结束")


async def _handle_wechat_message(base: str, token: str, c: dict):
    try:
        # 文件投送：用户说"把电脑某文件发我" → 桌面投送管线，微信发下载链接。
        if _is_file_request(c["text"]):
            if _owner_uid():
                await asyncio.to_thread(wx.send_text, base, token, c["from_user_id"], c["context_token"],
                                        "好的，正在从你的电脑里找这个文件——找到后把下载链接发你。")

                def _deliver(conv_id, path):
                    try:
                        import os.path as _osp
                        name = _osp.basename(path)
                        link = _file_link(conv_id, name)
                        msg = (f"文件已找到：{name}\n点此下载：{link}" if link
                               else f"文件已找到：{name}（要在微信里收到下载链接，请在客户端设置 channel_public_base 为后端公网地址）")
                        wx.send_text(base, token, c["from_user_id"], c["context_token"], msg)
                    except Exception:
                        pass
                asyncio.create_task(_await_channel_file(c["text"], _deliver))
                return
            # 识别为"取电脑文件"但还没绑定到你的桌面账号 → 给清晰可执行的指引，
            # 别退回成误导性的普通问答（那样会说"我看不到你的桌面"，让人以为没这功能）。
            await asyncio.to_thread(wx.send_text, base, token, c["from_user_id"], c["context_token"],
                "你想让我把电脑/桌面上的文件发给你。这个功能要满足两点：\n"
                "① 用同一个账号登录过 App 或桌面客户端（登录后系统会自动完成绑定）；\n"
                "② 桌面客户端正在你电脑上运行（由它去 桌面/下载/文档 里找文件并发出来）。\n"
                "确认这两点后，再说一次「把桌面上的XX文件发我」就能收到了。")
            return
        out = await rag_bridge.answer(c["text"], channel="wechat", peer_id=c["from_user_id"],
                                      user_id=c["from_user_id"])
        reply = out.get("text") or ""
        if not reply:
            logger.warning("微信 RAG 未产出回复，原文=%s", c["text"][:30])
            return
        sent_ok = True
        for chunk in chunk_for_im(reply, limit=_WECHAT_LIMIT):
            ok = await asyncio.to_thread(wx.send_text, base, token, c["from_user_id"], c["context_token"], chunk)
            sent_ok = sent_ok and ok
        if sent_ok:
            _wechat_health["last_reply"] = time.time()
            _wechat_health["replies"] += 1
        logger.info("微信 已回复 from=%s… 发送%s", c["from_user_id"][:8],
                    "成功" if sent_ok else "失败(send ret!=0，多半 context_token 失效)")
    except Exception as e:
        logger.warning("微信 处理消息异常: %s", e)
        log_suppressed(logger, e)


@router.get("/wechat/status")
async def wechat_status(request: Request):
    _require_admin(request)
    sess = wx.load_session()
    now = time.time()
    h = _wechat_health
    return {
        "enabled": wx.enabled(),
        "logged_in": bool(sess.get("bot_token")),
        "worker_running": bool(_wechat_task is not None and not _wechat_task.done()),
        "channel_version": wx.CHANNEL_VERSION,
        "health": {
            "last_poll_ago": round(now - h["last_poll"], 1) if h["last_poll"] else None,
            "last_msg_ago": round(now - h["last_msg"], 1) if h["last_msg"] else None,
            "last_reply_ago": round(now - h["last_reply"], 1) if h["last_reply"] else None,
            "msgs": h["msgs"],
            "replies": h["replies"],
            "errors": h["errors"],
            "last_error": h["last_error"],
        },
    }


@router.post("/wechat/login/start")
async def wechat_login_start(request: Request):
    """获取登录二维码（管理员扫码绑定）。"""
    _require_admin(request)
    if not wx.enabled():
        raise HTTPException(status_code=400, detail="微信渠道未启用（HASHMM_WECHAT_ENABLE=1）")
    qr = await asyncio.to_thread(wx.fetch_qrcode)
    if not qr.get("qrcode"):
        raise HTTPException(status_code=502, detail="获取二维码失败")
    # iLink 返回的 qrcode_img_content 是二维码「内容串」不是图片，直接 <img src> 会空白。
    # 这里把内容串生成为可扫描的二维码 PNG（data URL）。cv2 不可用则返回空串，前端回退提示。
    from hashmm.channels.qrgen import make_qr_data_url
    content = qr.get("qrcode_img_content") or qr.get("qrcode") or ""
    img = await asyncio.to_thread(make_qr_data_url, content)
    return {"qrcode": qr.get("qrcode"), "qrcode_img_content": img,
            "base_url": qr.get("baseurl") or wx.LOGIN_BASE}


@router.post("/wechat/login/poll")
async def wechat_login_poll(request: Request):
    """轮询扫码状态；确认后保存会话并启动 worker。body: {"qrcode": "...", "base_url": "..."}"""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    qrcode = (body.get("qrcode") or "").strip()
    base_url = (body.get("base_url") or wx.LOGIN_BASE).strip()
    if not qrcode:
        raise HTTPException(status_code=400, detail="缺少 qrcode")
    st = await asyncio.to_thread(wx.poll_qr_status, qrcode, base_url, 35)
    status = st.get("status", "")
    if status == "confirmed" and st.get("bot_token"):
        wx.save_session({"bot_token": st["bot_token"],
                         "base_url": st.get("baseurl") or base_url, "buf": ""})
        started = restart_wechat_worker()   # 重新扫码后强制重启，切换到新 token（否则旧 worker 还拿旧 token）
        return {"status": "confirmed", "worker_started": started}
    return {"status": status or "pending"}


# ══════════════ 渠道配置（客户端 UI 可视化开关，免改 AutoDL 启动命令）══════════════
def _mask(v: str) -> dict:
    """密钥脱敏展示：只回是否已配 + 掩码，不回明文。"""
    v = v or ""
    if not v:
        return {"configured": False, "masked": ""}
    masked = (v[:4] + "***" + v[-2:]) if len(v) > 6 else "***"
    return {"configured": True, "masked": masked}


@router.get("/config")
async def get_channels_config(request: Request):
    """读当前渠道配置（密钥脱敏）+ 状态。管理员。"""
    _require_admin(request)
    from hashmm.channels import config as ccfg
    sess = wx.load_session()
    return {
        "feishu": {
            "enable": ccfg.feishu("ENABLE") == "1",
            "app_id": ccfg.feishu("APP_ID"),
            "app_secret": _mask(ccfg.feishu("APP_SECRET")),
            "encrypt_key": _mask(ccfg.feishu("ENCRYPT_KEY")),
            "verification_token": _mask(ccfg.feishu("VERIFICATION_TOKEN")),
            "enabled": feishu.enabled(),
            "webhook_path": "/api/channels/feishu/webhook",
        },
        "wechat": {
            "enable": ccfg.wechat("ENABLE") == "1",
            "channel_version": ccfg.wechat("CHANNEL_VERSION") or wx.CHANNEL_VERSION,
            "enabled": wx.enabled(),
            "logged_in": bool(sess.get("bot_token")),
            "worker_running": bool(_wechat_task is not None and not _wechat_task.done()),
        },
    }


@router.put("/config")
async def put_channels_config(request: Request):
    """写渠道配置到 DB（settings_store）。管理员。密钥为空/占位时不覆盖既有值。
    body 形如 {"feishu":{"enable":true,"app_id":"...","app_secret":"..."},"wechat":{"enable":false}}。"""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    from hashmm.api import settings_store

    def _save(store_key: str, value, *, secret: bool = False):
        if value is None:
            return
        if isinstance(value, bool):
            value = "1" if value else "0"
        value = str(value)
        # 密钥：空串或脱敏占位（含 ***）→ 不覆盖既有
        if secret and (not value or "***" in value):
            return
        settings_store.set_setting(store_key, value)

    fs = body.get("feishu") or {}
    if "enable" in fs:
        _save("channel_feishu_enable", fs.get("enable"))
    if "app_id" in fs:
        _save("channel_feishu_app_id", fs.get("app_id"))
    if "app_secret" in fs:
        _save("channel_feishu_app_secret", fs.get("app_secret"), secret=True)
    if "encrypt_key" in fs:
        _save("channel_feishu_encrypt_key", fs.get("encrypt_key"), secret=True)
    if "verification_token" in fs:
        _save("channel_feishu_verification_token", fs.get("verification_token"), secret=True)

    wc = body.get("wechat") or {}
    if "enable" in wc:
        _save("channel_wechat_enable", wc.get("enable"))
    if "channel_version" in wc:
        _save("channel_wechat_channel_version", wc.get("channel_version"))

    # 微信开关变化时按需启停 worker（开启且已登录则启动）
    started = False
    if wx.enabled():
        started = start_wechat_worker()
    return {"ok": True, "wechat_worker_started": started,
            "feishu_enabled": feishu.enabled(), "wechat_enabled": wx.enabled()}
