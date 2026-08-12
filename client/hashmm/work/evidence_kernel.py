"""One bounded Evidence Graph projection over HashMM's existing graph engines."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from hashmm.work.domain import EVIDENCE_SCHEMA


def _text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def project_evidence_graph(run: Mapping[str, Any]) -> dict[str, Any]:
    """Project retrieval, task and causal graphs into one product contract.

    The function deliberately does not infer new edges.  It only normalizes
    deterministic runtime records already attached to the owned WorkRun.
    """
    snapshot = _mapping(run.get("snapshot"))
    manifest = _mapping(snapshot.get("run_manifest"))
    causal = _mapping(manifest.get("causal_work_graph"))
    task = _mapping(manifest.get("evidence_graph"))
    retrieval = _mapping(
        manifest.get("retrieval")
        or manifest.get("evidence_snapshot")
        or snapshot.get("retrieval")
    )

    graph = causal if causal.get("nodes") else task
    nodes: list[dict[str, Any]] = []
    for index, value in enumerate(list(graph.get("nodes") or [])[:400]):
        if not isinstance(value, Mapping):
            continue
        node_id = _text(value.get("id") or f"node-{index + 1}", 180)
        kind = _text(value.get("kind") or value.get("type") or "fact", 48)
        nodes.append({
            "id": node_id,
            "kind": kind,
            "label": _text(value.get("label") or value.get("title") or kind, 300),
            "status": _text(value.get("status") or "unknown", 40),
            "trust": _text(value.get("trust") or "runtime_projection", 48),
            "revision": max(1, int(value.get("revision") or 1)),
        })

    edges: list[dict[str, str]] = []
    for value in list(graph.get("edges") or [])[:800]:
        if not isinstance(value, Mapping):
            continue
        source = _text(value.get("source") or value.get("from"), 180)
        target = _text(value.get("target") or value.get("to"), 180)
        relation = _text(value.get("relation") or value.get("kind"), 64)
        if source and target and relation:
            edges.append({"source": source, "target": target, "relation": relation})

    source_count = sum(
        1 for item in nodes if item["kind"] in {"source", "source_snapshot", "retrieval"}
    )
    claim_count = sum(1 for item in nodes if item["kind"] == "claim")
    stale_count = sum(1 for item in nodes if item["status"] in {"stale", "unavailable"})
    contradiction_count = sum(1 for item in edges if item["relation"] == "contradicts")
    graph_status = _text(graph.get("status"), 32)
    if graph_status not in {"ready", "stale", "invalid", "blocked", "unavailable"}:
        graph_status = "ready" if nodes else "unavailable"
    retrieval_summary = {
        "available": bool(retrieval),
        "mode": _text(retrieval.get("mode"), 32),
        "query_hash": _text(retrieval.get("query_hash"), 96),
        "corpus_revision": _text(
            retrieval.get("corpus_revision") or retrieval.get("snapshot_id"), 96,
        ),
    }
    identity = {
        "run_id": run.get("id"),
        "source_graph_id": graph.get("graph_id"),
        "nodes": [(item["id"], item["status"], item["revision"]) for item in nodes],
        "edges": [(item["source"], item["target"], item["relation"]) for item in edges],
        "retrieval": retrieval_summary,
    }
    return {
        "schema": EVIDENCE_SCHEMA,
        "graph_id": _hash(identity)[:32],
        "source_schema": _text(graph.get("schema"), 96),
        "status": graph_status,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "sources": source_count,
            "claims": claim_count,
            "stale": stale_count,
            "contradictions": contradiction_count,
        },
        "retrieval": retrieval_summary,
        "nodes": nodes,
        "edges": edges,
        "integrity": {
            "model_inferred_edges": bool(graph.get("model_inferred_edges")),
            "projection_only": True,
            "source_bodies_included": False,
        },
        "limitation": (
            "图表示可追溯的运行关系，不单独证明现实世界中的结论正确；"
            "无来源或冲突结论仍需要用户或验证器复核。"
        ),
    }
