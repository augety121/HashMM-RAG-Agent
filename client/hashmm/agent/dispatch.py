"""远端派活队列（V205 P2-9，对齐 Qoder「一次派活云上闭环」的调度底座）。

模型：后端是唯一队列真源（SQLite）；任意「runner」（另一台桌面客户端 / 服务器
worker）用自己的名字轮询认领任务、跑完回填结果。协议四步：

    POST /api/dispatch                 {runner, kind, payload}      → {task_id}
    GET  /api/dispatch/poll?runner=X   认领最早一条 pending（原子置 claimed）
    POST /api/dispatch/{id}/complete   {ok, result}                 → 置 done/failed
    GET  /api/dispatch/{id}            查询状态/结果（发起方轮询或 UI 展示）

超时自愈：claimed 超过 HASHMM_DISPATCH_TIMEOUT（默认 600s）自动回 pending，
防 runner 掉线后任务卡死。与「接力」的关系：接力=会话交接给指定设备继续；
派活=离散任务丢进队列由任意可用 runner 消化，互补不重叠。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.dispatch")

_LOCK = threading.Lock()


def _timeout_s() -> int:
    try:
        return int(os.environ.get("HASHMM_DISPATCH_TIMEOUT", "600") or "600")
    except Exception:
        return 600


def _db() -> sqlite3.Connection:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    p = Path(d).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p / "dispatch.db"), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY, runner TEXT, kind TEXT, payload TEXT,
        status TEXT, result TEXT, created_by TEXT,
        created REAL, claimed_at REAL, done_at REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_task_runner ON tasks(runner, status)")
    # V208 runner 心跳：poll 即心跳（零新增请求），last_seen=最近轮询，last_claim=最近认领
    c.execute("""CREATE TABLE IF NOT EXISTS runners(
        name TEXT PRIMARY KEY, last_seen REAL, last_claim REAL)""")
    # V375: a queue name is not a device identity.  The legacy ``runners``
    # table collapses every user's desktop into one global row, which both
    # leaks another tenant's online state and makes the App show a false
    # positive/negative.  Presence is now scoped by the authenticated account
    # and a stable desktop device id; the old table remains for older clients.
    c.execute("""CREATE TABLE IF NOT EXISTS runner_presence(
        owner TEXT NOT NULL,
        device_id TEXT NOT NULL,
        runner TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        app_version TEXT NOT NULL DEFAULT '',
        last_seen REAL,
        last_claim REAL,
        PRIMARY KEY(owner, device_id))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_runner_presence_owner_seen "
              "ON runner_presence(owner, last_seen DESC)")
    return c


def _requeue_stale(c: sqlite3.Connection) -> None:
    """claimed 超时自动回 pending（runner 掉线自愈）。"""
    try:
        cutoff = time.time() - _timeout_s()
        c.execute("UPDATE tasks SET status='pending', claimed_at=NULL "
                  "WHERE status='claimed' AND claimed_at < ?", (cutoff,))
    except Exception:
        pass


def create_task(runner: str, kind: str, payload: dict, created_by: str = "") -> str:
    tid = uuid.uuid4().hex[:16]
    with _LOCK, _db() as c:
        c.execute("INSERT INTO tasks(id, runner, kind, payload, status, result, created_by, created) "
                  "VALUES(?,?,?,?,'pending','',?,?)",
                  (tid, (runner or "default").strip(), (kind or "task").strip(),
                   json.dumps(payload or {}, ensure_ascii=False)[:20000],
                   created_by, time.time()))
    logger.info(f"dispatch created: {tid} runner={runner} kind={kind}")
    return tid


def poll(
    runner: str,
    created_by: str | None = None,
    *,
    presence_owner: str = "",
    device_id: str = "",
    display_name: str = "",
    app_version: str = "",
) -> dict | None:
    """runner 认领最早一条 pending（原子）。无任务返回 None。"""
    name = (runner or "default").strip()
    try:
        with _LOCK, _db() as c:
            _requeue_stale(c)
            now = time.time()
            c.execute("INSERT INTO runners(name, last_seen, last_claim) VALUES(?,?,NULL) "
                      "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen", (name, now))
            owner_key = str(presence_owner or "").strip()[:160]
            device_key = str(device_id or f"legacy:{name}").strip()[:120]
            if owner_key and device_key:
                c.execute(
                    "INSERT INTO runner_presence(owner,device_id,runner,display_name,app_version,last_seen,last_claim) "
                    "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(owner,device_id) DO UPDATE SET "
                    "runner=excluded.runner,display_name=excluded.display_name,"
                    "app_version=excluded.app_version,last_seen=excluded.last_seen",
                    (owner_key, device_key, name, str(display_name or "")[:120],
                     str(app_version or "")[:40], now),
                )
            if created_by is None:
                row = c.execute("SELECT id, kind, payload FROM tasks "
                                "WHERE runner=? AND status='pending' "
                                "ORDER BY created LIMIT 1", (name,)).fetchone()
            else:
                row = c.execute("SELECT id, kind, payload FROM tasks "
                                "WHERE runner=? AND created_by=? AND status='pending' "
                                "ORDER BY created LIMIT 1", (name, created_by)).fetchone()
            if not row:
                return None
            claimed = c.execute(
                "UPDATE tasks SET status='claimed', claimed_at=? WHERE id=? AND status='pending'",
                (now, row[0]),
            )
            if claimed.rowcount == 0:
                return None  # another process won the atomic claim
            c.execute("UPDATE runners SET last_claim=? WHERE name=?", (now, name))
            if owner_key and device_key:
                c.execute(
                    "UPDATE runner_presence SET last_claim=? WHERE owner=? AND device_id=?",
                    (now, owner_key, device_key),
                )
        try:
            payload = json.loads(row[2] or "{}")
        except Exception:
            payload = {}
        return {"task_id": row[0], "kind": row[1], "payload": payload}
    except Exception as e:
        log_suppressed(logger, e, "dispatch.poll")
        return None


def complete(task_id: str, ok: bool, result: str = "") -> bool:
    try:
        with _LOCK, _db() as c:
            cur = c.execute("UPDATE tasks SET status=?, result=?, done_at=? "
                            "WHERE id=? AND status IN ('claimed','pending')",
                            ("done" if ok else "failed", (result or "")[:20000], time.time(), task_id))
            return cur.rowcount > 0
    except Exception as e:
        log_suppressed(logger, e, "dispatch.complete")
        return False


def heartbeat(task_id: str) -> bool:
    """Renew a claimed task lease so long-running desktop work is not duplicated."""
    try:
        with _LOCK, _db() as c:
            cur = c.execute(
                "UPDATE tasks SET claimed_at=? WHERE id=? AND status='claimed'",
                (time.time(), task_id),
            )
            return cur.rowcount > 0
    except Exception as e:
        log_suppressed(logger, e, "dispatch.heartbeat")
        return False


def release_claim(task_id: str, *, created_by: str) -> bool:
    """Return a just-claimed task to its owner queue without exposing payload."""
    try:
        with _LOCK, _db() as c:
            cur = c.execute(
                "UPDATE tasks SET status='pending',claimed_at=NULL "
                "WHERE id=? AND created_by=? AND status='claimed'",
                (str(task_id or ""), str(created_by or "")),
            )
            return cur.rowcount == 1
    except Exception as e:
        log_suppressed(logger, e, "dispatch.release_claim")
        return False


def touch_runner(*, owner: str, device_id: str, runner: str = "desktop",
                 display_name: str = "", app_version: str = "") -> bool:
    """Renew account-scoped device presence without claiming another task."""
    owner_key = str(owner or "").strip()[:160]
    device_key = str(device_id or "").strip()[:120]
    if not owner_key or not device_key:
        return False
    name = str(runner or "desktop").strip()[:80]
    try:
        with _LOCK, _db() as c:
            now = time.time()
            c.execute(
                "INSERT INTO runner_presence(owner,device_id,runner,display_name,app_version,last_seen,last_claim) "
                "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(owner,device_id) DO UPDATE SET "
                "runner=excluded.runner,display_name=excluded.display_name,"
                "app_version=excluded.app_version,last_seen=excluded.last_seen",
                (owner_key, device_key, name, str(display_name or "")[:120],
                 str(app_version or "")[:40], now),
            )
        return True
    except Exception as e:
        log_suppressed(logger, e, "dispatch.touch_runner")
        return False


def get_task(task_id: str) -> dict | None:
    try:
        with _db() as c:
            r = c.execute("SELECT id, runner, kind, payload, status, result, created, claimed_at, done_at, created_by "
                          "FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return None
        try:
            payload = json.loads(r[3] or "{}")
        except Exception:
            payload = {}
        return {"task_id": r[0], "runner": r[1], "kind": r[2], "payload": payload,
                "status": r[4], "result": r[5], "created": r[6],
                "claimed_at": r[7], "done_at": r[8], "created_by": r[9] or ""}
    except Exception:
        return None


def list_tasks(limit: int = 50, created_by: str | None = None) -> list[dict]:
    try:
        with _db() as c:
            _requeue_stale(c)
            if created_by is None:
                rows = c.execute("SELECT id, runner, kind, status, created, done_at, "
                                 "substr(result, 1, 300) FROM tasks "
                                 "ORDER BY created DESC LIMIT ?", (int(limit),)).fetchall()
            else:
                rows = c.execute("SELECT id, runner, kind, status, created, done_at, "
                                 "substr(result, 1, 300) FROM tasks "
                                 "WHERE created_by=? ORDER BY created DESC LIMIT ?",
                                 (created_by, int(limit))).fetchall()
        return [{"task_id": r[0], "runner": r[1], "kind": r[2], "status": r[3],
                 "created": r[4], "done_at": r[5], "result": r[6] or ""} for r in rows]
    except Exception:
        return []


def runners_status(owner: str | None = None, created_by: str | None = None) -> list[dict]:
    """所有 runner 的心跳与今日执行数。online tolerates two task heartbeats."""
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    out: list[dict] = []
    try:
        with _db() as c:
            owner_key = str(owner or "").strip()[:160]
            if owner_key:
                rows = c.execute(
                    "SELECT device_id,runner,display_name,app_version,last_seen,last_claim "
                    "FROM runner_presence WHERE owner=? ORDER BY last_seen DESC LIMIT 20",
                    (owner_key,),
                ).fetchall()
                for device, runner, label, version, seen, claim in rows:
                    suffix = " AND created_by=?" if created_by is not None else ""
                    args = (runner, midnight, created_by) if created_by is not None else (runner, midnight)
                    done = c.execute(
                        "SELECT COUNT(*) FROM tasks WHERE runner=? AND status='done' AND done_at>=?" + suffix,
                        args,
                    ).fetchone()[0]
                    failed = c.execute(
                        "SELECT COUNT(*) FROM tasks WHERE runner=? AND status='failed' AND done_at>=?" + suffix,
                        args,
                    ).fetchone()[0]
                    out.append({
                        "id": device,
                        "device_id": device,
                        "name": label or runner,
                        "runner": runner,
                        "version": version,
                        "online": bool(seen and now - seen < 35),
                        "last_seen": seen,
                        "last_claim": claim,
                        "today_done": int(done),
                        "today_failed": int(failed),
                    })
            else:
                rows = c.execute("SELECT name, last_seen, last_claim FROM runners "
                                 "ORDER BY last_seen DESC LIMIT 20").fetchall()
                for name, seen, claim in rows:
                    done = c.execute("SELECT COUNT(*) FROM tasks WHERE runner=? AND status='done' "
                                     "AND done_at>=?", (name, midnight)).fetchone()[0]
                    failed = c.execute("SELECT COUNT(*) FROM tasks WHERE runner=? AND status='failed' "
                                       "AND done_at>=?", (name, midnight)).fetchone()[0]
                    out.append({"name": name, "runner": name,
                                "online": bool(seen and now - seen < 35),
                                "last_seen": seen, "last_claim": claim,
                                "today_done": int(done), "today_failed": int(failed)})
    except Exception as e:
        log_suppressed(logger, e, "dispatch.runners_status")
    return out


def stats() -> dict:
    try:
        with _db() as c:
            rows = c.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
        return {s: int(n) for s, n in rows}
    except Exception:
        return {}
