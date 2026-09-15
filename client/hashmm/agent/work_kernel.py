"""Deterministic Work admission for HashMM V459-V473.

Every producer (Chat, AgentLoop, Team, browser, remote and artifacts) reaches
``work_runtime.create_run``.  This module is therefore the narrow place where
the user goal, completion boundary, capability facts and causal seed are
normalised before they become durable state.

It deliberately does not call an LLM and does not grant execution authority.
Capability readiness comes from the mounted runtime/tool registry; device
authority still requires an owner-scoped execution lease.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from hashmm.agent.task_method import build_task_contract
from hashmm.agent.operating_contract import (
    compile_operating_contract,
    normalize_work_method,
    requested_capability_path,
)
from hashmm.agent.long_horizon import initialise_handoff


ADMISSION_SCHEMA = "hashmm.work-admission.v1"
CAPABILITY_FACTS_SCHEMA = "hashmm.capability-facts.v1"

_BROWSER_TERMS = (
    "browser", "web page", "website", "浏览器", "网页", "网站", "页面",
    "打开链接", "联网调研",
)
_COMPUTER_TERMS = (
    "computer", "desktop", "file", "shell", "terminal", "电脑", "桌面",
    "文件", "代码", "终端", "命令", "word", "pdf", "ppt", "excel",
)
_EVIDENCE_TERMS = (
    "依据", "证据", "来源", "引用", "检索", "调研", "research", "source",
    "citation", "rag",
)
_ARTIFACT_TERMS = (
    "文档", "文件", "报告", "海报", "画布", "word", "pdf", "ppt",
    "excel", "artifact", "生成代码",
)


def _text(value: Any, limit: int = 800) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _requested_path(goal: str, kind: str, execution_mode: str) -> str:
    corpus = f"{kind}\n{execution_mode}\n{goal}".lower()
    if kind == "browser" or any(term in corpus for term in _BROWSER_TERMS):
        return "browser"
    if kind in {"computer", "artifact"} or any(
        term in corpus for term in _COMPUTER_TERMS
    ):
        return "computer"
    return "structured"


def _runtime_index(user_id: str) -> tuple[dict[str, dict[str, Any]], str, str]:
    """Return real capability records, fail-closed on partial startup."""
    try:
        from hashmm.agent.capabilities import build_runtime_capabilities

        snapshot = build_runtime_capabilities(user_id)
        rows = {
            str(item.get("id") or ""): dict(item)
            for item in list(snapshot.get("capabilities") or [])
            if isinstance(item, Mapping) and str(item.get("id") or "")
        }
        return rows, str(snapshot.get("revision") or ""), "runtime_registry"
    except Exception as exc:
        # Exception class is useful for diagnostics; exception text may contain
        # local paths or provider details and is intentionally not persisted.
        return {}, "", f"runtime_registry_error:{type(exc).__name__}"


def collect_capability_facts(
    *,
    user_id: str,
    goal: str,
    kind: str,
    execution_mode: str = "",
    requested_run_mode: str = "auto",
) -> dict[str, Any]:
    """Compile the least-invasive feasible route from live registry facts."""
    index, revision, source = _runtime_index(user_id)
    inferred = _requested_path(goal, kind, execution_mode)
    requested = requested_capability_path(
        {"run_mode": requested_run_mode}, inferred_path=inferred,
    )

    def capability(cap_ids: tuple[str, ...]) -> tuple[bool, str]:
        records = [index.get(item, {}) for item in cap_ids]
        ready = any(
            row.get("state") == "ready" and bool(row.get("wired"))
            for row in records
        )
        if ready:
            label = next(
                (
                    _text(row.get("title"), 100)
                    for row in records
                    if row.get("state") == "ready" and row.get("wired")
                ),
                "运行能力已连接",
            )
            return True, f"{label}已由运行时确认"
        reasons = [
            _text(row.get("reason"), 160)
            for row in records if row and _text(row.get("reason"), 160)
        ]
        return False, reasons[0] if reasons else "运行时未证明该能力可用"

    structured_ready, structured_reason = capability(
        ("rag", "web_research", "artifacts", "mcp")
    )
    browser_ready, browser_reason = capability(("browser_use",))
    computer_ready, computer_reason = capability(
        ("computer_use", "execution_sandbox")
    )

    def fact(path: str, ready: bool, reason: str, *, confirmation: bool) -> dict[str, Any]:
        selected_for_goal = requested == path
        return {
            "available": ready,
            "configured": ready,
            "within_scope": selected_for_goal,
            "requires_confirmation": bool(confirmation and selected_for_goal),
            "reason": (
                reason if selected_for_goal
                else "当前目标没有要求使用这类能力"
            ),
        }

    facts = {
        "structured": fact(
            "structured", structured_ready, structured_reason, confirmation=False,
        ),
        "browser": fact(
            "browser", browser_ready, browser_reason, confirmation=True,
        ),
        "computer": fact(
            "computer", computer_ready, computer_reason, confirmation=True,
        ),
        "manual": {
            "available": True,
            "configured": True,
            "within_scope": True,
            "requires_confirmation": True,
            "reason": "没有可安全自动执行的路径时由用户完成明确的一步",
        },
    }
    facts["_meta"] = {
        "schema": CAPABILITY_FACTS_SCHEMA,
        "requested_path": requested,
        "runtime_revision": revision,
        "source": source,
        "observed_at": time.time(),
        "server_facts_only": True,
    }
    return facts


def _causal_seed(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Persist only contract relationships which are true at admission."""
    run_id = _text(contract.get("run_id"), 96) or "pending"
    goal_id = f"goal:{run_id}"
    nodes: list[dict[str, Any]] = [{
        "id": goal_id,
        "kind": "Goal",
        "label": _text(contract.get("goal"), 240),
        "status": "recorded",
    }]
    edges: list[dict[str, str]] = []
    for index, item in enumerate(list(contract.get("success_criteria") or [])[:24]):
        if not isinstance(item, Mapping):
            continue
        criterion_id = _text(item.get("check_id"), 80) or f"criterion-{index + 1}"
        node_id = f"criterion:{criterion_id}"
        nodes.append({
            "id": node_id,
            "kind": "Criterion",
            "label": _text(item.get("label"), 240),
            "status": "required" if item.get("required", True) else "optional",
        })
        edges.append({"from": node_id, "to": goal_id, "kind": "supports"})
    return {
        "schema": "hashmm.causal-work-graph.v1",
        "nodes": nodes,
        "edges": edges,
        "source": "task_contract",
        "model_inferred_edges": False,
    }


def prepare_work_snapshot(
    *,
    user_id: str,
    run_id: str,
    kind: str,
    title: str,
    conv_id: str,
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a bounded admission snapshot shared by all Work producers."""
    prepared = dict(snapshot or {})
    manifest = _dict(prepared.get("run_manifest"))
    goal = _text(
        prepared.get("goal")
        or manifest.get("user_goal")
        or manifest.get("goal")
        or title,
    )
    execution_mode = _text(
        manifest.get("execution_mode")
        or prepared.get("execution_mode")
        or kind,
        80,
    )
    task_type = _text(prepared.get("task_type") or f"{kind}_task", 80)
    work_method = normalize_work_method(
        _dict(prepared.get("work_method"))
        or _dict(manifest.get("work_method"))
    )
    existing_contract = _dict(manifest.get("task_contract"))
    contract = existing_contract or build_task_contract(
        user_goal=goal,
        task_type=task_type,
        execution_mode=execution_mode,
        artifact_required=(
            kind == "artifact"
            or any(term in goal.lower() for term in _ARTIFACT_TERMS)
        ),
        evidence_expected=any(term in goal.lower() for term in _EVIDENCE_TERMS),
        run_id=run_id,
        conversation_id=conv_id,
    )
    if not contract.get("run_id"):
        contract["run_id"] = run_id
    if not contract.get("conversation_id"):
        contract["conversation_id"] = conv_id

    capability_facts = _dict(prepared.get("capability_facts"))
    if not capability_facts:
        capability_facts = collect_capability_facts(
            user_id=user_id,
            goal=goal,
            kind=kind,
            execution_mode=execution_mode,
            requested_run_mode=work_method["run_mode"],
        )
    operating_contract = compile_operating_contract(
        owner_id=user_id,
        run_id=run_id,
        kind=kind,
        goal=goal,
        work_method=work_method,
        capability_facts=capability_facts,
        task_contract=contract,
    )
    graph = (
        _dict(manifest.get("causal_work_graph"))
        or _dict(prepared.get("causal_work_graph"))
        or _causal_seed(contract)
    )
    long_horizon_handoff = initialise_handoff(
        contract,
        run_id=run_id,
        goal=goal,
    )
    task_trace = {
        "schema": "hashmm.task-trace.v1",
        "items": [{
            "seq": 1,
            "type": "admitted",
            "status": "queued",
            "summary": "目标、完成条件与权限边界已记录",
            "evidence": False,
            "private_reasoning": False,
        }],
        "limit": 80,
    }
    manifest.update({
        "schema": _text(manifest.get("schema"), 80) or ADMISSION_SCHEMA,
        "run_id": run_id,
        "execution_mode": execution_mode,
        "task_contract": contract,
        "work_method": work_method,
        "operating_contract": operating_contract,
        "capability_facts": capability_facts,
        "causal_work_graph": graph,
        "long_horizon_handoff": long_horizon_handoff,
        "task_trace": task_trace,
    })
    prepared["run_manifest"] = manifest
    prepared["work_method"] = work_method
    prepared["operating_contract"] = operating_contract
    # Backward-compatible projection; work_os reads either location.
    prepared["capability_facts"] = capability_facts
    prepared["long_horizon_handoff"] = long_horizon_handoff
    prepared["task_trace"] = task_trace
    prepared["admission_receipt"] = {
        "schema": ADMISSION_SCHEMA,
        "goal_hash": _hash(goal),
        "contract_hash": _hash(contract),
        "operating_contract_hash": _hash(operating_contract),
        "capability_revision": _text(
            _dict(capability_facts.get("_meta")).get("runtime_revision"), 32,
        ),
        "server_compiled": True,
        "model_prose_used_as_fact": False,
        "created_at": time.time(),
    }
    return prepared
