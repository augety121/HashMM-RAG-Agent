"""User-facing Work OS projections for HashMM V439-V473.

This module deliberately does not execute tools.  It turns already-persisted
runtime facts into one bounded product contract shared by desktop and App.
Model prose, a proposed plan, or a successful-looking UI state is never
promoted to execution, verification, recovery, or autonomy evidence.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


WORK_PROJECTION_SCHEMA = "hashmm.work-projection.v2"
CAPABILITY_PLAN_SCHEMA = "hashmm.capability-plan.v1"
WORK_TWIN_SCHEMA = "hashmm.work-twin.v1"
RECOVERY_CENTER_SCHEMA = "hashmm.recovery-center.v1"
WORKFLOW_CANDIDATE_SCHEMA = "hashmm.workflow-candidate.v1"
PROACTIVE_INBOX_SCHEMA = "hashmm.proactive-inbox.v1"
AUTONOMY_SCHEMA = "hashmm.autonomy-profile.v1"
AGENT_BENEFIT_SCHEMA = "hashmm.agent-benefit-gate.v1"
AGENT_PROJECTION_SCHEMA = "hashmm.agent-collaboration.v1"

_PLACEMENTS = {"local", "remote_device", "cloud", "waiting_device"}
_CAPABILITY_ORDER = ("structured", "browser", "computer", "manual")
_CAPABILITY_LABELS = {
    "structured": "使用已连接的服务",
    "browser": "需要打开浏览器",
    "computer": "需要操作你的电脑",
    "manual": "需要你手动完成一步",
}
_AUTONOMY_LABELS = {
    0: "回答与建议",
    1: "生成可审阅成果",
    2: "完成有界多步骤工作",
    3: "在授权范围内选择能力并恢复",
    4: "在明确预算内主动工作",
    5: "长期目标组合管理",
}


def _text(value: Any, limit: int = 400) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _int(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = 10_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_execution_target(value: Any) -> dict[str, Any]:
    """Return a non-secret execution placement chosen by server policy."""
    raw = _dict(value)
    kind = _text(raw.get("kind"), 32)
    if kind not in _PLACEMENTS:
        kind = "waiting_device"
    target_id = _text(raw.get("target_id") or raw.get("device_id"), 160)
    labels = {
        "local": "这台电脑",
        "remote_device": _text(raw.get("label"), 120) or "已连接的电脑",
        "cloud": "云端",
        "waiting_device": "等待电脑上线",
    }
    return {
        "schema": "hashmm.execution-placement.v1",
        "kind": kind,
        "target_id": target_id,
        "label": labels[kind],
        "state": _text(raw.get("state"), 32) or (
            "waiting" if kind == "waiting_device" else "ready"
        ),
        "requested_by": _text(raw.get("requested_by"), 32) or "server_policy",
        "authority_expanded": False,
    }


def resolve_capability_path(facts: Any) -> dict[str, Any]:
    """Select the least invasive feasible route from server-owned facts.

    ``facts`` is an availability projection, not a model request.  Unknown or
    malformed capabilities are unavailable.  A route outside the persisted
    scope is skipped even if an executor happens to exist.
    """
    rows = _dict(facts)
    considered: list[dict[str, Any]] = []
    selected = "manual"
    for capability in _CAPABILITY_ORDER:
        fact = _dict(rows.get(capability))
        available = bool(fact.get("available"))
        within_scope = bool(fact.get("within_scope"))
        configured = bool(fact.get("configured", available))
        ready = available and within_scope and configured
        considered.append({
            "kind": capability,
            "ready": ready,
            "reason": _text(fact.get("reason"), 240) or (
                "可用且位于当前任务授权范围内" if ready else "当前不可用或不在授权范围内"
            ),
            "requires_confirmation": bool(fact.get("requires_confirmation")),
        })
        if ready:
            selected = capability
            break
    chosen = next((item for item in considered if item["kind"] == selected), None)
    if chosen is None:
        chosen = {
            "kind": "manual",
            "ready": True,
            "reason": "没有可安全自动执行的能力",
            "requires_confirmation": True,
        }
        considered.append(chosen)
    return {
        "schema": CAPABILITY_PLAN_SCHEMA,
        "selected": selected,
        "label": _CAPABILITY_LABELS[selected],
        "requires_confirmation": bool(chosen["requires_confirmation"]),
        "reason": chosen["reason"],
        "considered": considered,
        "precedence": list(_CAPABILITY_ORDER),
        "integrity": {
            "server_facts_only": True,
            "model_selected": False,
            "widens_scope": False,
            "computer_use_is_fallback": selected == "computer",
        },
    }


def evaluate_agent_benefit(facts: Any) -> dict[str, Any]:
    """Gate parallel agents on measured independence, cost and conflict risk."""
    raw = _dict(facts)
    subtasks = _int(raw.get("subtasks"), 0, maximum=64)
    independent = max(0.0, min(_float(raw.get("independent_ratio")), 1.0))
    saved_ms = _int(raw.get("estimated_latency_saved_ms"), 0, maximum=86_400_000)
    extra_tokens = _int(raw.get("estimated_extra_tokens"), 0, maximum=10_000_000)
    conflict = max(0.0, min(_float(raw.get("conflict_risk")), 1.0))
    measured = bool(raw.get("measured"))
    eligible = bool(
        measured
        and subtasks >= 2
        and independent >= 0.6
        and saved_ms >= 1_000
        and conflict <= 0.35
        and extra_tokens <= _int(raw.get("token_budget"), 0, maximum=10_000_000)
    )
    reasons: list[str] = []
    if not measured:
        reasons.append("缺少调度器测量数据")
    if subtasks < 2:
        reasons.append("没有两个以上可并行子任务")
    if independent < 0.6:
        reasons.append("子任务独立度不足")
    if saved_ms < 1_000:
        reasons.append("预计时延收益不足")
    if conflict > 0.35:
        reasons.append("写入冲突风险过高")
    if extra_tokens > _int(raw.get("token_budget"), 0, maximum=10_000_000):
        reasons.append("额外成本超过预算")
    return {
        "schema": AGENT_BENEFIT_SCHEMA,
        "eligible": eligible,
        "decision": "parallel" if eligible else "single_agent",
        "facts": {
            "subtasks": subtasks,
            "independent_ratio": independent,
            "estimated_latency_saved_ms": saved_ms,
            "estimated_extra_tokens": extra_tokens,
            "conflict_risk": conflict,
        },
        "reasons": reasons,
        "integrity": {
            "measured_facts_required": True,
            "model_preference_is_not_admission": True,
        },
    }


def _contract(run: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _dict(_dict(run.get("snapshot")).get("run_manifest"))
    raw = _dict(manifest.get("task_contract"))
    criteria: list[dict[str, Any]] = []
    for index, item in enumerate(_list(raw.get("success_criteria"))[:32]):
        if not isinstance(item, Mapping):
            continue
        label = _text(item.get("label"), 300)
        if not label:
            continue
        criteria.append({
            "id": _text(item.get("check_id"), 120) or f"criterion-{index + 1}",
            "label": label,
            "required": bool(item.get("required", True)),
        })
    constraints = [
        _text(item, 300) for item in _list(raw.get("constraints"))[:24]
        if _text(item, 300)
    ]
    return {
        "goal": _text(raw.get("goal"), 800) or _text(run.get("title"), 240),
        "goal_source": _text(raw.get("goal_source"), 40) or "persisted_work_title",
        "constraints": constraints,
        "completion_criteria": criteria,
    }


def build_work_twin(
    run: Mapping[str, Any],
    canvas: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a bounded causal mirror from explicit persisted relationships."""
    contract = _contract(run)
    process = _dict(canvas.get("process"))
    evidence = _dict(canvas.get("evidence"))
    results = _list(canvas.get("results"))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    run_id = _text(run.get("id"), 120)
    goal_id = f"goal:{run_id}"
    if contract["goal"]:
        nodes.append({"id": goal_id, "kind": "Goal", "label": contract["goal"], "status": "recorded"})
    for criterion in contract["completion_criteria"]:
        node_id = f"criterion:{criterion['id']}"
        nodes.append({
            "id": node_id, "kind": "Criterion", "label": criterion["label"],
            "status": "required" if criterion["required"] else "optional",
        })
        if contract["goal"]:
            edges.append({"from": node_id, "to": goal_id, "kind": "supports"})
    for stage in _list(process.get("stages"))[:80]:
        if not isinstance(stage, Mapping):
            continue
        node_id = f"task:{_text(stage.get('id'), 120)}"
        nodes.append({
            "id": node_id, "kind": "Task", "label": _text(stage.get("label"), 240),
            "status": _text(stage.get("status"), 32),
        })
    for source in _list(evidence.get("sources"))[:80]:
        if not isinstance(source, Mapping):
            continue
        node_id = f"evidence:{_text(source.get('id'), 160)}"
        nodes.append({
            "id": node_id, "kind": "Evidence", "label": _text(source.get("label"), 240),
            "status": _text(source.get("status"), 32),
        })
    for result in results[:80]:
        if not isinstance(result, Mapping):
            continue
        node_id = f"artifact:{_text(result.get('id'), 160)}"
        nodes.append({
            "id": node_id, "kind": "ArtifactRevision",
            "label": _text(result.get("name"), 240),
            "status": _text(result.get("verification"), 32),
            "version_ref": _text(result.get("version_ref"), 240),
        })

    # Only retain graph edges that the persisted causal graph explicitly
    # recorded.  Labels or temporal adjacency do not manufacture causality.
    snapshot = _dict(run.get("snapshot"))
    manifest = _dict(snapshot.get("run_manifest"))
    graph = (
        _dict(manifest.get("causal_work_graph"))
        or _dict(snapshot.get("causal_work_graph"))
    )
    explicit_nodes = {
        _text(item.get("id"), 160): item
        for item in _list(graph.get("nodes"))
        if isinstance(item, Mapping) and _text(item.get("id"), 160)
    }
    for node_id, item in list(explicit_nodes.items())[:120]:
        public_id = f"recorded:{node_id}"
        nodes.append({
            "id": public_id,
            "kind": _text(item.get("kind"), 40) or "RecordedFact",
            "label": _text(item.get("label"), 240) or node_id,
            "status": _text(item.get("status"), 32),
        })
    allowed_edges = {
        "supports", "produces", "depends_on", "invalidates",
        "verified_by", "approved_by", "executed_on",
    }
    for edge in _list(graph.get("edges"))[:240]:
        if not isinstance(edge, Mapping):
            continue
        source = _text(edge.get("from") or edge.get("source"), 160)
        target = _text(edge.get("to") or edge.get("target"), 160)
        kind = _text(edge.get("kind") or edge.get("relation"), 40)
        if source in explicit_nodes and target in explicit_nodes and kind in allowed_edges:
            edges.append({
                "from": f"recorded:{source}",
                "to": f"recorded:{target}",
                "kind": kind,
            })
    # Deduplicate without changing stable order.
    seen_nodes: set[str] = set()
    public_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if node["id"] and node["id"] not in seen_nodes:
            seen_nodes.add(node["id"])
            public_nodes.append(node)
    return {
        "schema": WORK_TWIN_SCHEMA,
        "nodes": public_nodes[:240],
        "edges": edges[:320],
        "summary": {
            "goals": sum(item["kind"] == "Goal" for item in public_nodes),
            "criteria": sum(item["kind"] == "Criterion" for item in public_nodes),
            "tasks": sum(item["kind"] == "Task" for item in public_nodes),
            "evidence": sum(item["kind"] == "Evidence" for item in public_nodes),
            "artifacts": sum(item["kind"] == "ArtifactRevision" for item in public_nodes),
            "stale": sum(item.get("status") == "stale" for item in public_nodes),
        },
        "integrity": {
            "explicit_edges_only": True,
            "model_inferred_edges": False,
            "projection_only": True,
        },
    }


def _recovery_center(canvas: Mapping[str, Any]) -> dict[str, Any]:
    process = _dict(canvas.get("process"))
    receipt = _dict(canvas.get("completion_receipt"))
    recovery = _dict(receipt.get("recovery"))
    checkpoints = [
        item for item in _list(process.get("checkpoints"))[:24]
        if isinstance(item, Mapping)
    ]
    results = [item for item in _list(canvas.get("results")) if isinstance(item, Mapping)]
    context_points = [item for item in checkpoints if item.get("kind") == "context"]
    execution_points = [item for item in checkpoints if item.get("kind") == "execution"]
    artifact_versions = sum(max(0, _int(item.get("version"), 1) - 1) for item in results)
    return {
        "schema": RECOVERY_CENTER_SCHEMA,
        "conversation": {
            "available": bool(context_points),
            "checkpoint_count": len(context_points),
            "latest": dict(context_points[0]) if context_points else None,
        },
        "files": {
            "available": artifact_versions > 0,
            "previous_versions": artifact_versions,
        },
        "execution": {
            "available": bool(execution_points and recovery.get("can_resume")),
            "checkpoint_count": len(execution_points),
            "latest": dict(execution_points[0]) if execution_points else None,
        },
        "automatic_rollback_available": bool(
            recovery.get("automatic_rollback_available")
        ),
        "integrity": {
            "checkpoint_evidence_required": True,
            "conversation_file_execution_are_distinct": True,
            "model_prose_is_not_a_checkpoint": True,
        },
    }


def _workflow_candidate(run: Mapping[str, Any], canvas: Mapping[str, Any]) -> dict[str, Any]:
    learning = _dict(canvas.get("learning"))
    manifest = _dict(_dict(run.get("snapshot")).get("run_manifest"))
    demonstration = _dict(manifest.get("workflow_demonstration"))
    durable = _dict(run.get("workflow"))
    verified_demo = bool(
        (
            demonstration.get("verified")
            and demonstration.get("source") == "execution_receipts"
            and _text(demonstration.get("receipt_hash"), 64)
        )
        or durable.get("status") in {"verified", "published"}
    )
    durable_hash = _text(durable.get("action_hash"), 64)
    status = _text(durable.get("status"), 32)
    return {
        "schema": WORKFLOW_CANDIDATE_SCHEMA,
        "id": _text(durable.get("id"), 120),
        "revision": _int(durable.get("revision"), 0),
        "status": (
            "published" if status == "published"
            else "ready_for_review" if verified_demo
            else "replay_required" if status == "candidate"
            else "evidence_required"
        ),
        "demonstration_verified": verified_demo,
        "receipt_hash": (
            durable_hash
            or _text(demonstration.get("receipt_hash"), 64)
        ) if verified_demo else "",
        "used_skill_versions": [
            _text(item.get("version_ref"), 240)
            for item in _list(learning.get("used_versions"))
            if isinstance(item, Mapping) and _text(item.get("version_ref"), 240)
        ][:16],
        "can_publish": status == "verified",
        "required_next_step": (
            "工作流已发布，可在授权范围内复用"
            if status == "published"
            else "由用户审核范围、参数和危险操作后发布"
            if verified_demo
            else "用第二次独立执行回放验证"
            if status == "candidate"
            else "先完成一次有执行凭证的演示和回放验证"
        ),
        "integrity": {
            "automatic_publication": False,
            "paired_replay_required": True,
            "human_approval_required": True,
        },
    }


def _proactive_inbox(
    run: Mapping[str, Any], canvas: Mapping[str, Any],
) -> dict[str, Any]:
    next_actions = _dict(canvas.get("next_actions"))
    governance = _dict(next_actions.get("governance"))
    items: list[dict[str, Any]] = []
    for raw in _list(next_actions.get("items"))[:12]:
        if not isinstance(raw, Mapping):
            continue
        reason = _text(raw.get("reason"), 400)
        if not reason:
            continue
        items.append({
            "id": _text(raw.get("id"), 120),
            "label": _text(raw.get("label"), 240),
            "reason": reason,
            "risk": _text(raw.get("risk"), 24) or "medium",
            "requires_confirmation": bool(raw.get("requires_confirmation", True)),
            "cancelable": True,
            "auto_execute": False,
        })
    existing_ids = {item.get("id") for item in items}
    for raw in _list(run.get("proactive_items"))[:24]:
        if not isinstance(raw, Mapping):
            continue
        item_id = _text(raw.get("id"), 120)
        if not item_id or item_id in existing_ids:
            continue
        items.append({
            "id": item_id,
            "label": _text(raw.get("title"), 240),
            "reason": _text(raw.get("reason"), 400),
            "risk": _text(raw.get("risk"), 24) or "medium",
            "action": _text(raw.get("action"), 40),
            "status": _text(raw.get("status"), 32),
            "requires_confirmation": True,
            "cancelable": raw.get("status") == "suggested",
            "auto_execute": False,
        })
        existing_ids.add(item_id)
    return {
        "schema": PROACTIVE_INBOX_SCHEMA,
        "items": items,
        "count": len(items),
        "budget": dict(_dict(governance.get("budgets"))),
        "integrity": {
            "persisted_fact_required": True,
            "cancelable": True,
            "auto_execute": False,
        },
    }


def autonomy_profile(run: Mapping[str, Any]) -> dict[str, Any]:
    requested = min(_int(run.get("autonomy_level"), 0), 4)
    manifest = _dict(_dict(run.get("snapshot")).get("run_manifest"))
    receipt = _dict(manifest.get("autonomy_evaluation"))
    receipt_valid = bool(
        receipt.get("verified")
        and receipt.get("source") == "evaluation_service"
        and _text(receipt.get("suite_hash"), 64)
        and _int(receipt.get("security_failures"), 1) == 0
        and _int(receipt.get("duplicate_side_effects"), 1) == 0
        and _int(receipt.get("unapproved_high_risk_actions"), 1) == 0
    )
    evaluated = min(_int(receipt.get("level"), 0), 4) if receipt_valid else 0
    released = min(requested, evaluated)
    return {
        "schema": AUTONOMY_SCHEMA,
        "requested_level": requested,
        "released_level": released,
        "label": _AUTONOMY_LABELS[released],
        "evaluation_receipt_valid": receipt_valid,
        "suite_hash": _text(receipt.get("suite_hash"), 64) if receipt_valid else "",
        "limits": {
            "permission_ceiling": True,
            "cost_budget_required": released >= 2,
            "human_takeover": True,
            "failure_recovery_required": released >= 2,
            "evidence_coverage_required": released >= 1,
            "release_rollback_required": released >= 3,
        },
        "a5_available": False,
        "integrity": {
            "model_self_report_is_evidence": False,
            "demo_is_evidence": False,
            "verified_evaluation_required": True,
        },
    }


def _agent_projection(run: Mapping[str, Any], canvas: Mapping[str, Any]) -> dict[str, Any]:
    process = _dict(canvas.get("process"))
    assurance = _dict(_dict(canvas.get("assurance")).get("delegation"))
    manifest = _dict(_dict(run.get("snapshot")).get("run_manifest"))
    isolation = _dict(manifest.get("agent_workspace_isolation"))
    branches = [
        {
            "id": _text(item.get("id"), 120),
            "label": _text(item.get("label"), 240),
            "status": _text(item.get("status"), 32),
        }
        for item in _list(process.get("branches"))[:32]
        if isinstance(item, Mapping)
    ]
    return {
        "schema": AGENT_PROJECTION_SCHEMA,
        "strategy": _text(assurance.get("strategy"), 40) or "single_agent",
        "workers": branches,
        "blocked": [
            _text(item, 120) for item in _list(assurance.get("blocked_node_ids"))[:32]
        ],
        "write_isolation": {
            "status": "verified" if isolation.get("verified") else "not_proven",
            "mode": _text(isolation.get("mode"), 40),
            "integrator_only_merge": bool(isolation.get("integrator_only_merge")),
            "verifier_read_only": bool(isolation.get("verifier_read_only")),
        },
        "user_summary": (
            f"{len(branches)} 个并行分支正在协作"
            if branches else "本次工作由一个执行链完成"
        ),
        "integrity": {
            "technical_mailbox_hidden": True,
            "unverified_isolation_not_claimed": True,
        },
    }


def build_work_projection(
    run: Mapping[str, Any],
    canvas: Mapping[str, Any],
    *,
    lease: Mapping[str, Any] | None = None,
    annotations: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the V2 contract consumed by desktop and App."""
    contract = _contract(run)
    snapshot = _dict(run.get("snapshot"))
    manifest = _dict(snapshot.get("run_manifest"))
    capability_facts = (
        _dict(snapshot.get("capability_facts"))
        or _dict(manifest.get("capability_facts"))
    )
    presentation = _dict(run.get("presentation"))
    placement = normalize_execution_target(run.get("execution_target"))
    active_lease = _dict(lease)
    if active_lease:
        placement["lease"] = {
            "id": _text(active_lease.get("id"), 120),
            "state": _text(active_lease.get("state"), 32),
            "holder_type": _text(active_lease.get("holder_type"), 32),
            "holder_id": _text(active_lease.get("holder_id"), 160),
            "generation": _int(active_lease.get("generation"), 0),
            "expires_at": _float(active_lease.get("expires_at")),
        }
    annotation_rows = [
        {
            "id": _text(item.get("id"), 120),
            "artifact_id": _text(item.get("artifact_id"), 160),
            "artifact_revision": _int(item.get("artifact_revision"), 0),
            "target": dict(_dict(item.get("target"))),
            "note": _text(item.get("note"), 800),
            "status": _text(item.get("status"), 32),
            "impacted_result_ids": [
                _text(value, 160) for value in _list(item.get("impacted_result_ids"))[:64]
            ],
            "created_at": _float(item.get("created_at")),
        }
        for item in list(annotations or [])[:100]
        if isinstance(item, Mapping)
    ]
    twin = build_work_twin(run, canvas)
    result = {
        "schema": WORK_PROJECTION_SCHEMA,
        "identity": {
            "work_id": _text(run.get("id"), 120),
            "project_id": _text(run.get("project_id"), 120),
            "conversation_id": _text(run.get("conversation_id"), 160),
            "domain": "work",
        },
        "contract": contract,
        "status": {
            "label": _text(presentation.get("status_label"), 80),
            "phase": _text(presentation.get("phase"), 40),
            "current_step": _text(presentation.get("current_step"), 300),
            "needs_user": bool(presentation.get("needs_user")),
            "main_action": _text(presentation.get("primary_action"), 40) or "open",
            "main_action_label": _text(presentation.get("next_action"), 240),
        },
        "placement": placement,
        "capability_plan": resolve_capability_path(capability_facts),
        "work_twin": twin,
        "annotations": annotation_rows,
        "recovery": _recovery_center(canvas),
        "workflow_candidate": _workflow_candidate(run, canvas),
        "proactive_inbox": _proactive_inbox(run, canvas),
        "collaboration": _agent_projection(run, canvas),
        "autonomy": autonomy_profile(run),
        "integrity": {
            "projection_only": True,
            "owner_check_required": True,
            "model_prose_is_execution_evidence": False,
            "model_prose_is_verification_evidence": False,
            "fingerprint": "",
        },
    }
    result["integrity"]["fingerprint"] = _fingerprint({
        "identity": result["identity"],
        "contract": result["contract"],
        "status": result["status"],
        "placement": result["placement"],
        "capability_plan": result["capability_plan"],
        "work_twin": twin,
        "annotations": annotation_rows,
        "autonomy": result["autonomy"],
    })
    return result
