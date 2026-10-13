"""hashmm/api/routes/figma_import.py — Figma 设计稿导入到画布（V277）。

POST /api/figma/import  body: {url_or_key, token, conv_id?, page_index?}
  · url_or_key：Figma 文件链接或裸 key
  · token：用户的 Figma Personal Access Token（仅本次请求使用、**不落库**）
  · conv_id：给了就把导入结果写成该会话的画布文件（拿到画布全家桶：编辑/版本/划选提问/发布）

诚实边界：实时拉取需服务器能访问 api.figma.com（外网）。解析核心已单测；
无网络/token 时返回可行动的中文错误，不 500 崩溃。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.figma")
router = APIRouter(prefix="/api/figma", tags=["figma"])


@router.post("/import")
async def figma_import(request: Request):
    user = require_auth(request)
    body = await request.json()
    url_or_key = str(body.get("url_or_key") or body.get("url") or body.get("key") or "").strip()
    token = str(body.get("token") or "").strip()
    conv_id = str(body.get("conv_id") or "").strip()
    try:
        page_index = int(body.get("page_index") or 0)
    except (TypeError, ValueError):
        page_index = 0

    if not url_or_key:
        raise HTTPException(400, "请提供 Figma 文件链接或 key")

    from hashmm.connectors import figma as F
    key = F.file_key_from_url(url_or_key)
    if not key:
        raise HTTPException(400, "无法从输入解析出 Figma 文件 key（请粘贴形如 figma.com/file/KEY/... 的链接）")

    # 实时拉取（阻塞 IO 丢线程池）→ 解析成画布 HTML
    try:
        html = await run_in_threadpool(F.import_to_html, url_or_key, token, page_index=page_index)
    except RuntimeError as e:
        # 连接器已把 403/404/缺token 等转成人话
        raise HTTPException(502, str(e))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        raise HTTPException(502, f"导入失败：{str(e)[:160]}")

    # 写画布（可选）
    fname = ""
    if conv_id:
        try:
            from hashmm.api import database as db
            fdir = db.conv_files_dir(conv_id)
            fdir.mkdir(parents=True, exist_ok=True)
            fname = f"figma-{key[:8]}-{int(time.time()) % 100000}.html"
            (fdir / fname).write_text(html, encoding="utf-8")
        except Exception as e:  # noqa: BLE001 —— 写画布失败不吞产出
            log_suppressed(logger, e)
            fname = ""

    return {"ok": True, "file": fname, "html": html[:200000],
            "note": "已导入到会话画布（可编辑/版本/发布）" if fname else "已解析（未指定会话：仅返回 HTML）"}
