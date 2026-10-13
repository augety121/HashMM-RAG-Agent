"""hashmm/agent/skill_catalog.py — V300 第四期：技能市场（Skill Marketplace）。

区别于 skill_packs.py（管理"已安装"的技能）：本模块提供"可安装"的技能目录——
浏览精选技能 → 一键安装。对标 Claude Code plugins 的市场体验。

内置精选技能来自 Anthropic 官方 skills 仓库（docx/pdf/xlsx/pptx/frontend-design/
mcp-builder/skill-creator 等），已适配本项目的 SKILL.md 规范与权限模型——
不是硬搬，安装后走本项目统一的技能加载/权限约束链路。

设计：目录即真相（catalog 文件夹下每个子目录一个技能），一键安装 = 复制进用户技能仓
（走 skill_packs.install_from_dir），安装后与自建/导入的技能同等对待。
"""
from __future__ import annotations

from pathlib import Path

from hashmm.utils import get_logger, log_suppressed
from hashmm.agent.skill_packs import parse_skill_md, get_skill_pack_manager

logger = get_logger("hashmm.agent.skill_catalog")

_SKILL_MD = "SKILL.md"


def catalog_dir() -> Path:
    """内置技能市场目录 <repo>/hashmm/skills/catalog（随包发布，桌面 sidecar 同带）。"""
    return Path(__file__).resolve().parents[1] / "skills" / "catalog"


def _read_catalog_entry(d: Path, installed_ids: set[str]) -> dict | None:
    """读一个市场条目的元数据（不复制、不加载，纯预览）。"""
    md = d / _SKILL_MD
    if not md.is_file():
        return None
    try:
        meta, body = parse_skill_md(md.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        log_suppressed(logger, e, "catalog.read")
        return None
    files = [p for p in d.rglob("*") if p.is_file()]
    name = str(meta.get("name") or d.name)
    return {
        "catalog_id": d.name,
        "name": name,
        "description": str(meta.get("description") or "")[:600],
        "license": str(meta.get("license") or ""),
        "allowed_tools": list(meta.get("allowed_tools") or []),
        "network": bool(meta.get("network", False)),
        "filesystem": bool(meta.get("filesystem", False)),
        "file_count": len(files),
        "size_bytes": sum(p.stat().st_size for p in files),
        # 是否已安装（用户技能仓里已有同名/同 slug）
        "installed": d.name in installed_ids or name in installed_ids,
        # 正文预览（前 300 字，供市场卡片展示"这个技能教什么"）
        "preview": body.strip()[:300],
    }


def list_catalog() -> list[dict]:
    """列出市场里全部可安装技能（标注哪些已安装）。永不抛错。"""
    root = catalog_dir()
    if not root.is_dir():
        return []
    # 已安装集合（按目录名与 name 双重匹配）
    installed_ids: set[str] = set()
    try:
        for p in get_skill_pack_manager().list_packs():
            installed_ids.add(p.id)
            installed_ids.add(p.name)
    except Exception as e:
        log_suppressed(logger, e, "catalog.installed_set")
    out: list[dict] = []
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir():
                entry = _read_catalog_entry(d, installed_ids)
                if entry:
                    out.append(entry)
    except Exception as e:
        log_suppressed(logger, e, "catalog.list")
    return out


def install_from_catalog(catalog_id: str) -> dict:
    """从市场一键安装一个技能到用户技能仓。返回 {ok, message, pack?}。永不抛错。"""
    root = catalog_dir()
    src = root / catalog_id
    if not src.is_dir() or not (src / _SKILL_MD).is_file():
        return {"ok": False, "message": f"市场里没有找到技能：{catalog_id}"}
    try:
        pack = get_skill_pack_manager().install_from_dir(src, source=f"catalog:{catalog_id}")
        return {"ok": True, "message": f"已安装技能「{pack.name}」", "pack": pack.to_dict()}
    except Exception as e:
        log_suppressed(logger, e, "catalog.install")
        return {"ok": False, "message": f"安装失败：{e}"}


def catalog_stats() -> dict:
    """市场概览：可用数 / 已安装数。"""
    items = list_catalog()
    return {"total": len(items), "installed": sum(1 for x in items if x.get("installed"))}
