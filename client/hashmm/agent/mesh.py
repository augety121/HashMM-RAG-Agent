"""Persistent control plane for coordinated Agent work.

The Team JSON file is a compatibility/read model.  This module owns the parts
that must survive process restarts without being reconstructed from model
prose:

* a bounded task dependency graph;
* parent/child session binding;
* owner-scoped mailbox delivery with lease/ack/release semantics;
* deterministic readiness and terminal-state reconciliation.

Mailbox bodies are execution inputs, so they are never projected into
WorkRuntime.  Cross-device projections contain only counts, hashes and state.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from typing import Any

from hashmm.api import database as db


MESH_SCHEMA = "hashmm.agent-mesh.v1"
MAIL_SCHEMA = "hashmm.agent-mail.v1"
MESH_DECISION_SCHEMA = "hashmm.agent-mesh-decision.v1"
MESH_ADMISSION_SCHEMA = "hashmm.agent-mesh-admission.v1"

_TASK_STATUSES = {
    "planned", "ready", "running", "waiting_input", "waiting_approval",
    "blocked", "completed", "failed", "cancelled", "interrupted",
}
_TASK_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_MESSAGE_TYPES = {"task", "result", "correction", "control", "verification"}
_SENDER_KINDS = {"system", "user", "agent", "verifier"}
_MAIL_TERMINAL = {"acked", "dead"}
_MAX_ATTEMPTS = 3
_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|authorization|bearer|"
    r"access[_-]?token|refresh[_-]?token|cookie)\b\s*[:=]\s*([^\s,;]+)"
)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_body(value: Any) -> str:
    body = _clip(value, 6_000)
    return _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[已脱敏]", body)


def choose_mesh_strategy(
    *, goal: str, roles: list[dict[str, Any]], requested_mode: str = "auto",
) -> dict[str, Any]:
    """Select a deterministic, explainable collaboration topology.

    The admission decision intentionally does not call a model. Bounded task
    labels are enough to prevent retries from silently changing topology, and
    an explicit user selection always wins.
    """
    requested = _clip(requested_mode, 24).lower() or "auto"
    if requested not in {"auto", "parallel", "pipeline"}:
        raise ValueError("team mode must be auto, parallel, or pipeline")

    bounded_roles = [
        {
            "role": _clip(item.get("role"), 60).lower(),
            "task": _clip(item.get("task"), 800).lower(),
        }
        for item in roles[:8]
        if isinstance(item, dict)
    ]
    corpus = "\n".join(
        f"{item['role']} {item['task']}" for item in bounded_roles
    )
    role_count = len(bounded_roles)
    write_terms = (
        "write", "edit", "update", "modify", "merge", "publish", "deploy",
        "file", "code", "document", "写", "编辑", "修改", "合并", "发布",
        "部署", "文件", "代码", "文档", "表格", "演示文稿",
    )
    dependency_terms = (
        "then", "after", "based on", "review", "synthesize",
        "前序", "然后", "之后", "基于", "审核", "汇总", "整合", "校对",
    )
    research_terms = (
        "research", "search", "compare", "analyze", "collect", "verify",
        "检索", "调研", "搜索", "比较", "分析", "收集", "核验",
    )
    write_roles = sum(
        any(term in f"{item['role']} {item['task']}" for term in write_terms)
        for item in bounded_roles
    )
    dependency_hits = sum(term in corpus for term in dependency_terms)
    research_roles = sum(
        any(term in f"{item['role']} {item['task']}" for term in research_terms)
        for item in bounded_roles
    )
    shared_write_risk = 1.0 if write_roles >= 2 else (0.35 if write_roles else 0.0)
    dependency_risk = min(1.0, dependency_hits / 3.0)
    parallel_benefit = min(
        1.0,
        (0.18 * max(0, role_count - 1))
        + (0.16 * min(role_count, research_roles))
        + (0.12 if role_count >= 3 else 0.0),
    )
    coordination_cost = min(
        1.0,
        0.12 + (0.13 * max(0, role_count - 1))
        + (0.22 * shared_write_risk)
        + (0.16 * dependency_risk),
    )

    if requested != "auto":
        resolved = requested
        reason_codes = ["user_selected_topology"]
    elif shared_write_risk >= 1.0:
        resolved = "pipeline"
        reason_codes = ["shared_mutation_conflict", "ordered_handoff"]
    elif dependency_risk >= 0.34:
        resolved = "pipeline"
        reason_codes = ["task_dependency_detected", "ordered_handoff"]
    elif role_count >= 2 and parallel_benefit > coordination_cost:
        resolved = "parallel"
        reason_codes = ["independent_workstreams", "parallel_benefit_exceeds_cost"]
    else:
        resolved = "pipeline"
        reason_codes = ["coordination_cost_not_lower", "bounded_sequential_execution"]

    decision_input = json.dumps(
        {
            "goal": _clip(goal, 400),
            "roles": bounded_roles,
            "requested_mode": requested,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema": MESH_DECISION_SCHEMA,
        "decision_version": 2,
        "requested_mode": requested,
        "resolved_mode": resolved,
        "role_count": role_count,
        "parallel_benefit": round(parallel_benefit, 3),
        "coordination_cost": round(coordination_cost, 3),
        "parallel_net_benefit": round(parallel_benefit - coordination_cost, 3),
        "estimated_serial_rounds": max(1, role_count),
        "estimated_parallel_rounds": 1 if resolved == "parallel" and role_count else max(1, role_count),
        "shared_write_risk": round(shared_write_risk, 3),
        "dependency_risk": round(dependency_risk, 3),
        "reason_codes": reason_codes,
        "input_hash": _hash(decision_input),
    }


def admit_mesh_work(
    *,
    goal: str,
    roles: list[dict[str, Any]],
    requested_mode: str = "auto",
    execution_scope: dict[str, Any] | None = None,
    adapter: str = "native",
    require_delegation: bool = False,
) -> dict[str, Any]:
    """Produce the single admission record used by every orchestration path.

    Legacy planners may still decompose a goal, but they do not own topology or
    dependency semantics.  When a server-authored execution scope is supplied,
    its worker budget and delegation flag are authoritative and can only narrow
    the requested plan.
    """
    bounded = [
        {
            "id": _clip(item.get("id") or f"task_{index + 1}", 80),
            "role": _clip(item.get("role") or "worker", 60),
            "task": _clip(item.get("task"), 800),
        }
        for index, item in enumerate(list(roles or [])[:8])
        if isinstance(item, dict) and _clip(item.get("task"), 800)
    ]
    decision = choose_mesh_strategy(
        goal=goal,
        roles=bounded,
        requested_mode=requested_mode,
    )
    resolved = str(decision["resolved_mode"])
    reason_codes = list(decision["reason_codes"])
    admitted = True
    denial_code = ""
    max_workers = 8

    if execution_scope is not None:
        scope = execution_scope if isinstance(execution_scope, dict) else {}
        budgets = scope.get("budgets") if isinstance(scope.get("budgets"), dict) else {}
        max_workers = max(0, min(int(budgets.get("max_workers") or 0), 8))
        if require_delegation and not bool(scope.get("allow_subagents")):
            admitted = False
            denial_code = "subagents_not_in_execution_scope"
            reason_codes.append(denial_code)
        elif max_workers < 1 and len(bounded) > 1:
            admitted = False
            denial_code = "worker_budget_exhausted"
            reason_codes.append(denial_code)
        elif resolved == "parallel" and max_workers < 2:
            resolved = "pipeline"
            reason_codes.extend(["parallel_budget_insufficient", "ordered_handoff"])

    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(bounded):
        dependencies = (
            [bounded[index - 1]["id"]]
            if resolved == "pipeline" and index > 0
            else []
        )
        tasks.append({
            **item,
            "index": index,
            "depends_on": dependencies,
            "status": "planned" if admitted else "blocked",
        })

    admitted_count = min(len(tasks), max_workers) if execution_scope is not None else len(tasks)
    result = {
        **decision,
        "admission_schema": MESH_ADMISSION_SCHEMA,
        "admission_version": 1,
        "adapter": _clip(adapter, 60) or "native",
        "admitted": admitted,
        "denial_code": denial_code,
        "resolved_mode": resolved,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "max_workers": max_workers,
        "admitted_worker_count": admitted_count if admitted else 0,
        "tasks": tasks,
    }
    canonical = json.dumps(
        {
            "decision": decision["input_hash"],
            "resolved_mode": resolved,
            "adapter": result["adapter"],
            "admitted": admitted,
            "tasks": tasks,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result["admission_hash"] = _hash(canonical)
    return result


def _ensure_schema(conn: Any) -> None:
    """Keep isolated tests and partially migrated installations fail-safe."""
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_mesh_tasks (
        task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '', parent_task_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planned', result_hash TEXT NOT NULL DEFAULT '',
        upstream_failures INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, UNIQUE(owner_id,team_id,task_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_mesh_edges (
        owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
        from_task_id TEXT NOT NULL, to_task_id TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT 'requires', created_at REAL NOT NULL,
        PRIMARY KEY(owner_id,team_id,from_task_id,to_task_id,relation))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_mailbox_messages (
        message_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
        sender_session_id TEXT NOT NULL DEFAULT '', recipient_session_id TEXT NOT NULL,
        sender_kind TEXT NOT NULL DEFAULT 'agent', message_type TEXT NOT NULL DEFAULT 'result',
        body TEXT NOT NULL DEFAULT '', body_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        available_at REAL NOT NULL, lease_token_hash TEXT NOT NULL DEFAULT '',
        lease_expires_at REAL NOT NULL DEFAULT 0, acked_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(owner_id,team_id,idempotency_key))""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_mesh_tasks_team "
        "ON agent_mesh_tasks(owner_id,team_id,created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_mesh_edges_target "
        "ON agent_mesh_edges(owner_id,team_id,to_task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_mailbox_recipient "
        "ON agent_mailbox_messages(owner_id,recipient_session_id,status,available_at)"
    )


def _task_row(row: Any) -> dict[str, Any]:
    return {
        "task_id": str(row["task_id"] or ""),
        "team_id": str(row["team_id"] or ""),
        "session_id": str(row["session_id"] or ""),
        "parent_task_id": str(row["parent_task_id"] or ""),
        "role": str(row["role"] or ""),
        "title": str(row["title"] or ""),
        "status": str(row["status"] or "planned"),
        "result_hash": str(row["result_hash"] or ""),
        "upstream_failures": int(row["upstream_failures"] or 0),
        "revision": int(row["revision"] or 1),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }


def create_team_graph(
    *, owner_id: str, team_id: str, goal: str, roles: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    """Create the deterministic team DAG once; retry uses a new team id."""
    owner = _clip(owner_id, 160)
    team = _clip(team_id, 80)
    if not owner or not team:
        raise ValueError("agent mesh requires owner_id and team_id")
    now = time.time()
    root_id = f"{team}:root"
    role_ids = [f"{team}:role:{index + 1}" for index in range(len(roles))]
    synthesis_id = f"{team}:synthesis"
    verification_id = f"{team}:verification"
    delivery_id = f"{team}:delivery"
    nodes: list[tuple[str, str, str, str, str]] = [
        (root_id, "", "coordinator", _clip(goal, 400), "running"),
    ]
    for index, role in enumerate(roles):
        ready = "ready" if mode != "pipeline" or index == 0 else "blocked"
        nodes.append((
            role_ids[index], root_id, _clip(role.get("role"), 60),
            _clip(role.get("task"), 800), ready,
        ))
    nodes.extend([
        (synthesis_id, root_id, "synthesizer", "合并已完成角色产出", "blocked"),
        (verification_id, root_id, "verifier", "独立核验目标、证据、回执和产物", "blocked"),
        (delivery_id, root_id, "delivery", "提交通过门控的最终结果", "blocked"),
    ])
    edges: list[tuple[str, str, str]] = []
    if mode == "pipeline":
        for index, role_id in enumerate(role_ids):
            edges.append((root_id if index == 0 else role_ids[index - 1], role_id, "requires"))
    else:
        edges.extend((root_id, role_id, "decomposes_to") for role_id in role_ids)
    edges.extend((role_id, synthesis_id, "requires") for role_id in role_ids)
    edges.append((synthesis_id, verification_id, "requires"))
    edges.append((verification_id, delivery_id, "requires"))
    with db._conn() as conn:
        _ensure_schema(conn)
        for task_id, parent_id, role, title, status in nodes:
            conn.execute(
                "INSERT OR IGNORE INTO agent_mesh_tasks "
                "(task_id,owner_id,team_id,session_id,parent_task_id,role,title,status,"
                "result_hash,upstream_failures,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, owner, team, "", parent_id, role, title, status,
                    "", 0, 1, now, now,
                ),
            )
        for source, target, relation in edges:
            conn.execute(
                "INSERT OR IGNORE INTO agent_mesh_edges "
                "(owner_id,team_id,from_task_id,to_task_id,relation,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (owner, team, source, target, relation, now),
            )
    return get_team_graph(owner_id=owner, team_id=team) or {}


def bind_session(
    *, owner_id: str, team_id: str, task_id: str, session_id: str,
) -> bool:
    with db._conn() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE agent_mesh_tasks SET session_id=?,revision=revision+1,updated_at=? "
            "WHERE task_id=? AND owner_id=? AND team_id=?",
            (
                _clip(session_id, 80), time.time(), _clip(task_id, 120),
                _clip(owner_id, 160), _clip(team_id, 80),
            ),
        )
        return bool(cursor.rowcount)


def transition_task(
    *, owner_id: str, team_id: str, task_id: str, status: str,
    result: Any = None,
) -> dict[str, Any] | None:
    """CAS-like sticky transition; terminal results cannot be overwritten."""
    target = _clip(status, 32)
    if target not in _TASK_STATUSES:
        raise ValueError(f"invalid agent mesh task status: {target}")
    owner = _clip(owner_id, 160)
    team = _clip(team_id, 80)
    task = _clip(task_id, 120)
    now = time.time()
    result_hash = _hash(_safe_body(result)) if result not in (None, "") else ""
    with db._conn() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM agent_mesh_tasks WHERE task_id=? AND owner_id=? AND team_id=?",
            (task, owner, team),
        ).fetchone()
        if not row:
            return None
        current = str(row["status"] or "planned")
        if current in _TASK_TERMINAL and current != target:
            return _task_row(row)
        if current != target or result_hash:
            conn.execute(
                "UPDATE agent_mesh_tasks SET status=?,result_hash=CASE WHEN ?='' "
                "THEN result_hash ELSE ? END,revision=revision+1,updated_at=? "
                "WHERE task_id=? AND owner_id=? AND team_id=?",
                (target, result_hash, result_hash, now, task, owner, team),
            )
        _reconcile_ready_locked(conn, owner, team)
        saved = conn.execute(
            "SELECT * FROM agent_mesh_tasks WHERE task_id=? AND owner_id=? AND team_id=?",
            (task, owner, team),
        ).fetchone()
    return _task_row(saved) if saved else None


def _reconcile_ready_locked(conn: Any, owner_id: str, team_id: str) -> None:
    """Unblock only when every predecessor is terminal; failures stay visible."""
    rows = conn.execute(
        "SELECT * FROM agent_mesh_tasks WHERE owner_id=? AND team_id=?",
        (owner_id, team_id),
    ).fetchall()
    status_by_id = {str(row["task_id"]): str(row["status"]) for row in rows}
    edges = conn.execute(
        "SELECT from_task_id,to_task_id FROM agent_mesh_edges "
        "WHERE owner_id=? AND team_id=? AND relation='requires'",
        (owner_id, team_id),
    ).fetchall()
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        incoming.setdefault(str(edge["to_task_id"]), []).append(str(edge["from_task_id"]))
    now = time.time()
    for target, sources in incoming.items():
        current = status_by_id.get(target, "")
        if current not in {"planned", "blocked"}:
            continue
        upstream = [status_by_id.get(source, "planned") for source in sources]
        if upstream and all(item in _TASK_TERMINAL for item in upstream):
            failures = sum(item != "completed" for item in upstream)
            conn.execute(
                "UPDATE agent_mesh_tasks SET status='ready',upstream_failures=?,"
                "revision=revision+1,updated_at=? "
                "WHERE task_id=? AND owner_id=? AND team_id=? "
                "AND status IN ('planned','blocked')",
                (failures, now, target, owner_id, team_id),
            )


def get_team_graph(*, owner_id: str, team_id: str) -> dict[str, Any] | None:
    owner = _clip(owner_id, 160)
    team = _clip(team_id, 80)
    with db._conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM agent_mesh_tasks WHERE owner_id=? AND team_id=? ORDER BY created_at,task_id",
            (owner, team),
        ).fetchall()
        if not rows:
            return None
        edges = conn.execute(
            "SELECT from_task_id,to_task_id,relation FROM agent_mesh_edges "
            "WHERE owner_id=? AND team_id=? ORDER BY created_at,from_task_id,to_task_id",
            (owner, team),
        ).fetchall()
    nodes = [_task_row(row) for row in rows]
    public_edges = [{
        "from": str(row["from_task_id"]),
        "to": str(row["to_task_id"]),
        "relation": str(row["relation"]),
    } for row in edges]
    canonical = json.dumps(
        {"nodes": nodes, "edges": public_edges},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    active = sum(node["status"] in {"ready", "running", "waiting_input", "waiting_approval"}
                 for node in nodes)
    blocked = sum(node["status"] == "blocked" for node in nodes)
    return {
        "schema": MESH_SCHEMA,
        "team_id": team,
        "nodes": nodes,
        "edges": public_edges,
        "summary": {
            "total": len(nodes),
            "active": active,
            "blocked": blocked,
            "completed": sum(node["status"] == "completed" for node in nodes),
            "failed": sum(node["status"] in {"failed", "cancelled", "interrupted"} for node in nodes),
        },
        "graph_hash": _hash(canonical),
    }


def _session_owned(conn: Any, owner_id: str, session_id: str) -> bool:
    if not session_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM agent_sessions WHERE session_id=? AND owner_id=?",
        (_clip(session_id, 80), _clip(owner_id, 160)),
    ).fetchone()
    return bool(row)


def _mail_row(row: Any, *, include_body: bool = False, lease_token: str = "") -> dict[str, Any]:
    result = {
        "schema": MAIL_SCHEMA,
        "message_id": str(row["message_id"] or ""),
        "team_id": str(row["team_id"] or ""),
        "sender_session_id": str(row["sender_session_id"] or ""),
        "recipient_session_id": str(row["recipient_session_id"] or ""),
        "sender_kind": str(row["sender_kind"] or "agent"),
        "message_type": str(row["message_type"] or "result"),
        "body_hash": str(row["body_hash"] or ""),
        "status": str(row["status"] or "queued"),
        "attempts": int(row["attempts"] or 0),
        "available_at": float(row["available_at"] or 0),
        "lease_expires_at": float(row["lease_expires_at"] or 0),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }
    if include_body:
        result["body"] = str(row["body"] or "")
    if lease_token:
        result["lease_token"] = lease_token
    return result


def send_message(
    *, owner_id: str, team_id: str, recipient_session_id: str, body: str,
    message_type: str = "result", sender_kind: str = "agent",
    sender_session_id: str = "", idempotency_key: str = "",
) -> dict[str, Any]:
    owner = _clip(owner_id, 160)
    team = _clip(team_id, 80)
    recipient = _clip(recipient_session_id, 80)
    sender = _clip(sender_session_id, 80)
    kind = _clip(sender_kind, 24)
    msg_type = _clip(message_type, 32)
    safe = _safe_body(body)
    if not owner or not team or not recipient or not safe:
        raise ValueError("mailbox message requires owner, team, recipient and body")
    if kind not in _SENDER_KINDS or msg_type not in _MESSAGE_TYPES:
        raise ValueError("unsupported mailbox sender or message type")
    key = _clip(idempotency_key, 160) or _hash(
        f"{team}\0{sender}\0{recipient}\0{msg_type}\0{safe}"
    )
    now = time.time()
    message_id = "am" + uuid.uuid4().hex[:20]
    with db._conn() as conn:
        _ensure_schema(conn)
        if not _session_owned(conn, owner, recipient):
            raise LookupError("recipient session not found")
        if sender and not _session_owned(conn, owner, sender):
            raise LookupError("sender session not found")
        conn.execute(
            "INSERT OR IGNORE INTO agent_mailbox_messages "
            "(message_id,owner_id,team_id,sender_session_id,recipient_session_id,"
            "sender_kind,message_type,body,body_hash,idempotency_key,status,attempts,"
            "available_at,lease_token_hash,lease_expires_at,acked_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id, owner, team, sender, recipient, kind, msg_type, safe,
                _hash(safe), key, "queued", 0, now, "", 0.0, 0.0, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM agent_mailbox_messages "
            "WHERE owner_id=? AND team_id=? AND idempotency_key=?",
            (owner, team, key),
        ).fetchone()
    if not row:
        raise RuntimeError("mailbox message was not persisted")
    return _mail_row(row)


def _recover_expired_locked(conn: Any, owner_id: str, recipient: str, now: float) -> None:
    rows = conn.execute(
        "SELECT message_id,attempts FROM agent_mailbox_messages "
        "WHERE owner_id=? AND recipient_session_id=? AND status='leased' "
        "AND lease_expires_at>0 AND lease_expires_at<=?",
        (owner_id, recipient, now),
    ).fetchall()
    for row in rows:
        status = "dead" if int(row["attempts"] or 0) >= _MAX_ATTEMPTS else "queued"
        conn.execute(
            "UPDATE agent_mailbox_messages SET status=?,available_at=?,"
            "lease_token_hash='',lease_expires_at=0,updated_at=? "
            "WHERE message_id=? AND owner_id=? AND status='leased'",
            (status, now, now, row["message_id"], owner_id),
        )


def lease_messages(
    *, owner_id: str, recipient_session_id: str, limit: int = 8,
    lease_seconds: int = 30,
) -> list[dict[str, Any]]:
    owner = _clip(owner_id, 160)
    recipient = _clip(recipient_session_id, 80)
    if not owner or not recipient:
        return []
    take = max(1, min(int(limit or 1), 16))
    ttl = max(10, min(int(lease_seconds or 30), 120))
    now = time.time()
    leased: list[dict[str, Any]] = []
    with db._conn() as conn:
        _ensure_schema(conn)
        if not _session_owned(conn, owner, recipient):
            return []
        _recover_expired_locked(conn, owner, recipient, now)
        rows = conn.execute(
            "SELECT * FROM agent_mailbox_messages "
            "WHERE owner_id=? AND recipient_session_id=? AND status='queued' "
            "AND available_at<=? ORDER BY created_at,message_id LIMIT ?",
            (owner, recipient, now, take),
        ).fetchall()
        for row in rows:
            token = secrets.token_urlsafe(24)
            cursor = conn.execute(
                "UPDATE agent_mailbox_messages SET status='leased',attempts=attempts+1,"
                "lease_token_hash=?,lease_expires_at=?,updated_at=? "
                "WHERE message_id=? AND owner_id=? AND status='queued'",
                (_hash(token), now + ttl, now, row["message_id"], owner),
            )
            if not cursor.rowcount:
                continue
            updated = conn.execute(
                "SELECT * FROM agent_mailbox_messages WHERE message_id=? AND owner_id=?",
                (row["message_id"], owner),
            ).fetchone()
            if updated:
                leased.append(_mail_row(updated, include_body=True, lease_token=token))
    return leased


def ack_message(*, owner_id: str, message_id: str, lease_token: str) -> bool:
    now = time.time()
    with db._conn() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE agent_mailbox_messages SET status='acked',acked_at=?,"
            "lease_token_hash='',lease_expires_at=0,updated_at=? "
            "WHERE message_id=? AND owner_id=? AND status='leased' "
            "AND lease_token_hash=? AND lease_expires_at>?",
            (
                now, now, _clip(message_id, 80), _clip(owner_id, 160),
                _hash(_clip(lease_token, 200)), now,
            ),
        )
        return bool(cursor.rowcount)


def release_message(
    *, owner_id: str, message_id: str, lease_token: str, retry_after: int = 0,
) -> bool:
    now = time.time()
    owner = _clip(owner_id, 160)
    with db._conn() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT attempts FROM agent_mailbox_messages "
            "WHERE message_id=? AND owner_id=? AND status='leased' "
            "AND lease_token_hash=?",
            (_clip(message_id, 80), owner, _hash(_clip(lease_token, 200))),
        ).fetchone()
        if not row:
            return False
        status = "dead" if int(row["attempts"] or 0) >= _MAX_ATTEMPTS else "queued"
        cursor = conn.execute(
            "UPDATE agent_mailbox_messages SET status=?,available_at=?,"
            "lease_token_hash='',lease_expires_at=0,updated_at=? "
            "WHERE message_id=? AND owner_id=? AND status='leased'",
            (
                status, now + max(0, min(int(retry_after or 0), 300)), now,
                _clip(message_id, 80), owner,
            ),
        )
        return bool(cursor.rowcount)


def mailbox_summary(
    *, owner_id: str, team_id: str, recipient_session_id: str = "",
) -> dict[str, Any]:
    owner = _clip(owner_id, 160)
    team = _clip(team_id, 80)
    recipient = _clip(recipient_session_id, 80)
    clauses = ["owner_id=?", "team_id=?"]
    params: list[Any] = [owner, team]
    if recipient:
        clauses.append("recipient_session_id=?")
        params.append(recipient)
    where = " AND ".join(clauses)
    with db._conn() as conn:
        _ensure_schema(conn)
        if recipient:
            _recover_expired_locked(conn, owner, recipient, time.time())
        rows = conn.execute(
            f"SELECT * FROM agent_mailbox_messages WHERE {where} "
            "ORDER BY created_at DESC LIMIT 20",
            tuple(params),
        ).fetchall()
    counts = {status: 0 for status in ("queued", "leased", "acked", "dead")}
    items = []
    for row in rows:
        status = str(row["status"] or "queued")
        counts[status] = counts.get(status, 0) + 1
        item = _mail_row(row)
        item["preview"] = _clip(row["body"], 160)
        items.append(item)
    return {
        "schema": MAIL_SCHEMA,
        "team_id": team,
        "counts": counts,
        "items": items,
        "pending": counts.get("queued", 0) + counts.get("leased", 0),
    }


__all__ = [
    "MESH_SCHEMA",
    "MAIL_SCHEMA",
    "MESH_DECISION_SCHEMA",
    "MESH_ADMISSION_SCHEMA",
    "choose_mesh_strategy",
    "admit_mesh_work",
    "create_team_graph",
    "bind_session",
    "transition_task",
    "get_team_graph",
    "send_message",
    "lease_messages",
    "ack_message",
    "release_message",
    "mailbox_summary",
]
