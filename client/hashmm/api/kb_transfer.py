"""kb_transfer — 知识库/数据迁移（V91）。

场景：用户在 autodl（远程模式）积累的语料、向量索引、知识图谱、技能、模板，
一键导出 zip，再导入到桌面本地模式（或任何另一套 HashMM）。

设计要点：
- 白名单打包：只动知识库相关项，conversations/workspace 等不掺和；
- sqlite 用 backup API 拍快照再入包（服务运行中直接拷文件可能撕裂）；
- 导入有三道防线：zip-slip 路径穿越拦截、仅白名单顶层成员、导入前
  把现有数据移到 data/_backup-<ts>/ 可随时回滚；
- 纯标准库（zipfile/sqlite3/shutil），零新依赖；纯函数可单测。
"""
from __future__ import annotations

import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

# 导出/导入的白名单（data/ 下的顶层项；存在才打包）
EXPORT_ITEMS = ["hashmm.sqlite", "vector_index", "files", "uploads", "kg", "skills"]


def build_export_zip(data_dir: Path, out_path: Path) -> dict:
    """把 data_dir 下白名单内容打成 zip 快照，返回摘要。"""
    data_dir = Path(data_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in EXPORT_ITEMS:
            src = data_dir / item
            if not src.exists():
                continue
            if src.is_file():
                if item == "hashmm.sqlite":
                    snap = out_path.parent / f".snap-{int(time.time())}.sqlite"
                    try:
                        _sqlite_snapshot(src, snap)
                        zf.write(snap, arcname=item)
                    finally:
                        snap.unlink(missing_ok=True)
                else:
                    zf.write(src, arcname=item)
                counts[item] = 1
            else:
                n = 0
                for p in sorted(src.rglob("*")):
                    if p.is_file():
                        zf.write(p, arcname=str(p.relative_to(data_dir)))
                        n += 1
                counts[item] = n
        zf.writestr("_hashmm_export.json",
                    '{"format": 1, "exported_at": %d}' % int(time.time()))
    return {"path": str(out_path), "items": counts,
            "size": out_path.stat().st_size}


def _sqlite_snapshot(src: Path, dest: Path) -> None:
    """运行中安全拍快照（sqlite backup API，WAL/journal 一致性由引擎保证）。"""
    with sqlite3.connect(str(src)) as conn, sqlite3.connect(str(dest)) as out:
        conn.backup(out)


def inspect_zip(zip_path: Path) -> dict:
    """导入前校验：必须是 HashMM 导出包、所有成员都在白名单顶层、无路径穿越。"""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "_hashmm_export.json" not in names:
            return {"ok": False, "error": "不是 HashMM 导出包（缺标记文件）"}
        tops: set[str] = set()
        for n in names:
            norm = n.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/"):
                return {"ok": False, "error": f"包内存在非法路径：{n}"}
            top = norm.split("/")[0]
            if top != "_hashmm_export.json":
                if top not in EXPORT_ITEMS:
                    return {"ok": False, "error": f"包内存在白名单外内容：{top}"}
                tops.add(top)
        return {"ok": True, "items": sorted(tops), "files": len(names) - 1}


def apply_import_zip(data_dir: Path, zip_path: Path) -> dict:
    """导入：先把现有白名单项移入 data/_backup-<ts>/，再解包。失败时尽量回滚。"""
    data_dir = Path(data_dir)
    chk = inspect_zip(zip_path)
    if not chk.get("ok"):
        return chk
    data_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = data_dir / f"_backup-{ts}"
    moved: list[str] = []
    try:
        for item in chk["items"]:
            src = data_dir / item
            if src.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(backup / item))
                moved.append(item)
        with zipfile.ZipFile(zip_path) as zf:
            for n in zf.namelist():
                if n == "_hashmm_export.json":
                    continue
                # inspect 已拦穿越；这里再 resolve 双保险
                dest = (data_dir / n.replace("\\", "/")).resolve()
                if not str(dest).startswith(str(data_dir.resolve())):
                    raise RuntimeError(f"非法路径 {n}")
                zf.extract(n, data_dir)
        return {"ok": True, "items": chk["items"], "files": chk["files"],
                "backup": str(backup) if moved else "",
                "needs_restart": True,
                "message": "导入完成。请重启本地后端（或刷新远程服务）以加载新数据；"
                           "原数据已备份到 " + (str(backup) if moved else "（原为空，无备份）")}
    except Exception as e:  # 回滚
        for item in moved:
            try:
                shutil.move(str(backup / item), str(data_dir / item))
            except Exception:
                pass
        return {"ok": False, "error": f"导入失败已回滚：{e}"}
