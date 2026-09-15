"""受控设计渲染器 —— HTML → 图片 / 视频（迁移自 huashu-design 的导出能力）。

为什么单独做、不改 `tool_execute_code`：
  `tool_execute_code` 禁 subprocess 是**正确的安全设计** —— HashMM 是带对外 API 的多用户服务，
  放开任意命令执行 = 严重漏洞。所以本模块不碰它，而是新增一个**受控、白名单驱动**的渲染通道：
  - 只跑白名单工具（playwright 截图 / ffmpeg 转码），**不执行任意命令**；
  - 输入只接受 HTML 字符串/已生成的图片，输出只写进指定渲染目录；
  - **默认关**（`HASHMM_DESIGN_RENDER` 未开 → 直接拒绝，零暴露面）；
  - 工具不存在时**优雅降级**（返回"需安装 node/ffmpeg/playwright"提示，不报错、不崩）；
  - 永不抛错。

这让 HashMM 的设计 agent 获得 huashu-design 同款的「HTML → 截图 / 动画 MP4」能力，
但安全边界由本模块独占，不污染主代码执行工具。

启用：`HASHMM_DESIGN_RENDER=1`，且机器装有 node+playwright（截图）/ ffmpeg（视频）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.design_render")

# 渲染输出目录（隔离，不碰 data/ 等）
def _render_dir() -> Path:
    d = Path(os.environ.get("HASHMM_RENDER_DIR", "data/renders"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_enabled() -> bool:
    return os.environ.get("HASHMM_DESIGN_RENDER", "0").strip().lower() in {"1", "true", "yes", "on"}


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def capabilities() -> dict:
    """报告当前环境的渲染能力（不执行任何命令，只探测工具是否存在）。"""
    node = _tool_available("node") or _tool_available("npx")
    pw = False
    try:
        import importlib.util
        pw = importlib.util.find_spec("playwright") is not None or _tool_available("playwright")
    except Exception:
        pw = _tool_available("playwright")
    return {
        "enabled": render_enabled(),
        "html_to_image": bool(pw) or _tool_available("wkhtmltoimage"),  # playwright 或 wkhtmltoimage
        "html_to_pdf": _tool_available("wkhtmltopdf"),
        "images_to_video": _tool_available("ffmpeg"),
        "office_to_image": _tool_available("libreoffice") or _tool_available("soffice"),  # PPTX/DOCX→预览图
        "doc_convert": _tool_available("pandoc"),
        "diagram": _tool_available("dot"),
        "image_ops": _tool_available("convert"),
        # 底层工具明细
        "node": node, "ffmpeg": _tool_available("ffmpeg"), "playwright": pw,
        "wkhtmltoimage": _tool_available("wkhtmltoimage"),
        "libreoffice": _tool_available("libreoffice") or _tool_available("soffice"),
        "pandoc": _tool_available("pandoc"), "graphviz": _tool_available("dot"),
        "hint": ("已就绪" if render_enabled() else "设 HASHMM_DESIGN_RENDER=1 启用。"
                 "HTML→图片优先用 wkhtmltoimage(轻量)或 playwright；PPT/DOCX→预览图用 libreoffice；"
                 "视频用 ffmpeg。缺哪个装哪个，不装则该功能降级、其它不受影响。"),
    }


def _run_whitelisted(cmd: list[str], timeout: int = 60) -> tuple[bool, str]:
    """只执行白名单工具的受控 subprocess。

    白名单对齐 Claude 网页沙箱的真实工具集（全部是确定性的文档/图形渲染工具，
    没有任意命令执行能力）：
      - 截图/PDF：playwright / npx / node / wkhtmltoimage / wkhtmltopdf
      - 视频/图片：ffmpeg / convert（ImageMagick）
      - 文档转换：libreoffice / soffice / pandoc
      - 图表：dot（graphviz）
    任何不在白名单的命令（rm/cat/curl/bash/sh/...）一律拒绝。
    """
    allowed = {
        "playwright", "npx", "node",
        "wkhtmltoimage", "wkhtmltopdf",
        "ffmpeg", "convert",
        "libreoffice", "soffice", "pandoc",
        "dot",
    }
    exe = Path(cmd[0]).name
    if exe not in allowed:
        return False, f"命令不在白名单: {exe}"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        return ok, (r.stdout or "")[:1000] + (("\n[STDERR]\n" + r.stderr[:1000]) if r.stderr and not ok else "")
    except subprocess.TimeoutExpired:
        return False, f"渲染超时（{timeout}s）"
    except Exception as e:
        log_suppressed(logger, e)
        return False, f"渲染失败: {type(e).__name__}"


def html_to_image(html: str, *, width: int = 1200, height: int = 900,
                  out_name: str = "") -> dict:
    """HTML 字符串 → PNG 截图。优先 wkhtmltoimage（轻量，无需 chromium），
    退到 playwright。默认关 / 工具缺失时优雅降级 / 永不抛错。"""
    if not render_enabled():
        return {"ok": False, "reason": "design render disabled (set HASHMM_DESIGN_RENDER=1)"}
    cap = capabilities()
    if not cap["html_to_image"]:
        return {"ok": False, "reason": "需要 wkhtmltoimage（apt install wkhtmltopdf）或 playwright"}

    rd = _render_dir()
    stamp = out_name or f"shot_{int(time.time()*1000)}"
    html_path = rd / f"{stamp}.html"
    png_path = rd / f"{stamp}.png"
    try:
        html_path.write_text(html, encoding="utf-8")
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "reason": "写入 HTML 失败"}

    # 优先 wkhtmltoimage（轻量、稳定、无需浏览器）
    wk = shutil.which("wkhtmltoimage")
    if wk:
        ok, msg = _run_whitelisted([wk, "--width", str(width), "--quality", "90",
                                    str(html_path), str(png_path)], timeout=60)
        if ok and png_path.exists():
            return {"ok": True, "image_path": str(png_path), "html_path": str(html_path),
                    "engine": "wkhtmltoimage"}
    # 退到 playwright（精确、支持现代 CSS/JS）
    pw_cli = shutil.which("playwright")
    if pw_cli:
        ok, msg = _run_whitelisted([pw_cli, "screenshot",
                                    f"file://{html_path}", str(png_path),
                                    "--viewport-size", f"{width},{height}"], timeout=60)
    else:
        ok, msg = _run_whitelisted(["npx", "playwright", "screenshot",
                                    f"file://{html_path}", str(png_path),
                                    "--viewport-size", f"{width},{height}"], timeout=90)
    if ok and png_path.exists():
        return {"ok": True, "image_path": str(png_path), "html_path": str(html_path)}
    return {"ok": False, "reason": msg or "截图未生成"}


def images_to_video(image_dir: str = "", *, fps: int = 25, pattern: str = "frame_%04d.png",
                    out_name: str = "") -> dict:
    """图片序列 → MP4（ffmpeg）。默认关 / 工具缺失优雅降级 / 永不抛错。

    image_dir 下需有按 pattern 命名的帧（如 frame_0001.png ...）。
    """
    if not render_enabled():
        return {"ok": False, "reason": "design render disabled (set HASHMM_DESIGN_RENDER=1)"}
    if not _tool_available("ffmpeg"):
        return {"ok": False, "reason": "需要 ffmpeg（apt install ffmpeg）"}

    rd = _render_dir()
    src = Path(image_dir) if image_dir else rd
    if not src.exists():
        return {"ok": False, "reason": f"帧目录不存在: {src}"}
    out = rd / (out_name or f"video_{int(time.time())}.mp4")
    ffmpeg = shutil.which("ffmpeg")
    ok, msg = _run_whitelisted([
        ffmpeg, "-y", "-framerate", str(fps),
        "-i", str(src / pattern),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ], timeout=120)
    if ok and out.exists():
        return {"ok": True, "video_path": str(out)}
    return {"ok": False, "reason": msg or "视频未生成"}


def office_to_image(office_path: str, *, out_name: str = "") -> dict:
    """PPTX/DOCX/PDF → 预览图（PNG）。用 libreoffice 先转 PDF，再转图片。
    让用户做完 PPT 能直接看到预览图。默认关 / 工具缺失优雅降级 / 永不抛错。"""
    if not render_enabled():
        return {"ok": False, "reason": "design render disabled (set HASHMM_DESIGN_RENDER=1)"}
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        return {"ok": False, "reason": "需要 libreoffice（apt install libreoffice）"}
    src = Path(office_path)
    if not src.exists():
        return {"ok": False, "reason": f"文件不存在: {src}"}
    rd = _render_dir()
    # libreoffice 转 PDF（headless）
    ok, msg = _run_whitelisted([soffice, "--headless", "--convert-to", "pdf",
                                "--outdir", str(rd), str(src)], timeout=90)
    pdf_path = rd / (src.stem + ".pdf")
    if not (ok and pdf_path.exists()):
        return {"ok": False, "reason": f"转 PDF 失败: {msg}"}
    result = {"ok": True, "pdf_path": str(pdf_path)}
    # 若有 pdftoppm / convert，再出首页预览图
    conv = shutil.which("convert")
    if conv:
        png = rd / ((out_name or src.stem) + "_preview.png")
        ok2, _ = _run_whitelisted([conv, "-density", "120", f"{pdf_path}[0]",
                                   "-quality", "90", str(png)], timeout=60)
        if ok2 and png.exists():
            result["preview_image"] = str(png)
    return result
