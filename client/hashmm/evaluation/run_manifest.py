"""Deterministic, user-visible provenance for one Chat/RAG/Agent run.

The manifest is intentionally built from runtime facts that HashMM can inspect.
It never treats a model's prose or self-score as execution evidence.  Older
indexes which do not expose a pinned build id are reported as ``evidence_only``
instead of receiving an invented corpus version.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "hashmm.run-manifest.v2"
_PASS = "passed"
_FAIL = "failed"
_NA = "not_evaluable"


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _clean_ms(value: Any) -> int:
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError, OverflowError):
        return 0


def _runtime_config() -> tuple[dict, dict]:
    """Return public retrieval config and a truthful corpus snapshot marker."""
    config: dict[str, Any] = {}
    corpus: dict[str, Any] = {"status": "unavailable", "id": ""}
    try:
        from hashmm.api.core.services import ServiceRegistry
        cfg = (getattr(ServiceRegistry, "state", {}) or {}).get("cfg")
        if cfg is None:
            from hashmm.config import HashMMConfig
            cfg = HashMMConfig()
        config = {
            "top_k": int(getattr(cfg, "retrieval_top_k", 0) or 0),
            "hybrid_mode": str(getattr(cfg, "hybrid_mode", "") or ""),
            "rrf_k": int(getattr(cfg, "rrf_k", 0) or 0),
            "dedup_hamming_threshold": int(
                getattr(cfg, "dedup_hamming_threshold", 0) or 0),
            "embedding_model": str(getattr(cfg, "embedding_model", "") or ""),
            "embedding_dim": int(getattr(cfg, "embedding_dim", 0) or 0),
        }
        index_dir = str(getattr(cfg, "hash_index_dir", "") or "")
        if index_dir:
            from hashmm.index_version import read_index_meta
            meta = read_index_meta(index_dir)
            if isinstance(meta, dict) and meta:
                identity = {
                    "embedding_fingerprint": str(meta.get("fingerprint") or ""),
                    "vectors": int(meta.get("n_vectors") or 0),
                    "built_at": meta.get("built_at"),
                }
                corpus = {"status": "pinned", "id": _fingerprint(identity), **identity}
    except Exception:
        # A diagnostics feature must never make Chat unavailable.
        pass
    return config, corpus


def _source_identity(source: dict) -> dict:
    text = str(source.get("text") or "")
    return {
        "chunk_id": str(source.get("chunk_id") or ""),
        "doc_id": str(source.get("doc_id") or ""),
        "filename": str(source.get("filename") or ""),
        "page": source.get("page"),
        "section": str(source.get("section") or ""),
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else "",
    }


def _check(check_id: str, status: str, detail: str, *, evidence: dict | None = None) -> dict:
    item = {"id": check_id, "status": status, "detail": detail}
    if evidence:
        item["evidence"] = evidence
    return item


def _tool_failures(tool_steps: Iterable[dict]) -> tuple[int, int]:
    total = 0
    failed = 0
    for step in tool_steps or []:
        if not isinstance(step, dict):
            continue
        # Trace-only records are not tool execution evidence.
        name = step.get("tool") or step.get("name")
        if not name:
            continue
        total += 1
        status = str(step.get("status") or "").strip().lower()
        if status in {"error", "failed", "fail", "denied", "blocked", "stopped"}:
            failed += 1
    return total, failed


def _bounded_harness(raw: Any) -> dict[str, Any]:
    """Keep only the public, argument-free harness contract in a manifest."""
    if not isinstance(raw, dict) or raw.get("schema") != "hashmm.agent-harness.v1":
        return {}
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    trajectory = raw.get("trajectory") if isinstance(raw.get("trajectory"), dict) else {}
    terminal = raw.get("terminal") if isinstance(raw.get("terminal"), dict) else {}
    children = raw.get("children") if isinstance(raw.get("children"), dict) else {}
    raw_levels = raw.get("levels") if isinstance(raw.get("levels"), dict) else {}
    level_rows = []
    for item in list(raw_levels.get("levels") or [])[:4]:
        if isinstance(item, dict):
            level_rows.append({"level": str(item.get("level") or "")[:8], "name": str(item.get("name") or "")[:80], "status": str(item.get("status") or "")[:24]})
    events: list[dict[str, Any]] = []
    for row in list(trajectory.get("events") or [])[:240]:
        if not isinstance(row, dict):
            continue
        item = {
            "seq": max(0, int(row.get("seq") or 0)),
            "offset_ms": _clean_ms(row.get("offset_ms")),
            "type": str(row.get("type") or "")[:64],
            "status": str(row.get("status") or "")[:32],
        }
        if row.get("tool"):
            item["tool"] = str(row.get("tool") or "")[:120]
        if row.get("args_hash"):
            item["args_hash"] = str(row.get("args_hash") or "")[:32]
        if isinstance(row.get("detail"), dict):
            item["detail"] = {
                str(key)[:64]: value if isinstance(value, (bool, int, float))
                else str(value or "")[:240]
                for key, value in list(row["detail"].items())[:16]
            }
        events.append(item)
    safe_context = {
        key: context.get(key)
        for key in (
            "schema", "run_id", "owner_fingerprint", "conversation_id",
            "goal_fingerprint", "scope_id", "parent_scope_id", "depth",
            "approval_mode", "network_mode", "budgets",
            "allow_subagents", "capability_revision", "effective_tools",
        )
        if key in context
    }
    safe_capabilities = {
        "schema": str(capabilities.get("schema") or "")[:80],
        "revision": str(capabilities.get("revision") or "")[:32],
        "declared_count": max(0, int(capabilities.get("declared_count") or 0)),
        "effective_count": max(0, int(capabilities.get("effective_count") or 0)),
        "effective_tools": [str(name)[:120] for name in
                            list(capabilities.get("effective_tools") or [])[:160]],
        "missing_executors": [str(name)[:120] for name in
                              list(capabilities.get("missing_executors") or [])[:160]],
    }
    return {
        "schema": "hashmm.agent-harness.v1",
        "context": safe_context,
        "capabilities": safe_capabilities,
        "trajectory": {
            "event_count": len(events),
            "event_types": {
                str(key)[:64]: max(0, int(value or 0))
                for key, value in list((trajectory.get("event_types") or {}).items())[:64]
            } if isinstance(trajectory.get("event_types"), dict) else {},
            "events": events,
            "truncated": bool(trajectory.get("truncated")),
        },
        "children": {
            "active": max(0, int(children.get("active") or 0)),
            "total": max(0, int(children.get("total") or 0)),
            "limit": max(0, int(children.get("limit") or 0)),
        },
        "terminal": {
            key: terminal.get(key)
            for key in (
                "schema", "reason", "status", "stop_reason", "started_at",
                "ended_at", "error",
            )
            if key in terminal
        },
        "levels": {
            "schema": "hashmm.harness-levels.v1",
            "claim": "local_public_trajectory_replay_only",
            "passed": max(0, int(raw_levels.get("passed") or 0)),
            "total": max(0, int(raw_levels.get("total") or len(level_rows))),
            "levels": level_rows,
        },
        "limitation": "运行轨迹证明系统观察到的动作，不证明模型文本或外部世界状态为真。",
    }


def _public_process(
    *,
    task_contract: dict[str, Any],
    harness: dict[str, Any],
    tool_steps: list[dict[str, Any]],
    completion_gate: dict[str, Any],
    stop_reason: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Persist observable task progress without exposing hidden reasoning."""
    status_alias = {
        "completed": "done", "complete": "done", "success": "done",
        "passed": "done", "pass": "done", "ok": "done", "skipped": "done",
        "failed": "error", "fail": "error", "error": "error",
        "denied": "error", "blocked": "error", "cancelled": "error",
        "interrupted": "error", "stopped": "error",
        "running": "running", "started": "running", "pending": "pending",
    }

    def public_status(value: Any, fallback: str = "done") -> str:
        return status_alias.get(str(value or "").strip().lower(), fallback)

    timeline: list[dict[str, Any]] = []
    todo: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for index, row in enumerate(list(task_contract.get("plan") or [])[:24]):
        if not isinstance(row, dict):
            continue
        detail = str(row.get("text") or "").strip()[:240]
        if not detail:
            continue
        status = public_status(row.get("status"), "pending")
        todo.append({"text": detail, "status": "doing" if status == "running" else status})
        timeline.append({
            "id": f"plan-{index + 1}", "kind": "plan", "node": "任务计划",
            "detail": detail, "status": status,
        })

    trajectory = harness.get("trajectory") if isinstance(harness.get("trajectory"), dict) else {}
    for index, row in enumerate(list(trajectory.get("events") or [])[:160]):
        if not isinstance(row, dict):
            continue
        event_type = str(row.get("type") or "").strip()[:64]
        tool = str(row.get("tool") or "").strip()[:120]
        if event_type in {
            "", "run_started", "run_finished", "context_loaded",
            "capabilities_resolved", "heartbeat",
        }:
            continue
        detail_data = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        detail = ""
        for key in ("summary", "message", "label", "reason", "stage"):
            if str(detail_data.get(key) or "").strip():
                detail = str(detail_data[key]).strip()[:240]
                break
        if not detail:
            detail = tool or event_type.replace("_", " ")
        identity = (event_type, tool, detail)
        if identity in seen:
            continue
        seen.add(identity)
        item: dict[str, Any] = {
            "id": f"harness-{int(row.get('seq') or index + 1)}",
            "kind": "tool" if tool else "run",
            "node": tool or event_type,
            "detail": detail,
            "status": public_status(row.get("status"), "done"),
        }
        if tool:
            item["tool"] = tool
        offset_ms = _clean_ms(row.get("offset_ms"))
        if offset_ms:
            item["elapsed_ms"] = offset_ms
        timeline.append(item)

    for index, row in enumerate(tool_steps[:120]):
        tool = str(row.get("tool") or row.get("node") or "工具").strip()[:120]
        detail = str(row.get("detail") or row.get("message") or tool).strip()[:240]
        identity = ("tool", tool, detail)
        if identity in seen:
            continue
        seen.add(identity)
        item = {
            "id": f"tool-{index + 1}", "kind": "tool", "node": tool,
            "tool": tool, "detail": detail,
            "status": public_status(row.get("status"), "done"),
        }
        duration_ms = _clean_ms(row.get("duration_ms"))
        if duration_ms:
            item["elapsed_ms"] = duration_ms
        timeline.append(item)

    gate_status = str(completion_gate.get("status") or "").strip()
    final_status = (
        "done" if gate_status in {"passed", "delivered", "delivered_with_limits"}
        else "error" if gate_status in {"failed", "blocked", "unavailable"}
        else public_status(stop_reason, "done")
    )
    gate_summary = completion_gate.get("summary") if isinstance(completion_gate.get("summary"), dict) else {}
    gate_detail = str(completion_gate.get("message") or "").strip()
    if not gate_detail and gate_summary:
        gate_detail = (
            f"完成检查：{int(gate_summary.get('passed') or 0)}/"
            f"{int(gate_summary.get('required') or 0)} 项已验证"
        )
        outstanding = (
            int(gate_summary.get("failed") or 0)
            + int(gate_summary.get("review") or 0)
            + int(gate_summary.get("missing") or 0)
        )
        if outstanding:
            gate_detail += f"，{outstanding} 项仍需处理或复核"
    timeline.append({
        "id": "completion", "kind": "result", "node": "完成检查",
        "detail": (gate_detail or f"任务结束：{stop_reason or 'completed'}")[:240],
        "status": final_status,
        "elapsed_ms": _clean_ms(elapsed_ms),
    })

    return {
        "schema": "hashmm.public-process.v1",
        "goal": str(task_contract.get("goal") or "").strip()[:600],
        "method": "inspect_plan_act_verify_handoff",
        "timeline": timeline[-240:],
        "todo": todo,
        "completion_status": gate_status or str(stop_reason or "unknown"),
        "reasoning_disclosed": False,
        "integrity": {
            "source": "runtime_manifest",
            "raw_model_reasoning_included": False,
            "raw_tool_arguments_included": False,
            "persisted": True,
        },
    }


def _work_loop_projection(
    *,
    task_contract: dict[str, Any],
    harness: dict[str, Any],
    verification: dict[str, Any],
    completion_gate: dict[str, Any],
    context_lifecycle: dict[str, Any],
    skill_versions: list[dict[str, str]],
) -> dict[str, Any]:
    """Project the observable five-stage Agent work loop.

    A configured mechanism is not execution evidence.  Stages therefore stay
    ``not_observed`` until the current run contains a corresponding record.
    """
    dimensions: list[dict[str, Any]] = []

    criteria = [
        item for item in list(task_contract.get("success_criteria") or [])[:32]
        if isinstance(item, dict) and str(item.get("label") or "").strip()
    ]
    goal = str(task_contract.get("goal") or "").strip()
    understanding_evidence: list[str] = []
    if goal:
        understanding_evidence.append("task_contract.goal")
    if criteria:
        understanding_evidence.append("task_contract.success_criteria")
    dimensions.append({
        "id": "task_understanding",
        "label": "理解任务",
        "status": "observed" if goal and criteria else "partial" if goal else "not_observed",
        "summary": (
            f"已记录目标与 {len(criteria)} 项完成条件"
            if goal and criteria else
            "已记录目标，但完成条件仍不完整"
            if goal else "本轮没有可核验的任务目标"
        ),
        "evidence": understanding_evidence,
    })

    trajectory = harness.get("trajectory") if isinstance(harness.get("trajectory"), dict) else {}
    event_count = max(0, int(trajectory.get("event_count") or 0))
    capabilities = harness.get("capabilities") if isinstance(harness.get("capabilities"), dict) else {}
    effective_tools = list(capabilities.get("effective_tools") or [])
    execution_evidence: list[str] = []
    if event_count:
        execution_evidence.append("harness.trajectory")
    if effective_tools:
        execution_evidence.append("harness.capabilities.effective_tools")
    dimensions.append({
        "id": "controlled_execution",
        "label": "受控执行",
        "status": "observed" if event_count else "not_observed",
        "summary": (
            f"运行时记录了 {event_count} 个受控事件"
            if event_count else "本轮没有观察到受控执行轨迹"
        ),
        "evidence": execution_evidence,
    })

    verification_status = str(verification.get("status") or "not_evaluable")
    check_count = len([
        item for item in list(verification.get("checks") or [])[:64]
        if isinstance(item, dict)
    ])
    dimensions.append({
        "id": "change_validation",
        "label": "验证结果",
        "status": (
            "passed" if verification_status == _PASS else
            "failed" if verification_status == _FAIL else "not_evaluable"
        ),
        "summary": (
            f"{check_count} 项确定性检查已通过"
            if verification_status == _PASS else
            f"{check_count} 项检查中存在失败"
            if verification_status == _FAIL else
            f"{check_count} 项检查尚不足以自动判定"
        ),
        "evidence": ["verification.checks"] if check_count else [],
    })

    gate_status = str(completion_gate.get("status") or "unavailable")
    can_deliver = bool(completion_gate.get("can_claim_complete"))
    dimensions.append({
        "id": "reliable_delivery",
        "label": "可靠交付",
        "status": (
            "passed" if can_deliver and gate_status == "verified" else
            "limited" if gate_status == "delivered_with_limits" else
            "blocked" if gate_status in {"blocked", "unavailable"} else "incomplete"
        ),
        "summary": (
            "完成门允许声明已验证交付"
            if can_deliver and gate_status == "verified" else
            "结果已交付，但仍有需要独立复核的限制"
            if gate_status == "delivered_with_limits" else
            "完成门尚未开放"
        ),
        "evidence": ["completion_gate"] if completion_gate else [],
    })

    lifecycle_evidence: list[str] = []
    if context_lifecycle:
        lifecycle_evidence.append("context_lifecycle")
    if skill_versions:
        lifecycle_evidence.append("skill_versions")
    capture_count = len(skill_versions)
    compact_count = max(0, int(context_lifecycle.get("compact_count") or 0))
    dimensions.append({
        "id": "learning_capture",
        "label": "沉淀经验",
        "status": "observed" if lifecycle_evidence else "not_observed",
        "summary": (
            f"已记录 {capture_count} 个技能版本与 {compact_count} 次上下文压缩"
            if lifecycle_evidence else
            "本轮没有观察到可复用经验或上下文快照"
        ),
        "evidence": lifecycle_evidence,
    })

    return {
        "schema": "hashmm.agent-work-loop.v1",
        "method": "understand_execute_validate_deliver_capture",
        "dimensions": dimensions,
        "integrity": {
            "source": "deterministic_runtime_projection",
            "model_self_report_used": False,
            "configured_mechanism_counts_as_use": False,
            "hidden_reasoning_included": False,
        },
        "limitation": (
            "该闭环只说明本轮可观察记录覆盖了哪些阶段；"
            "未观察到的阶段不会因系统已配置对应功能而被标记为完成。"
        ),
    }


def _bounded_context_lifecycle(raw: Any) -> dict[str, Any]:
    """Project recovery facts without owner data, prompts or summaries."""
    if not isinstance(raw, dict) or raw.get("contract") != "hashmm.context-engine.v2":
        return {}
    result: dict[str, Any] = {
        "contract": "hashmm.context-engine.v2",
        "generation": max(1, int(raw.get("generation") or 1)),
        "turns": max(0, int(raw.get("turns") or 0)),
        "compact_count": max(0, int(raw.get("compact_count") or 0)),
        "has_summary": bool(raw.get("has_summary")),
        "compacted": bool(raw.get("compacted")),
        "tool_calls": max(0, int(raw.get("tool_calls") or 0)),
    }
    checkpoint_id = str(raw.get("checkpoint_id") or "")[:96]
    if checkpoint_id:
        result["checkpoint_id"] = checkpoint_id
    capsule = raw.get("context_capsule")
    if isinstance(capsule, dict) and capsule.get("schema") == "hashmm.context-capsule.v1":
        result["context_capsule"] = {
            key: capsule.get(key)
            for key in (
                "schema", "fingerprint", "generation", "conversation_revision",
                "active_goal", "criteria", "decisions", "blockers",
                "provider_profile", "sections", "rendered_hash", "rendered_chars",
                "source_bodies_included",
            )
            if key in capsule
        }
    return result


def _bounded_skill_versions(raw: Any) -> list[dict[str, str]]:
    """Keep only public, version-identifying metadata for skills used by Chat.

    Prompt bodies are deliberately excluded.  The hash is enough to attribute
    later owner-bound feedback to the exact promoted variant without copying
    private instructions into run manifests, App sync payloads or logs.
    """
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()[:96]
        prompt_hash = str(item.get("prompt_hash") or "").strip().lower()
        if not skill_id or len(prompt_hash) != 64 or any(
            char not in "0123456789abcdef" for char in prompt_hash
        ):
            continue
        identity = (skill_id, prompt_hash)
        if identity in seen:
            continue
        seen.add(identity)
        bounded = {
            "skill_id": skill_id,
            "name": str(item.get("name") or "").strip()[:160],
            "scope": str(item.get("scope") or "").strip()[:32],
            "prompt_hash": prompt_hash,
        }
        evolution_id = str(item.get("evolution_id") or "").strip()[:96]
        if evolution_id:
            bounded["evolution_id"] = evolution_id
        result.append(bounded)
        if len(result) >= 8:
            break
    return result


def build_run_manifest(
    *,
    run_id: str,
    task_type: str,
    execution_mode: str,
    model_name: str = "",
    retrieval_mode: str = "",
    retrieval_depth: str = "",
    retrieval_strategy: str = "",
    retrieval_contract: dict | None = None,
    sources: list[dict] | None = None,
    groundings: dict | None = None,
    stage_latency_ms: dict | None = None,
    elapsed_ms: int = 0,
    stop_reason: str = "completed",
    iterations: int = 0,
    tool_steps: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    artifact_required: bool = False,
    tokens: dict | None = None,
    token_counts_estimated: bool = False,
    user_goal: str = "",
    answer_text: str = "",
    plan_items: list[dict] | None = None,
    execution_scope: dict | None = None,
    orchestration: dict | None = None,
    harness: dict | None = None,
    context_lifecycle: dict | None = None,
    skill_versions: list[dict] | None = None,
    execution_receipts: list[dict] | None = None,
    previous_causal_graph: dict | None = None,
) -> dict:
    """Build a compact manifest and an externally checkable done predicate.

    ``artifacts`` may contain ``exists`` booleans supplied by the caller after
    checking the conversation workspace.  Merely mentioning a filename never
    counts as delivery evidence.
    """
    sources = [dict(s) for s in (sources or []) if isinstance(s, dict)]
    groundings = dict(groundings or {})
    artifacts = [dict(a) for a in (artifacts or []) if isinstance(a, dict)]
    tool_steps = [dict(s) for s in (tool_steps or []) if isinstance(s, dict)]
    harness = _bounded_harness(harness)
    context_lifecycle = _bounded_context_lifecycle(context_lifecycle)
    skill_versions = _bounded_skill_versions(skill_versions)
    from hashmm.agent.execution_receipt import public_execution_receipts
    receipt_candidates = list(execution_receipts or [])
    if not receipt_candidates:
        for step in tool_steps:
            candidate = step.get("receipt") or step.get("_execution_receipt")
            if isinstance(candidate, dict):
                receipt_candidates.append(candidate)
    execution_receipts = public_execution_receipts(receipt_candidates)
    # Receipt coverage is required by an observed action, not by the caller
    # already supplying a receipt.  Keying this gate off the receipt list used
    # to fail open when a legacy executor emitted tool steps but zero receipts.
    tool_total, tool_failed = _tool_failures(tool_steps)
    runtime_config, corpus = _runtime_config()

    from hashmm.agent.task_method import build_task_contract
    task_contract = build_task_contract(
        user_goal=user_goal,
        task_type=task_type,
        execution_mode=execution_mode,
        artifact_required=artifact_required,
        evidence_expected=bool(sources),
        plan_items=plan_items,
        run_id=run_id,
    )
    if tool_total or execution_receipts:
        task_contract.setdefault("success_criteria", []).append({
            "check_id": "execution_receipt_integrity",
            "label": "Each observed tool action has a valid proof-carrying receipt",
            "required": True,
            "source": "runtime",
        })
    required_check_ids = {
        str(item.get("check_id") or "")
        for item in (task_contract.get("success_criteria") or [])
        if isinstance(item, dict) and item.get("required", True)
    }

    evidence_identity = [_source_identity(s) for s in sources]
    evidence_snapshot = {
        "id": _fingerprint(evidence_identity) if evidence_identity else "",
        "sources": len(evidence_identity),
    }
    if corpus.get("status") != "pinned" and evidence_identity:
        corpus = {
            "status": "evidence_only",
            "id": evidence_snapshot["id"],
            "note": "索引未提供构建快照；这里只能复现本轮证据集合。",
        }

    retrieval_config = {
        **runtime_config,
        "mode": str(retrieval_mode or ""),
        "depth": str(retrieval_depth or ""),
        "strategy": str(retrieval_strategy or ""),
        "graph_engineering": {
            "evidence_sources": sum(
                1 for source in sources
                if str(source.get("method") or "") == "graph_evidence"
            ),
            "evidence_preserving": True,
        },
    }
    # Chat retrieval stores one deterministic contract on the first evidence
    # record. Carry its bounded run projection into the manifest so reopening a
    # conversation, the desktop inspector and the App all observe the same
    # retrieval attempts/filters instead of consulting a process singleton.
    retrieval_contract = dict(retrieval_contract or {}) or next((
        source.get("retrieval_contract") for source in sources
        if isinstance(source.get("retrieval_contract"), dict)
    ), {})
    retrieval_run = retrieval_contract.get("run") if isinstance(retrieval_contract, dict) else None
    if isinstance(retrieval_run, dict):
        retrieval_config["run"] = retrieval_run
        retrieval_config["result_contract"] = {
            key: retrieval_contract.get(key)
            for key in (
                "schema", "requested_top_k", "total_candidates", "evidence_count",
                "unique_documents", "citation_ready_count", "citation_ready",
                "filtered_count", "rerank_method", "source_methods", "graph",
            )
        }
    retrieval_config["fingerprint"] = _fingerprint(retrieval_config)
    from hashmm.evaluation.rag_diagnostics import diagnose_run
    retrieval_diagnostics = diagnose_run(
        sources=sources,
        groundings=groundings,
        corpus=corpus,
        iterations=iterations,
        tool_steps=tool_steps,
    )

    checks: list[dict] = []
    output_ok = bool(str(answer_text or "").strip())
    checks.append(_check(
        "output_delivery", _PASS if output_ok else _FAIL,
        "已交付非空回答。" if output_ok else "运行结束时没有可交付的回答内容。",
        evidence={"characters": len(str(answer_text or ""))},
    ))

    if task_contract.get("requires_plan"):
        plan = list(task_contract.get("plan") or [])
        unfinished = [p for p in plan if str(p.get("status") or "") not in {
            "done", "completed", "skipped",
        }]
        plan_status = _PASS if plan and not unfinished else _FAIL
        checks.append(_check(
            "plan_closure", plan_status,
            (f"计划 {len(plan)} 项均已闭环。" if plan_status == _PASS else
             f"仍有 {len(unfinished)} 个计划项未闭环。" if plan_status == _FAIL else
             "复杂任务未留下可核对的结构化计划。"),
            evidence={"total": len(plan), "unfinished": len(unfinished)} if plan else None,
        ))
    if sources:
        ledger_status = str(groundings.get("status") or _NA)
        review_required = bool(groundings.get("review_required", True))
        grounding_ok = ledger_status == _PASS and not review_required
        checks.append(_check(
            "claim_grounding", _PASS if grounding_ok else _FAIL,
            "事实主张均有可解析证据锚点。" if grounding_ok else "存在未支持、推断或不可核验的事实主张。",
            evidence={
                "supported": int(groundings.get("supported_claims") or 0),
                "total": int(groundings.get("total_factual_claims") or 0),
                "coverage_ratio": groundings.get("coverage_ratio"),
                "semantic_entailment_verified": bool(
                    groundings.get("semantic_entailment_verified", False)),
            },
        ))
        invalid = list(groundings.get("invalid_citations") or [])
        checks.append(_check(
            "citation_integrity", _PASS if not invalid else _FAIL,
            "引用编号均落在本轮证据集合内。" if not invalid else "回答包含不存在的引用编号。",
            evidence={"invalid_citations": invalid[:20]},
        ))
    elif "claim_grounding" in required_check_ids:
        checks.append(_check(
            "claim_grounding", _NA,
            "本轮没有使用可核验检索来源，不能声称回答已由语料证实。",
        ))

    if "tool_execution" in required_check_ids:
        checks.append(_check(
            "tool_execution",
            (_FAIL if tool_failed else _PASS) if tool_total else _NA,
            (f"{tool_failed}/{tool_total} 个工具执行失败。" if tool_failed
             else f"{tool_total} 个工具执行均返回成功状态。" if tool_total
             else "本轮没有可核验的工具执行记录。"),
            evidence={"total": tool_total, "failed": tool_failed} if tool_total else None,
        ))

    if tool_total or execution_receipts:
        from hashmm.agent.execution_receipt import validate_execution_receipt
        receipt_validation = [
            validate_execution_receipt(item) for item in execution_receipts
        ]
        invalid_receipts = sum(1 for item in receipt_validation if not item["valid"])
        missing_receipts = max(0, tool_total - len(execution_receipts))
        unexpected_receipts = max(0, len(execution_receipts) - tool_total)

        expected_calls: dict[str, str] = {}
        expected_call_ids: list[str] = []
        unbound_steps: list[str] = []
        for step in tool_steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool_name") or step.get("tool") or step.get("name") or "")[:120]
            # Only an explicit call_id is an execution binding.  Historical
            # ``id`` fields may identify a UI/plan step rather than the tool
            # invocation and must not be compared to a receipt call ID.
            call_id = str(step.get("call_id") or "")[:160]
            if tool and call_id:
                expected_call_ids.append(call_id)
                expected_calls[call_id] = tool
            elif tool:
                unbound_steps.append(tool)
        receipt_call_ids = [
            str(item.get("call_id") or "")[:160]
            # Use pre-projection candidates here because the public projection
            # intentionally de-duplicates identical receipts.  Duplicate
            # delivery is still a contract error and must remain observable.
            for item in receipt_candidates
            if isinstance(item, dict) and item.get("call_id")
        ]
        receipt_calls = {
            call_id: str((item.get("action") or {}).get("tool") or "")[:120]
            for item in execution_receipts
            if isinstance(item, dict)
            for call_id in [str(item.get("call_id") or "")[:160]]
            if call_id
        }
        unbound_calls = sorted(set(expected_calls) - set(receipt_calls))
        mismatched_calls = sorted(
            call_id for call_id in set(expected_calls) & set(receipt_calls)
            if expected_calls[call_id] != receipt_calls[call_id]
        )
        duplicate_step_calls = sorted({
            call_id for call_id in expected_call_ids
            if expected_call_ids.count(call_id) > 1
        })
        duplicate_receipt_calls = sorted({
            call_id for call_id in receipt_call_ids
            if receipt_call_ids.count(call_id) > 1
        })
        wrong_run_receipts = sorted({
            str(item.get("call_id") or "")[:160]
            for item in execution_receipts
            if isinstance(item, dict)
            and str(item.get("run_id") or "") != str(run_id or "")
        })
        receipt_ok = (
            invalid_receipts == 0
            and missing_receipts == 0
            and unexpected_receipts == 0
            and not unbound_steps
            and not unbound_calls
            and not mismatched_calls
            and not duplicate_step_calls
            and not duplicate_receipt_calls
            and not wrong_run_receipts
        )
        checks.append(_check(
            "execution_receipt_integrity",
            _PASS if receipt_ok else _FAIL,
            (
                f"{len(execution_receipts)} execution receipts passed structural and "
                "content-integrity validation."
                if receipt_ok else
                f"Execution receipt coverage is incomplete: {invalid_receipts} invalid, "
                f"{missing_receipts} missing, {unexpected_receipts} unexpected, "
                f"{len(unbound_steps)} steps without call ids, {len(unbound_calls)} unbound, "
                f"{len(mismatched_calls)} mismatched, "
                f"{len(duplicate_step_calls) + len(duplicate_receipt_calls)} duplicate, "
                f"{len(wrong_run_receipts)} bound to another run."
            ),
            evidence={
                "receipts": len(execution_receipts),
                "invalid": invalid_receipts,
                "missing": missing_receipts,
                "unexpected": unexpected_receipts,
                "steps_without_call_id": len(unbound_steps),
                "unbound_call_ids": unbound_calls[:20],
                "mismatched_call_ids": mismatched_calls[:20],
                "duplicate_step_call_ids": duplicate_step_calls[:20],
                "duplicate_receipt_call_ids": duplicate_receipt_calls[:20],
                "wrong_run_call_ids": wrong_run_receipts[:20],
                "raw_arguments_persisted": False,
                "raw_results_persisted": False,
            },
        ))

    # Evaluation-driven development: validate the trajectory contract itself,
    # not only the answer.  This is deterministic and never delegates the
    # success decision to the model that produced the output.
    if harness:
        trajectory_events = list((harness.get("trajectory") or {}).get("events") or [])
        expected_seq = list(range(1, len(trajectory_events) + 1))
        observed_seq = [int(row.get("seq") or 0) for row in trajectory_events]
        terminal = harness.get("terminal") or {}
        trajectory_ok = bool(trajectory_events and observed_seq == expected_seq
                             and terminal.get("reason"))
        checks.append(_check(
            "trajectory_integrity", _PASS if trajectory_ok else _FAIL,
            (f"运行时记录了 {len(trajectory_events)} 个有序生命周期事件及终止结果。"
             if trajectory_ok else "运行轨迹缺少连续事件序号或规范化终止结果。"),
            evidence={"events": len(trajectory_events),
                      "terminal_reason": str(terminal.get("reason") or "")},
        ))
        capability_data = harness.get("capabilities") or {}
        missing = list(capability_data.get("missing_executors") or [])
        checks.append(_check(
            "runtime_wiring", _PASS,
            (f"本轮向模型暴露 {int(capability_data.get('effective_count') or 0)} 个"
             f"可执行能力；另有 {len(missing)} 个无执行器 schema 已在模型调用前移除。"),
            evidence={
                "capability_revision": str(capability_data.get("revision") or ""),
                "effective": int(capability_data.get("effective_count") or 0),
                "excluded_missing_executor": len(missing),
            },
        ))

    if "agent_completion" in required_check_ids:
        members = [
            item for item in ((orchestration or {}).get("members") or [])
            if isinstance(item, dict)
        ]
        done_states = {"done", "ok", "completed", "complete"}
        fail_states = {"failed", "fail", "error", "stopped", "blocked", "cancelled"}
        states = [str(item.get("status") or "").strip().lower() for item in members]
        completed_agents = sum(1 for state in states if state in done_states)
        failed_agents = sum(1 for state in states if state in fail_states)
        open_agents = max(0, len(states) - completed_agents - failed_agents)
        if members and completed_agents == len(members):
            agent_status = _PASS
        elif failed_agents or (members and str(stop_reason or "").lower() in {"completed", "complete", "done"}):
            agent_status = _FAIL
        else:
            agent_status = _NA
        checks.append(_check(
            "agent_completion", agent_status,
            f"{completed_agents}/{len(members)} 个协作分工完成；失败 {failed_agents}，未结束 {open_agents}。",
            evidence={
                "total": len(members), "completed": completed_agents,
                "failed": failed_agents, "open": open_agents,
            },
        ))

    if artifact_required:
        valid_artifacts = [a for a in artifacts if a.get("exists") is True]
        checks.append(_check(
            "artifact_delivery", _PASS if valid_artifacts else _FAIL,
            (f"已在会话工作区验证 {len(valid_artifacts)} 个交付文件。"
             if valid_artifacts else "任务要求文件交付，但会话工作区中没有验证到成品。"),
            evidence={"files": [str(a.get("filename") or "") for a in valid_artifacts[:20]]},
        ))

    failed_checks = [c["id"] for c in checks if c["status"] == _FAIL]
    passed_checks = [c["id"] for c in checks if c["status"] == _PASS]
    unevaluated_checks = [c["id"] for c in checks if c["status"] == _NA]
    checks_by_id = {str(c.get("id") or ""): c for c in checks}
    failed_required = [
        check_id for check_id in required_check_ids
        if (checks_by_id.get(check_id) or {}).get("status") == _FAIL
    ]
    unverified_required = [
        check_id for check_id in required_check_ids
        if (checks_by_id.get(check_id) or {}).get("status") in {None, _NA}
    ]
    terminal_ok = str(stop_reason or "").lower() in {"completed", "complete", "done"}
    if not terminal_ok or failed_required:
        done_status = _FAIL
    elif unverified_required:
        done_status = _NA
    else:
        done_status = _PASS

    stages = {str(k): _clean_ms(v) for k, v in (stage_latency_ms or {}).items()
              if str(k) and v is not None}
    stages["total"] = _clean_ms(elapsed_ms)
    token_data = {
        "input": int((tokens or {}).get("input", 0) or 0),
        "output": int((tokens or {}).get("output", 0) or 0),
        "estimated": bool(token_counts_estimated),
    }

    try:
        from hashmm import RELEASE
    except Exception:
        RELEASE = ""

    if not terminal_ok:
        handoff_status = "blocked"
        handoff_summary = "运行未按完成状态结束，可从停止点继续。"
        next_action = "检查停止原因后恢复或重试未完成步骤。"
    elif failed_checks:
        handoff_status = "needs_attention"
        handoff_summary = f"已产出结果，但有 {len(failed_checks)} 项完成检查未通过。"
        next_action = "先处理失败检查，再把本轮标记为完成。"
    elif unevaluated_checks:
        handoff_status = "delivered_with_limits"
        handoff_summary = f"已交付结果；另有 {len(unevaluated_checks)} 项无法由运行时自动核验。"
        next_action = "对不可自动核验的结论做人工或独立来源复核。"
    else:
        handoff_status = "checks_passed"
        handoff_summary = "本轮结构化完成条件均已通过。"
        next_action = "可基于当前交付继续迭代。"

    verification = {
        "status": done_status,
        "method": "deterministic_external_checks",
        "model_self_score_used": False,
        "failed_checks": failed_required,
        "not_evaluable_required": unverified_required,
        "checks": checks,
    }
    from hashmm.agent.task_evidence_graph import build_task_evidence_graph
    evidence_graph = build_task_evidence_graph(
        run_id=run_id,
        goal=user_goal,
        task_contract=task_contract,
        execution_scope=execution_scope,
        retrieval_contract=retrieval_contract,
        sources=sources,
        groundings=groundings,
        tool_steps=tool_steps,
        artifacts=artifacts,
        orchestration=orchestration,
        verification=verification,
    )
    try:
        from hashmm.api.tool_registry import get_executor_map
        available_tools = get_executor_map().keys()
    except Exception:
        available_tools = []
    from hashmm.agent.execution_frontier import build_execution_frontier
    execution_frontier = build_execution_frontier(
        evidence_graph,
        execution_scope=execution_scope,
        available_tools=available_tools,
    )
    from hashmm.agent.causal_work_graph import build_causal_work_graph
    context_capsule = context_lifecycle.get("context_capsule", {})
    causal_work_graph = build_causal_work_graph(
        run_id=run_id,
        evidence_graph=evidence_graph,
        execution_receipts=execution_receipts,
        source_snapshots=sources,
        context_capsule=context_capsule,
        previous_graph=previous_causal_graph,
    )
    from hashmm.agent.completion_gate import build_completion_gate
    completion_gate = build_completion_gate(
        task_contract=task_contract,
        verification=verification,
        evidence_graph=evidence_graph,
        execution_frontier=execution_frontier,
        termination_reason=stop_reason,
        tool_steps=tool_steps,
        orchestration=orchestration,
        causal_work_graph=causal_work_graph,
    )
    frontier_items = [
        item for item in (execution_frontier.get("items") or [])
        if isinstance(item, dict) and str(item.get("minimum_action") or "").strip()
    ]
    if frontier_items:
        next_action = str(frontier_items[0].get("minimum_action") or next_action)[:300]
    next_action = str(completion_gate.get("next_action") or next_action)[:300]
    gate_status = str(completion_gate.get("status") or "unavailable")
    if gate_status == "verified":
        handoff_status = "checks_passed"
        handoff_summary = "可观察的必需完成条件与执行轨迹均已闭环。"
    elif gate_status == "delivered_with_limits":
        handoff_status = "delivered_with_limits"
        handoff_summary = "结果已交付，但仍有必需条件需要用户或独立来源复核。"
    elif gate_status in {"blocked", "unavailable"}:
        handoff_status = "blocked"
        handoff_summary = "完成门未开放；任务仍受运行状态、权限或证据缺口阻塞。"
    else:
        handoff_status = "needs_attention"
        handoff_summary = "已有交付，但必需完成条件或执行轨迹尚未闭环。"

    public_process = _public_process(
        task_contract=task_contract,
        harness=harness,
        tool_steps=tool_steps,
        completion_gate=completion_gate,
        stop_reason=stop_reason,
        elapsed_ms=elapsed_ms,
    )
    work_loop = _work_loop_projection(
        task_contract=task_contract,
        harness=harness,
        verification=verification,
        completion_gate=completion_gate,
        context_lifecycle=context_lifecycle,
        skill_versions=skill_versions,
    )

    return {
        "schema": SCHEMA,
        "run_id": str(run_id or ""),
        "release": str(RELEASE or ""),
        "task_type": str(task_type or ""),
        "execution_mode": str(execution_mode or ""),
        "model": str(model_name or "unknown"),
        "retrieval": retrieval_config,
        "retrieval_diagnostics": retrieval_diagnostics,
        "corpus_snapshot": corpus,
        "evidence_snapshot": evidence_snapshot,
        "stage_latency_ms": stages,
        "termination": {
            "reason": str(stop_reason or "unknown"),
            "iterations": max(0, int(iterations or 0)),
        },
        "tokens": token_data,
        "task_contract": task_contract,
        "evidence_graph": evidence_graph,
        "causal_work_graph": causal_work_graph,
        "execution_receipts": execution_receipts,
        "execution_frontier": execution_frontier,
        "completion_gate": completion_gate,
        "harness": harness,
        "process": public_process,
        "work_loop": work_loop,
        "context_lifecycle": context_lifecycle,
        "skill_versions": skill_versions,
        "handoff": {
            "status": handoff_status,
            "summary": handoff_summary,
            "passed_checks": passed_checks,
            "failed_checks": failed_checks,
            "not_evaluable_checks": unevaluated_checks,
            "next_action": next_action,
            "limitation": "运行检查只证明可观察状态，不证明模型结论在现实世界中为真。",
        },
        "verification": verification,
    }


def validated_artifacts(conv_id: str, files: list[dict] | None) -> list[dict]:
    """Resolve file records against the conversation workspace without guessing."""
    result: list[dict] = []
    try:
        from hashmm.api import database as db
        for item in files or []:
            if not isinstance(item, dict):
                continue
            filename = Path(str(item.get("filename") or "")).name
            path = db.get_conversation_file_path(conv_id, filename) if filename else None
            exists = bool(path and Path(path).is_file())
            result.append({"filename": filename, "exists": exists})
    except Exception:
        # Returning explicit false is safer than turning an exception into success.
        return [{"filename": Path(str(i.get("filename") or "")).name, "exists": False}
                for i in (files or []) if isinstance(i, dict)]
    return result


def runtime_model_name(llm_fn: Any = None) -> str:
    """Return only the public model identifier; never serialize credentials."""
    for attr in ("model_name", "model"):
        value = getattr(llm_fn, attr, "") if llm_fn is not None else ""
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    try:
        from hashmm.api.core.services import ServiceRegistry
        info = getattr(ServiceRegistry, "llm_info", None) or {}
        for key in ("model_name", "name"):
            value = info.get(key) if isinstance(info, dict) else ""
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
    except Exception:
        pass
    return "unknown"
