"""Versioned causal WorkGraph projected from observable HashMM run facts.

The existing task-evidence graph remains the compact compatibility view.  This
module adds revisions, source snapshots, execution receipts and selective
invalidation so a changed document, browser snapshot or artifact invalidates
only downstream claims/checks instead of discarding an entire long task.
"""
from __future__ import annotations

from collections import Counter, deque
import hashlib
import json
from typing import Any, Iterable, Mapping

from hashmm.agent.execution_receipt import (
    SCHEMA as RECEIPT_SCHEMA,
    public_execution_receipts,
    validate_execution_receipt,
)


SCHEMA = "hashmm.causal-work-graph.v1"
_DEPENDENCY_RELATIONS = {
    "supports", "contradicts", "derived_from", "produces", "produced_by",
    "informs", "contributes_to", "delivers", "materializes_as", "verifies",
    "verified_by", "executes_for", "retrieves_for", "works_on",
}
# Only changes to execution inputs/observations invalidate downstream work.
# A check progressing from ``not_evaluable`` to ``passed`` is the act of
# revalidation itself; treating that expected lifecycle transition as a new
# stale source would make a completion gate impossible to close.
_INVALIDATION_ROOT_KINDS = {
    "source", "source_snapshot", "receipt", "tool", "artifact",
    "context_capsule", "scope", "approval", "query", "retrieval",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(value: Any, length: int = 24) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _source_key(item: Mapping[str, Any], index: int) -> str:
    return _text(
        item.get("evidence_id") or item.get("source_id")
        or item.get("chunk_id") or item.get("doc_id")
        or item.get("citation_id") or item.get("id") or index,
        180,
    )


def _source_hash(item: Mapping[str, Any]) -> str:
    if item.get("content_hash"):
        return _text(item.get("content_hash"), 96)
    return _hash({
        "source_id": item.get("source_id"),
        "chunk_id": item.get("chunk_id"),
        "doc_id": item.get("doc_id"),
        "page": item.get("page"),
        "section": item.get("section"),
        "revision": item.get("revision") or item.get("etag"),
        "text": item.get("text") or item.get("content"),
    }, 64)


def _trust(kind: str) -> str:
    if kind in {"scope", "approval", "criterion", "goal"}:
        return "runtime_authority"
    if kind in {"receipt", "tool", "check", "artifact"}:
        return "runtime_observation"
    if kind in {"source", "source_snapshot", "claim", "query", "retrieval"}:
        return "untrusted_evidence"
    return "runtime_fact"


def _sensitivity(kind: str) -> str:
    return "restricted" if kind in {"scope", "approval"} else "workspace"


def _previous_nodes(previous: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(previous, Mapping) or previous.get("schema") != SCHEMA:
        return {}
    return {
        _text(node.get("semantic_key"), 240): dict(node)
        for node in list(previous.get("nodes") or [])
        if isinstance(node, Mapping) and _text(node.get("semantic_key"), 240)
    }


def build_causal_work_graph(
    *,
    run_id: str,
    evidence_graph: Mapping[str, Any] | None = None,
    execution_receipts: Iterable[Mapping[str, Any]] | None = None,
    source_snapshots: Iterable[Mapping[str, Any]] | None = None,
    context_capsule: Mapping[str, Any] | None = None,
    previous_graph: Mapping[str, Any] | None = None,
    observed_at: float = 0,
) -> dict[str, Any]:
    """Build a deterministic graph generation and its invalidation frontier."""
    base = dict(evidence_graph or {})
    receipts = public_execution_receipts(execution_receipts)
    sources = [
        dict(item) for item in list(source_snapshots or [])[:80]
        if isinstance(item, Mapping)
    ]
    capsule = dict(context_capsule or {})
    run = _text(run_id, 160) or _text(base.get("run_id"), 160) or "unidentified-run"
    observed = max(
        [float(observed_at or 0)]
        + [
            float((item.get("timing") or {}).get("finished_at") or 0)
            for item in receipts if isinstance(item.get("timing"), Mapping)
        ]
    )
    generation_seed = {
        "run_id": run,
        "evidence_graph_id": base.get("graph_id"),
        "receipts": [item.get("integrity", {}).get("content_hash") for item in receipts],
        "sources": [_source_hash(item) for item in sources],
        "context": capsule.get("fingerprint") or capsule.get("rendered_hash"),
    }
    generation_id = "gen_" + _hash(generation_seed)
    previous = _previous_nodes(previous_graph)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(
        *,
        semantic_key: str,
        kind: str,
        label: str,
        status: str,
        payload: Mapping[str, Any] | None = None,
        content_hash: str = "",
    ) -> str:
        semantic = _text(semantic_key, 240)
        node_id = "cw:" + _hash([run, semantic], 20)
        safe_payload = dict(payload or {})
        digest = content_hash or _hash({
            "kind": kind, "label": label, "status": status, "payload": safe_payload,
        }, 64)
        old = previous.get(semantic, {})
        unchanged = bool(old and old.get("content_hash") == digest)
        revision = max(1, int(old.get("revision") or 0) + (0 if unchanged else 1))
        nodes[node_id] = {
            "id": node_id,
            "semantic_key": semantic,
            "kind": _text(kind, 64),
            "label": _text(label, 260) or _text(kind, 64),
            "status": _text(status, 40) or "unknown",
            "content_hash": digest,
            "generation_id": generation_id,
            "revision": revision,
            "observed_at": observed,
            "valid_from": observed,
            "valid_to": None,
            "trust": _trust(kind),
            "sensitivity": _sensitivity(kind),
            "payload": safe_payload,
        }
        return node_id

    def add_edge(source: str, target: str, relation: str, **metadata: Any) -> None:
        if source not in nodes or target not in nodes:
            return
        key = (source, target, relation)
        row: dict[str, Any] = {
            "source": source,
            "target": target,
            "relation": _text(relation, 64),
        }
        clean = {
            _text(key, 64): value for key, value in metadata.items()
            if value not in (None, "", [], {})
        }
        if clean:
            row["metadata"] = clean
        edges[key] = row

    base_to_causal: dict[str, str] = {}
    for item in list(base.get("nodes") or [])[:320]:
        if not isinstance(item, Mapping):
            continue
        base_id = _text(item.get("id"), 240)
        if not base_id:
            continue
        kind = _text(item.get("kind"), 64) or "runtime_fact"
        payload = dict(item.get("meta") or {}) if isinstance(item.get("meta"), Mapping) else {}
        node_id = add_node(
            semantic_key=f"evidence:{base_id}",
            kind=kind,
            label=_text(item.get("label"), 260),
            status=_text(item.get("status"), 40),
            payload=payload,
        )
        base_to_causal[base_id] = node_id
    for edge in list(base.get("edges") or [])[:640]:
        if not isinstance(edge, Mapping):
            continue
        add_edge(
            base_to_causal.get(_text(edge.get("source"), 240), ""),
            base_to_causal.get(_text(edge.get("target"), 240), ""),
            _text(edge.get("relation"), 64),
        )

    source_nodes_by_ref: dict[str, str] = {}
    goal_id = base_to_causal.get(_text(base.get("root_node_id"), 240), "")
    base_source_nodes = [
        (node_id, item) for node_id, item in nodes.items()
        if item.get("kind") == "source"
    ]
    for index, item in enumerate(sources):
        key = _source_key(item, index)
        freshness = _text(item.get("freshness"), 32).lower()
        source_status = "available" if freshness in {"", "current", "available"} else (
            freshness if freshness in {"stale", "unavailable"} else "available"
        )
        node_id = add_node(
            semantic_key=f"source_snapshot:{key}",
            kind="source_snapshot",
            label=_text(item.get("filename") or item.get("doc_id") or key, 220),
            status=source_status,
            content_hash=_source_hash(item),
            payload={
                "source_ref": _hash(["source", key], 20),
                "revision": _text(item.get("revision") or item.get("etag"), 80),
                "page": item.get("page"),
                "method": _text(item.get("method"), 48),
                "freshness": freshness,
                "artifact_ref": _text(item.get("artifact_ref"), 240),
            },
        )
        source_nodes_by_ref[key] = node_id
        for base_source_id, base_source in base_source_nodes:
            payload = (
                base_source.get("payload")
                if isinstance(base_source.get("payload"), Mapping) else {}
            )
            aliases = {
                _text(base_source.get("label"), 180),
                _text(payload.get("chunk_id"), 180),
                _text(payload.get("doc_id"), 180),
            }
            if key in aliases or _text(item.get("filename"), 180) in aliases:
                add_edge(node_id, base_source_id, "materializes_as")
        if goal_id:
            add_edge(node_id, goal_id, "informs")

    tool_nodes: dict[str, list[str]] = {}
    artifact_nodes: list[str] = []
    check_nodes: list[str] = []
    scope_nodes: list[str] = []
    for node_id, item in nodes.items():
        kind = item.get("kind")
        if kind == "tool":
            tool_nodes.setdefault(_text(item.get("label"), 120), []).append(node_id)
        elif kind == "artifact":
            artifact_nodes.append(node_id)
        elif kind == "check":
            check_nodes.append(node_id)
        elif kind == "scope":
            scope_nodes.append(node_id)

    # Browser EvidenceRefs can point at an Artifact/Canvas without copying page
    # prose into the graph.  A later observation with a different content hash
    # therefore invalidates only the linked artifact dependency closure.
    for index, item in enumerate(sources):
        artifact_ref = _text(item.get("artifact_ref"), 240)
        if not artifact_ref:
            continue
        source_id = source_nodes_by_ref.get(_source_key(item, index), "")
        for artifact_id in artifact_nodes:
            artifact = nodes.get(artifact_id) or {}
            if artifact_ref in {
                _text(artifact.get("label"), 240),
                _text((artifact.get("payload") or {}).get("filename"), 240)
                if isinstance(artifact.get("payload"), Mapping) else "",
            }:
                add_edge(source_id, artifact_id, "supports", origin="evidence_ref")

    tool_offsets: Counter[str] = Counter()
    for receipt in receipts:
        receipt_id = _text(receipt.get("receipt_id"), 160)
        action = receipt.get("action") if isinstance(receipt.get("action"), Mapping) else {}
        outcome = receipt.get("outcome") if isinstance(receipt.get("outcome"), Mapping) else {}
        tool_name = _text(action.get("tool"), 120)
        valid = validate_execution_receipt(receipt)["valid"]
        node_id = add_node(
            semantic_key=f"receipt:{receipt_id}",
            kind="receipt",
            label=f"{tool_name} execution receipt",
            status=_text(outcome.get("status"), 40) if valid else "invalid",
            content_hash=_text(
                (receipt.get("integrity") or {}).get("content_hash"), 96,
            ) or _hash(receipt, 64),
            payload={
                "receipt_id": receipt_id,
                "valid": valid,
                "risk_level": _text((receipt.get("risk") or {}).get("level"), 24),
                "side_effect_class": _text(
                    (receipt.get("side_effect") or {}).get("class"), 40,
                ),
            },
        )
        candidates = tool_nodes.get(tool_name, [])
        offset = tool_offsets[tool_name]
        if offset < len(candidates):
            add_edge(node_id, candidates[offset], "proves")
            tool_offsets[tool_name] += 1
        for scope_id in scope_nodes[:1]:
            add_edge(node_id, scope_id, "authorized_by")
        for artifact_id in artifact_nodes[: len(receipt.get("artifacts") or [])]:
            add_edge(node_id, artifact_id, "materializes_as")
        for check_id in check_nodes[: len(receipt.get("verification") or [])]:
            add_edge(node_id, check_id, "verified_by")
        for ref in list(receipt.get("evidence_refs") or [])[:24]:
            source_id = source_nodes_by_ref.get(_text(ref, 180))
            if source_id:
                add_edge(node_id, source_id, "derived_from")

    if capsule.get("schema") == "hashmm.context-capsule.v1":
        capsule_id = add_node(
            semantic_key=f"context_capsule:{capsule.get('fingerprint')}",
            kind="context_capsule",
            label="Compiled context capsule",
            status="compiled",
            content_hash=_text(capsule.get("fingerprint"), 96) or _hash(capsule, 64),
            payload={
                "generation": max(1, int(capsule.get("generation") or 1)),
                "section_count": len(capsule.get("sections") or []),
                "rendered_chars": max(0, int(capsule.get("rendered_chars") or 0)),
            },
        )
        if goal_id:
            add_edge(capsule_id, goal_id, "constrains")

    current_by_semantic = {
        _text(node.get("semantic_key"), 240): node for node in nodes.values()
    }
    observed_changed = sorted(
        semantic for semantic, node in current_by_semantic.items()
        if semantic in previous
        and previous[semantic].get("content_hash") != node.get("content_hash")
    )
    changed = [
        semantic for semantic in observed_changed
        if str(current_by_semantic[semantic].get("kind") or "")
        in _INVALIDATION_ROOT_KINDS
    ]
    added = sorted(set(current_by_semantic) - set(previous))
    removed = sorted(set(previous) - set(current_by_semantic))
    changed_ids = {
        current_by_semantic[semantic]["id"] for semantic in changed
        if semantic in current_by_semantic
    }
    adjacency: dict[str, set[str]] = {}
    for edge in edges.values():
        if edge["relation"] in _DEPENDENCY_RELATIONS:
            adjacency.setdefault(edge["source"], set()).add(edge["target"])
    intrinsic_stale_ids = {
        node_id for node_id, node in nodes.items()
        if node.get("kind") == "source_snapshot"
        and node.get("status") in {"stale", "unavailable"}
    }
    stale: set[str] = set(intrinsic_stale_ids)
    queue = deque(changed_ids | intrinsic_stale_ids)
    while queue:
        source = queue.popleft()
        for target in adjacency.get(source, set()):
            if target not in stale and target not in changed_ids:
                stale.add(target)
                queue.append(target)
    for node_id in stale:
        if node_id not in intrinsic_stale_ids:
            nodes[node_id]["status_before_invalidation"] = nodes[node_id]["status"]
            nodes[node_id]["status"] = "stale"
        nodes[node_id]["stale_because"] = sorted(changed_ids | intrinsic_stale_ids)[:16]

    node_list = sorted(nodes.values(), key=lambda item: (item["kind"], item["id"]))
    edge_list = sorted(
        edges.values(), key=lambda item: (
            item["source"], item["target"], item["relation"],
        ),
    )
    invalid_receipts = sum(
        1 for item in receipts if not validate_execution_receipt(item)["valid"]
    )
    graph_id = _hash({
        "generation_id": generation_id,
        "nodes": [(item["id"], item["content_hash"], item["revision"]) for item in node_list],
        "edges": [(item["source"], item["target"], item["relation"]) for item in edge_list],
    })
    return {
        "schema": SCHEMA,
        "graph_id": graph_id,
        "run_id": run,
        "generation_id": generation_id,
        "previous_generation_id": _text(
            (previous_graph or {}).get("generation_id") if isinstance(previous_graph, Mapping) else "",
            80,
        ),
        "status": (
            "invalid" if invalid_receipts else "stale" if stale else "ready"
        ),
        "nodes": node_list,
        "edges": edge_list,
        "summary": {
            "nodes": len(node_list),
            "edges": len(edge_list),
            "counts": dict(sorted(Counter(item["kind"] for item in node_list).items())),
            "receipts": len(receipts),
            "invalid_receipts": invalid_receipts,
            "stale_nodes": len(stale),
        },
        "invalidation": {
            "changed_semantic_keys": changed[:80],
            "observed_changed_semantic_keys": observed_changed[:80],
            "added_semantic_keys": added[:80],
            "removed_semantic_keys": removed[:80],
            "stale_node_ids": sorted(stale)[:160],
            "strategy": "content_hash_dependency_closure",
        },
        "integrity": {
            "construction": "deterministic_runtime_projection",
            "model_inferred_edges": 0,
            "raw_tool_arguments_included": False,
            "raw_tool_results_included": False,
            "source_bodies_included": False,
            "content_addressed": True,
            "bounded": True,
        },
        "limitation": (
            "The graph proves recorded dependencies and execution observations; "
            "it does not prove that external-world claims are true."
        ),
    }


__all__ = ["SCHEMA", "build_causal_work_graph"]
