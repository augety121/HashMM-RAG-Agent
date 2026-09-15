"""hashmm/agent/vision.py — 视觉模型通路（V86）。

为「问答栏截屏」与「图片上传」提供统一的图像理解能力。

设计约束（项目铁律）：
- 默认关闭：HASHMM_VISION_MODEL / HASHMM_VISION_KEY 未配置时所有入口安静短路；
- 永不抛错：任何网络/SDK 异常都被吞掉并以 (text="", error=...) 返回；
- 零新依赖：复用 openai SDK（项目既有依赖，files.py 上传分析同款用法）。

接入方式（接 Codex / qwen-vl 等 OpenAI 兼容视觉 API 时）：
    HASHMM_VISION_BASE=https://...   # Base URL（可省，OpenAI 官方时）
    HASHMM_VISION_KEY=sk-...
    HASHMM_VISION_MODEL=gpt-...      # 视觉模型名
后端无需任何代码改动。
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 与 files.py 的上传目录保持一致（截屏走 /api/upload 落盘到这里）。
# 注意：files.py 落盘时会做文件名净化 re.sub(r'[^\w.\-]','_',fname)[:80]，
# 而问答请求携带的是原始文件名，因此读取时要先试原名、再试净化名。
UPLOAD_DIR = Path("data/uploads")

_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
}

MAX_IMAGES = 4               # 单轮最多分析的图片数
MAX_IMAGE_BYTES = 8 * 1024 * 1024   # 单图大小上限（8MB）


def configured() -> bool:
    """是否已配置专用视觉模型（key + model 必填，base 可选）。"""
    return bool(os.environ.get("HASHMM_VISION_KEY") and os.environ.get("HASHMM_VISION_MODEL"))


def config_summary() -> dict:
    """脱敏的配置概览（给 /health、调试用）。"""
    return {
        "configured": configured(),
        "model": os.environ.get("HASHMM_VISION_MODEL", ""),
        "base": os.environ.get("HASHMM_VISION_BASE", ""),
    }


def _make_client():
    """惰性构建 OpenAI 兼容客户端；未配置或 SDK 缺失返回 None（不抛错）。"""
    if not configured():
        return None
    try:
        from openai import OpenAI
        from hashmm.llm_timeout import client_timeout
        kwargs = {"api_key": os.environ["HASHMM_VISION_KEY"], "timeout": client_timeout()}
        base = os.environ.get("HASHMM_VISION_BASE", "").strip()
        if base:
            kwargs["base_url"] = base
        return OpenAI(**kwargs)
    except Exception as e:  # pragma: no cover - SDK 缺失等极端情形
        logger.warning("[vision] 客户端构建失败: %s", e)
        return None


def mime_of(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME.get(ext, "image/png")


def is_image_name(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in _MIME


def describe_images(images: list[tuple[str, str]], question: str = "",
                    max_tokens: int = 1500) -> tuple[str, str]:
    """调视觉模型描述图片。

    Args:
        images: [(b64, mime), ...]（最多 MAX_IMAGES 张）
        question: 用户问题；非空时做「定向分析」——只描述与问题相关的内容，
                  比上传时的泛泛全图描述对回答更有用。
    Returns:
        (text, error)；永不抛错。未配置 → ("", "vision 未配置")。
    """
    if not images:
        return "", "无图片"
    client = _make_client()
    if client is None:
        return "", "vision 未配置"
    if question:
        prompt = (
            "用户基于以下截图提问，请围绕用户问题精确描述截图中相关的内容"
            "（包括可见的文字、代码、报错信息、界面元素、图表数据），无关区域一笔带过。"
            f"用中文回答。\n用户问题：{question}"
        )
    else:
        prompt = "请详细描述这张图片的内容，包括文字、图表、公式、界面元素等。用中文回答。"
    content: list[dict] = []
    for b64, mime in images[:MAX_IMAGES]:
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": prompt})
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("HASHMM_VISION_MODEL", ""),
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return "", "视觉模型返回为空"
        return text, ""
    except Exception as e:
        logger.warning("[vision] 调用失败: %s", e)
        return "", f"视觉模型调用失败: {e}"


def describe_image_file(path: str, question: str = "", max_tokens: int = 800) -> str:
    """摄取期：按文件路径读图 → 调视觉模型得到语义描述。默认关、永不抛错。
    未配置 / 读取失败 / 调用失败 → 返回 ""（调用方据空串决定是否写入）。
    与 chat 路同一通路：配 HASHMM_VISION_BASE 指向自托管 VLM 即可让图像不出内网。"""
    if not configured():
        return ""
    try:
        fp = Path(path)
        if not fp.is_file():
            return ""
        data = fp.read_bytes()
        if not data or len(data) > MAX_IMAGE_BYTES:
            return ""
        b64 = base64.b64encode(data).decode()
        text, _err = describe_images([(b64, mime_of(fp.name))], question=question, max_tokens=max_tokens)
        return text or ""
    except Exception as e:  # 永不抛错（铁律）
        logger.warning("[vision] describe_image_file 失败 %s: %s", path, e)
        return ""


def read_upload_b64(filename: str) -> Optional[tuple[str, str]]:
    """从上传目录读取图片为 (b64, mime)。

    仅取 basename（防路径穿越）；非图片扩展名/不存在/超限 → None。永不抛错。
    """
    try:
        safe = Path(filename).name
        if not safe or safe != filename or not is_image_name(safe):
            return None
        fp = UPLOAD_DIR / safe
        if not fp.is_file():
            # files.py 落盘名经过净化（与原名可能不同），按同一规则再试一次
            import re as _re
            sanitized = _re.sub(r'[^\w.\-]', '_', safe)[:80]
            fp = UPLOAD_DIR / sanitized
            if sanitized == safe or Path(sanitized).name != sanitized or not fp.is_file():
                return None
        data = fp.read_bytes()
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        return base64.b64encode(data).decode(), mime_of(safe)
    except Exception as e:
        logger.warning("[vision] 读取上传图片失败 %s: %s", filename, e)
        return None


def sanitize_image_names(names: list | None) -> list[str]:
    """问答请求里的图片名清洗：仅 basename、仅图片扩展名、去重、上限 MAX_IMAGES。"""
    out: list[str] = []
    for n in names or []:
        if not isinstance(n, str):
            continue
        safe = Path(n).name
        if safe and safe == n and is_image_name(safe) and safe not in out:
            out.append(safe)
        if len(out) >= MAX_IMAGES:
            break
    return out


def analyze_for_chat(image_names: list[str], question: str,
                     reader: Callable[[str], Optional[tuple[str, str]]] | None = None,
                     describer: Callable[..., tuple[str, str]] | None = None,
                     ) -> tuple[str, str]:
    """问答时的定向图像理解（/stream 前置步骤）。

    Args:
        image_names: 已清洗的上传文件名列表。
        question: 用户问题（用于定向分析）。
        reader/describer: 可注入（测试用）；缺省走 read_upload_b64 / describe_images。
    Returns:
        (context_block, trace_detail)
        - context_block: 注入 file_context 的「[截图内容分析]」块；失败/未配置为 ""。
        - trace_detail: 给前端 trace 事件的人话描述（成功/失败/未配置都有交代）。
    永不抛错。
    """
    reader = reader or read_upload_b64
    describer = describer or describe_images
    names = list(image_names or [])[:MAX_IMAGES]
    if not names:
        return "", ""
    if not configured():
        return "", f"收到 {len(names)} 张截图，但未配置视觉模型（HASHMM_VISION_*），本轮按文字回答"
    loaded: list[tuple[str, str]] = []
    for n in names:
        item = reader(n)
        if item:
            loaded.append(item)
    if not loaded:
        return "", f"收到 {len(names)} 张截图但读取失败，本轮按文字回答"
    text, err = describer(loaded, question)
    if not text:
        return "", f"图像理解失败（{err}），本轮按文字回答"
    block = f"[截图内容分析]（视觉模型对用户所附 {len(loaded)} 张截图的定向解读，可直接作为事实依据）\n{text}"
    detail = f"图像理解：已分析 {len(loaded)} 张截图（{len(text)} 字）"
    return block, detail
