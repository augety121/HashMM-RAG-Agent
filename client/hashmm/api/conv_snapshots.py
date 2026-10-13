"""hashmm/api/conv_snapshots.py — 会话工作区文件快照（V273）。

对照资料 13.3.5 的两条路线：Pi 内核只回退对话、文件靠 git 扩展；**Claude Code 的
回退内核自带文件快照，把对话和文件一起还原**——用户点名要 CC 路线。本模块即
"会话节点 → 文件快照"的那张映射表：每次 assistant 回答开始前，对该会话的文件
工作区打一份快照（tag=assistant 消息 id）；回退时按 tag 还原。

护栏：单快照 ≤20MB（超限跳过并留痕，不阻塞回答）；每会话仅保留最近 12 份；
所有操作永不抛错（快照失败绝不影响对话）。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.conv_snapshots")

_MAX_BYTES = 20 * 1024 * 1024
_KEEP = 12


def _roots():
    from hashmm.api.database import DATA_ROOT, CONV_FILES_ROOT
    snap_root = Path(DATA_ROOT) / "conv_snapshots"
    snap_root.mkdir(parents=True, exist_ok=True)
    return Path(CONV_FILES_ROOT), snap_root


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:  # noqa: BLE001
        return 0


def snapshot(conv_id: str, tag: str) -> bool:
    """给会话工作区打快照。空工作区也落一个空目录（语义：回退=清空）。"""
    try:
        files_root, snap_root = _roots()
        src = files_root / str(conv_id)
        dst = snap_root / str(conv_id) / str(tag)
        if dst.exists():
            return True
        if src.exists() and _dir_size(src) > _MAX_BYTES:
            logger.info("[snap] %s 超过 20MB，跳过快照", conv_id)
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
        # 只留最近 _KEEP 份
        sibs = sorted(dst.parent.iterdir(), key=lambda p: p.stat().st_mtime)
        for old in sibs[:-_KEEP]:
            shutil.rmtree(old, ignore_errors=True)
        return True
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return False


def restore(conv_id: str, tag: str) -> bool:
    """把工作区还原到 tag 时刻（先清空再拷回；无该快照返回 False）。"""
    try:
        files_root, snap_root = _roots()
        src = snap_root / str(conv_id) / str(tag)
        if not src.exists():
            return False
        dst = files_root / str(conv_id)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        return True
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return False


def list_tags(conv_id: str) -> list[dict]:
    try:
        _, snap_root = _roots()
        d = snap_root / str(conv_id)
        if not d.exists():
            return []
        return [{"tag": p.name, "ts": p.stat().st_mtime, "files": sum(1 for _ in p.rglob("*") if _.is_file())}
                for p in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime)]
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return []
