"""Durable, owner-bound lifecycle records for main and delegated agents.

AgentSession is the execution identity shared by Worker, Team, WorkRuntime and
cross-device clients.  The database is authoritative; the process-local map is
only a bounded read-through cache.  A process restart never pretends an
in-flight model or tool call is still running: stale rows are reconciled to an
explicit ``interrupted`` state and external side effects are never replayed.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from typing import Any


CONTRACT = "hashmm.agent-session.v2"
_TERMINAL = {"completed", "failed", "stopped", "interrupted", "orphaned"}
_ACTIVE = {"queued", "running", "waiting_input", "waiting_approval", "paused", "stopping"}
_STATUSES = _ACTIVE | _TERMINAL


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dump(value: Any, limit: int = 64_000) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        encoded = "{}"
    if len(encoded) <= limit:
        return encoded
    summary = ""
    if isinstance(value, dict):
        summary = _clip(value.get("summary") or value.get("status") or "", 2_000)
    return json.dumps({"truncated": True, "summary": summary}, ensure_ascii=False)


def _load(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return copy.deepcopy(default)


class AgentSessionRegistry:
    """Durable lifecycle store with a bounded in-memory projection.

    ``persist=False`` is useful for isolated unit instances.  The process-wide
    registry is persistent and uses the same owner-scoped SQLite database as
    WorkRuntime so tests and deployments share one transaction boundary.
    """

    def __init__(self, max_records: int = 512, *, persist: bool = False):
        self.max_records = max(32, int(max_records))
        self.persist = bool(persist)
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._ready = False

    def _conn(self):
        from hashmm.api import database as db
        return db._conn()

    def _ensure_ready_locked(self) -> None:
        if not self.persist or self._ready:
            return
        recovered: list[dict[str, Any]] = []
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    contract TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL DEFAULT '',
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    task TEXT NOT NULL DEFAULT '',
                    scope_id TEXT NOT NULL DEFAULT '',
                    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    work_run_id TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    generation INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    started_at REAL NOT NULL DEFAULT 0,
                    finished_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_sessions_owner_updated "
                "ON agent_sessions(owner_id, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent "
                "ON agent_sessions(owner_id, parent_session_id, created_at)"
            )
            # A previous process cannot still own an in-memory model/tool call.
            # Record the interruption truthfully; a future scheduler may create
            # a new generation from a checkpoint, but must not reuse this one.
            now = time.time()
            rows = conn.execute(
                "SELECT session_id,result_json FROM agent_sessions "
                "WHERE status IN ('queued','running','stopping')"
            ).fetchall()
            for row in rows:
                result = _load(row["result_json"], {})
                if not isinstance(result, dict):
                    result = {}
                result.update({
                    "recovery_reason": "process_restarted",
                    "safe_to_replay_side_effects": False,
                })
                conn.execute(
                    "UPDATE agent_sessions SET status='interrupted',stop_requested=1,"
                    "result_json=?,finished_at=?,updated_at=?,revision=revision+1 "
                    "WHERE session_id=?",
                    (_dump(result), now, now, row["session_id"]),
                )
            if rows:
                placeholders = ",".join("?" for _ in rows)
                recovered_rows = conn.execute(
                    f"SELECT * FROM agent_sessions WHERE session_id IN ({placeholders})",
                    tuple(row["session_id"] for row in rows),
                ).fetchall()
                recovered = [self._row_to_record(row) for row in recovered_rows]
        self._ready = True
        for record in recovered:
            self._cache_locked(record)
            self._project(
                record, "agent_session_interrupted",
                "Agent 会话因桌面服务重启而中断；外部副作用不会自动重放",
            )

    @staticmethod
    def _row_to_record(row: Any) -> dict[str, Any]:
        return {
            "contract": str(row["contract"] or CONTRACT),
            "session_id": str(row["session_id"] or ""),
            "parent_session_id": str(row["parent_session_id"] or ""),
            "owner_id": str(row["owner_id"] or ""),
            "conversation_id": str(row["conversation_id"] or ""),
            "role": str(row["role"] or "worker"),
            "task": str(row["task"] or ""),
            "scope_id": str(row["scope_id"] or ""),
            "allowed_tools": _load(row["allowed_tools_json"], []),
            "status": str(row["status"] or "failed"),
            "stop_requested": bool(row["stop_requested"]),
            "steps": _load(row["steps_json"], []),
            "result": _load(row["result_json"], {}),
            "work_run_id": str(row["work_run_id"] or ""),
            "revision": int(row["revision"] or 0),
            "generation": int(row["generation"] or 1),
            "created_at": float(row["created_at"] or 0),
            "started_at": float(row["started_at"] or 0),
            "finished_at": float(row["finished_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
        }

    def _cache_locked(self, record: dict[str, Any]) -> dict[str, Any]:
        self._items[str(record["session_id"])] = copy.deepcopy(record)
        self._prune_locked()
        return copy.deepcopy(record)

    def _persist_record_locked(self, record: dict[str, Any]) -> None:
        if not self.persist:
            return
        self._ensure_ready_locked()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO agent_sessions (
                    session_id,contract,parent_session_id,owner_id,conversation_id,
                    role,task,scope_id,allowed_tools_json,status,stop_requested,
                    steps_json,result_json,work_run_id,revision,generation,
                    created_at,started_at,finished_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    contract=excluded.contract,parent_session_id=excluded.parent_session_id,
                    owner_id=excluded.owner_id,conversation_id=excluded.conversation_id,
                    role=excluded.role,task=excluded.task,scope_id=excluded.scope_id,
                    allowed_tools_json=excluded.allowed_tools_json,status=excluded.status,
                    stop_requested=excluded.stop_requested,steps_json=excluded.steps_json,
                    result_json=excluded.result_json,work_run_id=excluded.work_run_id,
                    revision=excluded.revision,generation=excluded.generation,
                    started_at=excluded.started_at,finished_at=excluded.finished_at,
                    updated_at=excluded.updated_at""",
                (
                    record["session_id"], record["contract"], record["parent_session_id"],
                    record["owner_id"], record["conversation_id"], record["role"],
                    record["task"], record["scope_id"], _dump(record["allowed_tools"], 16_000),
                    record["status"], int(bool(record["stop_requested"])),
                    _dump(record["steps"]), _dump(record["result"]), record.get("work_run_id", ""),
                    int(record.get("revision") or 1), int(record.get("generation") or 1),
                    float(record["created_at"]), float(record["started_at"]),
                    float(record["finished_at"]), float(record["updated_at"]),
                ),
            )

    def _project(self, record: dict[str, Any], event_type: str, summary: str) -> None:
        if not self.persist or not record.get("owner_id"):
            return
        try:
            from hashmm.agent import work_runtime
            run_id = str(record.get("work_run_id") or "")
            snapshot = {
                "session_id": record["session_id"],
                "parent_session_id": record["parent_session_id"],
                "role": record["role"],
                "scope_id": record["scope_id"],
                "generation": record["generation"],
                "session_revision": record["revision"],
                "tool_count": len(record.get("steps") or []),
            }
            if not run_id:
                run = work_runtime.create_run(
                    user_id=record["owner_id"], kind="agent_session",
                    source_id=record["session_id"], conv_id=record["conversation_id"],
                    title=f"{record['role']} · {_clip(record['task'], 120)}",
                    status=record["status"] if record["status"] in work_runtime._STATUSES else "queued",
                    snapshot=snapshot,
                )
                run_id = str(run.get("id") or "")
                record["work_run_id"] = run_id
                with self._lock:
                    self._persist_record_locked(record)
                    self._items[record["session_id"]] = copy.deepcopy(record)
                return
            status_map = {"stopped": "cancelled", "orphaned": "interrupted"}
            status = status_map.get(record["status"], record["status"])
            # A session admission/update is an observation of the session,
            # not permission to rewind the parent WorkRun.  In particular a
            # queued child may be admitted to an already-running team run;
            # fail-closed task transitions must not drop that evidence merely
            # because ``running -> queued`` is intentionally invalid.
            if status in work_runtime._STATUSES:
                from hashmm.agent.task_state import can_transition
                current_run = work_runtime.get_run(run_id, record["owner_id"])
                if current_run and not can_transition(
                    current_run.get("status", ""), status
                ):
                    status = ""
            work_runtime.append_event(
                run_id, user_id=record["owner_id"], event_type=event_type,
                status=status, summary=summary, snapshot_updates=snapshot,
                payload={"session_id": record["session_id"], "role": record["role"]},
            )
        except Exception:
            # Projection is observability. Session durability remains authoritative.
            return

    def create(
        self, *, role: str, task: str, owner_id: str, conversation_id: str,
        execution_scope: dict[str, Any], parent_session_id: str = "",
        work_run_id: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        owner = _clip(owner_id, 160)
        if not owner:
            raise ValueError("agent session requires owner_id")
        session_id = "as" + uuid.uuid4().hex[:16]
        record = {
            "contract": CONTRACT,
            "session_id": session_id,
            "parent_session_id": _clip(parent_session_id, 80),
            "owner_id": owner,
            "conversation_id": _clip(conversation_id, 160),
            "role": _clip(role or "worker", 40),
            "task": _clip(task, 2_000),
            "scope_id": _clip((execution_scope or {}).get("scope_id"), 80),
            "allowed_tools": [
                _clip(item, 120) for item in list((execution_scope or {}).get("allowed_tools") or [])[:64]
                if _clip(item, 120)
            ],
            "status": "queued", "stop_requested": False, "steps": [], "result": {},
            "work_run_id": _clip(work_run_id, 80), "revision": 1, "generation": 1,
            "created_at": now, "started_at": 0.0, "finished_at": 0.0, "updated_at": now,
        }
        with self._lock:
            self._persist_record_locked(record)
            saved = self._cache_locked(record)
        self._project(record, "agent_session_admitted", "子 Agent 会话已进入运行时")
        return self.get(session_id, owner_id=owner) or saved

    def _mutate(self, session_id: str, mutate, *, owner_id: str = "") -> dict[str, Any] | None:
        with self._lock:
            item = self.get(session_id, owner_id=owner_id)
            if not item:
                return None
            mutate(item)
            item["revision"] = int(item.get("revision") or 0) + 1
            item["updated_at"] = time.time()
            self._persist_record_locked(item)
            return self._cache_locked(item)

    def start(self, session_id: str, *, owner_id: str = "") -> None:
        def change(item):
            if item["status"] not in _TERMINAL:
                item["status"] = "running"
                item["started_at"] = item["started_at"] or time.time()
        item = self._mutate(session_id, change, owner_id=owner_id)
        if item:
            self._project(item, "agent_session_started", "子 Agent 开始执行")

    def append_step(self, session_id: str, step: dict[str, Any], *, owner_id: str = "") -> None:
        def change(item):
            if item["status"] in _TERMINAL:
                return
            safe = {
                "index": len(item["steps"]) + 1,
                "tool": _clip(step.get("tool"), 120),
                "status": _clip(step.get("status") or "running", 24),
                "detail": _clip(step.get("detail"), 500),
                "at": time.time(),
            }
            item["steps"] = (list(item.get("steps") or []) + [safe])[-80:]
        item = self._mutate(session_id, change, owner_id=owner_id)
        if item:
            self._project(item, "agent_session_step", "子 Agent 工具步骤已更新")

    def request_interrupt(self, session_id: str, *, owner_id: str = "") -> bool:
        changed = {"value": False}
        def change(item):
            if item["status"] not in _TERMINAL:
                item["stop_requested"] = True
                item["status"] = "stopping"
                changed["value"] = True
        item = self._mutate(session_id, change, owner_id=owner_id)
        if item and changed["value"]:
            self._project(item, "agent_session_stopping", "子 Agent 正在安全停止")
        return changed["value"]

    def should_stop(self, session_id: str, *, owner_id: str = "") -> bool:
        item = self.get(session_id, owner_id=owner_id)
        return bool((item or {}).get("stop_requested"))

    def finish(
        self, session_id: str, status: str, result: dict[str, Any] | None = None,
        *, owner_id: str = "",
    ) -> None:
        terminal = status if status in _TERMINAL else "failed"
        def change(item):
            if item["status"] in _TERMINAL:
                return
            item["status"] = terminal
            item["finished_at"] = time.time()
            item["result"] = copy.deepcopy(result or {})
        item = self._mutate(session_id, change, owner_id=owner_id)
        if item:
            self._project(item, "agent_session_finished", "子 Agent 会话已结束")

    def get(self, session_id: str, owner_id: str = "") -> dict[str, Any] | None:
        sid = _clip(session_id, 80)
        owner = _clip(owner_id, 160)
        with self._lock:
            cached = self._items.get(sid)
            if cached and (not owner or cached.get("owner_id") == owner):
                return copy.deepcopy(cached)
            if not self.persist:
                return None
            self._ensure_ready_locked()
            with self._conn() as conn:
                if owner:
                    row = conn.execute(
                        "SELECT * FROM agent_sessions WHERE session_id=? AND owner_id=?", (sid, owner),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM agent_sessions WHERE session_id=?", (sid,),
                    ).fetchone()
            return self._cache_locked(self._row_to_record(row)) if row else None

    def children(self, parent_session_id: str, owner_id: str = "") -> list[dict[str, Any]]:
        parent = _clip(parent_session_id, 80)
        owner = _clip(owner_id, 160)
        if self.persist:
            with self._lock:
                self._ensure_ready_locked()
                with self._conn() as conn:
                    if owner:
                        rows = conn.execute(
                            "SELECT * FROM agent_sessions WHERE parent_session_id=? AND owner_id=? "
                            "ORDER BY created_at", (parent, owner),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM agent_sessions WHERE parent_session_id=? ORDER BY created_at", (parent,),
                        ).fetchall()
                return [self._cache_locked(self._row_to_record(row)) for row in rows]
        with self._lock:
            rows = [
                copy.deepcopy(item) for item in self._items.values()
                if item.get("parent_session_id") == parent
                and (not owner or item.get("owner_id") == owner)
            ]
        return sorted(rows, key=lambda item: float(item.get("created_at") or 0))

    def tree(
        self, root_session_id: str, *, owner_id: str, max_depth: int = 8,
        max_nodes: int = 128,
    ) -> dict[str, Any] | None:
        """Return an owner-scoped persistent lineage without model/tool bodies."""
        root = self.get(root_session_id, owner_id=owner_id)
        if not root:
            return None
        depth_limit = max(1, min(int(max_depth or 1), 16))
        node_limit = max(1, min(int(max_nodes or 1), 512))
        queue: list[tuple[dict[str, Any], int]] = [(root, 0)]
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        seen: set[str] = set()
        while queue and len(nodes) < node_limit:
            item, depth = queue.pop(0)
            session_id = str(item.get("session_id") or "")
            if not session_id or session_id in seen:
                continue
            seen.add(session_id)
            nodes.append({
                "session_id": session_id,
                "parent_session_id": str(item.get("parent_session_id") or ""),
                "role": str(item.get("role") or ""),
                "task": _clip(item.get("task"), 240),
                "scope_id": str(item.get("scope_id") or ""),
                "status": str(item.get("status") or ""),
                "stop_requested": bool(item.get("stop_requested")),
                "revision": int(item.get("revision") or 0),
                "generation": int(item.get("generation") or 1),
                "tool_calls": len(item.get("steps") or []),
                "created_at": float(item.get("created_at") or 0),
                "started_at": float(item.get("started_at") or 0),
                "finished_at": float(item.get("finished_at") or 0),
                "depth": depth,
            })
            if depth >= depth_limit:
                continue
            for child in self.children(session_id, owner_id=owner_id):
                child_id = str(child.get("session_id") or "")
                if child_id and child_id not in seen:
                    edges.append({
                        "from": session_id,
                        "to": child_id,
                        "relation": "delegated_to",
                    })
                    queue.append((child, depth + 1))
        return {
            "schema": "hashmm.agent-session-tree.v1",
            "root_session_id": str(root.get("session_id") or ""),
            "nodes": nodes,
            "edges": edges,
            "truncated": bool(queue),
        }

    def _prune_locked(self) -> None:
        if len(self._items) <= self.max_records:
            return
        removable = sorted(
            (item for item in self._items.values() if item.get("status") in _TERMINAL),
            key=lambda item: float(item.get("finished_at") or item.get("updated_at") or 0),
        )
        for item in removable[: max(0, len(self._items) - self.max_records)]:
            self._items.pop(str(item.get("session_id") or ""), None)


_REGISTRY = AgentSessionRegistry(persist=True)


def get_session_registry() -> AgentSessionRegistry:
    return _REGISTRY


__all__ = ["AgentSessionRegistry", "get_session_registry", "CONTRACT"]
