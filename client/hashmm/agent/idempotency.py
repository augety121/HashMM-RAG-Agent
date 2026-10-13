"""hashmm/agent/idempotency.py — V300 第二期：写操作幂等 + 任务事务日志。

为什么要它（可靠性工程）：
  · 幂等键：Agent 重试时，同一个"写操作"（相同内容写同一文件、发同一封邮件）不该重复执行。
    给每个写操作生成基于内容的幂等键，持久化"已执行"标记；重试遇到相同键直接跳过，
    避免重复创建/发送（大厂 Agent 敢重试的前提）。
  · 事务日志：把一次任务的"每个有副作用的步骤 + 结果"记成结构化事务，支持事后回放与"做了哪些改动"审计。

设计（与项目风格一致）：纯标准库、永不抛错、SQLite 持久化（幂等键要跨进程/跨重试存活）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.idempotency")

# Runtime configuration is immutable after process start. Resolving the ledger
# root for every SQL statement allowed an environment refresh to reserve in
# one database and commit in another, even though the write had already run.
# Bind the root once per module load; isolated tests deliberately reload the
# module after selecting their temporary data directory.
_DATA_ROOT = Path(os.environ.get("HASHMM_DATA_DIR", "data")).resolve()


class IdempotencyUnavailable(RuntimeError):
    """The side-effect ledger could not prove a reservation decision.

    A write tool must not continue when the ledger is unavailable: returning
    ``None`` is indistinguishable from a fresh reservation and would silently
    turn an infrastructure failure into a duplicate side effect.
    """

    code = "idempotency_unavailable"


class IdempotencyCommitUnavailable(IdempotencyUnavailable):
    """The side-effect result could not be durably committed.

    The external write may already have happened. Callers must surface this
    as an uncertain outcome and must not automatically retry the operation.
    """

    code = "idempotency_commit_unavailable"

_TTL_SECONDS = 30 * 60   # 幂等键有效期 30 分钟。幂等是为了防【同一任务内的重试】重复写，
                         # 不是长期缓存；24h 过长会让“删文件→重建”在一天内一直命中旧结果。
                         # 且命中时另有副作用验证（见 loop._idem_verify_side_effect）双保险。


def _db_path() -> Path:
    d = _DATA_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d / "idempotency.db"


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()), timeout=5)
    # V306：WAL + busy_timeout —— 提升并发读写吞吐，减少"database is locked"（大厂级并发下必需）。
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS idem(
        key TEXT PRIMARY KEY, op TEXT, result TEXT, created REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS txlog(
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, step INTEGER,
        op TEXT, target TEXT, ok INTEGER, detail TEXT, created REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tx_task ON txlog(task_id)")
    return c


def make_key(op: str, *parts: object) -> str:
    """基于操作类型 + 关键参数生成稳定幂等键。相同语义的写操作 → 相同键。"""
    raw = op + "|" + "|".join(str(p) for p in parts)
    return op + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def check_and_reserve(key: str, op: str = "") -> dict | None:
    """检查幂等键：已存在且未过期 → 返回上次结果 dict（调用方应跳过执行）；
    不存在/已过期 → 占位并返回 None（调用方应执行，完成后 commit）。永不抛错。

    V306 修并发重复执行（大厂级压测暴露）：旧实现 SELECT→INSERT 非原子，并发抢同一键时
      · 新键：偶发两个线程都拿到执行权（TOCTOU）；
      · 过期键：**所有并发线程都判"过期"并全部重复执行**（缓存过期踩踏，最严重）。
    现用 `BEGIN IMMEDIATE` 在读之前先取写锁 → 保留操作对同一库严格互斥（其余线程排队，
    WAL 下并发读不受影响），SELECT→写 成为原子临界区 → 严格 exactly-once。
    """
    c = None
    try:
        now = time.time()
        c = sqlite3.connect(str(_db_path()), timeout=5, isolation_level=None)  # 手动事务
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        c.execute("""CREATE TABLE IF NOT EXISTS idem(
            key TEXT PRIMARY KEY, op TEXT, result TEXT, created REAL)""")
        c.execute("BEGIN IMMEDIATE")   # 立刻取写锁 → 保留操作互斥
        try:
            row = c.execute("SELECT result, created FROM idem WHERE key=?", (key,)).fetchone()
            if row and (now - row[1]) < _TTL_SECONDS:
                c.execute("COMMIT")
                try:
                    return {"hit": True, "result": json.loads(row[0]) if row[0] else None}
                except Exception:
                    return {"hit": True, "result": None}
            # 不存在或过期 → 占位/刷新（此刻独占写锁，无并发）→ 唯一拿到执行权
            c.execute("INSERT OR REPLACE INTO idem(key, op, result, created) VALUES(?,?,?,?)",
                      (key, op, "", now))
            c.execute("COMMIT")
            return None
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
    except Exception as e:
        log_suppressed(logger, e, "idempotency.check")
        raise IdempotencyUnavailable(
            "idempotency ledger unavailable; refusing side-effect execution"
        ) from e
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def commit(key: str, result: object) -> None:
    """写操作成功后回填结果，供后续重试命中。永不抛错。"""
    try:
        with _db() as c:
            cur = c.execute(
                "UPDATE idem SET result=?, created=? WHERE key=?",
                (json.dumps(result, ensure_ascii=False)[:4000], time.time(), key),
            )
            if int(cur.rowcount or 0) != 1:
                raise IdempotencyCommitUnavailable(
                    "idempotency reservation disappeared before commit"
                )
    except IdempotencyCommitUnavailable:
        raise
    except Exception as e:
        log_suppressed(logger, e, "idempotency.commit")
        raise IdempotencyCommitUnavailable(
            "idempotency result could not be durably committed"
        ) from e


def invalidate(key: str) -> None:
    """V308：作废一条幂等记录（无条件删除，不管是否已 commit）。

    与 release 的区别：release 只删【未完成的占位】（result 为空）；invalidate 删整条，
    用于“缓存曾成功、但副作用已不存在（文件被删）”——此时必须让下次调用重新真正执行。
    永不抛错。
    """
    try:
        with _db() as c:
            c.execute("DELETE FROM idem WHERE key=?", (key,))
    except Exception as e:
        log_suppressed(logger, e, "idempotency.invalidate")


def release(key: str) -> None:
    """写操作失败时释放占位（让下次重试能重新执行）。永不抛错。"""
    try:
        with _db() as c:
            c.execute("DELETE FROM idem WHERE key=? AND (result='' OR result IS NULL)", (key,))
    except Exception as e:
        log_suppressed(logger, e, "idempotency.release")


# ── 事务日志 ──────────────────────────────────────────────────────────
def log_step(task_id: str, step: int, op: str, target: str, ok: bool, detail: str = "") -> None:
    """记一条有副作用步骤的事务日志。永不抛错。"""
    try:
        with _db() as c:
            c.execute("INSERT INTO txlog(task_id, step, op, target, ok, detail, created) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (str(task_id), int(step), op, str(target)[:300], 1 if ok else 0,
                       str(detail)[:500], time.time()))
    except Exception as e:
        log_suppressed(logger, e, "idempotency.log_step")


def get_transaction(task_id: str) -> list[dict]:
    """取某任务的完整事务日志（按步骤序），用于回放/审计"做了哪些改动"。"""
    try:
        with _db() as c:
            rows = c.execute("SELECT step, op, target, ok, detail, created FROM txlog "
                             "WHERE task_id=? ORDER BY step, id", (str(task_id),)).fetchall()
        return [{"step": r[0], "op": r[1], "target": r[2], "ok": bool(r[3]),
                 "detail": r[4], "created": r[5]} for r in rows]
    except Exception as e:
        log_suppressed(logger, e, "idempotency.get_transaction")
        return []
