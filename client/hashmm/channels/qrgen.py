"""hashmm/channels/qrgen.py — 把二维码「内容串」生成为可扫描的二维码图（PNG data URL）。

背景：iLink 的 get_bot_qrcode 返回的 qrcode_img_content 是二维码「内容字符串」（一段 URL/token），
不是图片。直接 <img src> 会显示空白。这里用 OpenCV 的 QRCodeEncoder 把内容生成为二维码 PNG，
经 base64 data URL 返回，前端即可显示扫描。

cv2 为软依赖（项目已引用，但未必装在每台机器）：不可用时返回空串，调用方回退展示内容串，
并提示用户 `pip install opencv-python-headless`。永不抛错、零强制新依赖。
"""
from __future__ import annotations

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.channels.qrgen")


def make_qr_data_url(content: str, scale: int = 8, quiet: int = 4) -> str:
    """内容串 → 二维码 PNG 的 data URL。失败/cv2 不可用时返回空串。"""
    if not content:
        return ""
    try:
        import base64

        import cv2
        import numpy as np  # noqa: F401  (cv2 依赖 numpy，确保可用)

        enc = cv2.QRCodeEncoder_create()
        qr = enc.encode(content)  # uint8 矩阵（0/255）
        big = cv2.resize(
            qr, (qr.shape[1] * scale, qr.shape[0] * scale), interpolation=cv2.INTER_NEAREST
        )
        pad = quiet * scale
        padded = cv2.copyMakeBorder(
            big, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255
        )  # 白边 quiet zone，扫码必需
        ok, buf = cv2.imencode(".png", padded)
        if not ok:
            return ""
        return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()
    except Exception as e:
        log_suppressed(logger, e)
        return ""
