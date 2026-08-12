"""v17 Phase 86 — database backup / restore subsystem (data safety).

Production-grade data protection so a stray ``rm -rf data`` (or disk fault, bad
migration, fat-finger) can't lose the database again:

- **Consistent online snapshots** via SQLite's backup API (safe even while the
  app holds the DB open / WAL is active) — not a raw file copy.
- **Stored OUTSIDE the data dir** (default ``./backups``) so deleting ``data/``
  doesn't take the backups with it.
- **Rotation** (keep the newest N), **integrity check** (PRAGMA integrity_check),
  and **safe restore** (the current DB is itself backed up first, WAL sidecars
  cleared) — one command, reversible.
- CLI: ``python -m hashmm.api.backup {backup|restore|list|check} [...]``.
- ``auto_backup()`` is a one-call "snapshot + rotate" for a scheduler/cron.

Never throws on the read paths; restore raises only on a clearly bad request
(missing/ corrupt backup) so you don't silently overwrite a good DB with garbage.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from hashmm.utils import get_logger

logger = get_logger(__name__)

_PREFIX = "hashmm-"
_SUFFIX = ".sqlite"


def db_path() -> Path:
    explicit = os.environ.get("HASHMM_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    data_root = os.environ.get("HASHMM_DATA_DIR", "").strip()
    return ((Path(data_root).expanduser().resolve() if data_root else Path("data").resolve())
            / "hashmm.sqlite")


def backup_dir() -> Path:
    explicit = os.environ.get("HASHMM_BACKUP_DIR", "").strip()
    if explicit:
        d = Path(explicit).expanduser().resolve()
    else:
        data_root = os.environ.get("HASHMM_DATA_DIR", "").strip()
        d = (Path(data_root).expanduser().resolve() if data_root else Path("data").resolve()) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def integrity_check(path: str | Path | None = None) -> bool:
    """PRAGMA integrity_check == 'ok'. False on any problem (never raises)."""
    p = Path(path) if path else db_path()
    if not p.exists():
        return False
    try:
        conn = sqlite3.connect(str(p))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"integrity_check failed for {p}: {e}")
        return False


def backup_db(src: str | Path | None = None, dest_dir: str | Path | None = None,
              label: str = "") -> Path | None:
    """Take a consistent snapshot of the DB → backups/hashmm-<ts>[-label].sqlite.
    Uses the SQLite online backup API. Returns the path, or None on failure."""
    src_path = Path(src) if src else db_path()
    if not src_path.exists():
        logger.warning(f"backup_db: source DB not found: {src_path}")
        return None
    out_dir = Path(dest_dir) if dest_dir else backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(c for c in label if c.isalnum() or c in "-_")[:32]
    name = f"{_PREFIX}{_stamp()}{('-' + safe_label) if safe_label else ''}{_SUFFIX}"
    dest = out_dir / name
    try:
        sconn = sqlite3.connect(str(src_path))
        dconn = sqlite3.connect(str(dest))
        try:
            with dconn:
                sconn.backup(dconn)        # consistent online snapshot
        finally:
            sconn.close(); dconn.close()
        logger.info(f"DB backup written: {dest} ({dest.stat().st_size} bytes)")
        return dest
    except Exception as e:
        logger.error(f"backup_db failed: {e}")
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return None


@dataclass
class BackupInfo:
    path: str
    size: int
    mtime: float


def list_backups(dest_dir: str | Path | None = None) -> list[BackupInfo]:
    out_dir = Path(dest_dir) if dest_dir else backup_dir()
    items = []
    try:
        for p in out_dir.glob(f"{_PREFIX}*{_SUFFIX}"):
            st = p.stat()
            items.append(BackupInfo(str(p), st.st_size, st.st_mtime))
    except Exception as e:
        logger.warning(f"list_backups failed: {e}")
    items.sort(key=lambda b: b.mtime, reverse=True)   # newest first
    return items


def rotate(keep: int = 14, dest_dir: str | Path | None = None) -> int:
    """Keep the newest ``keep`` backups, delete the rest. Returns count removed."""
    keep = max(0, int(keep))
    backups = list_backups(dest_dir)
    removed = 0
    for b in backups[keep:]:
        try:
            Path(b.path).unlink(missing_ok=True)
            removed += 1
        except Exception as e:
            logger.warning(f"rotate: could not remove {b.path}: {e}")
    return removed


def auto_backup(keep: int = 14, label: str = "auto") -> Path | None:
    """Snapshot + rotate in one call (for a scheduler / cron)."""
    dest = backup_db(label=label)
    if dest:
        rotate(keep)
    return dest


def restore_db(backup_path: str | Path, target: str | Path | None = None,
               *, safety_backup: bool = True) -> bool:
    """Restore ``backup_path`` over the live DB. The current DB is snapshotted
    first (label 'prerestore') so the action is reversible, and stale WAL/SHM
    sidecars are cleared. **Stop the server before restoring.**

    Raises ValueError if the backup is missing or fails integrity check (so a
    good DB is never overwritten with a corrupt one)."""
    bpath = Path(backup_path)
    if not bpath.exists():
        raise ValueError(f"backup not found: {bpath}")
    if not integrity_check(bpath):
        raise ValueError(f"backup failed integrity check, refusing to restore: {bpath}")

    tgt = Path(target) if target else db_path()
    tgt.parent.mkdir(parents=True, exist_ok=True)

    if safety_backup and tgt.exists():
        backup_db(src=tgt, label="prerestore")

    try:
        shutil.copy2(str(bpath), str(tgt))
        # the snapshot is a standalone, checkpointed DB → drop stale sidecars
        for side in ("-wal", "-shm"):
            sp = Path(str(tgt) + side)
            if sp.exists():
                sp.unlink()
        logger.info(f"DB restored from {bpath} → {tgt}")
        return True
    except Exception as e:
        logger.error(f"restore_db failed: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="hashmm.api.backup", description="HashMM DB backup/restore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup").add_argument("--label", default="manual")
    sub.add_parser("list")
    sub.add_parser("check").add_argument("--path", default=None)
    pr = sub.add_parser("restore"); pr.add_argument("backup"); pr.add_argument("--no-safety", action="store_true")
    rt = sub.add_parser("rotate"); rt.add_argument("--keep", type=int, default=14)
    args = ap.parse_args(argv)

    if args.cmd == "backup":
        p = backup_db(label=args.label)
        print(p or "backup failed"); return 0 if p else 1
    if args.cmd == "list":
        for b in list_backups():
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(b.mtime))}  {b.size:>10}  {b.path}")
        return 0
    if args.cmd == "check":
        ok = integrity_check(args.path)
        print("ok" if ok else "FAILED"); return 0 if ok else 1
    if args.cmd == "restore":
        try:
            ok = restore_db(args.backup, safety_backup=not args.no_safety)
            print("restored" if ok else "restore failed"); return 0 if ok else 1
        except ValueError as e:
            print(f"refused: {e}"); return 2
    if args.cmd == "rotate":
        print(f"removed {rotate(args.keep)}"); return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
