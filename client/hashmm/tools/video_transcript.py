"""video_transcript — 视频链接拿字幕（V267，思路来自 github.com/Panniantong/Agent-Reach）。

用户贴一个 YouTube / B站 视频链接问"讲了什么"，Agent 此前抓瞎（fetch_url 拿到的是
播放器页面骨架）。本工具用 yt-dlp 只取字幕（不下视频），转成干净文本供总结：
  - YouTube：优先人工字幕，退自动字幕（zh/en）
  - B 站：CC 字幕
  - 拿不到字幕时如实报告（绝不编造视频内容）

依赖 yt-dlp（启动脚本已加自动安装）；无依赖/无字幕/网络失败都返回可读错误，不抛。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.video")

_VIDEO_URL = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/watch\?[^ ]*v=|youtu\.be/|bilibili\.com/video/)[\w\-?=&/%.]+",
    re.I)


def looks_like_video_url(text: str) -> str | None:
    """从文本里找视频链接；找到返回 URL，否则 None。"""
    m = _VIDEO_URL.search(text or "")
    return m.group(0) if m else None


def _vtt_to_text(vtt: str) -> str:
    """WebVTT → 纯文本：去时间轴/标签/重复行。"""
    lines: list[str] = []
    last = ""
    for raw in vtt.splitlines():
        s = raw.strip()
        if not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if "-->" in s or re.fullmatch(r"\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s)          # <c> 标签
        s = re.sub(r"\s+", " ", s).strip()
        if s and s != last:                      # 自动字幕滚动重复
            lines.append(s)
            last = s
    return "\n".join(lines)


def execute(args: dict, ctx: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return "Error: 缺少 url 参数"
    if not _VIDEO_URL.search(url):
        return "Error: 不是支持的视频链接（支持 YouTube / B站视频页）"
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return ("Error: 服务器缺少 yt-dlp。重启后端即可（启动脚本已自动安装），"
                "或手动: pip install yt-dlp")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "sub"
        cmd = [
            "yt-dlp", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "zh.*,zh-Hans,zh-CN,en.*,en",
            "--sub-format", "vtt",
            "-o", str(out),
            "--no-playlist", "--quiet", "--no-warnings",
            url,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return "Error: 拉取字幕超时（90s），可能是网络问题或视频区域限制"
        except FileNotFoundError:
            return "Error: 服务器缺少 yt-dlp 可执行文件。重启后端即可（启动脚本已自动安装）"

        vtts = sorted(Path(td).glob("sub*.vtt"))
        if not vtts:
            err = (r.stderr or "").strip().splitlines()
            tail = err[-1][:160] if err else ""
            return ("Error: 该视频没有可用字幕（人工/自动都没有）。"
                    "只能拿到字幕的视频才能总结，无法凭空得知内容。" + (f"（{tail}）" if tail else ""))
        # 中文优先
        def _rank(p: Path) -> int:
            n = p.name.lower()
            return 0 if ("zh" in n) else 1
        pick = sorted(vtts, key=_rank)[0]
        text = _vtt_to_text(pick.read_text(encoding="utf-8", errors="ignore"))
        if len(text) < 40:
            return "Error: 字幕内容为空或过短，无法用于总结"
        lang = "中文" if "zh" in pick.name.lower() else "英文"

        # 标题（尽力而为）
        title = ""
        try:
            rt = subprocess.run(["yt-dlp", "--skip-download", "--print", "%(title)s",
                                 "--no-playlist", "--quiet", url],
                                capture_output=True, text=True, timeout=30)
            title = (rt.stdout or "").strip().splitlines()[0][:120] if rt.stdout else ""
        except Exception:
            pass

        head = f"【视频字幕｜{lang}】" + (f"《{title}》\n" if title else "\n")
        clipped = text[:12000]
        note = "" if len(text) <= 12000 else f"\n\n（字幕共 {len(text)} 字，已截取前 12000 字）"
        return head + clipped + note


# 工具 schema（注入 LLM function-calling）
SCHEMA = {
    "type": "function",
    "function": {
        "name": "video_transcript",
        "description": "获取 YouTube / B站 视频的字幕文本（不下载视频）。用户贴视频链接问"
                       "\"讲了什么/总结一下\"时用这个，拿到字幕后据此回答；没有字幕会明确报错，"
                       "此时如实告知用户拿不到内容，绝不编造。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "视频页链接"}},
            "required": ["url"],
        },
    },
}
