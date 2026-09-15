"""Durable cross-device work runtime and incremental event ledger.

The message table remains the source of truth for conversation prose.  This
module is the source of truth for *work state*: one run can be resumed and
observed by Chat, desktop and App without each surface reconstructing state
from unrelated loop/team/tool stores.

Security boundary: ledger payloads are projections, never execution inputs.
Raw prompts, file bodies, credentials and tool arguments are deliberately not
accepted by the public API and are redacted again before persistence.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

from hashmm.api import database as db
from hashmm.utils import get_logger

logger = get_logger("hashmm.work_runtime")

RUN_SCHEMA = "hashmm.work-run.v1"
EVENT_SCHEMA = "hashmm.work-event.v1"
FEED_SCHEMA = "hashmm.work-feed.v1"
COMMAND_SCHEMA = "hashmm.work-command.v1"
PRESENTATION_SCHEMA = "hashmm.work-presentation.v1"
ACTION_INBOX_SCHEMA = "hashmm.action-inbox.v1"
WORK_CANVAS_SCHEMA = "hashmm.work-canvas.v1"
WORK_RESULT_SCHEMA = "hashmm.work-result.v1"
COMPLETION_RECEIPT_SCHEMA = "hashmm.completion-receipt.v1"
NEXT_ACTIONS_SCHEMA = "hashmm.governed-next-actions.v1"
DECISION_SCHEMA = "hashmm.work-decision.v1"
GENERATION_SCHEMA = "hashmm.work-generation.v1"
ARTIFACT_REVISION_SCHEMA = "hashmm.artifact-revision.v1"
EXECUTION_LEASE_SCHEMA = "hashmm.execution-lease.v1"
ARTIFACT_ANNOTATION_SCHEMA = "hashmm.artifact-annotation.v1"
SYNC_SCHEMA = "hashmm.work-sync.v1"
EVENT_ENVELOPE_SCHEMA = "hashmm.event.v1"

_KINDS = {
    "chat", "loop", "team", "agent_session", "browser", "computer",
    "remote", "artifact", "workflow",
}
_STATUSES = {
    "draft", "created", "queued", "running", "waiting_input", "waiting_approval", "paused",
    "retrying", "verifying", "blocked",
    "observed", "delivered", "completed", "completed_with_limits", "failed", "cancelled", "interrupted",
}
_TERMINAL = {"observed", "completed", "completed_with_limits", "failed", "cancelled", "interrupted"}
_ACTIONS = {"pause", "resume", "cancel", "retry"}
_DECISIONS = {"accept_delivery", "request_changes"}
_SECRET_KEY = re.compile(
    r"(^|_)(api[_-]?key|secret|password|passwd|token|authorization|cookie|"
    r"credential|private[_-]?key|args?|arguments?|file[_-]?body|content)(_|$)",
    re.I,
)


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return default


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            return max(0, int(default))
        except (TypeError, ValueError):
            return 0


def _fingerprint(value: Any, limit: int = 24) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[: max(8, limit)]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact a value before it enters the durable ledger."""
    # Run manifests contain proof receipts and causal nodes six levels deep.
    # Preserve those bounded projections while still refusing arbitrary
    # recursive structures.
    if depth > 6:
        return "[省略]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clip(value, 1_000)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:40]:
            key = _clip(raw_key, 80)
            if not key:
                continue
            # Proof hashes are intentionally public runtime metadata.  They
            # must survive the projection so clients can invalidate cached
            # results without receiving the underlying content.  Credential
            # hashes are not part of this allow-list.
            if key.lower() in {
                "content_hash", "result_hash", "arguments_hash", "rendered_hash",
                "prompt_hash", "query_hash", "reference_hash", "answer_hash",
                "baseline_answer_hash", "candidate_answer_hash", "sha256",
                "fingerprint", "etag",
            }:
                result[key] = _safe_value(item, depth=depth + 1)
            elif _SECRET_KEY.search(key):
                result[key] = "[已脱敏]"
            else:
                result[key] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:40]]
    return _clip(value, 300)


def _json_load(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _available_actions_for(kind: str, status: str, snapshot: dict[str, Any] | None = None) -> list[str]:
    """Return only controls backed by a real owner-checked implementation."""
    if kind == "loop":
        if status in {"queued", "running"}:
            return ["pause", "cancel"]
        if status == "blocked":
            return ["resume", "cancel"]
    if kind == "team":
        if status in {"queued", "running"}:
            return [] if bool((snapshot or {}).get("stop_requested")) else ["cancel"]
        if status in {"delivered", "completed", "failed", "cancelled", "interrupted"}:
            return ["retry"]
    if kind == "workflow" and status in {"failed", "interrupted"}:
        # A retry creates a new schedule occurrence; the failed run remains
        # immutable audit history and is never rewritten in place.
        return ["retry"]
    if kind in {"browser", "computer"} and status == "queued":
        return ["cancel"]
    return []


def _side_effect_boundary(kind: str) -> str:
    if kind == "loop":
        return "checkpointed_cooperative"
    if kind == "team":
        return "cooperative_after_current_model_call"
    if kind in {"browser", "computer"}:
        return "cancel_before_desktop_claim_only"
    if kind == "workflow":
        return "new_owner_scoped_occurrence"
    return "observe_only"


_KIND_LABELS = {
    "chat": "对话工作",
    "loop": "持续工作",
    "team": "并行协作",
    "agent_session": "协作步骤",
    "browser": "网页工作",
    "computer": "电脑工作",
    "artifact": "内容创作",
    "workflow": "自动工作",
}
_KIND_LABELS["remote"] = "远程协作"
_STATUS_PRESENTATION = {
    "queued": ("准备中", "preparing", "HashMM 正在准备这项工作"),
    "running": ("进行中", "working", "HashMM 正在继续处理"),
    "waiting_input": ("需要补充", "needs_user", "补充信息后可以继续"),
    "waiting_approval": ("等待确认", "needs_user", "确认授权后可以继续"),
    "blocked": ("暂时受阻", "needs_attention", "处理当前问题后可以继续"),
    "observed": ("历史记录", "finished", "这是迁移的历史记录，未重建完成证据"),
    "delivered": ("等待验收", "review", "成果已生成，请检查是否符合要求"),
    "completed": ("已完成", "finished", "工作已完成"),
    "failed": ("未完成", "needs_attention", "查看原因后可以重试"),
    "cancelled": ("已取消", "finished", "工作已取消"),
    "interrupted": ("已中断", "needs_attention", "可以从最近保存的位置继续"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _presentation_progress(snapshot: dict[str, Any], status: str) -> dict[str, Any]:
    """Build honest progress from explicit criteria; never invent a percentage."""
    manifest = _as_dict(snapshot.get("run_manifest"))
    contract = _as_dict(manifest.get("task_contract"))
    criteria = [item for item in _as_list(contract.get("success_criteria")) if isinstance(item, dict)]
    gate = _as_dict(manifest.get("completion_gate"))
    gate_summary = _as_dict(gate.get("summary"))
    total = _nonnegative_int(gate_summary.get("total"), len(criteria))
    passed = _nonnegative_int(gate_summary.get("passed") or gate_summary.get("ok"))
    if total > 0:
        bounded_passed = max(0, min(passed, total))
        return {
            "mode": "criteria",
            "completed": bounded_passed,
            "total": total,
            "label": f"已满足 {bounded_passed}/{total} 项验收条件",
        }
    phase_order = {
        "queued": 0, "running": 1, "waiting_input": 1, "waiting_approval": 1,
        "blocked": 1, "delivered": 2, "completed": 3, "observed": 3,
        "failed": 1, "cancelled": 1, "interrupted": 1,
    }
    phase = phase_order.get(status, 0)
    return {
        "mode": "phase",
        "completed": phase,
        "total": 3,
        "label": ("准备工作", "执行工作", "检查成果", "工作结束")[phase],
    }


def _presentation_deliverables(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    candidates = list(_as_list(snapshot.get("files")))
    latest = snapshot.get("latest_artifact")
    if isinstance(latest, dict):
        candidates.append(latest)
    for item in candidates[:20]:
        if not isinstance(item, dict):
            continue
        name = _clip(item.get("filename") or item.get("name"), 180)
        kind = _clip(item.get("type") or item.get("kind") or "file", 40)
        if not name or (name, kind) in seen:
            continue
        seen.add((name, kind))
        result.append({"name": name, "type": kind})
    return result


def _presentation_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    remote_completion = _as_dict(snapshot.get("remote_completion"))
    if remote_completion.get("schema") == "hashmm.remote-completion.v1":
        verification = str(remote_completion.get("verification") or "not_observed")
        samples = _nonnegative_int(remote_completion.get("transport_samples"))
        return {
            "status": verification,
            "count": samples,
            "label": {
                "verified": "两端连接记录已核验",
                "partial": "仅核验到一端连接记录",
                "not_observed": "未取得连接质量记录",
            }.get(verification, "远程结束记录待检查"),
        }
    manifest = _as_dict(snapshot.get("run_manifest"))
    verification = _as_dict(manifest.get("verification"))
    graph = _as_dict(manifest.get("evidence_graph") or snapshot.get("causal_work_graph"))
    summary = _as_dict(graph.get("summary"))
    status = str(verification.get("status") or graph.get("status") or "not_available")
    count = _nonnegative_int(
        summary.get("evidence")
        or summary.get("evidence_nodes")
        or summary.get("nodes")
        or 0
    )
    return {
        "status": status,
        "count": count,
        "label": {
            "verified": "已核验",
            "passed": "已核验",
            "ready": "依据已整理",
            "partial": "部分依据待检查",
            "failed": "核验未通过",
            "blocked": "缺少必要依据",
            "not_reconstructed": "历史证据未重建",
        }.get(status, "尚无可展示的完成证据"),
    }


def present_run(run: dict[str, Any]) -> dict[str, Any]:
    """Project an internal run into stable, user-facing work language.

    This is a read-only projection.  It deliberately retains no executable
    arguments and does not let presentation fields drive the runtime.
    """
    snapshot = _as_dict(run.get("snapshot"))
    status = str(run.get("status") or "queued")
    kind = str(run.get("kind") or "workflow")
    status_label, phase, default_next = _STATUS_PRESENTATION.get(
        status, ("状态更新", "working", "查看最新工作记录"),
    )
    title = _clip(run.get("title"), 240) or _KIND_LABELS.get(kind, "一项工作")
    current_step = _clip(snapshot.get("last_summary"), 500)
    if not current_step:
        current_step = {
            "queued": "已收到目标，等待开始",
            "running": "正在按计划处理",
            "delivered": "成果已经生成",
            "completed": "验收条件已经完成",
        }.get(status, default_next)
    available = list(_as_dict(run.get("control")).get("available_actions") or [])
    operating_contract = (
        _as_dict(snapshot.get("operating_contract"))
        or _as_dict(_as_dict(snapshot.get("run_manifest")).get("operating_contract"))
    )
    try:
        from hashmm.agent.operating_contract import user_operating_projection
        operating = user_operating_projection(operating_contract)
    except Exception:
        operating = {
            "schema": "hashmm.user-operating-projection.v1",
            "method_label": "自动安排",
            "route_state": "setup_required",
            "route_label": "运行方式待确认",
            "reason": "当前服务未提供可核验的运行方式",
            "requires_confirmation": False,
            "can_resume": False,
            "evidence_required": False,
        }
    primary_action = {
        "waiting_input": "answer",
        "waiting_approval": "review_approval",
        "blocked": "resume" if "resume" in available else "inspect",
        "delivered": "review_delivery",
        "failed": "retry" if "retry" in available else "inspect",
        "interrupted": "retry" if "retry" in available else "inspect",
    }.get(status, "open")
    return {
        "schema": PRESENTATION_SCHEMA,
        "title": title,
        "category": _KIND_LABELS.get(kind, "工作"),
        "status_label": status_label,
        "phase": phase,
        "current_step": current_step,
        "next_action": default_next,
        "needs_user": status in {"waiting_input", "waiting_approval", "blocked", "delivered", "failed", "interrupted"},
        "primary_action": primary_action,
        "progress": _presentation_progress(snapshot, status),
        "deliverables": _presentation_deliverables(snapshot),
        "evidence": _presentation_evidence(snapshot),
        "sync": {
            "state": "synced",
            "updated_at": float(run.get("updated_at") or 0),
        },
        "operating": operating,
    }


def action_inbox(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only actionable work, ordered by urgency then recency."""
    action_types = {
        "waiting_approval": ("approval", "high"),
        "waiting_input": ("question", "high"),
        "blocked": ("blocker", "high"),
        "failed": ("failure", "high"),
        "interrupted": ("interruption", "normal"),
        "delivered": ("delivery", "normal"),
    }
    rows: list[dict[str, Any]] = []
    for run in items:
        status = str(run.get("status") or "")
        if status not in action_types:
            continue
        presentation = _as_dict(run.get("presentation")) or present_run(run)
        action_type, priority = action_types[status]
        snapshot = _as_dict(run.get("snapshot"))
        remote = _as_dict(snapshot.get("remote_session"))
        context: dict[str, Any] = {}
        if str(run.get("kind") or "") == "remote" and remote.get("session_id"):
            context = {
                "kind": "remote_session",
                "session_id": _clip(remote.get("session_id"), 128),
                "state": _clip(remote.get("state"), 32),
                "generation": _nonnegative_int(remote.get("generation")),
                "scopes": [
                    _clip(scope, 32) for scope in _as_list(remote.get("scopes"))[:16]
                ],
                "predecessor_session_id": _clip(
                    remote.get("predecessor_session_id"), 128,
                ),
            }
        rows.append({
            "schema": "hashmm.action-item.v1",
            "id": f"action:{run.get('id')}",
            "run_id": str(run.get("id") or ""),
            "conversation_id": str(run.get("conversation_id") or ""),
            "type": action_type,
            "priority": priority,
            "title": str(presentation.get("title") or "一项工作"),
            "summary": str(presentation.get("current_step") or ""),
            "primary_action": str(presentation.get("primary_action") or "open"),
            "context": context,
            "updated_at": float(run.get("updated_at") or 0),
        })
    rows.sort(key=lambda item: (item["priority"] != "high", -item["updated_at"]))
    return {
        "schema": ACTION_INBOX_SCHEMA,
        "items": rows,
        "count": len(rows),
        "high_priority_count": sum(1 for item in rows if item["priority"] == "high"),
    }


def _stage_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"done", "completed", "passed", "ok", "success"}:
        return "done"
    if value in {"running", "active", "in_progress", "working"}:
        return "running"
    if value in {"failed", "blocked", "error", "denied"}:
        return "blocked"
    if value in {"skipped", "cancelled", "canceled"}:
        return "skipped"
    return "pending"


def _result_kind(name: str, declared: Any = "") -> str:
    suffix = PurePath(name).suffix.lower()
    if suffix in {".docx", ".doc"}:
        return "document"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".pptx", ".ppt"}:
        return "presentation"
    if suffix in {".xlsx", ".xls", ".csv"}:
        return "spreadsheet"
    if suffix in {".html", ".htm", ".svg"}:
        return "canvas"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if suffix in {".md", ".txt", ".json", ".py", ".js", ".ts", ".tsx", ".css"}:
        return "text"
    declared_text = str(declared or "").strip().lower()
    return declared_text if declared_text in {
        "document", "pdf", "presentation", "spreadsheet", "canvas", "image", "text",
    } else "file"


def _work_results(run: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = _as_dict(run.get("snapshot"))
    manifest = _as_dict(snapshot.get("run_manifest"))
    review = _as_dict(snapshot.get("user_review"))
    changes_requested = review.get("action") == "request_changes"
    gate = _as_dict(manifest.get("completion_gate"))
    causal = _as_dict(manifest.get("causal_work_graph"))
    gate_status = str(gate.get("status") or "")
    artifact_nodes: dict[str, dict[str, Any]] = {}
    for node in _as_list(causal.get("nodes"))[:500]:
        if not isinstance(node, dict) or str(node.get("kind") or "") != "artifact":
            continue
        payload = _as_dict(node.get("payload"))
        raw_artifact_name = _clip(
            payload.get("filename") or node.get("label"), 240,
        )
        artifact_name = (
            PurePath(raw_artifact_name.replace("\\", "/")).name
            if raw_artifact_name else ""
        )
        if artifact_name:
            artifact_nodes[artifact_name.casefold()] = node
    candidates: list[dict[str, Any]] = [
        item for item in _as_list(snapshot.get("files")) if isinstance(item, dict)
    ]
    latest = snapshot.get("latest_artifact")
    if isinstance(latest, dict):
        candidates.append(latest)
    for receipt in _as_list(manifest.get("execution_receipts")):
        if not isinstance(receipt, dict):
            continue
        for artifact in _as_list(receipt.get("artifacts")):
            if isinstance(artifact, dict):
                candidates.append(artifact)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    conv_id = _clip(run.get("conversation_id"), 160)
    for item in candidates[:64]:
        raw_name = _clip(item.get("filename") or item.get("name"), 240)
        name = PurePath(raw_name.replace("\\", "/")).name if raw_name else ""
        if not name:
            continue
        kind = _result_kind(name, item.get("type") or item.get("kind"))
        result_id = "result:" + _fingerprint([run.get("id"), name, kind], 20)
        if result_id in seen:
            continue
        seen.add(result_id)
        exists = item.get("exists")
        causal_artifact = artifact_nodes.get(name.casefold(), {})
        causal_artifact_status = str(causal_artifact.get("status") or "")
        if exists is False:
            verification = "missing"
        elif changes_requested:
            # A user change request supersedes the prior delivery as a whole.
            # The file is still available, but it is no longer current against
            # the revised acceptance intent.
            verification = "stale"
        elif causal_artifact_status == "stale":
            verification = "stale"
        elif gate_status == "verified":
            verification = "verified"
        elif run.get("status") in {"delivered", "completed"}:
            verification = "ready"
        else:
            verification = "reported"
        raw_version = item.get("revision") or item.get("version") or 1
        version_ref = _fingerprint([
            result_id, raw_version, item.get("size"), item.get("content_hash"),
            run.get("revision"),
        ], 24)
        actions = ["open", "trace", "continue_editing", "regenerate"]
        download_url = ""
        if conv_id:
            download_url = (
                f"/api/conversations/{quote(conv_id, safe='')}/download/"
                f"{quote(name, safe='')}"
            )
            actions.insert(1, "download")
        results.append({
            "schema": WORK_RESULT_SCHEMA,
            "id": result_id,
            "name": name,
            "kind": kind,
            "version": max(1, int(raw_version or 1)) if str(raw_version or "").isdigit() else 1,
            "version_ref": version_ref,
            "verification": verification,
            "size": _nonnegative_int(item.get("size")),
            "download_url": download_url,
            "actions": actions,
            "evidence_node_id": _clip(causal_artifact.get("id"), 180),
            "updated_at": float(run.get("updated_at") or 0),
        })
    return results


def _work_process(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = _as_dict(run.get("snapshot"))
    manifest = _as_dict(snapshot.get("run_manifest"))
    contract = _as_dict(manifest.get("task_contract"))
    # The admitted task contract is immutable intent.  Runtime progress lives in
    # the durable handoff projection, so the user-facing task chain must prefer
    # it after work has started.  Falling back keeps older persisted runs
    # readable without pretending their static plan is live state.
    handoff = _as_dict(
        snapshot.get("long_horizon_handoff")
        or manifest.get("long_horizon_handoff")
    )
    plan_items = _as_list(handoff.get("plan")) or _as_list(contract.get("plan"))
    stages: list[dict[str, Any]] = []
    for index, item in enumerate(plan_items[:80]):
        if not isinstance(item, dict):
            continue
        label = _clip(item.get("text") or item.get("label") or item.get("task"), 300)
        if not label:
            continue
        stages.append({
            "id": _clip(item.get("id"), 160) or f"stage:{index + 1}",
            "order": index + 1,
            "label": label,
            "status": _stage_status(item.get("status")),
        })
    if not stages:
        presentation = _as_dict(run.get("presentation")) or present_run(run)
        stages.append({
            "id": "stage:runtime",
            "order": 1,
            "label": _clip(presentation.get("current_step"), 300) or "查看当前工作状态",
            "status": (
                "done" if run.get("status") in _TERMINAL
                else "blocked" if run.get("status") in {"blocked", "failed", "interrupted"}
                else "running" if run.get("status") == "running"
                else "pending"
            ),
        })

    evidence_graph = _as_dict(manifest.get("evidence_graph"))
    branches: list[dict[str, Any]] = []
    for node in _as_list(evidence_graph.get("nodes"))[:320]:
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind") or "").lower()
        if kind not in {"agent", "role", "worker", "task"}:
            continue
        branches.append({
            "id": _clip(node.get("id"), 160),
            "label": _clip(node.get("label"), 240) or "协作步骤",
            "status": _stage_status(node.get("status")),
        })
        if len(branches) >= 32:
            break

    checkpoints: list[dict[str, Any]] = []
    seen_checkpoints: set[str] = set()
    lifecycle = _as_dict(manifest.get("context_lifecycle"))
    checkpoint_id = _clip(lifecycle.get("checkpoint_id"), 96)
    if checkpoint_id:
        seen_checkpoints.add(checkpoint_id)
        checkpoints.append({
            "id": checkpoint_id,
            "label": "最近保存的上下文恢复点",
            "created_at": float(run.get("updated_at") or 0),
            "kind": "context",
            "can_resume": "resume" in _as_dict(run.get("control")).get("available_actions", []),
        })
    for event in events[-160:]:
        event_type = str(event.get("type") or "").lower()
        if "checkpoint" not in event_type and event_type not in {"paused", "interrupted"}:
            continue
        payload = _as_dict(event.get("payload"))
        event_checkpoint = _clip(payload.get("checkpoint_id") or event.get("id"), 96)
        if not event_checkpoint or event_checkpoint in seen_checkpoints:
            continue
        seen_checkpoints.add(event_checkpoint)
        checkpoints.append({
            "id": event_checkpoint,
            "label": _clip(event.get("summary"), 240) or "工作恢复点",
            "created_at": float(event.get("created_at") or 0),
            "kind": "execution" if event_type != "context_checkpoint" else "context",
            "can_resume": "resume" in _as_dict(run.get("control")).get("available_actions", []),
        })
    checkpoints.sort(key=lambda item: item["created_at"], reverse=True)
    return {
        "stages": stages,
        "branches": branches,
        "checkpoints": checkpoints[:24],
        "event_count": max(int(run.get("event_cursor") or 0), len(events)),
        "latest_events": [{
            "id": _clip(item.get("id"), 160),
            "type": _clip(item.get("type"), 64),
            "summary": _clip(item.get("summary"), 300),
            "status": _clip(item.get("status"), 40),
            "created_at": float(item.get("created_at") or 0),
        } for item in events[-40:]],
    }


def _work_evidence(run: dict[str, Any]) -> dict[str, Any]:
    snapshot = _as_dict(run.get("snapshot"))
    manifest = _as_dict(snapshot.get("run_manifest"))
    verification = _as_dict(manifest.get("verification"))
    causal = _as_dict(manifest.get("causal_work_graph"))
    graph = causal if causal.get("schema") else _as_dict(manifest.get("evidence_graph"))
    sources: list[dict[str, Any]] = []
    for node in _as_list(graph.get("nodes"))[:400]:
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind") or "")
        if kind not in {"source", "source_snapshot", "evidence", "claim"}:
            continue
        sources.append({
            "id": _clip(node.get("id"), 180),
            "kind": kind,
            "label": _clip(node.get("label"), 260) or "未命名依据",
            "status": _clip(node.get("status"), 40) or "unknown",
            "trust": _clip(node.get("trust"), 40),
            "revision": max(1, int(node.get("revision") or 1)),
        })
        if len(sources) >= 100:
            break

    checks: list[dict[str, Any]] = []
    for item in _as_list(verification.get("checks"))[:80]:
        if not isinstance(item, dict):
            continue
        checks.append({
            "id": _clip(item.get("id") or item.get("check_id"), 120),
            "label": _clip(item.get("label") or item.get("detail"), 300) or "运行检查",
            "status": _clip(item.get("status"), 40) or "not_evaluable",
            "detail": _clip(item.get("detail"), 400),
        })

    receipts: list[dict[str, Any]] = []
    invalid_receipts = 0
    for item in _as_list(manifest.get("execution_receipts"))[:160]:
        if not isinstance(item, dict):
            continue
        try:
            from hashmm.agent.execution_receipt import validate_execution_receipt
            valid = bool(validate_execution_receipt(item).get("valid"))
        except Exception:
            valid = False
        if not valid:
            invalid_receipts += 1
        action = _as_dict(item.get("action"))
        outcome = _as_dict(item.get("outcome"))
        effect = _as_dict(item.get("side_effect"))
        receipts.append({
            "id": _clip(item.get("receipt_id"), 180),
            "tool": _clip(action.get("tool"), 120) or "一次操作",
            "status": _clip(outcome.get("status"), 40) or "unknown",
            "success": bool(outcome.get("success")),
            "side_effect": _clip(effect.get("class"), 40) or "unknown",
            "reversible": bool(effect.get("reversible")),
            "valid": valid,
        })

    invalidation = _as_dict(causal.get("invalidation"))
    return {
        "status": _clip(causal.get("status") or verification.get("status"), 40) or "not_available",
        "sources": sources,
        "checks": checks,
        "execution_receipts": receipts,
        "summary": {
            "sources": len(sources),
            "checks": len(checks),
            "receipts": len(receipts),
            "invalid_receipts": invalid_receipts,
            "stale_nodes": len(_as_list(invalidation.get("stale_node_ids"))),
        },
        "invalidation": {
            "strategy": _clip(invalidation.get("strategy"), 80),
            "stale_node_ids": [
                _clip(item, 180) for item in _as_list(invalidation.get("stale_node_ids"))[:160]
            ],
        },
    }


def _completion_receipt(
    run: dict[str, Any],
    *,
    results: list[dict[str, Any]],
    evidence: dict[str, Any],
    process: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _as_dict(run.get("snapshot"))
    manifest = _as_dict(snapshot.get("run_manifest"))
    gate = _as_dict(manifest.get("completion_gate"))
    gate_status = str(gate.get("status") or "unavailable")
    causal_status = str(_as_dict(manifest.get("causal_work_graph")).get("status") or "")
    review = _as_dict(snapshot.get("user_review"))
    accepted = review.get("action") == "accept_delivery"
    changes_requested = review.get("action") == "request_changes"
    if changes_requested:
        status = "not_ready"
    elif causal_status in {"invalid", "stale"}:
        status = "blocked"
    elif gate_status == "verified":
        status = "verified"
    elif accepted and run.get("status") == "completed":
        status = "accepted_with_limits"
    elif run.get("status") == "delivered":
        status = "awaiting_review"
    elif run.get("status") == "completed":
        status = "completed_with_limits"
    else:
        status = "not_ready"

    limitations: list[str] = []
    for failure in _as_list(gate.get("failure_modes"))[:40]:
        if not isinstance(failure, dict):
            continue
        detail = _clip(failure.get("detail"), 300)
        if detail and detail not in limitations:
            limitations.append(detail)
    handoff_limit = _clip(_as_dict(manifest.get("handoff")).get("limitation"), 300)
    if handoff_limit and handoff_limit not in limitations:
        limitations.append(handoff_limit)

    side_effects = [
        {
            "tool": item.get("tool"),
            "status": item.get("status"),
            "class": item.get("side_effect"),
            "reversible": item.get("reversible"),
        }
        for item in _as_list(evidence.get("execution_receipts"))[:64]
    ]
    irreversible = sum(
        1 for item in side_effects
        if item.get("class") in {"external_write", "privileged_control"}
        and not item.get("reversible")
    )
    core = {
        "schema": COMPLETION_RECEIPT_SCHEMA,
        "run_id": str(run.get("id") or ""),
        "status": status,
        "can_claim_complete": status in {"verified", "accepted_with_limits"},
        "can_claim_verified": status == "verified",
        "summary": {
            "results": len(results),
            "sources": int(_as_dict(evidence.get("summary")).get("sources") or 0),
            "checks": int(_as_dict(evidence.get("summary")).get("checks") or 0),
            "side_effects": len(side_effects),
            "irreversible_side_effects": irreversible,
        },
        "verification": {
            "status": gate_status,
            "criteria": _as_list(gate.get("criteria"))[:40],
            "next_action": _clip(gate.get("next_action"), 300),
        },
        "user_review": {
            "status": "accepted" if accepted else "pending",
            "authority": "actual_user_confirmation" if accepted else "",
            "decided_at": float(review.get("created_at") or 0),
        },
        "results": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "version_ref": item.get("version_ref"),
                "verification": item.get("verification"),
            }
            for item in results[:64]
        ],
        "side_effects": side_effects,
        "limitations": limitations[:40],
        "recovery": {
            "checkpoints": len(_as_list(process.get("checkpoints"))),
            "can_resume": "resume" in _as_dict(run.get("control")).get("available_actions", []),
            "can_retry": "retry" in _as_dict(run.get("control")).get("available_actions", []),
            "automatic_rollback_available": bool(side_effects) and irreversible == 0,
        },
        "completed_at": float(run.get("updated_at") or 0)
        if run.get("status") in {"delivered", "completed"} else 0,
    }
    core["receipt_id"] = "done_" + _fingerprint(core, 24)
    core["integrity"] = {
        "algorithm": "sha256",
        "content_hash": _fingerprint(core, 64),
        "model_prose_is_completion_evidence": False,
    }
    return core


def _governed_next_actions(run: dict[str, Any]) -> dict[str, Any]:
    snapshot = _as_dict(run.get("snapshot"))
    manifest = _as_dict(snapshot.get("run_manifest"))
    frontier = _as_dict(manifest.get("execution_frontier"))
    harness = _as_dict(manifest.get("harness"))
    scope = _as_dict(_as_dict(harness.get("context")))
    control = _as_dict(run.get("control"))
    available = set(control.get("available_actions") or [])
    rows: list[dict[str, Any]] = []

    for action in ("resume", "retry", "pause", "cancel"):
        if action not in available:
            continue
        label, reason, risk = {
            "resume": ("从保存位置继续", "任务存在可恢复状态，继续时仍使用原权限范围", "low"),
            "retry": ("重新尝试", "原运行保留用于审计，新运行不会覆盖旧证据", "medium"),
            "pause": ("暂停这项工作", "在执行器支持的安全边界保存并暂停", "low"),
            "cancel": ("停止这项工作", "停止可能无法撤回已经发生的外部操作", "high"),
        }[action]
        rows.append({
            "id": "next:" + _fingerprint([run.get("id"), run.get("revision"), action], 20),
            "kind": "control",
            "label": label,
            "reason": reason,
            "risk": risk,
            "within_scope": True,
            "requires_confirmation": True,
            "control_action": action,
            "auto_execute": False,
        })

    for item in _as_list(frontier.get("items"))[:16]:
        if not isinstance(item, dict):
            continue
        route = _as_dict(item.get("selected_route"))
        rows.append({
            "id": "next:" + _fingerprint([
                run.get("id"), frontier.get("frontier_id"), item.get("id"),
            ], 20),
            "kind": "recommendation",
            "label": _clip(item.get("minimum_action"), 300) or "检查下一步",
            "reason": _clip(item.get("reason") or route.get("reason"), 300),
            "risk": "medium" if route.get("kind") == "tool" else "low",
            "within_scope": route.get("status") == "ready",
            "requires_confirmation": True,
            "control_action": "",
            "auto_execute": False,
        })
        if len(rows) >= 12:
            break

    if run.get("status") == "delivered":
        rows.insert(0, {
            "id": "next:" + _fingerprint([run.get("id"), run.get("revision"), "review"], 20),
            "kind": "review",
            "label": "检查并验收成果",
            "reason": "只有用户可以确认主观交付是否符合真实需求",
            "risk": "low",
            "within_scope": True,
            "requires_confirmation": True,
            "control_action": "",
            "auto_execute": False,
        })
    elif (
        run.get("status") == "waiting_input"
        and _as_dict(snapshot.get("user_review")).get("action") == "request_changes"
    ):
        rows.insert(0, {
            "id": "next:" + _fingerprint([
                run.get("id"), run.get("revision"), "continue_changes",
            ], 20),
            "kind": "recommendation",
            "label": "按修改要求继续这项工作",
            "reason": _clip(
                _as_dict(snapshot.get("user_review")).get("note"), 300,
            ) or "用户已提出修改要求",
            "risk": "low",
            "within_scope": True,
            "requires_confirmation": True,
            "control_action": "",
            "auto_execute": False,
        })

    high_risk_receipts = sum(
        1 for receipt in _as_list(manifest.get("execution_receipts"))
        if isinstance(receipt, dict)
        and str(_as_dict(receipt.get("risk")).get("level") or "") in {"high", "critical"}
    )
    return {
        "schema": NEXT_ACTIONS_SCHEMA,
        "items": rows[:12],
        "governance": {
            "scope_id": _clip(scope.get("scope_id"), 160),
            "approval_mode": _clip(scope.get("approval_mode"), 40) or "ask",
            "network_mode": _clip(scope.get("network_mode"), 40) or "unknown",
            "budgets": _safe_value(_as_dict(scope.get("budgets"))),
            "high_risk_observations": high_risk_receipts,
            "auto_execution_enabled": False,
            "widens_scope": False,
        },
        "limitation": "建议来自持久工作状态和确定性执行前沿；执行仍需通过所有者、预算、权限、Hook 与审批门禁。",
    }


def _skill_learning(run: dict[str, Any]) -> dict[str, Any]:
    """Project exact skill versions used by this run without exposing prompts.

    A run proves only that a particular promoted version was used.  It is not
    evidence that a new candidate is better, so this projection deliberately
    cannot promote, approve or score a candidate.
    """
    manifest = _as_dict(_as_dict(run.get("snapshot")).get("run_manifest"))
    used: list[dict[str, Any]] = []
    for item in _as_list(manifest.get("skill_versions"))[:8]:
        if not isinstance(item, dict):
            continue
        skill_id = _clip(item.get("skill_id"), 96)
        prompt_hash = _clip(item.get("prompt_hash"), 64).lower()
        if not skill_id or len(prompt_hash) != 64 or any(
            char not in "0123456789abcdef" for char in prompt_hash
        ):
            continue
        used.append({
            "id": skill_id,
            "name": _clip(item.get("name"), 160) or skill_id,
            "scope": _clip(item.get("scope"), 32) or "unknown",
            "version_ref": f"skill:{skill_id}:sha256:{prompt_hash}",
            "evolution_id": _clip(item.get("evolution_id"), 96),
            "status": "used",
        })
    return {
        "schema": "hashmm.skill-learning-projection.v1",
        "used_versions": used,
        "governance": {
            "records_exact_version": True,
            "prompt_body_exposed": False,
            "automatic_promotion_allowed": False,
            "owner_bound_feedback_required": True,
            "paired_replay_required_before_promotion": True,
            "safety_regression_blocks_promotion": True,
        },
        "limitation": (
            "本次运行只记录实际使用的技能版本；使用记录不会自动生成、评分或晋升候选。"
        ),
    }


def _work_protocol_projection(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    evidence: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project the durable ledger through the portable Work Protocol.

    The projection is intentionally conservative: every ledger event becomes
    an observation, while only events with a known executor boundary become an
    action.  This prevents a model-authored progress sentence from being
    represented as an executed operation.
    """
    from hashmm.work.protocol import (
        ActionRecord,
        ArtifactRef,
        EvidenceReceipt,
        ObservationRecord,
        WorkSpec,
        build_work_envelope,
    )

    snapshot = _as_dict(run.get("snapshot"))
    contract = _as_dict(_as_dict(snapshot.get("run_manifest")).get("task_contract"))

    def criterion_text(item: Any) -> str:
        if isinstance(item, dict):
            return _clip(
                item.get("label") or item.get("criterion") or item.get("description"),
                300,
            )
        return _clip(item, 300)

    criteria = tuple(
        text for text in (
            criterion_text(item)
            for item in _as_list(contract.get("success_criteria"))[:24]
        ) if text
    )
    constraints = tuple(
        text for text in (
            criterion_text(item)
            for item in _as_list(contract.get("constraints"))[:24]
        ) if text
    )
    spec = WorkSpec(
        goal=_clip(contract.get("goal"), 2_000)
        or _clip(run.get("title"), 2_000)
        or "未命名工作",
        deliverable=_clip(
            contract.get("deliverable")
            or contract.get("expected_deliverable")
            or snapshot.get("deliverable"),
            1_200,
        ),
        criteria=criteria,
        constraints=constraints,
        permission_mode=_clip(
            contract.get("permission_mode")
            or _as_dict(snapshot.get("execution_scope")).get("permission_mode")
            or "ask",
            40,
        ),
        project_id=_clip(run.get("project_id"), 160),
    )

    action_prefixes = (
        "tool_", "command_", "browser_", "computer_", "remote_",
        "workflow_", "execution_", "artifact_",
    )
    actions: list[ActionRecord] = []
    observations: list[ObservationRecord] = []
    event_observation_ids: dict[str, str] = {}
    for index, event in enumerate(events[:128]):
        event_id = _clip(event.get("id"), 120) or f"event-{index + 1}"
        event_type = _clip(event.get("type"), 64) or "runtime_event"
        status = _clip(event.get("status"), 32) or "recorded"
        summary = _clip(event.get("summary"), 500) or event_type
        payload = _as_dict(event.get("payload"))
        action_id = ""
        if event_type.startswith(action_prefixes):
            action_id = f"action:{event_id}"
            side_effect = _clip(payload.get("side_effect"), 32).lower()
            if side_effect not in {
                "none", "read", "write", "execute", "network",
                "external", "unknown",
            }:
                side_effect = "unknown"
            actions.append(ActionRecord(
                action_id=action_id,
                kind=event_type,
                summary=summary,
                actor=_clip(payload.get("actor"), 120) or "work_runtime",
                status=status,
                idempotency_key=_clip(event.get("idempotency_key"), 160),
                side_effect=side_effect,
                approval_id=_clip(payload.get("approval_id"), 120),
            ))
        observation_id = f"observation:{event_id}"
        event_observation_ids[event_id] = observation_id
        observations.append(ObservationRecord(
            observation_id=observation_id,
            action_id=action_id,
            source="durable_work_ledger",
            summary=summary,
            status=status,
            measured={
                "sequence": int(event.get("seq") or index + 1),
                "created_at": float(event.get("created_at") or 0),
            },
        ))

    artifact_refs: list[ArtifactRef] = []
    receipts: list[EvidenceReceipt] = []
    for item in results[:64]:
        artifact_id = _clip(item.get("artifact_id") or item.get("id"), 160)
        if not artifact_id:
            continue
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            revision=_nonnegative_int(item.get("version") or item.get("revision")),
            content_hash=_clip(item.get("content_hash"), 64),
            media_type=_clip(item.get("media_type"), 120)
            or "application/octet-stream",
            locator=_as_dict(item.get("locator")),
        )
        artifact_refs.append(artifact)
        verification = _clip(item.get("verification"), 32).lower()
        if verification not in {
            "pending", "reported", "verified", "failed", "stale", "missing",
        }:
            verification = "pending"
        receipts.append(EvidenceReceipt(
            receipt_id=f"result:{artifact_id}:{artifact.revision}",
            claim=f"成果已记录：{_clip(item.get('name'), 300) or artifact_id}",
            artifact_refs=(artifact,),
            verification=verification,
            verifier="work_runtime",
        ))

    for item in _as_list(evidence.get("execution_receipts"))[:64]:
        if not isinstance(item, dict):
            continue
        receipt_id = _clip(item.get("id"), 120)
        if not receipt_id:
            continue
        valid = bool(item.get("valid"))
        success = bool(item.get("success"))
        receipts.append(EvidenceReceipt(
            receipt_id=f"execution:{receipt_id}",
            claim=(
                f"执行凭证：{_clip(item.get('tool'), 160) or '受控工具'}"
            ),
            verification="verified" if valid and success else (
                "failed" if not success else "reported"
            ),
            verifier="execution_receipt_validator",
        ))

    return build_work_envelope(
        owner_id=str(run.get("user_id") or "owner"),
        run_id=str(run.get("id") or ""),
        spec=spec,
        actions=actions,
        observations=observations,
        evidence=receipts,
    )


def build_work_canvas(
    run: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the single user-facing view over plan, evidence and results.

    The canvas is a projection only.  It cannot execute a tool, widen a scope
    or convert model prose into evidence.
    """
    safe_events = [item for item in list(events or []) if isinstance(item, dict)]
    presentation = _as_dict(run.get("presentation")) or present_run(run)
    process = _work_process(run, safe_events)
    evidence = _work_evidence(run)
    results = _work_results(run)
    receipt = _completion_receipt(
        run, results=results, evidence=evidence, process=process,
    )
    from hashmm.agent.work_assurance import build_work_assurance
    assurance = build_work_assurance(
        run,
        safe_events,
        results=results,
        completion_receipt=receipt,
    )
    next_actions = _governed_next_actions(run)
    learning = _skill_learning(run)
    protocol = _work_protocol_projection(
        run, safe_events, evidence=evidence, results=results,
    )
    snapshot = _as_dict(run.get("snapshot"))
    review = _as_dict(snapshot.get("user_review"))
    changes_requested = review.get("action") == "request_changes"
    impacted_result_ids = [
        item.get("id") for item in results
        if changes_requested or item.get("verification") == "stale"
    ]
    impacted_stage_ids = [
        item.get("id") for item in _as_list(process.get("stages"))
        if isinstance(item, dict)
        and (
            changes_requested
            or item.get("status") in {"blocked"}
        )
    ]
    change_impact = {
        "schema": "hashmm.change-impact.v1",
        "requested": changes_requested,
        "reason": _clip(review.get("note"), 800) if changes_requested else "",
        "impacted_result_ids": impacted_result_ids[:64],
        "impacted_stage_ids": impacted_stage_ids[:80],
        "next_action": (
            "回到原工作确认修改范围并继续执行"
            if changes_requested else
            "当前没有尚未处理的用户修改要求"
        ),
        "integrity": {
            "construction": "deterministic_user_decision_and_dependency_projection",
            "model_inferred_impact": False,
            "auto_executes": False,
        },
    }
    contract = _as_dict(_as_dict(_as_dict(run.get("snapshot")).get("run_manifest")).get("task_contract"))
    operating = _as_dict(presentation.get("operating"))
    sync_identity = [
        run.get("id"), run.get("revision"), run.get("event_cursor"),
        [item.get("version_ref") for item in results],
        _as_dict(evidence.get("invalidation")).get("stale_node_ids"),
        receipt.get("receipt_id"),
        _as_dict(assurance.get("integrity")).get("fingerprint"),
        operating.get("revision"),
        protocol.get("fingerprint"),
    ]
    canvas = {
        "schema": WORK_CANVAS_SCHEMA,
        "run_id": str(run.get("id") or ""),
        "conversation_id": str(run.get("conversation_id") or ""),
        "overview": {
            "title": presentation.get("title"),
            "category": presentation.get("category"),
            "status_label": presentation.get("status_label"),
            "phase": presentation.get("phase"),
            "goal": _clip(contract.get("goal"), 800) or presentation.get("title"),
            "current_step": presentation.get("current_step"),
            "next_action": presentation.get("next_action"),
            "progress": presentation.get("progress"),
            "needs_user": presentation.get("needs_user"),
            "primary_action": presentation.get("primary_action"),
        },
        "process": process,
        "evidence": evidence,
        "results": results,
        "completion_receipt": receipt,
        "next_actions": next_actions,
        "learning": learning,
        "protocol": protocol,
        "change_impact": change_impact,
        "assurance": assurance,
        "operating": operating,
        "sync": {
            "revision": int(run.get("revision") or 0),
            "event_cursor": int(run.get("event_cursor") or 0),
            "change_cursor": int(run.get("change_cursor") or 0),
            "etag": "wc_" + _fingerprint(sync_identity, 32),
            "updated_at": float(run.get("updated_at") or 0),
            "active_generation_id": str(run.get("active_generation_id") or ""),
            "generation_hash": str(
                _as_dict(run.get("active_generation")).get("manifest_hash") or ""
            ),
            "invalidated_result_ids": [
                item.get("id") for item in results if item.get("verification") == "stale"
            ],
        },
        "integrity": {
            "projection_only": True,
            "auto_executes": False,
            "widens_scope": False,
            "model_prose_is_evidence": False,
            "owner_check_required_by_api": True,
        },
    }
    # V439-V473 extends the canvas with one shared desktop/App product
    # projection.  The V1 canvas remains intact for backwards compatibility.
    from hashmm.agent.work_os import build_work_projection
    canvas["product"] = build_work_projection(run, canvas)
    return canvas


def _public_run(row: Any) -> dict[str, Any]:
    snapshot = _json_load(_row_value(row, "snapshot_json", 10, "{}"))
    kind = str(_row_value(row, "kind", 3, "chat"))
    status = str(_row_value(row, "status", 6, "queued"))
    revision = int(_row_value(row, "revision", 7, 0) or 0)
    result = {
        "schema": RUN_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "user_id": str(_row_value(row, "user_id", 1, "")),
        "conversation_id": str(_row_value(row, "conv_id", 2, "")),
        "kind": kind,
        "source_id": str(_row_value(row, "source_id", 4, "")),
        "title": str(_row_value(row, "title", 5, "")),
        "status": status,
        "revision": revision,
        "event_cursor": int(_row_value(row, "event_seq", 8, 0) or 0),
        "change_cursor": int(_row_value(row, "change_seq", 9, 0) or 0),
        "snapshot": snapshot,
        "created_at": float(_row_value(row, "created_at", 11, 0) or 0),
        "updated_at": float(_row_value(row, "updated_at", 12, 0) or 0),
        "active_generation_id": str(
            _row_value(row, "active_generation_id", 13, "") or ""
        ),
        "project_id": str(_row_value(row, "project_id", 14, "") or ""),
        "execution_target": _json_load(
            _row_value(row, "execution_target_json", 15, "{}")
        ),
        "autonomy_level": max(
            0, min(int(_row_value(row, "autonomy_level", 16, 0) or 0), 4)
        ),
        "control": {
            "schema": "hashmm.work-control.v1",
            "expected_revision": revision,
            "available_actions": _available_actions_for(kind, status, snapshot),
            "side_effect_boundary": _side_effect_boundary(kind),
        },
    }
    try:
        from hashmm.agent.task_state import public_contract
        result["task_state"] = public_contract(status)
    except Exception:
        result["task_state"] = {"schema": "hashmm.task-state.v1", "state": status}
    result["presentation"] = present_run(result)
    return result


def _public_event(row: Any) -> dict[str, Any]:
    event = {
        "schema": EVENT_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "run_id": str(_row_value(row, "run_id", 1, "")),
        "seq": int(_row_value(row, "seq", 3, 0) or 0),
        "type": str(_row_value(row, "event_type", 4, "progress")),
        "status": str(_row_value(row, "status", 5, "")),
        "summary": str(_row_value(row, "summary", 6, "")),
        "payload": _json_load(_row_value(row, "payload_json", 7, "{}")),
        "created_at": float(_row_value(row, "created_at", 8, 0) or 0),
        "idempotency_key": str(
            _row_value(row, "idempotency_key", 9, "") or ""
        ),
        "expected_revision": int(
            _row_value(row, "expected_revision", 10, 0) or 0
        ),
        "generation_id": str(_row_value(row, "generation_id", 11, "") or ""),
    }
    payload = event["payload"] if isinstance(event["payload"], dict) else {}
    transition = payload.get("task_transition")
    transition = transition if isinstance(transition, dict) else {}
    event["envelope"] = {
        "schema": EVENT_ENVELOPE_SCHEMA,
        "event_id": event["id"],
        "stream_id": event["run_id"],
        "event_seq": event["seq"],
        "schema_version": EVENT_ENVELOPE_SCHEMA,
        "actor": {
            "type": "system",
            "id": _clip(transition.get("actor"), 160),
        },
        "type": f"work.{event['type']}",
        "idempotency_key": event["idempotency_key"],
        "trace_id": _clip(
            payload.get("trace_id") or transition.get("request_id")
            or event["idempotency_key"], 160,
        ),
        "occurred_at": event["created_at"],
        "payload_ref": {
            "kind": "inline_projection",
            "event_id": event["id"],
            "content_redacted": True,
        },
    }
    return event


def _public_generation(row: Any) -> dict[str, Any]:
    return {
        "schema": GENERATION_SCHEMA,
        "id": str(_row_value(row, "id", 0, "") or ""),
        "run_id": str(_row_value(row, "run_id", 1, "") or ""),
        "generation": int(_row_value(row, "generation", 3, 0) or 0),
        "status": str(_row_value(row, "status", 4, "") or ""),
        "manifest_hash": str(_row_value(row, "manifest_hash", 6, "") or ""),
        "manifest": _json_load(_row_value(row, "manifest_json", 7, "{}")),
        "created_at": float(_row_value(row, "created_at", 8, 0) or 0),
        "activated_at": float(_row_value(row, "activated_at", 9, 0) or 0),
    }


def _public_artifact_revision(row: Any) -> dict[str, Any]:
    return {
        "schema": ARTIFACT_REVISION_SCHEMA,
        "id": str(_row_value(row, "id", 0, "") or ""),
        "artifact_id": str(_row_value(row, "artifact_id", 1, "") or ""),
        "run_id": str(_row_value(row, "run_id", 2, "") or ""),
        "revision": int(_row_value(row, "revision", 4, 0) or 0),
        "content_hash": str(_row_value(row, "content_hash", 5, "") or ""),
        "media_type": str(
            _row_value(row, "media_type", 6, "application/octet-stream")
            or "application/octet-stream"
        ),
        "size_bytes": int(_row_value(row, "size_bytes", 7, 0) or 0),
        "locator": _json_load(_row_value(row, "locator_json", 8, "{}")),
        "verification": str(_row_value(row, "verification", 9, "pending") or "pending"),
        "created_at": float(_row_value(row, "created_at", 10, 0) or 0),
    }


def _generation_manifest(
    *,
    run_id: str,
    revision: int,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Build a bounded three-plane manifest without persisting source bodies."""
    run_manifest = _json_load(snapshot.get("run_manifest"))
    context = (
        _json_load(run_manifest.get("context_capsule"))
        or _json_load(snapshot.get("context_capsule"))
        or _json_load(run_manifest.get("context_lifecycle"))
    )
    causal = (
        _json_load(run_manifest.get("causal_work_graph"))
        or _json_load(run_manifest.get("evidence_graph"))
    )
    verification = _json_load(run_manifest.get("verification"))
    artifacts = [
        item for item in list(run_manifest.get("artifacts") or [])[:40]
        if isinstance(item, dict)
    ]
    latest_artifact = _json_load(snapshot.get("latest_artifact"))
    if latest_artifact:
        artifacts.append(latest_artifact)
    planes = {
        "knowledge": {
            "context_fingerprint": str(context.get("fingerprint") or "")[:64],
            "context_generation": _nonnegative_int(context.get("generation")),
            "rendered_hash": str(context.get("rendered_hash") or "")[:64],
            "source_refs": [
                {
                    "key": str(item.get("key") or "")[:80],
                    "content_hash": str(item.get("content_hash") or "")[:64],
                    "trust": str(item.get("trust") or "")[:40],
                }
                for item in list(context.get("sections") or [])[:32]
                if isinstance(item, dict)
            ],
        },
        "work": {
            "contract_hash": _fingerprint(
                _safe_value(run_manifest.get("task_contract") or {}), 64
            ),
            "graph_generation": str(causal.get("generation_id") or "")[:96],
            "graph_hash": str(
                causal.get("content_hash") or causal.get("graph_hash") or ""
            )[:64],
        },
        "evidence": {
            "verification_status": str(verification.get("status") or "")[:40],
            "verification_hash": _fingerprint(_safe_value(verification), 64),
            "artifact_refs": [
                {
                    "id": str(item.get("id") or item.get("artifact_id") or "")[:120],
                    "name": PurePath(
                        str(item.get("filename") or item.get("name") or "")
                        .replace("\\", "/")
                    ).name[:240],
                    "content_hash": str(
                        item.get("content_hash") or item.get("sha256") or ""
                    )[:64],
                    "version": _positive_int(item.get("version"), 1),
                }
                for item in artifacts[:40]
            ],
            "receipt_hashes": [
                str(item.get("receipt_id") or item.get("content_hash") or "")[:160]
                for item in list(run_manifest.get("execution_receipts") or [])[:80]
                if isinstance(item, dict)
            ],
        },
    }
    safe_planes = _safe_value(planes)
    manifest = {
        "schema": GENERATION_SCHEMA,
        "run_id": run_id,
        "revision": max(1, int(revision)),
        "planes": safe_planes,
        "plane_hashes": {
            name: _fingerprint(value, 64) for name, value in safe_planes.items()
        },
        "source_bodies_included": False,
        "model_prose_is_activation_evidence": False,
    }
    manifest["manifest_hash"] = _fingerprint(manifest, 64)
    return manifest


def _activate_generation_locked(
    conn: Any,
    *,
    run_id: str,
    user_id: str,
    revision: int,
    snapshot: dict[str, Any],
    idempotency_key: str,
    now: float,
) -> tuple[str, dict[str, Any]]:
    """Construct and activate one complete generation inside the caller txn."""
    manifest = _generation_manifest(
        run_id=run_id, revision=revision, snapshot=snapshot,
    )
    existing = conn.execute(
        "SELECT * FROM work_generations "
        "WHERE user_id=? AND run_id=? AND idempotency_key=?",
        (user_id, run_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        return str(_row_value(existing, "id", 0, "")), _public_generation(existing)
    row = conn.execute(
        "SELECT MAX(generation) AS value FROM work_generations "
        "WHERE run_id=? AND user_id=?",
        (run_id, user_id),
    ).fetchone()
    number = int(_row_value(row, "value", 0, 0) or 0) + 1
    generation_id = f"wg_{uuid.uuid4().hex}"
    conn.execute(
        "UPDATE work_generations SET status='superseded' "
        "WHERE run_id=? AND user_id=? AND status='active'",
        (run_id, user_id),
    )
    conn.execute(
        "INSERT INTO work_generations "
        "(id,run_id,user_id,generation,status,idempotency_key,manifest_hash,"
        "manifest_json,created_at,activated_at) "
        "VALUES (?,?,?,?,'active',?,?,?,?,?)",
        (
            generation_id, run_id, user_id, number, idempotency_key,
            manifest["manifest_hash"],
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            now, now,
        ),
    )
    saved = conn.execute(
        "SELECT * FROM work_generations WHERE id=? AND user_id=?",
        (generation_id, user_id),
    ).fetchone()
    return generation_id, _public_generation(saved)


def _public_command(row: Any) -> dict[str, Any]:
    persisted_status = str(_row_value(row, "status", 5, "executing"))
    created_at = float(_row_value(row, "created_at", 7, 0) or 0)
    effective_status = persisted_status
    if persisted_status == "executing" and created_at and time.time() - created_at > 120:
        # Never replay an uncertain external side effect automatically.  The
        # user sees the uncertainty and may inspect the run before acting.
        effective_status = "uncertain"
    return {
        "schema": COMMAND_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "run_id": str(_row_value(row, "run_id", 1, "")),
        "action": str(_row_value(row, "action", 3, "")),
        "expected_revision": int(_row_value(row, "expected_revision", 4, 0) or 0),
        "status": effective_status,
        "result": _json_load(_row_value(row, "result_json", 6, "{}")),
        "created_at": created_at,
        "finished_at": float(_row_value(row, "finished_at", 8, 0) or 0),
    }


def _public_decision(row: Any) -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "run_id": str(_row_value(row, "run_id", 1, "")),
        "action": str(_row_value(row, "action", 3, "")),
        "expected_revision": int(_row_value(row, "expected_revision", 4, 0) or 0),
        "status": str(_row_value(row, "status", 5, "applied")),
        "note": str(_row_value(row, "note", 6, "")),
        "result": _json_load(_row_value(row, "result_json", 7, "{}")),
        "created_at": float(_row_value(row, "created_at", 8, 0) or 0),
    }


def _next_change_seq(conn: Any) -> int:
    conn.execute(
        "INSERT INTO work_runtime_meta (id,value) VALUES (?,0) "
        "ON CONFLICT(id) DO NOTHING",
        ("global",),
    )
    conn.execute("UPDATE work_runtime_meta SET value=value+1 WHERE id=?", ("global",))
    row = conn.execute("SELECT value FROM work_runtime_meta WHERE id=?", ("global",)).fetchone()
    return int(_row_value(row, "value", 0, 0) or 0)


def _append_sync_change_locked(
    conn: Any,
    *,
    user_id: str,
    change_seq: int,
    entity_type: str,
    entity_id: str,
    operation: str,
    revision: int,
    payload: dict[str, Any],
    created_at: float,
) -> None:
    """Append an owner-bound public projection inside the state transaction."""
    safe_payload = _safe_value(payload)
    conn.execute(
        "INSERT OR IGNORE INTO work_sync_changes "
        "(id,user_id,change_seq,entity_type,entity_id,operation,revision,payload_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            f"wsc_{_fingerprint([user_id, change_seq, entity_type, entity_id], 32)}",
            _clip(user_id, 160), max(0, int(change_seq)),
            _clip(entity_type, 40), _clip(entity_id, 160),
            _clip(operation, 40), max(0, int(revision)),
            json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
            float(created_at),
        ),
    )


def high_water_cursor() -> int:
    with db._conn() as conn:  # one transaction and the repository's shared pool
        row = conn.execute("SELECT value FROM work_runtime_meta WHERE id=?", ("global",)).fetchone()
        if row is not None:
            return int(_row_value(row, "value", 0, 0) or 0)
        row = conn.execute("SELECT MAX(change_seq) AS value FROM work_runs").fetchone()
        return int(_row_value(row, "value", 0, 0) or 0)


def owner_high_water_cursor(user_id: str) -> int:
    """Return an account-local watermark without exposing other tenants' churn."""
    with db._conn() as conn:
        row = conn.execute(
            "SELECT MAX(change_seq) AS value FROM work_runs WHERE user_id=?",
            (_clip(user_id, 160),),
        ).fetchone()
        return int(_row_value(row, "value", 0, 0) or 0)


def create_run(
    *,
    user_id: str,
    kind: str,
    source_id: str,
    conv_id: str = "",
    title: str = "",
    status: str = "queued",
    snapshot: dict[str, Any] | None = None,
    run_id: str = "",
    project_id: str = "",
    execution_target: dict[str, Any] | None = None,
    autonomy_level: int = 0,
) -> dict[str, Any]:
    """Create an idempotent run keyed by (owner, kind, source_id)."""
    owner = _clip(user_id, 160)
    source = _clip(source_id, 180)
    if not owner or not source:
        raise ValueError("work run requires owner and source_id")
    safe_kind = kind if kind in _KINDS else "workflow"
    safe_status = status if status in _STATUSES else "queued"
    safe_project = _clip(project_id, 120)
    clean_conv_id = _clip(conv_id, 160)
    owned_project: dict[str, Any] | None = None
    if not safe_project and clean_conv_id:
        # A project selected in Chat follows the resulting Work automatically.
        # The join includes the owner so a guessed conversation ID cannot
        # import another tenant's project boundary.
        db._ensure_project_table()
        with db._conn() as project_conn:
            conversation = project_conn.execute(
                "SELECT project_id FROM conversations WHERE id=? AND user_id=?",
                (clean_conv_id, owner),
            ).fetchone()
        safe_project = _clip(
            _row_value(conversation, "project_id", 0, "") if conversation else "",
            120,
        )
    if safe_project:
        # Project membership is a server-owned boundary.  A guessed project ID
        # is rejected without attaching the run to another tenant's workspace.
        db._ensure_project_table()
        owned_project = db.get_project_for_user(safe_project, owner)
        if owned_project is None:
            raise ValueError("project is unavailable")
    from hashmm.agent.work_os import normalize_execution_target
    safe_target = normalize_execution_target(execution_target or {})
    safe_autonomy = max(0, min(_nonnegative_int(autonomy_level), 4))
    # Capability observation can touch tool registries, sandbox probes and
    # graph storage. Never perform it while holding the Work SQLite write
    # transaction. A second idempotency check below closes the race between
    # this observation phase and the atomic insert.
    with db._conn() as existing_conn:
        existing = existing_conn.execute(
            "SELECT * FROM work_runs WHERE user_id=? AND kind=? AND source_id=?",
            (owner, safe_kind, source),
        ).fetchone()
    if existing is not None:
        return _public_run(existing)
    rid = _clip(run_id, 96) or f"wr_{uuid.uuid4().hex}"
    snapshot_payload = dict(snapshot or {})
    if owned_project:
        project_contract = {
            "schema": "hashmm.project-contract.v1",
            "project_id": safe_project,
            "revision": max(1, int(owned_project.get("revision") or 1)),
            "name": _clip(owned_project.get("name"), 120),
            "goal": _clip(owned_project.get("goal"), 2000),
            "deliverable": _clip(owned_project.get("deliverable"), 1200),
            "custom_prompt": _clip(owned_project.get("custom_prompt"), 8000),
            "success_criteria": [
                _clip(item, 240)
                for item in _as_list(owned_project.get("success_criteria"))[:24]
                if _clip(item, 240)
            ],
            "permission_mode": _clip(
                owned_project.get("permission_mode") or "ask", 40
            ),
        }
        snapshot_payload["project_contract"] = project_contract
        snapshot_payload["project_revision"] = project_contract["revision"]
        if project_contract["goal"]:
            snapshot_payload.setdefault("goal", project_contract["goal"])
    from hashmm.agent.work_kernel import prepare_work_snapshot
    prepared_snapshot = prepare_work_snapshot(
        user_id=owner,
        run_id=rid,
        kind=safe_kind,
        title=_clip(title, 240),
        conv_id=clean_conv_id,
        snapshot=snapshot_payload,
    )
    if owned_project:
        manifest = prepared_snapshot.get("run_manifest")
        if isinstance(manifest, dict):
            contract = manifest.get("task_contract")
            if isinstance(contract, dict):
                criteria = contract.setdefault("success_criteria", [])
                existing_labels = {
                    _clip(item.get("label"), 240)
                    for item in criteria
                    if isinstance(item, dict)
                }
                for index, label in enumerate(
                    snapshot_payload["project_contract"]["success_criteria"]
                ):
                    if label in existing_labels:
                        continue
                    criteria.append({
                        "check_id": f"project-{index + 1}",
                        "label": label,
                        "required": True,
                        "source": "project",
                    })
                contract["project_id"] = safe_project
                contract["project_revision"] = snapshot_payload["project_revision"]
                if snapshot_payload["project_contract"]["deliverable"]:
                    contract["deliverable"] = snapshot_payload["project_contract"]["deliverable"]
    admission_checkpoint_id = f"rc_{uuid.uuid4().hex}"
    initial_snapshot = {
        "schema": RUN_SCHEMA,
        "last_event": "admitted",
        "latest_checkpoint_id": admission_checkpoint_id,
        **_safe_value(prepared_snapshot),
    }
    now = time.time()
    with db._conn() as conn:
        existing = conn.execute(
            "SELECT * FROM work_runs WHERE user_id=? AND kind=? AND source_id=?",
            (owner, safe_kind, source),
        ).fetchone()
        if existing is not None:
            return _public_run(existing)
        change_seq = _next_change_seq(conn)
        conn.execute(
            "INSERT INTO work_runs "
            "(id,user_id,conv_id,kind,source_id,title,status,revision,event_seq,"
            "change_seq,snapshot_json,created_at,updated_at,project_id,"
            "execution_target_json,autonomy_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,kind,source_id) DO NOTHING",
            (rid, owner, clean_conv_id, safe_kind, source, _clip(title, 240),
             safe_status, 1, 1, change_seq,
             json.dumps(initial_snapshot, ensure_ascii=False, separators=(",", ":")),
             now, now, safe_project,
             json.dumps(safe_target, ensure_ascii=False, separators=(",", ":")),
             safe_autonomy),
        )
        current = conn.execute(
            "SELECT * FROM work_runs WHERE user_id=? AND kind=? AND source_id=?",
            (owner, safe_kind, source),
        ).fetchone()
        # Another request may have won the idempotency race. Only the winner
        # owns the admission event; both callers return the same durable run.
        if current is None:
            raise RuntimeError("work run admission did not produce a row")
        if str(_row_value(current, "id", 0, "")) != rid:
            return _public_run(current)
        conn.execute(
            "INSERT INTO work_events (id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"we_{uuid.uuid4().hex}", rid, owner, 1, "admitted", safe_status,
             "任务已进入统一工作运行时", "{}", now),
        )
        admission_state = {
            "schema": "hashmm.chat-continuation.v2",
            "phase": "admitted",
            "continuation_cursor": 0,
            "completed_action_ids": [],
            "pending_approval_id": "",
            "project_id": safe_project,
            "project_revision": snapshot_payload.get("project_revision", 0),
            "retry_policy": "check_execution_receipt_before_replay",
        }
        conn.execute(
            "INSERT INTO run_checkpoints "
            "(id,run_id,user_id,generation,reason,state_json,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                admission_checkpoint_id, rid, owner, 0, "admitted",
                json.dumps(admission_state, ensure_ascii=False, separators=(",", ":")), now,
            ),
        )
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="run",
            entity_id=rid,
            operation="created",
            revision=1,
            payload=_public_run(current),
            created_at=now,
        )
        return _public_run(current)


def append_event_once(
    run_id: str,
    *,
    user_id: str,
    event_type: str,
    summary: str = "",
    status: str = "",
    payload: dict[str, Any] | None = None,
    snapshot_updates: dict[str, Any] | None = None,
    idempotency_key: str = "",
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Admit one event with owner, revision and idempotency convergence.

    The event, work snapshot, account cursor and optional three-plane graph
    generation become visible in one SQLite transaction.  A replay returns the
    first durable result.  A stale writer receives a revision conflict instead
    of overwriting newer desktop/App state.
    """
    rid = _clip(run_id, 96)
    owner = _clip(user_id, 160)
    if not rid or not owner:
        return {"state": "invalid"}
    safe_event = re.sub(r"[^a-z0-9_.-]", "_", str(event_type or "progress").lower())[:64]
    safe_status = status if status in _STATUSES else ""
    idem = re.sub(
        r"[^A-Za-z0-9_.:-]", "_", str(idempotency_key or "").strip()
    )[:120]
    revision_claim: int | None = None
    if expected_revision is not None:
        try:
            revision_claim = int(expected_revision)
        except (TypeError, ValueError):
            return {"state": "invalid"}
        if revision_claim < 1:
            return {"state": "invalid"}
    now = time.time()
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)).fetchone()
        if row is None:
            return {"state": "not_found"}
        if idem:
            duplicate = conn.execute(
                "SELECT * FROM work_events "
                "WHERE run_id=? AND user_id=? AND idempotency_key=?",
                (rid, owner, idem),
            ).fetchone()
            if duplicate is not None:
                return {
                    "state": "duplicate",
                    "run": _public_run(row),
                    "event": _public_event(duplicate),
                }
        previous = _public_run(row)
        # All surfaces share one fail-closed state machine.  A stale or
        # terminal run cannot be resurrected by a late model callback; retry
        # creates a new source occurrence instead.
        if safe_status:
            from hashmm.agent.task_state import can_transition
            if not can_transition(previous["status"], safe_status):
                return {
                    "state": "invalid_transition",
                    "current_status": previous["status"],
                    "requested_status": safe_status,
                    "run": previous,
                }
        if revision_claim is not None and previous["revision"] != revision_claim:
            return {
                "state": "revision_conflict",
                "current_revision": previous["revision"],
                "run": previous,
            }
        next_status = safe_status or previous["status"]
        snapshot = dict(previous.get("snapshot") or {})
        if isinstance(snapshot_updates, dict):
            snapshot.update(_safe_value(snapshot_updates))
        safe_payload = _safe_value(payload or {})
        if not isinstance(safe_payload, dict):
            safe_payload = {}
        checkpoint = snapshot.get("latest_checkpoint")
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        # Every admitted transition carries the same server-derived lineage.
        # Caller prose cannot replace owner/project/conversation/run identity.
        safe_payload["task_transition"] = {
            "owner_id": owner,
            "project_id": str(previous.get("project_id") or ""),
            "conversation_id": str(previous.get("conversation_id") or ""),
            "turn_id": _clip(safe_payload.get("turn_id"), 120),
            "work_run_id": rid,
            "checkpoint_id": _clip(
                checkpoint.get("id") or snapshot.get("latest_checkpoint_id"), 120,
            ),
            "actor": owner,
            "reason": _clip(summary or safe_event, 500),
            "timestamp": now,
            "request_id": idem,
            "from_state": previous["status"],
            "to_state": next_status,
        }
        from hashmm.agent.long_horizon import advance_handoff
        handoff, trace_item = advance_handoff(
            snapshot.get("long_horizon_handoff"),
            event_type=safe_event,
            summary=summary,
            status=next_status,
            payload=safe_payload,
            event_seq=previous["event_cursor"] + 1,
        )
        trace = (
            dict(snapshot.get("task_trace"))
            if isinstance(snapshot.get("task_trace"), dict)
            else {"schema": "hashmm.task-trace.v1", "items": [], "limit": 80}
        )
        trace_items = [
            item for item in list(trace.get("items") or [])
            if isinstance(item, dict)
        ]
        trace_items.append(trace_item)
        trace.update({"schema": "hashmm.task-trace.v1", "items": trace_items[-80:], "limit": 80})
        snapshot["long_horizon_handoff"] = handoff
        snapshot["task_trace"] = trace
        manifest = (
            dict(snapshot.get("run_manifest"))
            if isinstance(snapshot.get("run_manifest"), dict) else {}
        )
        if manifest:
            manifest["long_horizon_handoff"] = handoff
            manifest["task_trace"] = trace
            snapshot["run_manifest"] = manifest
        snapshot.update({"schema": RUN_SCHEMA, "last_event": safe_event, "last_summary": _clip(summary, 500)})
        next_revision = previous["revision"] + 1
        generation_id = ""
        generation: dict[str, Any] | None = None
        should_generate = bool(snapshot_updates) and any(
            key in snapshot
            for key in (
                "run_manifest", "context_capsule", "latest_artifact",
                "causal_work_graph", "task_evidence_graph",
            )
        )
        if next_status in {"delivered", "completed", "completed_with_limits"}:
            should_generate = True
        if should_generate:
            generation_key = idem or (
                f"event:{rid}:{previous['event_cursor'] + 1}:"
                f"{_fingerprint([safe_event, snapshot], 24)}"
            )
            generation_id, generation = _activate_generation_locked(
                conn,
                run_id=rid,
                user_id=owner,
                revision=next_revision,
                snapshot=snapshot,
                idempotency_key=generation_key,
                now=now,
            )
        change_seq = _next_change_seq(conn)
        update = conn.execute(
            "UPDATE work_runs SET status=?, revision=revision+1, event_seq=event_seq+1, "
            "change_seq=?, snapshot_json=?, active_generation_id=?, updated_at=? "
            "WHERE id=? AND user_id=? AND revision=?",
            (next_status, change_seq,
             json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
             generation_id or previous.get("active_generation_id", ""),
             now, rid, owner, previous["revision"]),
        )
        if not update.rowcount:
            raise RuntimeError("work event lost its revision claim")
        current = conn.execute("SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)).fetchone()
        seq = int(_row_value(current, "event_seq", 8, 0) or 0)
        event_id = f"we_{uuid.uuid4().hex}"
        try:
            conn.execute(
                "INSERT INTO work_events "
                "(id,run_id,user_id,seq,event_type,status,summary,payload_json,"
                "created_at,idempotency_key,expected_revision,generation_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, rid, owner, seq, safe_event, next_status,
             _clip(summary, 500),
                 json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
                 now, idem, revision_claim or previous["revision"], generation_id),
            )
        except sqlite3.IntegrityError:
            # The transaction is serialized locally, but preserve a defensive
            # duplicate read for alternate SQLite backends/pools.
            if not idem:
                raise
            duplicate = conn.execute(
                "SELECT * FROM work_events "
                "WHERE run_id=? AND user_id=? AND idempotency_key=?",
                (rid, owner, idem),
            ).fetchone()
            if duplicate is None:
                raise
            return {
                "state": "duplicate",
                "run": _public_run(current),
                "event": _public_event(duplicate),
            }
        event = conn.execute(
            "SELECT * FROM work_events WHERE id=? AND user_id=?",
            (event_id, owner),
        ).fetchone()
        result = {
            "state": "applied",
            "run": _public_run(current),
            "event": _public_event(event),
        }
        if generation is not None:
            result["generation"] = generation
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="run",
            entity_id=rid,
            operation=safe_event,
            revision=int(result["run"].get("revision") or 0),
            payload={
                "run": result["run"],
                "event": result["event"],
                "generation": generation or {},
            },
            created_at=now,
        )
        return result


def list_sync_changes(
    user_id: str,
    *,
    after_cursor: int = 0,
    limit: int = 250,
) -> dict[str, Any]:
    """Pull a cursor-based account delta; another owner's rows are invisible."""
    owner = _clip(user_id, 160)
    cursor = _nonnegative_int(after_cursor)
    bounded = max(1, min(int(limit or 250), 500))
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM work_sync_changes "
            "WHERE user_id=? AND change_seq>? ORDER BY change_seq ASC LIMIT ?",
            (owner, cursor, bounded),
        ).fetchall()
        high_row = conn.execute(
            "SELECT MAX(change_seq) AS value FROM work_sync_changes WHERE user_id=?",
            (owner,),
        ).fetchone()
    changes = []
    for row in rows:
        item = {
            "id": str(_row_value(row, "id", 0, "")),
            "cursor": int(_row_value(row, "change_seq", 2, 0) or 0),
            "entity_type": str(_row_value(row, "entity_type", 3, "")),
            "entity_id": str(_row_value(row, "entity_id", 4, "")),
            "operation": str(_row_value(row, "operation", 5, "")),
            "revision": int(_row_value(row, "revision", 6, 0) or 0),
            "payload": _json_load(_row_value(row, "payload_json", 7, "{}")),
            "created_at": float(_row_value(row, "created_at", 8, 0) or 0),
        }
        item["envelope"] = {
            "schema": EVENT_ENVELOPE_SCHEMA,
            "event_id": item["id"],
            "stream_id": item["entity_id"],
            "event_seq": item["cursor"],
            "schema_version": EVENT_ENVELOPE_SCHEMA,
            "actor": {"type": "system", "id": owner},
            "type": f"{item['entity_type']}.{item['operation']}",
            "idempotency_key": item["id"],
            "trace_id": _clip(
                _as_dict(item["payload"]).get("trace_id")
                or _as_dict(_as_dict(item["payload"]).get("event")).get("idempotency_key"),
                160,
            ),
            "occurred_at": item["created_at"],
            "payload_ref": {
                "kind": "inline_projection",
                "entity_type": item["entity_type"],
                "entity_id": item["entity_id"],
                "revision": item["revision"],
                "content_redacted": True,
            },
        }
        changes.append(item)
    high = int(_row_value(high_row, "value", 0, 0) or 0)
    next_cursor = changes[-1]["cursor"] if changes else max(cursor, high)
    return {
        "schema": SYNC_SCHEMA,
        "changes": changes,
        "after_cursor": cursor,
        "next_cursor": next_cursor,
        "high_water_cursor": high,
        "has_more": bool(changes and next_cursor < high),
        "authoritative": "server",
        "client_events_accepted": False,
    }


def append_event(
    run_id: str,
    *,
    user_id: str,
    event_type: str,
    summary: str = "",
    status: str = "",
    payload: dict[str, Any] | None = None,
    snapshot_updates: dict[str, Any] | None = None,
    idempotency_key: str = "",
    expected_revision: int | None = None,
) -> dict[str, Any] | None:
    """Compatibility wrapper returning the durable run for admitted/replayed events."""
    result = append_event_once(
        run_id,
        user_id=user_id,
        event_type=event_type,
        summary=summary,
        status=status,
        payload=payload,
        snapshot_updates=snapshot_updates,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
    return result.get("run") if result.get("state") in {"applied", "duplicate"} else None


def register_artifact_revision(
    run_id: str,
    *,
    user_id: str,
    artifact_id: str,
    content_hash: str,
    media_type: str = "application/octet-stream",
    size_bytes: int = 0,
    locator: dict[str, Any] | None = None,
    verification: str = "pending",
    expected_run_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically register one content-addressed Artifact generation.

    The content hash is the idempotency boundary.  The Artifact row, work
    event, snapshot, account cursor and active three-plane generation commit
    together, so desktop and App cannot observe a file version without the
    corresponding durable work state.
    """
    rid = _clip(run_id, 96)
    owner = _clip(user_id, 160)
    aid = re.sub(r"[^A-Za-z0-9_.:-]", "_", _clip(artifact_id, 160))
    digest = str(content_hash or "").strip().lower()
    safe_media = _clip(media_type, 120) or "application/octet-stream"
    safe_verification = str(verification or "pending").strip().lower()
    allowed_verification = {
        "pending", "reported", "ready", "verified", "stale", "missing", "failed",
    }
    try:
        safe_size = max(0, min(int(size_bytes or 0), 1_099_511_627_776))
    except (TypeError, ValueError):
        return {"state": "invalid"}
    revision_claim: int | None = None
    if expected_run_revision is not None:
        try:
            revision_claim = int(expected_run_revision)
        except (TypeError, ValueError):
            return {"state": "invalid"}
    if (
        not rid
        or not owner
        or not aid
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or safe_verification not in allowed_verification
        or (revision_claim is not None and revision_claim < 1)
    ):
        return {"state": "invalid"}
    safe_locator = _safe_value(locator or {})
    if not isinstance(safe_locator, dict):
        safe_locator = {}
    now = time.time()
    with db._conn() as conn:
        run_row = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?",
            (rid, owner),
        ).fetchone()
        if run_row is None:
            return {"state": "not_found"}
        existing = conn.execute(
            "SELECT * FROM artifact_revisions "
            "WHERE user_id=? AND artifact_id=? AND content_hash=?",
            (owner, aid, digest),
        ).fetchone()
        if existing is not None:
            if str(_row_value(existing, "run_id", 2, "")) != rid:
                return {"state": "artifact_id_conflict"}
            return {
                "state": "duplicate",
                "artifact": _public_artifact_revision(existing),
                "run": _public_run(run_row),
            }
        previous = _public_run(run_row)
        if revision_claim is not None and previous["revision"] != revision_claim:
            return {
                "state": "revision_conflict",
                "current_revision": previous["revision"],
                "run": previous,
            }
        foreign_run = conn.execute(
            "SELECT 1 FROM artifact_revisions "
            "WHERE user_id=? AND artifact_id=? AND run_id<>? LIMIT 1",
            (owner, aid, rid),
        ).fetchone()
        if foreign_run is not None:
            return {"state": "artifact_id_conflict"}
        row = conn.execute(
            "SELECT MAX(revision) AS value FROM artifact_revisions "
            "WHERE user_id=? AND artifact_id=? AND run_id=?",
            (owner, aid, rid),
        ).fetchone()
        artifact_revision = int(_row_value(row, "value", 0, 0) or 0) + 1
        artifact_row_id = f"ar_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO artifact_revisions "
            "(id,artifact_id,run_id,user_id,revision,content_hash,media_type,"
            "size_bytes,locator_json,verification,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                artifact_row_id, aid, rid, owner, artifact_revision, digest,
                safe_media, safe_size,
                json.dumps(safe_locator, ensure_ascii=False, separators=(",", ":")),
                safe_verification, now,
            ),
        )
        saved_artifact = conn.execute(
            "SELECT * FROM artifact_revisions WHERE id=? AND user_id=?",
            (artifact_row_id, owner),
        ).fetchone()
        public_artifact = _public_artifact_revision(saved_artifact)

        snapshot = dict(previous.get("snapshot") or {})
        prior_refs = [
            item for item in list(snapshot.get("artifact_revisions") or [])
            if isinstance(item, dict)
        ]
        prior_refs.append({
            "id": public_artifact["id"],
            "artifact_id": aid,
            "version": artifact_revision,
            "content_hash": digest,
            "media_type": safe_media,
            "size_bytes": safe_size,
            "verification": safe_verification,
            "locator": safe_locator,
        })
        artifact_refs = prior_refs[-40:]
        latest_artifact = {
            "id": aid,
            "artifact_id": aid,
            "name": _clip(
                safe_locator.get("filename") or safe_locator.get("name") or aid,
                240,
            ),
            "version": artifact_revision,
            "content_hash": digest,
            "media_type": safe_media,
            "size_bytes": safe_size,
            "verification": safe_verification,
            "locator": safe_locator,
        }
        snapshot.update({
            "schema": RUN_SCHEMA,
            "last_event": "artifact_revision",
            "last_summary": f"Artifact {aid} revision {artifact_revision}",
            "artifact_revisions": artifact_refs,
            "latest_artifact": latest_artifact,
        })
        next_revision = previous["revision"] + 1
        generation_id, generation = _activate_generation_locked(
            conn,
            run_id=rid,
            user_id=owner,
            revision=next_revision,
            snapshot=snapshot,
            idempotency_key=f"artifact:{aid}:{digest}",
            now=now,
        )
        change_seq = _next_change_seq(conn)
        update = conn.execute(
            "UPDATE work_runs SET revision=revision+1,event_seq=event_seq+1,"
            "change_seq=?,snapshot_json=?,active_generation_id=?,updated_at=? "
            "WHERE id=? AND user_id=? AND revision=?",
            (
                change_seq,
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                generation_id, now, rid, owner, previous["revision"],
            ),
        )
        if not update.rowcount:
            raise RuntimeError("artifact revision lost its run revision claim")
        current = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?",
            (rid, owner),
        ).fetchone()
        seq = int(_row_value(current, "event_seq", 8, 0) or 0)
        event_id = f"we_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,"
            "created_at,idempotency_key,expected_revision,generation_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id, rid, owner, seq, "artifact_revision",
                previous["status"],
                f"Artifact {aid} revision {artifact_revision}",
                json.dumps({
                    "artifact_id": aid,
                    "revision": artifact_revision,
                    "content_hash": digest,
                    "media_type": safe_media,
                    "size_bytes": safe_size,
                    "verification": safe_verification,
                }, ensure_ascii=False, separators=(",", ":")),
                now, f"artifact:{aid}:{digest}",
                revision_claim or previous["revision"], generation_id,
            ),
        )
        event = conn.execute(
            "SELECT * FROM work_events WHERE id=? AND user_id=?",
            (event_id, owner),
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="artifact",
            entity_id=aid,
            operation="revision_registered",
            revision=artifact_revision,
            payload={
                "artifact": public_artifact,
                "run": _public_run(current),
                "event": _public_event(event),
            },
            created_at=now,
        )
        return {
            "state": "applied",
            "artifact": public_artifact,
            "run": _public_run(current),
            "event": _public_event(event),
            "generation": generation,
        }


def admit_command(
    run_id: str,
    *,
    user_id: str,
    command_id: str,
    action: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Atomically validate and claim one control command.

    The row is stored as ``executing`` before the dispatcher performs a side
    effect. A repeated command id returns the original record and is never
    dispatched again. This gives exactly-once admission and at-most-once local
    dispatch without pretending an external API is transactionally reversible.
    """
    rid = _clip(run_id, 96)
    owner = _clip(user_id, 160)
    cid = _clip(command_id, 120)
    safe_action = str(action or "").strip().lower()
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        revision = 0
    if not rid or not owner or not cid or safe_action not in _ACTIONS or revision < 1:
        return {"state": "invalid"}
    with db._conn() as conn:
        run_row = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner),
        ).fetchone()
        if run_row is None:
            return {"state": "not_found"}
        existing = conn.execute(
            "SELECT * FROM work_commands WHERE id=? AND user_id=?", (cid, owner),
        ).fetchone()
        if existing is not None:
            same_scope = (
                str(_row_value(existing, "run_id", 1, "")) == rid
                and str(_row_value(existing, "user_id", 2, "")) == owner
                and str(_row_value(existing, "action", 3, "")) == safe_action
            )
            return {
                "state": "duplicate" if same_scope else "command_id_conflict",
                **({"command": _public_command(existing)} if same_scope else {}),
            }
        run = _public_run(run_row)
        if run["revision"] != revision:
            return {
                "state": "revision_conflict",
                "current_revision": run["revision"],
                "run": run,
            }
        if safe_action not in run["control"]["available_actions"]:
            return {"state": "unsupported", "run": run}
        now = time.time()
        try:
            conn.execute(
                "INSERT INTO work_commands "
                "(id,run_id,user_id,action,expected_revision,status,result_json,created_at,finished_at) "
                "VALUES (?,?,?,?,?,'executing','{}',?,0)",
                (cid, rid, owner, safe_action, revision, now),
            )
        except sqlite3.IntegrityError:
            # A concurrent caller may have won the command-id race. Read it
            # back and preserve the same no-replay semantics.
            raced = conn.execute(
                "SELECT * FROM work_commands WHERE id=? AND user_id=?", (cid, owner),
            ).fetchone()
            if raced is not None and str(_row_value(raced, "run_id", 1, "")) == rid \
                    and str(_row_value(raced, "user_id", 2, "")) == owner \
                    and str(_row_value(raced, "action", 3, "")) == safe_action:
                return {"state": "duplicate", "command": _public_command(raced)}
            return {"state": "command_id_conflict"}
        claimed = conn.execute(
            "SELECT * FROM work_commands WHERE id=? AND user_id=?", (cid, owner),
        ).fetchone()
        return {"state": "admitted", "command": _public_command(claimed), "run": run}


def finalize_command(
    command_id: str,
    *,
    user_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Finalize a claimed command once; later calls return the first result."""
    final_status = status if status in {"applied", "failed"} else "failed"
    safe_result = _safe_value(result or {})
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_commands WHERE id=? AND user_id=?",
            (_clip(command_id, 120), _clip(user_id, 160)),
        ).fetchone()
        if row is None:
            return None
        if str(_row_value(row, "status", 5, "")) == "executing":
            conn.execute(
                "UPDATE work_commands SET status=?,result_json=?,finished_at=? "
                "WHERE id=? AND user_id=? AND status='executing'",
                (final_status, json.dumps(safe_result, ensure_ascii=False, separators=(",", ":")),
                 now, command_id, user_id),
            )
            row = conn.execute(
                "SELECT * FROM work_commands WHERE id=? AND user_id=?", (command_id, user_id),
            ).fetchone()
        return _public_command(row)


def get_command(command_id: str, user_id: str) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_commands WHERE id=? AND user_id=?", (command_id, user_id),
        ).fetchone()
    return _public_command(row) if row is not None else None


def apply_decision(
    run_id: str,
    *,
    user_id: str,
    decision_id: str,
    action: str,
    expected_revision: int,
    note: str = "",
) -> dict[str, Any]:
    """Atomically persist a delivery decision and advance the work ledger.

    Decisions never dispatch tools.  They are actual user authority for
    accepting a delivered result or asking for changes.  A stale/invalid
    causal graph cannot be overridden by an acceptance click.
    """
    rid = _clip(run_id, 96)
    owner = _clip(user_id, 160)
    did = _clip(decision_id, 120)
    safe_action = str(action or "").strip().lower()
    safe_note = _clip(note, 800)
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        revision = 0
    if not rid or not owner or not did or safe_action not in _DECISIONS or revision < 1:
        return {"ok": False, "error": "invalid"}
    if safe_action == "request_changes" and len(safe_note) < 2:
        return {"ok": False, "error": "note_required"}

    now = time.time()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?",
            (rid, owner),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found"}
        existing = conn.execute(
            "SELECT * FROM work_decisions WHERE id=? AND user_id=?",
            (did, owner),
        ).fetchone()
        if existing is not None:
            same_scope = (
                str(_row_value(existing, "run_id", 1, "")) == rid
                and str(_row_value(existing, "action", 3, "")) == safe_action
            )
            if not same_scope:
                return {"ok": False, "error": "decision_id_conflict"}
            current = conn.execute(
                "SELECT * FROM work_runs WHERE id=? AND user_id=?",
                (rid, owner),
            ).fetchone()
            return {
                "ok": True,
                "duplicate": True,
                "decision": _public_decision(existing),
                "run": _public_run(current),
            }

        run = _public_run(row)
        if run["revision"] != revision:
            return {
                "ok": False,
                "error": "revision_conflict",
                "current_revision": run["revision"],
                "run": run,
            }
        current_status = str(run.get("status") or "")
        snapshot = dict(run.get("snapshot") or {})
        manifest = _as_dict(snapshot.get("run_manifest"))
        gate = _as_dict(manifest.get("completion_gate"))
        causal = _as_dict(manifest.get("causal_work_graph"))
        if safe_action == "accept_delivery":
            if current_status != "delivered":
                return {"ok": False, "error": "unsupported", "run": run}
            if str(causal.get("status") or "") in {"invalid", "stale"}:
                return {
                    "ok": False,
                    "error": "verification_blocked",
                    "reason": "来源或执行凭证已失效，重新核验后才能验收",
                    "run": run,
                }
            if str(gate.get("status") or "") in {"blocked", "incomplete"}:
                return {
                    "ok": False,
                    "error": "verification_blocked",
                    "reason": _clip(gate.get("next_action"), 300) or "仍有必需完成条件未闭环",
                    "run": run,
                }
            next_status = "completed"
            event_type = "delivery_accepted"
            summary = "用户已验收本次交付"
            decision_result = {
                "accepted": True,
                "verification_status": str(gate.get("status") or "unavailable"),
                "verified": str(gate.get("status") or "") == "verified",
            }
        else:
            if current_status not in {"delivered", "completed"}:
                return {"ok": False, "error": "unsupported", "run": run}
            next_status = "waiting_input"
            event_type = "changes_requested"
            summary = "用户已提出修改要求，请回到原工作继续"
            decision_result = {"accepted": False, "reopened": True}

        snapshot["user_review"] = {
            "id": did,
            "action": safe_action,
            "note": safe_note,
            "authority": "actual_user_confirmation",
            "created_at": now,
        }
        snapshot.update({
            "schema": RUN_SCHEMA,
            "last_event": event_type,
            "last_summary": summary,
        })
        change_seq = _next_change_seq(conn)
        conn.execute(
            "INSERT INTO work_decisions "
            "(id,run_id,user_id,action,expected_revision,status,note,result_json,created_at) "
            "VALUES (?,?,?,?,?,'applied',?,?,?)",
            (
                did, rid, owner, safe_action, revision, safe_note,
                json.dumps(
                    _safe_value(decision_result),
                    ensure_ascii=False, separators=(",", ":"),
                ),
                now,
            ),
        )
        conn.execute(
            "UPDATE work_runs SET status=?, revision=revision+1, "
            "event_seq=event_seq+1, change_seq=?, snapshot_json=?, updated_at=? "
            "WHERE id=? AND user_id=? AND revision=?",
            (
                next_status, change_seq,
                json.dumps(
                    _safe_value(snapshot),
                    ensure_ascii=False, separators=(",", ":"),
                ),
                now, rid, owner, revision,
            ),
        )
        current = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?",
            (rid, owner),
        ).fetchone()
        if current is None or int(_row_value(current, "revision", 7, 0) or 0) != revision + 1:
            raise RuntimeError("work decision lost its revision claim")
        seq = int(_row_value(current, "event_seq", 8, 0) or 0)
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"we_{uuid.uuid4().hex}", rid, owner, seq, event_type,
                next_status, summary,
                json.dumps(
                    _safe_value({
                        "decision_id": did,
                        "action": safe_action,
                        "note": safe_note,
                    }),
                    ensure_ascii=False, separators=(",", ":"),
                ),
                now,
            ),
        )
        decision = conn.execute(
            "SELECT * FROM work_decisions WHERE id=? AND user_id=?",
            (did, owner),
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="decision",
            entity_id=did,
            operation=safe_action,
            revision=revision + 1,
            payload={
                "decision": _public_decision(decision),
                "run": _public_run(current),
            },
            created_at=now,
        )
        return {
            "ok": True,
            "duplicate": False,
            "decision": _public_decision(decision),
            "run": _public_run(current),
        }


def _public_execution_lease(row: Any) -> dict[str, Any]:
    return {
        "schema": EXECUTION_LEASE_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "run_id": str(_row_value(row, "run_id", 1, "")),
        "holder_type": str(_row_value(row, "holder_type", 3, "")),
        "holder_id": str(_row_value(row, "holder_id", 4, "")),
        "state": str(_row_value(row, "state", 5, "")),
        "generation": int(_row_value(row, "generation", 6, 0) or 0),
        "acquired_at": float(_row_value(row, "acquired_at", 7, 0) or 0),
        "heartbeat_at": float(_row_value(row, "heartbeat_at", 8, 0) or 0),
        "expires_at": float(_row_value(row, "expires_at", 9, 0) or 0),
        "released_at": float(_row_value(row, "released_at", 11, 0) or 0),
    }


def get_active_execution_lease(run_id: str, user_id: str) -> dict[str, Any] | None:
    """Return an unexpired owner-bound lease; expiry revokes authority."""
    now = time.time()
    with db._conn() as conn:
        conn.execute(
            "UPDATE work_execution_leases SET state='expired' "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at<=?",
            (_clip(run_id, 96), _clip(user_id, 160), now),
        )
        row = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at>? "
            "ORDER BY generation DESC LIMIT 1",
            (_clip(run_id, 96), _clip(user_id, 160), now),
        ).fetchone()
    return _public_execution_lease(row) if row is not None else None


def request_execution_device(
    run_id: str,
    *,
    user_id: str,
    device_id: str,
    device_label: str = "",
    expected_revision: int,
) -> dict[str, Any]:
    """Select an owner-visible device without granting execution authority.

    Selection is user intent.  The desktop must still atomically claim its
    queue item and acquire the execution lease before it can act.
    """
    rid = _clip(run_id, 96)
    owner = _clip(user_id, 160)
    target_id = _clip(device_id, 120)
    if not rid or not owner or not target_id or int(expected_revision or 0) < 1:
        return {"ok": False, "error": "invalid"}
    now = time.time()
    with db._conn() as conn:
        run = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        if run is None:
            return {"ok": False, "error": "not_found"}
        revision = int(_row_value(run, "revision", 7, 0) or 0)
        if revision != int(expected_revision):
            return {
                "ok": False, "error": "revision_conflict",
                "current_revision": revision,
            }
        active = conn.execute(
            "SELECT * FROM work_execution_leases WHERE run_id=? AND user_id=? "
            "AND state='active' AND expires_at>? ORDER BY generation DESC LIMIT 1",
            (rid, owner, now),
        ).fetchone()
        if active is not None and str(
            _row_value(active, "holder_id", 4, "")
        ) != target_id:
            return {
                "ok": False, "error": "already_leased",
                "lease": _public_execution_lease(active),
            }
        target = {
            "schema": "hashmm.execution-placement.v1",
            "kind": "remote_device",
            "target_id": target_id,
            "label": _clip(device_label, 120) or "已选择的电脑",
            "state": "selected",
            "requested_by": "user",
            "authority_expanded": False,
        }
        snapshot = _json_load(_row_value(run, "snapshot_json", 10, "{}"))
        snapshot["execution_request"] = {
            "device_id": target_id,
            "requested_at": now,
            "authority_granted": False,
        }
        event_seq = int(_row_value(run, "event_seq", 8, 0) or 0) + 1
        change_seq = _next_change_seq(conn)
        changed = conn.execute(
            "UPDATE work_runs SET execution_target_json=?,snapshot_json=?,"
            "revision=revision+1,event_seq=?,change_seq=?,updated_at=? "
            "WHERE id=? AND user_id=? AND revision=?",
            (
                json.dumps(target, ensure_ascii=False, separators=(",", ":")),
                json.dumps(_safe_value(snapshot), ensure_ascii=False, separators=(",", ":")),
                event_seq, change_seq, now, rid, owner, revision,
            ),
        )
        if changed.rowcount != 1:
            return {"ok": False, "error": "revision_conflict"}
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"we_{uuid.uuid4().hex}", rid, owner, event_seq,
                "execution_device_selected",
                str(_row_value(run, "status", 6, "")),
                "已选择在线电脑；等待该电脑取得执行权",
                json.dumps({
                    "device_id": target_id,
                    "authority_granted": False,
                }, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="placement",
            entity_id=rid,
            operation="selected",
            revision=revision + 1,
            payload={"run": _public_run(updated), "execution_target": target},
            created_at=now,
        )
    return {"ok": True, "run": _public_run(updated), "execution_target": target}


def acquire_execution_lease(
    run_id: str,
    *,
    user_id: str,
    holder_type: str,
    holder_id: str,
    expected_revision: int,
    idempotency_key: str,
    ttl_seconds: int = 90,
) -> dict[str, Any]:
    """Claim one Work on a device/cloud executor without widening its scope."""
    owner = _clip(user_id, 160)
    rid = _clip(run_id, 96)
    lease_key = _clip(idempotency_key, 120)
    safe_holder_type = holder_type if holder_type in {
        "local", "remote_device", "cloud",
    } else ""
    safe_holder_id = _clip(holder_id, 160)
    if not owner or not rid or not lease_key or not safe_holder_type:
        return {"ok": False, "error": "invalid_request"}
    if safe_holder_type != "cloud" and not safe_holder_id:
        return {"ok": False, "error": "invalid_request"}
    if safe_holder_type == "cloud" and not safe_holder_id:
        safe_holder_id = "cloud"
    ttl = max(15, min(_positive_int(ttl_seconds, 90), 300))
    now = time.time()
    with db._conn() as conn:
        run = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?",
            (rid, owner),
        ).fetchone()
        if run is None:
            return {"ok": False, "error": "not_found"}
        duplicate = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE user_id=? AND run_id=? AND idempotency_key=?",
            (owner, rid, lease_key),
        ).fetchone()
        if duplicate is not None:
            duplicate_active = bool(
                str(_row_value(duplicate, "state", 5, "")) == "active"
                and float(_row_value(duplicate, "expires_at", 9, 0) or 0) > now
            )
            return {
                "ok": duplicate_active,
                "duplicate": True,
                "lease": _public_execution_lease(duplicate),
                "run": _public_run(run),
                **({} if duplicate_active else {
                    "error": "idempotency_replayed_after_expiry",
                }),
            }
        current_revision = int(_row_value(run, "revision", 7, 0) or 0)
        if current_revision != _positive_int(expected_revision):
            return {
                "ok": False, "error": "revision_conflict",
                "current_revision": current_revision,
            }
        conn.execute(
            "UPDATE work_execution_leases SET state='expired' "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at<=?",
            (rid, owner, now),
        )
        active = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at>?",
            (rid, owner, now),
        ).fetchone()
        if active is not None:
            return {
                "ok": False,
                "error": "already_leased",
                "lease": _public_execution_lease(active),
            }
        selected_target = _json_load(
            _row_value(run, "execution_target_json", 15, "{}")
        )
        selected_id = _clip(selected_target.get("target_id"), 160)
        if (
            selected_target.get("kind") == "remote_device"
            and selected_id
            and selected_id != safe_holder_id
        ):
            return {"ok": False, "error": "different_device_selected"}
        generation_row = conn.execute(
            "SELECT MAX(generation) AS value FROM work_execution_leases WHERE run_id=?",
            (rid,),
        ).fetchone()
        generation = int(_row_value(generation_row, "value", 0, 0) or 0) + 1
        lease_id = f"wl_{uuid.uuid4().hex}"
        expires = now + ttl
        conn.execute(
            "INSERT INTO work_execution_leases "
            "(id,run_id,user_id,holder_type,holder_id,state,generation,"
            "acquired_at,heartbeat_at,expires_at,idempotency_key,released_at) "
            "VALUES (?,?,?,?,?,'active',?,?,?,?,?,0)",
            (
                lease_id, rid, owner, safe_holder_type, safe_holder_id,
                generation, now, now, expires, lease_key,
            ),
        )
        next_event = int(_row_value(run, "event_seq", 8, 0) or 0) + 1
        change_seq = _next_change_seq(conn)
        target = {
            "schema": "hashmm.execution-placement.v1",
            "kind": safe_holder_type,
            "target_id": safe_holder_id,
            "state": "leased",
            "requested_by": "execution_lease",
            "authority_expanded": False,
        }
        conn.execute(
            "UPDATE work_runs SET revision=revision+1,event_seq=?,change_seq=?,"
            "execution_target_json=?,updated_at=? WHERE id=? AND user_id=?",
            (
                next_event, change_seq,
                json.dumps(target, ensure_ascii=False, separators=(",", ":")),
                now, rid, owner,
            ),
        )
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,"
            "created_at,idempotency_key,expected_revision,generation_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"we_{uuid.uuid4().hex}", rid, owner, next_event,
                "execution_lease_acquired", str(_row_value(run, "status", 6, "")),
                "工作已由一个受限执行位置接管",
                json.dumps({
                    "lease_id": lease_id,
                    "holder_type": safe_holder_type,
                    "holder_id": safe_holder_id,
                    "generation": generation,
                    "expires_at": expires,
                }, ensure_ascii=False, separators=(",", ":")),
                now, lease_key, current_revision, "",
            ),
        )
        lease = conn.execute(
            "SELECT * FROM work_execution_leases WHERE id=? AND user_id=?",
            (lease_id, owner),
        ).fetchone()
        updated = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="execution_lease",
            entity_id=lease_id,
            operation="acquired",
            revision=current_revision + 1,
            payload={
                "lease": _public_execution_lease(lease),
                "run": _public_run(updated),
            },
            created_at=now,
        )
    return {
        "ok": True, "duplicate": False,
        "lease": _public_execution_lease(lease),
        "run": _public_run(updated),
    }


def heartbeat_execution_lease(
    run_id: str,
    *,
    user_id: str,
    lease_id: str,
    generation: int,
    ttl_seconds: int = 90,
) -> dict[str, Any]:
    """Renew only the currently active generation owned by this account."""
    now = time.time()
    ttl = max(15, min(_positive_int(ttl_seconds, 90), 300))
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE id=? AND run_id=? AND user_id=?",
            (_clip(lease_id, 120), _clip(run_id, 96), _clip(user_id, 160)),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found"}
        if (
            str(_row_value(row, "state", 5, "")) != "active"
            or int(_row_value(row, "generation", 6, 0) or 0)
            != _positive_int(generation)
            or float(_row_value(row, "expires_at", 9, 0) or 0) <= now
        ):
            if str(_row_value(row, "state", 5, "")) == "active":
                conn.execute(
                    "UPDATE work_execution_leases SET state='expired' WHERE id=?",
                    (_clip(lease_id, 120),),
                )
            return {"ok": False, "error": "lease_expired_or_replaced"}
        conn.execute(
            "UPDATE work_execution_leases SET heartbeat_at=?,expires_at=? WHERE id=?",
            (now, now + ttl, _clip(lease_id, 120)),
        )
        renewed = conn.execute(
            "SELECT * FROM work_execution_leases WHERE id=?",
            (_clip(lease_id, 120),),
        ).fetchone()
    return {"ok": True, "lease": _public_execution_lease(renewed)}


def release_execution_lease(
    run_id: str,
    *,
    user_id: str,
    lease_id: str,
    generation: int,
) -> dict[str, Any]:
    """Release the exact active generation; stale devices cannot release a successor."""
    now = time.time()
    owner = _clip(user_id, 160)
    rid = _clip(run_id, 96)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE id=? AND run_id=? AND user_id=?",
            (_clip(lease_id, 120), rid, owner),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found"}
        if (
            str(_row_value(row, "state", 5, "")) != "active"
            or int(_row_value(row, "generation", 6, 0) or 0)
            != _positive_int(generation)
        ):
            return {"ok": False, "error": "lease_expired_or_replaced"}
        run = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        if run is None:
            return {"ok": False, "error": "not_found"}
        conn.execute(
            "UPDATE work_execution_leases SET state='released',released_at=?,"
            "heartbeat_at=? WHERE id=?",
            (now, now, _clip(lease_id, 120)),
        )
        next_event = int(_row_value(run, "event_seq", 8, 0) or 0) + 1
        change_seq = _next_change_seq(conn)
        target = {
            "schema": "hashmm.execution-placement.v1",
            "kind": "waiting_device",
            "target_id": "",
            "state": "waiting",
            "requested_by": "lease_release",
            "authority_expanded": False,
        }
        conn.execute(
            "UPDATE work_runs SET revision=revision+1,event_seq=?,change_seq=?,"
            "execution_target_json=?,updated_at=? WHERE id=? AND user_id=?",
            (
                next_event, change_seq,
                json.dumps(target, ensure_ascii=False, separators=(",", ":")),
                now, rid, owner,
            ),
        )
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"we_{uuid.uuid4().hex}", rid, owner, next_event,
                "execution_lease_released", str(_row_value(run, "status", 6, "")),
                "执行位置已释放，可由其他设备接力",
                json.dumps({
                    "lease_id": _clip(lease_id, 120),
                    "generation": _positive_int(generation),
                }, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        released = conn.execute(
            "SELECT * FROM work_execution_leases WHERE id=?",
            (_clip(lease_id, 120),),
        ).fetchone()
        updated = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="execution_lease",
            entity_id=_clip(lease_id, 120),
            operation="released",
            revision=int(_row_value(updated, "revision", 7, 0) or 0),
            payload={
                "lease": _public_execution_lease(released),
                "run": _public_run(updated),
            },
            created_at=now,
        )
    return {
        "ok": True,
        "lease": _public_execution_lease(released),
        "run": _public_run(updated),
    }


def _public_annotation(row: Any) -> dict[str, Any]:
    impacted = []
    try:
        value = json.loads(_row_value(row, "impacted_json", 8, "[]") or "[]")
        impacted = value if isinstance(value, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        impacted = []
    return {
        "schema": ARTIFACT_ANNOTATION_SCHEMA,
        "id": str(_row_value(row, "id", 0, "")),
        "run_id": str(_row_value(row, "run_id", 1, "")),
        "artifact_id": str(_row_value(row, "artifact_id", 3, "")),
        "artifact_revision": int(
            _row_value(row, "artifact_revision", 4, 0) or 0
        ),
        "target": _json_load(_row_value(row, "target_json", 5, "{}")),
        "note": str(_row_value(row, "note", 6, "")),
        "status": str(_row_value(row, "status", 7, "open")),
        "impacted_result_ids": [
            _clip(item, 160) for item in impacted[:64] if _clip(item, 160)
        ],
        "created_at": float(_row_value(row, "created_at", 10, 0) or 0),
        "resolved_at": float(_row_value(row, "resolved_at", 11, 0) or 0),
    }


def list_artifact_annotations(run_id: str, user_id: str) -> list[dict[str, Any]]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM work_artifact_annotations "
            "WHERE run_id=? AND user_id=? ORDER BY created_at DESC LIMIT 100",
            (_clip(run_id, 96), _clip(user_id, 160)),
        ).fetchall()
    return [_public_annotation(row) for row in rows]


def add_artifact_annotation(
    run_id: str,
    *,
    user_id: str,
    annotation_id: str,
    idempotency_key: str,
    artifact_id: str,
    artifact_revision: int,
    target: dict[str, Any],
    note: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Record region-level feedback and stale only the referenced revision."""
    owner = _clip(user_id, 160)
    rid = _clip(run_id, 96)
    aid = _clip(artifact_id, 160)
    annotation = _clip(annotation_id, 120)
    idem = _clip(idempotency_key, 120)
    safe_note = _clip(note, 800)
    safe_target = _safe_value(target if isinstance(target, dict) else {})
    if not all((owner, rid, aid, annotation, idem, safe_note)):
        return {"ok": False, "error": "invalid_request"}
    now = time.time()
    with db._conn() as conn:
        run = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        if run is None:
            return {"ok": False, "error": "not_found"}
        duplicate = conn.execute(
            "SELECT * FROM work_artifact_annotations "
            "WHERE run_id=? AND user_id=? AND idempotency_key=?",
            (rid, owner, idem),
        ).fetchone()
        if duplicate is not None:
            return {
                "ok": True, "duplicate": True,
                "annotation": _public_annotation(duplicate),
                "run": _public_run(run),
            }
        current_revision = int(_row_value(run, "revision", 7, 0) or 0)
        if current_revision != _positive_int(expected_revision):
            return {
                "ok": False, "error": "revision_conflict",
                "current_revision": current_revision,
            }
        requested_artifact_revision = _nonnegative_int(artifact_revision)
        if requested_artifact_revision:
            artifact = conn.execute(
                "SELECT * FROM artifact_revisions WHERE run_id=? AND user_id=? "
                "AND artifact_id=? AND revision=?",
                (rid, owner, aid, requested_artifact_revision),
            ).fetchone()
        else:
            artifact = conn.execute(
                "SELECT * FROM artifact_revisions WHERE run_id=? AND user_id=? "
                "AND artifact_id=? ORDER BY revision DESC LIMIT 1",
                (rid, owner, aid),
            ).fetchone()
        if artifact is None:
            return {"ok": False, "error": "artifact_not_found"}
        exact_revision = int(_row_value(artifact, "revision", 4, 0) or 0)
        impacted = [aid]
        conn.execute(
            "UPDATE artifact_revisions SET verification='stale' "
            "WHERE run_id=? AND user_id=? AND artifact_id=? AND revision=?",
            (rid, owner, aid, exact_revision),
        )
        conn.execute(
            "INSERT INTO work_artifact_annotations "
            "(id,run_id,user_id,artifact_id,artifact_revision,target_json,note,"
            "status,impacted_json,idempotency_key,created_at,resolved_at) "
            "VALUES (?,?,?,?,?,?,?,'open',?,?,?,0)",
            (
                annotation, rid, owner, aid, exact_revision,
                json.dumps(safe_target, ensure_ascii=False, separators=(",", ":")),
                safe_note,
                json.dumps(impacted, ensure_ascii=False, separators=(",", ":")),
                idem, now,
            ),
        )
        next_event = int(_row_value(run, "event_seq", 8, 0) or 0) + 1
        change_seq = _next_change_seq(conn)
        conn.execute(
            "UPDATE work_runs SET revision=revision+1,event_seq=?,change_seq=?,"
            "updated_at=? WHERE id=? AND user_id=?",
            (next_event, change_seq, now, rid, owner),
        )
        conn.execute(
            "INSERT INTO work_events "
            "(id,run_id,user_id,seq,event_type,status,summary,payload_json,"
            "created_at,idempotency_key,expected_revision,generation_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"we_{uuid.uuid4().hex}", rid, owner, next_event,
                "artifact_annotation_added",
                str(_row_value(run, "status", 6, "")),
                "成果收到一条局部修改意见，相关版本需要重新核验",
                json.dumps({
                    "annotation_id": annotation,
                    "artifact_id": aid,
                    "artifact_revision": exact_revision,
                    "impacted_result_ids": impacted,
                }, ensure_ascii=False, separators=(",", ":")),
                now, idem, current_revision, "",
            ),
        )
        row = conn.execute(
            "SELECT * FROM work_artifact_annotations "
            "WHERE id=? AND run_id=? AND user_id=?",
            (annotation, rid, owner),
        ).fetchone()
        updated = conn.execute(
            "SELECT * FROM work_runs WHERE id=? AND user_id=?", (rid, owner)
        ).fetchone()
        _append_sync_change_locked(
            conn,
            user_id=owner,
            change_seq=change_seq,
            entity_type="artifact_annotation",
            entity_id=annotation,
            operation="created",
            revision=int(_row_value(updated, "revision", 7, 0) or 0),
            payload={
                "annotation": _public_annotation(row),
                "run": _public_run(updated),
            },
            created_at=now,
        )
    return {
        "ok": True, "duplicate": False,
        "annotation": _public_annotation(row),
        "run": _public_run(updated),
    }


def save_checkpoint(
    run_id: str,
    *,
    user_id: str,
    reason: str = "periodic",
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist a bounded, non-executable handoff for process restart recovery."""
    rid, owner = _clip(run_id, 96), _clip(user_id, 160)
    if not rid or not owner:
        return None
    now = time.time()
    with db._conn() as conn:
        run = conn.execute("SELECT id,revision FROM work_runs WHERE id=? AND user_id=?", (rid, owner)).fetchone()
        if run is None:
            return None
        generation = int(conn.execute("SELECT COALESCE(MAX(generation),0)+1 FROM run_checkpoints WHERE run_id=? AND user_id=?", (rid, owner)).fetchone()[0] or 1)
        checkpoint_id = f"rc_{uuid.uuid4().hex}"
        safe_state = _safe_value(state or {})
        conn.execute("INSERT INTO run_checkpoints (id,run_id,user_id,generation,reason,state_json,created_at) VALUES (?,?,?,?,?,?,?)", (checkpoint_id, rid, owner, generation, _clip(reason, 80) or "periodic", json.dumps(safe_state, ensure_ascii=False, separators=(",", ":")), now))
        conn.execute("UPDATE work_runs SET snapshot_json=json_set(snapshot_json,'$.latest_checkpoint_id',?),updated_at=? WHERE id=? AND user_id=?", (checkpoint_id, now, rid, owner))
        row = conn.execute("SELECT * FROM run_checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
    return {
        "schema": "hashmm.run-checkpoint.v1", "id": checkpoint_id, "run_id": rid,
        "generation": generation, "reason": _clip(reason, 80),
        "state": _json_load(row["state_json"] if row else "{}"), "created_at": now,
    }


def latest_checkpoint(run_id: str, user_id: str) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM run_checkpoints WHERE run_id=? AND user_id=? ORDER BY generation DESC LIMIT 1", (_clip(run_id, 96), _clip(user_id, 160))).fetchone()
    if row is None:
        return None
    return {"schema": "hashmm.run-checkpoint.v1", "id": row["id"], "run_id": row["run_id"], "generation": int(row["generation"] or 0), "reason": row["reason"], "state": _json_load(row["state_json"]), "created_at": float(row["created_at"] or 0)}


def list_checkpoints(run_id: str, user_id: str, *, limit: int = 24) -> list[dict[str, Any]]:
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM run_checkpoints WHERE run_id=? AND user_id=? ORDER BY generation DESC LIMIT ?", (_clip(run_id, 96), _clip(user_id, 160), max(1, min(int(limit or 24), 100)))).fetchall()
    return [{"schema": "hashmm.run-checkpoint.v1", "id": row["id"], "run_id": row["run_id"], "generation": int(row["generation"] or 0), "reason": row["reason"], "state": _json_load(row["state_json"]), "created_at": float(row["created_at"] or 0)} for row in rows]


def get_run(run_id: str, user_id: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any] | None:
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM work_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if row is None:
            return None
        bounded = max(1, min(int(limit or 100), 250))
        events = conn.execute(
            "SELECT * FROM work_events WHERE run_id=? AND user_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
            (run_id, user_id, _nonnegative_int(after_seq), bounded),
        ).fetchall()
        result = _public_run(row)
        latest_command = conn.execute(
            "SELECT * FROM work_commands WHERE run_id=? AND user_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id, user_id),
        ).fetchone()
        if latest_command is not None:
            result["control"]["latest_command"] = _public_command(latest_command)
        active_generation_id = str(result.get("active_generation_id") or "")
        if active_generation_id:
            generation = conn.execute(
                "SELECT * FROM work_generations "
                "WHERE id=? AND run_id=? AND user_id=? AND status='active'",
                (active_generation_id, run_id, user_id),
            ).fetchone()
            if generation is not None:
                result["active_generation"] = _public_generation(generation)
        artifact_rows = conn.execute(
            "SELECT * FROM artifact_revisions "
            "WHERE run_id=? AND user_id=? ORDER BY created_at DESC LIMIT 100",
            (run_id, user_id),
        ).fetchall()
        result["artifact_revisions"] = [
            _public_artifact_revision(item) for item in artifact_rows
        ]
        now = time.time()
        conn.execute(
            "UPDATE work_execution_leases SET state='expired' "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at<=?",
            (run_id, user_id, now),
        )
        lease_row = conn.execute(
            "SELECT * FROM work_execution_leases "
            "WHERE run_id=? AND user_id=? AND state='active' AND expires_at>? "
            "ORDER BY generation DESC LIMIT 1",
            (run_id, user_id, now),
        ).fetchone()
        annotation_rows = conn.execute(
            "SELECT * FROM work_artifact_annotations "
            "WHERE run_id=? AND user_id=? ORDER BY created_at DESC LIMIT 100",
            (run_id, user_id),
        ).fetchall()
        result["execution_lease"] = (
            _public_execution_lease(lease_row) if lease_row is not None else None
        )
        result["artifact_annotations"] = [
            _public_annotation(item) for item in annotation_rows
        ]
        try:
            from hashmm.agent.work_automation import (
                proactive_items_for_run,
                workflow_for_run,
            )
            result["workflow"] = workflow_for_run(user_id, run_id)
            result["proactive_items"] = proactive_items_for_run(user_id, run_id)
        except Exception:
            # Workflow reuse is additive; a partially migrated store must not
            # make the underlying Work unreadable.
            result["workflow"] = None
            result["proactive_items"] = []
        result["events"] = [_public_event(event) for event in events]
        result["checkpoints"] = list_checkpoints(run_id, user_id, limit=24)
        result["events_truncated"] = bool(events and len(events) >= bounded and result["event_cursor"] > events[-1]["seq"])
        # An incremental event page is intentionally not enough to rebuild a
        # full process timeline. The dedicated workspace endpoint requests
        # from seq=0; delta consumers keep their previously cached canvas until
        # its revision changes.
        if _nonnegative_int(after_seq) == 0:
            result["workspace"] = build_work_canvas(result, result["events"])
            from hashmm.agent.work_os import build_work_projection
            result["workspace"]["product"] = build_work_projection(
                result,
                result["workspace"],
                lease=result.get("execution_lease"),
                annotations=result.get("artifact_annotations"),
            )
            latest_decision = conn.execute(
                "SELECT * FROM work_decisions WHERE run_id=? AND user_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id, user_id),
            ).fetchone()
            if latest_decision is not None:
                result["workspace"]["latest_decision"] = _public_decision(latest_decision)
        return result


def get_run_by_source(source_id: str, user_id: str) -> dict[str, Any] | None:
    """Resolve an owned run from an external queue id without enumerating it.

    Cross-device runners only know the durable request id they claimed.  The
    owner predicate is deliberately part of the lookup so a guessed id cannot
    reveal either the run kind or its existence.
    """
    source = _clip(source_id, 180)
    owner = _clip(user_id, 160)
    if not source or not owner:
        return None
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_runs WHERE source_id=? AND user_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (source, owner),
        ).fetchone()
    return _public_run(row) if row is not None else None


def get_latest_run_by_source_prefix(
    source_prefix: str, user_id: str,
) -> dict[str, Any] | None:
    """Return the newest owned run for a bounded external source prefix."""
    prefix = _clip(source_prefix, 160)
    owner = _clip(user_id, 160)
    if not prefix or not owner:
        return None
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_runs WHERE user_id=? AND source_id LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT 1",
            (owner, escaped + "%"),
        ).fetchone()
    return _public_run(row) if row is not None else None


def list_runs(
    user_id: str,
    *,
    after_cursor: int = 0,
    conv_id: str = "",
    project_id: str = "",
    include_terminal: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    where = ["user_id=?", "change_seq>?"]
    params: list[Any] = [user_id, _nonnegative_int(after_cursor)]
    if conv_id:
        where.append("conv_id=?")
        params.append(conv_id)
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if not include_terminal:
        where.append("status NOT IN ('observed','completed','failed','cancelled','interrupted')")
    bounded = max(1, min(int(limit or 100), 250))
    params.append(bounded)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM work_runs WHERE " + " AND ".join(where)
            + " ORDER BY change_seq ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        inbox_where = [
            "user_id=?",
            "status IN ('waiting_approval','waiting_input','blocked','failed','interrupted','delivered')",
        ]
        inbox_params: list[Any] = [user_id]
        if conv_id:
            inbox_where.append("conv_id=?")
            inbox_params.append(conv_id)
        if project_id:
            inbox_where.append("project_id=?")
            inbox_params.append(project_id)
        inbox_params.append(100)
        inbox_rows = conn.execute(
            "SELECT * FROM work_runs WHERE " + " AND ".join(inbox_where)
            + " ORDER BY updated_at DESC LIMIT ?",
            tuple(inbox_params),
        ).fetchall()
        high = conn.execute(
            "SELECT MAX(change_seq) AS value FROM work_runs WHERE user_id=?",
            (user_id,),
        ).fetchone()
        high_water = int(_row_value(high, "value", 0, 0) or 0)
    items = [_public_run(row) for row in rows]
    inbox_items = [_public_run(row) for row in inbox_rows]
    # When the filtered page is not full we have scanned all matching rows up
    # to the account high-water mark. Advancing to high-water prevents a quiet
    # conversation from polling the same unrelated account gap forever.
    next_cursor = (
        items[-1]["change_cursor"]
        if len(items) >= bounded
        else max(high_water, _nonnegative_int(after_cursor))
    )
    return {
        "schema": FEED_SCHEMA,
        "items": items,
        # The action inbox is a current owner-scoped projection, not an
        # incremental delta. Pending decisions must remain visible even when
        # no work event changed after the client's cursor.
        "action_inbox": action_inbox(inbox_items),
        "after_cursor": _nonnegative_int(after_cursor),
        "next_cursor": next_cursor,
        "high_water_cursor": high_water,
        "has_more": bool(len(items) >= bounded and next_cursor < high_water),
    }


def project_run_manifest(manifest: Any) -> dict[str, Any]:
    """Keep the decision/evidence projection; omit prompts and model prose."""
    if not isinstance(manifest, dict):
        return {}
    allowed = (
        "schema", "run_id", "execution_mode", "verification", "termination",
        "completion_gate", "evidence_graph", "causal_work_graph",
        "execution_receipts", "execution_frontier", "task_contract",
        "stage_latency_ms", "retrieval", "corpus_snapshot", "evidence_snapshot",
        "approval_request", "harness", "context_lifecycle",
        "skill_versions", "capability_facts", "agent_benefit",
        "work_method", "operating_contract",
        "agent_workspace_isolation", "workflow_demonstration",
        "autonomy_evaluation", "long_horizon_handoff", "task_trace",
    )
    projected = {key: manifest[key] for key in allowed if key in manifest}
    lifecycle = projected.get("context_lifecycle")
    if isinstance(lifecycle, dict):
        lifecycle_fields = (
            "contract", "generation", "turns", "compact_count", "has_summary",
            "checkpoint_id", "compacted", "tool_calls", "context_capsule",
        )
        projected["context_lifecycle"] = {
            key: lifecycle[key] for key in lifecycle_fields if key in lifecycle
        }
    return _safe_value(projected)


@dataclass
class SSEProjector:
    """Project coarse SSE transitions into the durable runtime.

    Token/thinking deltas are intentionally ignored: the message row owns prose
    and the ledger stays bounded even during very long generations.
    """

    run_id: str
    user_id: str
    seen_nodes: set[str] = field(default_factory=set)
    terminal: bool = False
    continuation_cursor: int = 0

    _INTERESTING = (
        "event: trace\n", "event: plan\n", "event: orchestration\n",
        "event: subagent\n", "event: step_start\n", "event: step_done\n",
        "event: file\n", "event: approval_request\n", "event: input_request\n",
        "event: steer_applied\n", "event: done\n",
    )

    def interested(self, chunk: str) -> bool:
        return isinstance(chunk, str) and chunk.startswith(self._INTERESTING)

    def _checkpoint(self, phase: str, *, status: str = "running", data: dict[str, Any] | None = None) -> None:
        self.continuation_cursor += 1
        payload = data if isinstance(data, dict) else {}
        receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
        approval = payload.get("approval_request") if isinstance(payload.get("approval_request"), dict) else payload
        save_checkpoint(
            self.run_id,
            user_id=self.user_id,
            reason=_clip(phase, 80),
            state={
                "schema": "hashmm.chat-continuation.v2",
                "phase": _clip(phase, 80),
                "status": _clip(status, 32),
                "continuation_cursor": self.continuation_cursor,
                "observed_nodes": sorted(self.seen_nodes)[-40:],
                "completed_action_ids": [
                    _clip(receipt.get("receipt_id") or receipt.get("action_id"), 120)
                ] if receipt else [],
                "pending_approval_id": _clip(
                    approval.get("request_id") if isinstance(approval, dict) else "", 120
                ),
                "retry_policy": "check_execution_receipt_before_replay",
            },
        )

    def observe(self, chunk: str) -> None:
        try:
            head, raw = chunk.split("\ndata: ", 1)
            event = head.removeprefix("event: ").strip()
            data = json.loads(raw.rsplit("\n\n", 1)[0])
        except (ValueError, TypeError, json.JSONDecodeError):
            return
        if event == "trace":
            for step in (data.get("steps") or [])[:4]:
                if not isinstance(step, dict):
                    continue
                node = _clip(step.get("node"), 64) or "progress"
                if node in self.seen_nodes:
                    continue
                self.seen_nodes.add(node)
                append_event(self.run_id, user_id=self.user_id, event_type="progress",
                             status="running", summary=_clip(step.get("detail") or node, 500),
                             payload={"node": node})
            return
        if event in {"plan", "orchestration", "subagent", "step_start", "step_done", "steer_applied"}:
            summary = {
                "plan": "执行计划已建立", "orchestration": "多 Agent 编排已建立",
                "subagent": "子 Agent 状态更新", "step_start": "工具步骤开始",
                "step_done": "工具步骤结束", "steer_applied": "运行中追加要求已生效",
            }[event]
            append_event(self.run_id, user_id=self.user_id, event_type=event,
                         status="running", summary=summary, payload=data)
            if event in {"plan", "step_done", "steer_applied"}:
                self._checkpoint(event, data=data)
            return
        if event == "file":
            append_event(self.run_id, user_id=self.user_id, event_type="artifact",
                         status="running", summary=f"产物已生成：{_clip(data.get('filename'), 180)}",
                         payload={"filename": data.get("filename"), "type": data.get("type")},
                         snapshot_updates={"latest_artifact": {"filename": data.get("filename"), "type": data.get("type")}})
            return
        if event in {"approval_request", "input_request"}:
            waiting = "waiting_approval" if event == "approval_request" else "waiting_input"
            append_event(self.run_id, user_id=self.user_id, event_type=event, status=waiting,
                         summary="任务需要用户确认" if event == "approval_request" else "任务需要补充信息",
                         payload=data)
            self._checkpoint(event, status=waiting, data=data)
            return
        if event == "done":
            raw_status = str(data.get("status") or "").strip().lower()
            stop_reason = str(data.get("stop_reason") or "").strip().lower()
            # Streaming message statuses and WorkRuntime statuses are related
            # but not interchangeable.  In particular, an Agent error must
            # never be projected as a successful delivery merely because it
            # is not in the WorkRuntime vocabulary.
            if raw_status == "waiting_approval" or stop_reason == "waiting_approval":
                status = "waiting_approval"
            elif raw_status == "waiting_input" or stop_reason == "waiting_input":
                status = "waiting_input"
            elif raw_status == "interrupted" or stop_reason in {
                "interrupted", "client_disconnected"
            }:
                status = "interrupted"
            elif raw_status in {"error", "failed"} or stop_reason in {
                "llm_error", "llm_unavailable", "tool_or_agent_error",
                "delivery_incomplete", "max_iterations", "deadline",
                "budget_exceeded", "no_progress", "error", "failed",
            }:
                status = "blocked"
            elif raw_status in {"complete", "completed", "delivered"}:
                status = "delivered"
            else:
                status = raw_status if raw_status in _STATUSES else "blocked"
            manifest = project_run_manifest(data.get("run_manifest"))
            # Runtime diagnostics may arrive without the immutable project
            # criteria admitted before model execution. Preserve those
            # criteria so a late done event cannot weaken the completion gate.
            current = get_run(self.run_id, self.user_id, limit=1) or {}
            admitted_manifest = _as_dict(
                _as_dict(current.get("snapshot")).get("run_manifest")
            )
            admitted_contract = _as_dict(admitted_manifest.get("task_contract"))
            runtime_contract = _as_dict(manifest.get("task_contract"))
            if admitted_contract:
                if runtime_contract:
                    merged_criteria = [
                        item for item in _as_list(runtime_contract.get("success_criteria"))
                        if isinstance(item, dict)
                    ]
                    labels = {_clip(item.get("label"), 240) for item in merged_criteria}
                    for item in _as_list(admitted_contract.get("success_criteria")):
                        if isinstance(item, dict) and _clip(item.get("label"), 240) not in labels:
                            merged_criteria.append(item)
                    runtime_contract["success_criteria"] = merged_criteria[:40]
                    for key in ("project_id", "project_revision", "deliverable"):
                        if admitted_contract.get(key) not in (None, ""):
                            runtime_contract[key] = admitted_contract[key]
                    manifest["task_contract"] = runtime_contract
                else:
                    manifest["task_contract"] = admitted_contract
            if manifest.get("task_contract"):
                try:
                    from hashmm.agent.completion_gate import build_completion_gate
                    manifest["completion_gate"] = build_completion_gate(
                        task_contract=_as_dict(manifest.get("task_contract")),
                        verification=_as_dict(manifest.get("verification")),
                        evidence_graph=_as_dict(manifest.get("evidence_graph")),
                        execution_frontier=_as_dict(manifest.get("execution_frontier")),
                        termination_reason=stop_reason or raw_status,
                        tool_steps=_as_list(manifest.get("tool_steps")),
                        orchestration=_as_dict(manifest.get("orchestration")),
                        causal_work_graph=_as_dict(manifest.get("causal_work_graph")),
                        previous=_as_dict(manifest.get("completion_gate")),
                    )
                except Exception:
                    pass
            files = [
                {"filename": item.get("filename"), "type": item.get("type")}
                for item in (data.get("files") or [])[:20] if isinstance(item, dict)
            ]
            append_event(
                self.run_id, user_id=self.user_id, event_type="delivered", status=status,
                summary=(
                    "结果已交付，等待证据门控或用户验收"
                    if status == "delivered"
                    else "本轮已暂停，需处理阻塞条件后续接"
                    if status in {"blocked", "waiting_approval", "waiting_input"}
                    else "本轮运行已结束"
                ),
                payload={
                    "elapsed_ms": data.get("elapsed_ms"),
                    "files": files,
                    "message_status": raw_status,
                    "stop_reason": stop_reason,
                },
                snapshot_updates={"run_manifest": manifest, "files": files},
            )
            self._checkpoint("stream_done", status=status, data=data)
            # `terminal` means this transport projection has a definitive done
            # event. Waiting/blocked work is resumable, but must not be
            # overwritten as interrupted when the HTTP response closes.
            self.terminal = True

    def close_if_open(self, *, interrupted: bool = True) -> None:
        if self.terminal:
            return
        append_event(
            self.run_id, user_id=self.user_id,
            event_type="interrupted" if interrupted else "failed",
            status="interrupted" if interrupted else "failed",
            summary="连接或执行在完成事件前结束，可从最近事件续接",
        )
        self._checkpoint("transport_interrupted", status="interrupted")
        self.terminal = True
