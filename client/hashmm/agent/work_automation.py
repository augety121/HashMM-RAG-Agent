"""Evidence-gated workflow reuse and proactive work inbox.

This is the V464-V473 control plane.  It is intentionally separate from
``work_os``: projections may explain a candidate, but only this module can
persist, verify or publish one.  Publication requires two owner-scoped runs
with the same valid execution-receipt signature plus an explicit approval.

Proactive items are durable suggestions.  They never execute merely because a
model, timer or UI card proposed them.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Mapping

from hashmm.agent.execution_receipt import validate_execution_receipt
from hashmm.api import database as db


WORKFLOW_SCHEMA = "hashmm.workflow.v1"
PROACTIVE_SCHEMA = "hashmm.proactive-work-item.v1"
_WORKFLOW_STATUSES = {"candidate", "verified", "published", "retired"}
_PROACTIVE_STATUSES = {
    "suggested", "approved", "executed", "dismissed", "needs_user", "failed",
}
_PROACTIVE_ACTIVE_STATUSES = {"suggested", "approved", "needs_user", "failed"}
_PROACTIVE_ACTIVE_BUDGET = 24


def _text(value: Any, limit: int = 400) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _receipt_actions(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshot = _dict(run.get("snapshot"))
    manifest = _dict(snapshot.get("run_manifest"))
    actions: list[dict[str, Any]] = []
    for item in list(manifest.get("execution_receipts") or [])[:160]:
        if not isinstance(item, Mapping):
            continue
        validation = validate_execution_receipt(item)
        action = _dict(item.get("action"))
        outcome = _dict(item.get("outcome"))
        side_effect = _dict(item.get("side_effect"))
        if not validation.get("valid") or not outcome.get("success"):
            continue
        actions.append({
            "tool": _text(action.get("tool"), 120),
            "arguments_hash": _text(action.get("arguments_hash"), 96),
            "scope_id": _text(action.get("scope_id"), 120),
            "side_effect": _text(side_effect.get("class"), 40),
            "reversible": bool(side_effect.get("reversible")),
        })
    return [item for item in actions if item["tool"] and item["arguments_hash"]]


def _public_workflow(row: Any) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_SCHEMA,
        "id": str(row["id"]),
        "name": str(row["name"] or ""),
        "source_run_id": str(row["source_run_id"]),
        "replay_run_id": str(row["replay_run_id"] or ""),
        "action_hash": str(row["action_hash"]),
        "actions": _loads(row["action_json"], []),
        "parameters": _loads(row["parameter_json"], {}),
        "risk": _loads(row["risk_json"], {}),
        "status": str(row["status"]),
        "revision": int(row["revision"] or 0),
        "published_at": float(row["published_at"] or 0),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
        "integrity": {
            "paired_replay_required": True,
            "explicit_publish_approval_required": True,
            "raw_arguments_stored": False,
        },
    }


def _matching_replay_id(
    conn: Any,
    *,
    user_id: str,
    source_run_id: str,
    action_hash: str,
) -> str:
    """Find a separate owner-scoped run with the same valid action signature."""
    rows = conn.execute(
        "SELECT id,snapshot_json FROM work_runs "
        "WHERE user_id=? AND id<>? AND status='completed' "
        "ORDER BY updated_at DESC LIMIT 200",
        (user_id, source_run_id),
    ).fetchall()
    for row in rows:
        run = {"snapshot": _loads(row["snapshot_json"], {})}
        actions = _receipt_actions(run)
        if actions and _canonical_hash(actions) == action_hash:
            return _text(row["id"], 96)
    return ""


def create_workflow_candidate(
    *,
    user_id: str,
    source_run_id: str,
    name: str,
    idempotency_key: str,
) -> dict[str, Any]:
    from hashmm.agent import work_runtime

    owner = _text(user_id, 160)
    source = work_runtime.get_run(source_run_id, owner, limit=1)
    if source is None:
        return {"ok": False, "error": "not_found"}
    actions = _receipt_actions(source)
    if not actions:
        return {"ok": False, "error": "execution_evidence_required"}
    if str(source.get("status") or "") != "completed":
        return {"ok": False, "error": "completed_run_required"}
    now = time.time()
    action_hash = _canonical_hash(actions)
    workflow_id = f"wf_{uuid.uuid4().hex}"
    idem = _text(idempotency_key, 160)
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO work_workflows "
            "(id,user_id,name,source_run_id,replay_run_id,action_hash,action_json,"
            "parameter_json,risk_json,status,revision,idempotency_key,published_at,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'candidate',1,?,0,?,?) "
            "ON CONFLICT(user_id,idempotency_key) DO NOTHING",
            (
                workflow_id, owner, _text(name, 160) or _text(source.get("title"), 160),
                source_run_id, "", action_hash,
                json.dumps(actions, ensure_ascii=False, separators=(",", ":")),
                "{}", json.dumps({
                    "contains_external_write": any(
                        item["side_effect"] in {"external_write", "privileged_control"}
                        for item in actions
                    ),
                    "action_count": len(actions),
                }, ensure_ascii=False, separators=(",", ":")),
                idem, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM work_workflows WHERE user_id=? AND idempotency_key=?",
            (owner, idem),
        ).fetchone()
        if row is not None and str(row["status"]) == "candidate":
            replay_run_id = _matching_replay_id(
                conn,
                user_id=owner,
                source_run_id=str(row["source_run_id"]),
                action_hash=str(row["action_hash"]),
            )
            if replay_run_id:
                conn.execute(
                    "UPDATE work_workflows SET replay_run_id=?,status='verified',"
                    "revision=revision+1,updated_at=? "
                    "WHERE id=? AND user_id=? AND status='candidate'",
                    (replay_run_id, now, str(row["id"]), owner),
                )
                row = conn.execute(
                    "SELECT * FROM work_workflows WHERE id=? AND user_id=?",
                    (str(row["id"]), owner),
                ).fetchone()
    return {"ok": True, "workflow": _public_workflow(row)}


def verify_workflow_replay(
    *,
    user_id: str,
    workflow_id: str,
    replay_run_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    from hashmm.agent import work_runtime

    owner = _text(user_id, 160)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_workflows WHERE id=? AND user_id=?",
            (_text(workflow_id, 96), owner),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found"}
        if int(row["revision"] or 0) != int(expected_revision):
            return {
                "ok": False, "error": "revision_conflict",
                "current_revision": int(row["revision"] or 0),
            }
    replay = work_runtime.get_run(replay_run_id, owner, limit=1)
    if replay is None:
        return {"ok": False, "error": "not_found"}
    if str(replay.get("status") or "") != "completed":
        return {"ok": False, "error": "completed_run_required"}
    if replay_run_id == str(row["source_run_id"]):
        return {"ok": False, "error": "independent_replay_required"}
    replay_actions = _receipt_actions(replay)
    if not replay_actions:
        return {"ok": False, "error": "execution_evidence_required"}
    if _canonical_hash(replay_actions) != str(row["action_hash"]):
        return {"ok": False, "error": "replay_signature_mismatch"}
    now = time.time()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE work_workflows SET replay_run_id=?,status='verified',"
            "revision=revision+1,updated_at=? "
            "WHERE id=? AND user_id=? AND revision=? AND status='candidate'",
            (replay_run_id, now, workflow_id, owner, int(expected_revision)),
        )
        if changed.rowcount != 1:
            current = conn.execute(
                "SELECT revision FROM work_workflows WHERE id=? AND user_id=?",
                (workflow_id, owner),
            ).fetchone()
            return {
                "ok": False, "error": "revision_conflict",
                "current_revision": int(current[0] or 0) if current else 0,
            }
        updated = conn.execute(
            "SELECT * FROM work_workflows WHERE id=? AND user_id=?",
            (workflow_id, owner),
        ).fetchone()
    return {"ok": True, "workflow": _public_workflow(updated)}


def publish_workflow(
    *,
    user_id: str,
    workflow_id: str,
    expected_revision: int,
    approved: bool,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    owner = _text(user_id, 160)
    if approved is not True:
        return {"ok": False, "error": "explicit_approval_required"}
    safe_parameters = {
        _text(key, 80): _text(value, 240)
        for key, value in list(dict(parameters or {}).items())[:24]
        if _text(key, 80)
    }
    now = time.time()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE work_workflows SET status='published',parameter_json=?,"
            "revision=revision+1,published_at=?,updated_at=? "
            "WHERE id=? AND user_id=? AND revision=? AND status='verified' "
            "AND replay_run_id<>''",
            (
                json.dumps(safe_parameters, ensure_ascii=False, separators=(",", ":")),
                now, now, workflow_id, owner, int(expected_revision),
            ),
        )
        if changed.rowcount != 1:
            row = conn.execute(
                "SELECT * FROM work_workflows WHERE id=? AND user_id=?",
                (workflow_id, owner),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "not_found"}
            error = (
                "revision_conflict"
                if int(row["revision"] or 0) != int(expected_revision)
                else "paired_replay_required"
            )
            return {"ok": False, "error": error}
        row = conn.execute(
            "SELECT * FROM work_workflows WHERE id=? AND user_id=?",
            (workflow_id, owner),
        ).fetchone()
    return {"ok": True, "workflow": _public_workflow(row)}


def list_workflows(user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM work_workflows WHERE user_id=? "
            "ORDER BY updated_at DESC LIMIT ?",
            (_text(user_id, 160), max(1, min(int(limit or 100), 200))),
        ).fetchall()
    return [_public_workflow(row) for row in rows]


def workflow_for_run(user_id: str, run_id: str) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_workflows WHERE user_id=? AND source_run_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (_text(user_id, 160), _text(run_id, 96)),
        ).fetchone()
    return _public_workflow(row) if row is not None else None


def _public_proactive(row: Any) -> dict[str, Any]:
    return {
        "schema": PROACTIVE_SCHEMA,
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "kind": str(row["kind"]),
        "title": str(row["title"]),
        "reason": str(row["reason"]),
        "risk": str(row["risk"]),
        "action": str(row["action"]),
        "status": str(row["status"]),
        "budget_key": str(row["budget_key"]),
        "result": _loads(row["result_json"], {}),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
        "acted_at": float(row["acted_at"] or 0),
        "requires_confirmation": True,
        "auto_executes": False,
    }


def refresh_proactive_items(user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Derive suggestions from durable facts, never from model prose."""
    from hashmm.agent import work_runtime

    owner = _text(user_id, 160)
    feed = work_runtime.list_runs(owner, include_terminal=True, limit=limit)
    candidates: list[dict[str, str]] = []
    for run in list(feed.get("items") or []):
        if not isinstance(run, Mapping):
            continue
        run_id = _text(run.get("id"), 96)
        status = _text(run.get("status"), 32)
        actions = set(_dict(run.get("control")).get("available_actions") or [])
        placement = _dict(run.get("execution_target"))
        if placement.get("kind") == "waiting_device" and status not in {
            "completed", "cancelled", "failed",
        }:
            candidates.append({
                "run_id": run_id, "kind": "device",
                "title": "选择一台在线电脑继续",
                "reason": "这项工作需要桌面能力，但还没有取得设备执行权",
                "risk": "low", "action": "select_device",
            })
        elif status in {"failed", "interrupted"} and "retry" in actions:
            candidates.append({
                "run_id": run_id, "kind": "recovery",
                "title": "从已保存状态重试",
                "reason": f"工作状态为 {status}，运行时确认可以创建独立重试",
                "risk": "medium", "action": "retry",
            })
    now = time.time()
    with db._conn() as conn:
        active_count = int(conn.execute(
            "SELECT COUNT(*) FROM proactive_work_items WHERE user_id=? "
            "AND status IN ('suggested','approved','needs_user','failed')",
            (owner,),
        ).fetchone()[0] or 0)
        candidate_keys = [
            _canonical_hash([owner, item["run_id"], item["kind"], item["action"]])
            for item in candidates[:100]
        ]
        existing_keys: set[str] = set()
        if candidate_keys:
            placeholders = ",".join("?" for _ in candidate_keys)
            existing_keys = {
                str(row[0])
                for row in conn.execute(
                    "SELECT idempotency_key FROM proactive_work_items "
                    f"WHERE user_id=? AND idempotency_key IN ({placeholders})",
                    (owner, *candidate_keys),
                ).fetchall()
            }
        for item, idem in zip(candidates[:100], candidate_keys):
            if idem not in existing_keys and active_count >= _PROACTIVE_ACTIVE_BUDGET:
                continue
            conn.execute(
                "INSERT INTO proactive_work_items "
                "(id,user_id,run_id,kind,title,reason,risk,action,status,budget_key,"
                "result_json,idempotency_key,created_at,updated_at,acted_at) "
                "VALUES(?,?,?,?,?,?,?,?,'suggested','user_attention','{}',?,?,?,0) "
                "ON CONFLICT(user_id,idempotency_key) DO UPDATE SET "
                "title=excluded.title,reason=excluded.reason,risk=excluded.risk,"
                "updated_at=CASE WHEN proactive_work_items.status='suggested' "
                "THEN excluded.updated_at ELSE proactive_work_items.updated_at END",
                (
                    f"pwi_{uuid.uuid4().hex}", owner, item["run_id"], item["kind"],
                    item["title"], item["reason"], item["risk"], item["action"],
                    idem, now, now,
                ),
            )
            if idem not in existing_keys:
                active_count += 1
                existing_keys.add(idem)
        rows = conn.execute(
            "SELECT * FROM proactive_work_items WHERE user_id=? "
            "AND status IN ('suggested','approved','needs_user','failed') "
            "ORDER BY updated_at DESC LIMIT ?",
            (
                owner,
                max(
                    1,
                    min(
                        int(limit or _PROACTIVE_ACTIVE_BUDGET),
                        _PROACTIVE_ACTIVE_BUDGET,
                    ),
                ),
            ),
        ).fetchall()
    return [_public_proactive(row) for row in rows]


def decide_proactive_item(
    *,
    user_id: str,
    item_id: str,
    decision: str,
) -> dict[str, Any]:
    """Record the user's decision. Execution is a separate governed call."""
    owner = _text(user_id, 160)
    safe_decision = _text(decision, 24).lower()
    if safe_decision not in {"approve", "dismiss"}:
        return {"ok": False, "error": "invalid_decision"}
    target = "approved" if safe_decision == "approve" else "dismissed"
    now = time.time()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE proactive_work_items SET status=?,updated_at=?,acted_at=? "
            "WHERE id=? AND user_id=? AND status='suggested'",
            (target, now, now, _text(item_id, 96), owner),
        )
        row = conn.execute(
            "SELECT * FROM proactive_work_items WHERE id=? AND user_id=?",
            (_text(item_id, 96), owner),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "not_found"}
    if changed.rowcount != 1 and str(row["status"]) != target:
        return {"ok": False, "error": "state_conflict", "item": _public_proactive(row)}
    return {"ok": True, "item": _public_proactive(row)}


def get_proactive_item(user_id: str, item_id: str) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM proactive_work_items WHERE id=? AND user_id=?",
            (_text(item_id, 96), _text(user_id, 160)),
        ).fetchone()
    return _public_proactive(row) if row is not None else None


def proactive_items_for_run(
    user_id: str, run_id: str, *, limit: int = 24,
) -> list[dict[str, Any]]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM proactive_work_items WHERE user_id=? AND run_id=? "
            "ORDER BY updated_at DESC LIMIT ?",
            (
                _text(user_id, 160), _text(run_id, 96),
                max(1, min(int(limit or 24), 100)),
            ),
        ).fetchall()
    return [_public_proactive(row) for row in rows]


def finalize_proactive_item(
    *,
    user_id: str,
    item_id: str,
    status: str,
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    owner = _text(user_id, 160)
    safe_status = status if status in _PROACTIVE_STATUSES else "failed"
    safe_result = {
        _text(key, 80): _text(value, 240)
        for key, value in list(dict(result or {}).items())[:24]
        if _text(key, 80)
    }
    now = time.time()
    with db._conn() as conn:
        changed = conn.execute(
            "UPDATE proactive_work_items SET status=?,result_json=?,updated_at=?,acted_at=? "
            "WHERE id=? AND user_id=? AND status='approved'",
            (
                safe_status,
                json.dumps(safe_result, ensure_ascii=False, separators=(",", ":")),
                now, now, item_id, owner,
            ),
        )
        row = conn.execute(
            "SELECT * FROM proactive_work_items WHERE id=? AND user_id=?",
            (item_id, owner),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "not_found"}
    if changed.rowcount != 1:
        return {
            "ok": False,
            "error": "approval_required",
            "item": _public_proactive(row),
        }
    return {"ok": True, "item": _public_proactive(row)}
