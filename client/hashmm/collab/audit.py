"""hashmm/collab/audit.py —— 协作审计留痕（V317）。

安全的最后一环是**可追溯**：谁在什么时候向谁请求了什么、结果如何、有没有触发脱敏，
全部落库。用户事后可以查"我的 agent 有没有被别人套过数据"。

存储：与 trust 同库（collab.sqlite3）的 audit_log 表。只追加、不修改（审计日志的基本要求）。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
import uuid
from pathlib import Path

__all__ = ["AuditLog", "get_audit_log"]


def _db_path() -> Path:
    try:
        from hashmm.api.database import DATA_ROOT
        base = Path(DATA_ROOT)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HASHMM_DATA_ROOT", "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "collab.sqlite3"


class AuditLog:
    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else _db_path()
        self._init_db()

    @contextlib.contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY, ts REAL,
                from_user TEXT, to_user TEXT, action TEXT,
                ok INTEGER, scope TEXT, detail TEXT)""")

    def record(self, from_user: str, to_user: str, action: str, ok: bool,
               detail: str = "", scope: str = "") -> str:
        """追加一条审计记录，返回 audit_id。永不抛错（审计失败不能拖垮协作）。"""
        aid = uuid.uuid4().hex[:16]
        try:
            with self._conn() as c:
                c.execute("INSERT INTO audit_log(id, ts, from_user, to_user, action, ok, scope, detail) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          (aid, time.time(), str(from_user), str(to_user), str(action),
                           1 if ok else 0, str(scope), str(detail)[:500]))
        except Exception:  # noqa: BLE001
            pass
        return aid

    def query(self, user_id: str = "", limit: int = 50) -> list[dict]:
        """查审计日志。user_id 非空时查与该用户相关的（作为发起方或接收方）。"""
        try:
            with self._conn() as c:
                if user_id:
                    rows = c.execute(
                        "SELECT * FROM audit_log WHERE from_user=? OR to_user=? "
                        "ORDER BY ts DESC LIMIT ?", (str(user_id), str(user_id), limit)).fetchall()
                else:
                    rows = c.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?",
                                     (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def incoming_data_access(self, user_id: str, limit: int = 50) -> list[dict]:
        """专门查"别人向我请求过什么"——用户自我审计的核心视图。"""
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM audit_log WHERE to_user=? AND action LIKE 'collab%' "
                    "ORDER BY ts DESC LIMIT ?", (str(user_id), limit)).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            return []


_LOG: AuditLog | None = None


def get_audit_log(db_path: Path | None = None) -> AuditLog:
    global _LOG
    if db_path is not None:
        return AuditLog(db_path)
    if _LOG is None:
        _LOG = AuditLog()
    return _LOG
