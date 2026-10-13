"""Owner-scoped command/query facade over the durable WorkRuntime."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from hashmm.agent import work_runtime
from hashmm.api import database as db
from hashmm.work.domain import (
    RUN_SCHEMA,
    WORKSPACE_SCHEMA,
    OutcomeContract,
    available_transitions,
    canonical_state,
)
from hashmm.work.context_kernel import compile_stateless_context
from hashmm.work.evidence_kernel import project_evidence_graph


PERSONAL_WORKSPACE = "personal"
_ACTIVE_STATES = {
    "draft", "planned", "ready", "running", "waiting_user",
    "waiting_approval", "blocked", "review", "change_requested", "interrupted",
}


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _workspace_id(value: Any) -> str:
    return _text(value, 120) or PERSONAL_WORKSPACE


def resolve_workspace(owner_id: str, workspace_id: str) -> dict[str, Any] | None:
    """Resolve ``personal`` or one owned project without an enumerable error."""
    owner = _text(owner_id, 160)
    wid = _workspace_id(workspace_id)
    if not owner:
        return None
    if wid == PERSONAL_WORKSPACE:
        return {
            "id": PERSONAL_WORKSPACE,
            "kind": "personal",
            "name": "我的工作",
            "goal": "",
            "deliverable": "",
            "success_criteria": [],
            "permission_mode": "ask",
            "revision": 1,
        }
    project = db.get_project_for_user(wid, owner)
    if not project:
        return None
    return {
        "id": str(project.get("id") or ""),
        "kind": "project",
        "name": _text(project.get("name"), 120) or "未命名项目",
        "goal": _text(project.get("goal"), 2_000),
        "deliverable": _text(project.get("deliverable"), 1_200),
        "success_criteria": list(project.get("success_criteria") or [])[:24],
        "permission_mode": _text(project.get("permission_mode"), 32) or "ask",
        "revision": max(1, int(project.get("revision") or 1)),
        "updated_at": float(project.get("updated_at") or 0),
        "conversation_count": max(0, int(project.get("conv_count") or 0)),
    }


def _artifact_projection(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    workspace = _mapping(run.get("workspace"))
    product = _mapping(workspace.get("product"))
    revisions = product.get("artifact_revisions")
    if not isinstance(revisions, list):
        revisions = run.get("artifact_revisions")
    if not isinstance(revisions, list):
        revisions = []
    presentation = _mapping(run.get("presentation"))
    deliverables = presentation.get("deliverables")
    if not isinstance(deliverables, list):
        deliverables = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*revisions, *deliverables][:80]:
        if not isinstance(item, Mapping):
            continue
        artifact_id = _text(
            item.get("artifact_id") or item.get("id")
            or item.get("filename") or item.get("name"),
            180,
        )
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        locator = _mapping(item.get("locator"))
        rows.append({
            "id": artifact_id,
            "name": _text(
                item.get("filename") or item.get("name") or artifact_id, 240,
            ),
            "media_type": _text(
                item.get("media_type") or item.get("type") or "", 96,
            ),
            "revision": max(1, int(item.get("revision") or 1)),
            "verification": _text(
                item.get("verification") or item.get("status") or "pending", 40,
            ),
            "content_hash": _text(item.get("content_hash"), 96),
            "locator": {
                key: locator[key] for key in (
                    "conversation_id", "filename", "download_url", "page",
                ) if key in locator
            },
        })
    return rows


def project_run(run: Mapping[str, Any]) -> dict[str, Any]:
    state = canonical_state(run.get("status"))
    contract = OutcomeContract.from_run(run)
    presentation = _mapping(run.get("presentation"))
    evidence = project_evidence_graph(run)
    artifacts = _artifact_projection(run)
    control = _mapping(run.get("control"))
    return {
        "schema": RUN_SCHEMA,
        "id": _text(run.get("id"), 96),
        "workspace_id": _text(run.get("project_id"), 120) or PERSONAL_WORKSPACE,
        "thread_id": _text(run.get("conversation_id") or run.get("conv_id"), 160),
        "kind": _text(run.get("kind"), 48) or "workflow",
        "state": state.value,
        "legacy_status": _text(run.get("status"), 40),
        "revision": max(1, int(run.get("revision") or 1)),
        "change_cursor": max(0, int(run.get("change_cursor") or 0)),
        "title": _text(
            presentation.get("title") or run.get("title") or contract.goal, 240,
        ),
        "contract": contract.public(),
        "current_step": _text(
            presentation.get("current_step") or "打开任务查看最新运行记录", 500,
        ),
        "next_action": _text(
            presentation.get("next_action") or "查看最新状态", 300,
        ),
        "needs_user": bool(presentation.get("needs_user")) or state.value in {
            "waiting_user", "waiting_approval", "blocked", "review",
            "failed", "interrupted",
        },
        "progress": _mapping(presentation.get("progress")),
        "evidence": evidence,
        "artifacts": artifacts,
        "available_transitions": list(available_transitions(state)),
        "available_commands": list(control.get("available_actions") or []),
        "execution_target": _mapping(run.get("execution_target")),
        "created_at": float(run.get("created_at") or 0),
        "updated_at": float(run.get("updated_at") or 0),
    }


def get_workspace_run(
    owner_id: str, workspace_id: str, run_id: str,
) -> dict[str, Any] | None:
    workspace = resolve_workspace(owner_id, workspace_id)
    if not workspace:
        return None
    run = work_runtime.get_run(_text(run_id, 96), _text(owner_id, 160))
    if not run:
        return None
    actual = _text(run.get("project_id"), 120) or PERSONAL_WORKSPACE
    # The personal workspace is the owner's aggregate and intentionally
    # contains both ungrouped and project-scoped work. A concrete project
    # remains a strict boundary and may only read runs assigned to itself.
    if workspace["id"] != PERSONAL_WORKSPACE and actual != workspace["id"]:
        return None
    return project_run(run)


def _device_projection(user: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from hashmm.agent import dispatch

        rows = dispatch.runners_status(
            owner=_text(user.get("uid"), 160),
            created_by=_text(user.get("sub"), 160),
        )
        items = [{
            "id": _text(item.get("device_id") or item.get("id"), 160),
            "name": _text(item.get("name") or "电脑", 120),
            "online": bool(item.get("online")),
            "last_seen": float(item.get("last_seen") or 0),
            "runner": _text(item.get("runner") or "desktop", 48),
            "version": _text(item.get("version"), 48),
        } for item in rows if item.get("device_id") or item.get("id")]
        return {
            "state": "ready",
            "items": items,
            "online_count": sum(1 for item in items if item["online"]),
            "authoritative": True,
        }
    except Exception as exc:
        return {
            "state": "unknown",
            "items": [],
            "online_count": 0,
            "authoritative": False,
            "reason": f"runner_registry:{type(exc).__name__}",
        }


def build_workspace_snapshot(
    *,
    user: Mapping[str, Any],
    workspace_id: str = PERSONAL_WORKSPACE,
    after_cursor: int = 0,
    limit: int = 250,
) -> dict[str, Any] | None:
    owner = _text(user.get("uid"), 160)
    workspace = resolve_workspace(owner, workspace_id)
    if not workspace:
        return None
    project_id = "" if workspace["id"] == PERSONAL_WORKSPACE else workspace["id"]
    feed = work_runtime.list_runs(
        owner,
        after_cursor=max(0, int(after_cursor or 0)),
        project_id=project_id,
        include_terminal=True,
        limit=max(1, min(int(limit or 250), 250)),
    )
    runs = [project_run(item) for item in list(feed.get("items") or [])]
    current = [
        item for item in runs if item["state"] in _ACTIVE_STATES
    ]
    results = [
        item for item in runs
        if item["state"] in {"review", "accepted", "completed", "observed"}
        or item["artifacts"]
    ]
    action_ids = {
        str(item.get("run_id") or "")
        for item in list(_mapping(feed.get("action_inbox")).get("items") or [])
        if isinstance(item, Mapping)
    }
    action_items = [
        item for item in runs
        if item["id"] in action_ids or item["needs_user"]
    ]
    projects = [{
        "id": str(item.get("id") or ""),
        "name": _text(item.get("name"), 120),
        "goal": _text(item.get("goal"), 400),
        "deliverable": _text(item.get("deliverable"), 300),
        "success_criteria": list(item.get("success_criteria") or [])[:24],
        "permission_mode": _text(item.get("permission_mode"), 32) or "ask",
        "revision": max(1, int(item.get("revision") or 1)),
        "conversation_count": max(0, int(item.get("conv_count") or 0)),
        "updated_at": float(item.get("updated_at") or 0),
    } for item in db.list_projects(owner)]
    devices = _device_projection(user)
    payload = {
        "schema": WORKSPACE_SCHEMA,
        "generated_at": time.time(),
        "workspace": workspace,
        "projects": projects,
        "today": {
            "needs_user": action_items,
            "in_progress": current,
            "recent_results": results[:12],
        },
        "runs": runs,
        "devices": devices,
        "sync": {
            "after_cursor": max(0, int(after_cursor or 0)),
            "next_cursor": max(0, int(feed.get("next_cursor") or 0)),
            "high_water_cursor": max(0, int(feed.get("high_water_cursor") or 0)),
            "has_more": bool(feed.get("has_more")),
            "delta": max(0, int(after_cursor or 0)) > 0,
        },
        "trust": {
            "owner_isolation": True,
            "model_prose_is_execution_evidence": False,
            "side_effects_require_policy": True,
            "completion_requires_evidence": True,
            "role": _text(user.get("role") or "user", 32),
        },
    }
    etag_seed = {
        "owner": owner,
        "workspace": workspace["id"],
        "workspace_revision": workspace.get("revision"),
        "project_revisions": [(item["id"], item["revision"]) for item in projects],
        "cursor": payload["sync"]["high_water_cursor"],
        "devices": [(item["id"], item["online"], item["last_seen"]) for item in devices["items"]],
    }
    payload["etag"] = hashlib.sha256(json.dumps(
        etag_seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return payload


def create_workspace_run(
    *,
    user: Mapping[str, Any],
    workspace_id: str,
    idempotency_key: str,
    goal: str,
    deliverable: str = "",
    success_criteria: list[Any] | None = None,
    permission_mode: str = "ask",
    conversation_id: str = "",
    kind: str = "workflow",
    work_method: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    owner = _text(user.get("uid"), 160)
    workspace = resolve_workspace(owner, workspace_id)
    if not workspace:
        return {"ok": False, "error": "not_found"}
    try:
        contract = OutcomeContract.from_values(
            goal=goal,
            deliverable=deliverable or workspace.get("deliverable"),
            criteria=success_criteria or workspace.get("success_criteria"),
            permission_mode=permission_mode or workspace.get("permission_mode"),
            source="user",
            user_confirmed=True,
        )
    except ValueError:
        return {"ok": False, "error": "goal_required"}
    project_id = "" if workspace["id"] == PERSONAL_WORKSPACE else workspace["id"]
    source_id = f"workspace-command:{_text(idempotency_key, 120)}"
    admission_capsule, context_projection = compile_stateless_context(
        {
            "task": lambda: json.dumps(
                {
                    "goal": contract.goal,
                    "deliverable": contract.deliverable,
                    "criteria": [item.public() for item in contract.criteria],
                    "permission_mode": contract.permission_mode,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
        active_goal=contract.goal,
        criteria=[item.public() for item in contract.criteria],
    )
    snapshot = {
        "goal": contract.goal,
        "deliverable": contract.deliverable,
        "success_criteria": [item.public() for item in contract.criteria],
        "permission_mode": contract.permission_mode,
        "work_method": dict(work_method or {}),
        # Only the content-addressed manifest enters WorkRuntime. The rendered
        # task body is transient compiler output and is deliberately excluded.
        "context_capsule": admission_capsule.public(),
        "context_kernel": context_projection,
        "run_manifest": {
            "schema": "hashmm.work-admission.v2",
            "task_contract": {
                "schema": "hashmm.task-contract.v2",
                "goal": contract.goal,
                "deliverable": contract.deliverable,
                "success_criteria": [
                    {
                        "check_id": item.id,
                        "label": item.label,
                        "required": item.required,
                        "status": item.status,
                    }
                    for item in contract.criteria
                ],
                "user_confirmed": True,
            },
        },
    }
    try:
        run = work_runtime.create_run(
            user_id=owner,
            kind=_text(kind, 48) or "workflow",
            source_id=source_id,
            conv_id=_text(conversation_id, 160),
            title=contract.goal,
            status="queued",
            snapshot=snapshot,
            project_id=project_id,
        )
    except ValueError as exc:
        return {"ok": False, "error": "invalid_scope", "reason": type(exc).__name__}
    return {"ok": True, "run": project_run(run)}
