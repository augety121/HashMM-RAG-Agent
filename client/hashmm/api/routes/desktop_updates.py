"""hashmm/api/routes/desktop_updates.py — 桌面端自动更新分发（V81）。

Marvis 架构对应物：MarvisUpdate.exe 的服务端。HashMM 的闭环设计：
**你的后端就是更新服务器**——发新版 = 把 electron-builder 的产物
（latest.yml + HashMM-Setup-x.y.z.exe + .blockmap）放进 data/desktop-updates/，
所有已安装的桌面端下次启动自动检测、下载、提示安装。

electron-builder 侧配置（已随 V81 写入 electron-builder.yml）：
    publish: { provider: generic, url: "${后端}/desktop-updates" }

铁律：
- **默认关**：HASHMM_DESKTOP_UPDATES=1 才注册路由（未开 → 404、零暴露面）；
- **只读静态**：只 GET 不写；路径白名单（防目录穿越）；
- 永不抛错：缺文件 404，不影响主服务。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/desktop-updates", tags=["desktop-updates"])

# 允许的文件名模式（白名单，防目录穿越/任意读）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,120}$")
_ALLOWED_EXT = {".yml", ".yaml", ".exe", ".blockmap", ".zip", ".dmg", ".AppImage"}


def updates_enabled() -> bool:
    return os.environ.get("HASHMM_DESKTOP_UPDATES", "0").strip().lower() in {"1", "true", "yes", "on"}


def updates_dir() -> Path:
    return Path(os.environ.get("HASHMM_DESKTOP_UPDATES_DIR", "data/desktop-updates"))


def safe_update_file(name: str) -> Path | None:
    """白名单校验文件名并解析到更新目录内的真实路径；非法/越界/不存在 → None。

    纯函数（目录可注入via env），可单测。
    """
    name = str(name or "")
    if not _SAFE_NAME.match(name) or "/" in name or "\\" in name or ".." in name:
        return None
    if not any(name.endswith(ext) for ext in _ALLOWED_EXT):
        return None
    base = updates_dir().resolve()
    fp = (base / name).resolve()
    try:
        fp.relative_to(base)        # 越界防御（解析后仍须在目录内）
    except Exception:
        return None
    return fp if fp.is_file() else None


@router.get("")
@router.get("/")
async def updates_info():
    """探活/目录概览：列出可用更新文件名（仅元信息）。"""
    if not updates_enabled():
        return JSONResponse({"enabled": False, "hint": "set HASHMM_DESKTOP_UPDATES=1"}, status_code=404)
    d = updates_dir()
    files = []
    try:
        if d.is_dir():
            files = sorted(p.name for p in d.iterdir()
                           if p.is_file() and safe_update_file(p.name) is not None)
    except Exception:
        files = []
    return JSONResponse({"enabled": True, "files": files})


@router.get("/{name}")
async def updates_file(name: str):
    if not updates_enabled():
        return JSONResponse({"error": "disabled"}, status_code=404)
    fp = safe_update_file(name)
    if fp is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(fp))
