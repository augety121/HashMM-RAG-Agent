"""Browser EvidenceRef and Canvas linkage endpoints.

The browser observes an untrusted page inside Electron and sends only a bounded
quote plus a relocation anchor.  The server owns hashes, freshness state,
conversation authorization and the Canvas/WorkRuntime projection.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from hashmm.agent.causal_work_graph import build_causal_work_graph
from hashmm.agent.evidence_ref import (
    build_canvas_evidence_link,
    build_evidence_ref,
    public_evidence_ref,
    verify_evidence_ref,
)
from hashmm.agent import work_runtime
from hashmm.api import database as db
from hashmm.api.auth import require_conv_access
from hashmm.utils import get_logger, log_suppressed


router = APIRouter(tags=["canvas-evidence"])
logger = get_logger("hashmm.canvas_evidence")
_LOCK = threading.RLock()
_STORE_SCHEMA = "hashmm.evidence-fabric.v1"
_REF_CAP = 500
_LINK_CAP = 1_000


def _safe_filename(value: Any) -> str:
    name = os.path.basename(unquote(str(value or "")).split("?")[0].split("#")[0])
    if not name or name in {".", ".."} or len(name) > 240:
        return ""
    return name


def _store_path(conv_id: str) -> Path:
    directory = db.conv_files_dir(conv_id) / ".evidence"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "evidence-fabric.json"


def _empty_store() -> dict[str, Any]:
    return {"schema": _STORE_SCHEMA, "revision": 0, "refs": {}, "links": []}


def _load_store(conv_id: str) -> dict[str, Any]:
    path = _store_path(conv_id)
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("schema") == _STORE_SCHEMA:
                refs = value.get("refs") if isinstance(value.get("refs"), dict) else {}
                links = value.get("links") if isinstance(value.get("links"), list) else []
                return {
                    "schema": _STORE_SCHEMA,
                    "revision": max(0, int(value.get("revision") or 0)),
                    "refs": dict(list(refs.items())[-_REF_CAP:]),
                    "links": [item for item in links[-_LINK_CAP:] if isinstance(item, dict)],
                }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        log_suppressed(logger, exc)
    return _empty_store()


def _save_store(conv_id: str, store: dict[str, Any]) -> None:
    path = _store_path(conv_id)
    store["schema"] = _STORE_SCHEMA
    store["revision"] = max(0, int(store.get("revision") or 0)) + 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _links_for(store: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    return [
        dict(item) for item in list(store.get("links") or [])
        if isinstance(item, dict) and item.get("filename") == filename
    ]


def _linked_projection(store: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    refs = store.get("refs") if isinstance(store.get("refs"), dict) else {}
    result: list[dict[str, Any]] = []
    for link in _links_for(store, filename):
        evidence = refs.get(str(link.get("evidence_id") or ""))
        if not isinstance(evidence, dict):
            continue
        result.append({
            **dict(link),
            "evidence": public_evidence_ref(evidence),
        })
    return result


def canvas_evidence_summary(conv_id: str, filename: str) -> dict[str, Any]:
    """Public helper used by the publishing gate and tests."""
    fname = _safe_filename(filename)
    if not fname:
        return {"linked": 0, "current": 0, "stale": 0, "unavailable": 0, "ready": True}
    with _LOCK:
        rows = _linked_projection(_load_store(conv_id), fname)
    counts = {"current": 0, "stale": 0, "unavailable": 0}
    for row in rows:
        status = str(((row.get("evidence") or {}).get("freshness") or {}).get("status") or "unavailable")
        counts[status if status in counts else "unavailable"] += 1
    return {
        "linked": len(rows),
        **counts,
        "ready": counts["stale"] == 0 and counts["unavailable"] == 0,
    }


def _canvas_hash(conv_id: str, filename: str) -> str:
    path = db.conv_files_dir(conv_id) / filename
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _project_work_runtime(
    *,
    conv: dict[str, Any],
    conv_id: str,
    filename: str,
    store: dict[str, Any],
    event_type: str,
    summary: str,
) -> None:
    """Make Browser–Canvas evidence visible through the same durable work feed."""
    try:
        owner = str(conv.get("user_id") or "anonymous")
        rows = _linked_projection(store, filename)
        evidence_nodes = []
        evidence_edges = []
        snapshots = []
        artifact_node = f"artifact:{filename}"
        for row in rows:
            evidence = row.get("evidence") or {}
            evidence_id = str(evidence.get("evidence_id") or "")
            freshness = evidence.get("freshness") or {}
            status = str(freshness.get("status") or "unavailable")
            node_id = f"source:{evidence_id}"
            evidence_nodes.append({
                "id": node_id,
                "kind": "source",
                "label": str(evidence.get("page_title") or evidence.get("url") or "网页证据")[:240],
                "status": status,
                "meta": {
                    "source_id": evidence_id,
                    "trust": "untrusted_web",
                    "freshness": status,
                },
            })
            evidence_edges.append({
                "source": node_id, "target": artifact_node, "relation": "supports",
            })
            snapshots.append({
                "evidence_id": evidence_id,
                "source_id": evidence_id,
                "filename": evidence.get("page_title") or "网页证据",
                "content_hash": freshness.get("observed_content_hash") or evidence.get("content_hash"),
                "revision": int(float(freshness.get("verified_at") or evidence.get("captured_at") or 0)),
                "method": "browser_selection",
                "freshness": status,
                "artifact_ref": filename,
            })
        artifact_hash = _canvas_hash(conv_id, filename)
        evidence_nodes.append({
            "id": artifact_node,
            "kind": "artifact",
            "label": filename,
            "status": "ready",
            "meta": {"content_hash": artifact_hash},
        })
        base_graph = {
            "schema": "hashmm.task-evidence-graph.v1",
            "run_id": f"canvas-evidence:{conv_id}:{filename}",
            "root_node_id": artifact_node,
            "nodes": evidence_nodes,
            "edges": evidence_edges,
        }
        source_id = f"canvas-evidence:{conv_id}:{filename}"
        run = work_runtime.create_run(
            user_id=owner,
            kind="artifact",
            source_id=source_id,
            conv_id=conv_id,
            title=f"证据画布 · {filename}",
            status="observed",
            snapshot={"evidence_fabric": canvas_evidence_summary(conv_id, filename)},
        )
        previous = (run.get("snapshot") or {}).get("causal_work_graph")
        graph = build_causal_work_graph(
            run_id=run["id"],
            evidence_graph=base_graph,
            source_snapshots=snapshots,
            previous_graph=previous if isinstance(previous, dict) else None,
            observed_at=time.time(),
        )
        updated_run = work_runtime.append_event(
            run["id"],
            user_id=owner,
            event_type=event_type,
            status="observed",
            summary=summary,
            payload={
                "filename": filename,
                "linked": len(rows),
                "stale": int(graph.get("summary", {}).get("stale_nodes") or 0),
            },
            snapshot_updates={
                "evidence_fabric": canvas_evidence_summary(conv_id, filename),
                "causal_work_graph": graph,
                "latest_artifact": {"filename": filename, "type": "html"},
            },
        )
        if artifact_hash and updated_run:
            path = db.conv_files_dir(conv_id) / filename
            work_runtime.register_artifact_revision(
                run["id"],
                user_id=owner,
                artifact_id=(
                    "canvas:"
                    + hashlib.sha256(
                        f"{conv_id}:{filename}".encode("utf-8")
                    ).hexdigest()[:24]
                ),
                content_hash=artifact_hash,
                media_type="text/html",
                size_bytes=path.stat().st_size if path.exists() else 0,
                locator={
                    "filename": filename,
                    "download_url": (
                        f"/api/conversations/{conv_id}/download/{filename}"
                    ),
                },
                verification="ready",
                expected_run_revision=int(updated_run.get("revision") or 0),
            )
    except Exception as exc:  # evidence persistence must not be rolled back by projection failure
        log_suppressed(logger, exc)


@router.post("/api/conversations/{conv_id}/evidence-refs")
async def capture_evidence_ref(conv_id: str, request: Request):
    conv = require_conv_access(request, conv_id)
    try:
        body = await request.json()
        evidence = build_evidence_ref(conv_id=conv_id, raw=body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with _LOCK:
        store = _load_store(conv_id)
        existing = store["refs"].get(evidence["evidence_id"])
        if isinstance(existing, dict):
            return {"ok": True, "dedup": True, "evidence": public_evidence_ref(existing)}
        store["refs"][evidence["evidence_id"]] = evidence
        if len(store["refs"]) > _REF_CAP:
            oldest = min(
                store["refs"],
                key=lambda key: float((store["refs"].get(key) or {}).get("captured_at") or 0),
            )
            store["refs"].pop(oldest, None)
            store["links"] = [
                item for item in store["links"] if item.get("evidence_id") != oldest
            ]
        _save_store(conv_id, store)
    return {"ok": True, "dedup": False, "evidence": public_evidence_ref(evidence)}


@router.get("/api/conversations/{conv_id}/evidence-refs")
async def list_evidence_refs(conv_id: str, request: Request):
    require_conv_access(request, conv_id)
    with _LOCK:
        store = _load_store(conv_id)
        items = [
            public_evidence_ref(item) for item in store["refs"].values()
            if isinstance(item, dict)
        ]
    items.sort(key=lambda item: float(item.get("captured_at") or 0), reverse=True)
    return {"schema": _STORE_SCHEMA, "revision": store["revision"], "items": items}


@router.post("/api/conversations/{conv_id}/evidence-refs/{evidence_id}/verify")
async def verify_evidence(conv_id: str, evidence_id: str, request: Request):
    conv = require_conv_access(request, conv_id)
    body = await request.json()
    affected: list[str] = []
    with _LOCK:
        store = _load_store(conv_id)
        existing = store["refs"].get(evidence_id)
        if not isinstance(existing, dict):
            raise HTTPException(status_code=404, detail="证据引用不存在")
        verified = verify_evidence_ref(existing, body or {})
        store["refs"][evidence_id] = verified
        affected = sorted({
            str(item.get("filename") or "") for item in store["links"]
            if item.get("evidence_id") == evidence_id and item.get("filename")
        })
        _save_store(conv_id, store)
    for filename in affected:
        _project_work_runtime(
            conv=conv, conv_id=conv_id, filename=filename, store=store,
            event_type="evidence_verified",
            summary="网页证据已重新验证" if verified["freshness"]["status"] == "current"
            else "网页证据发生变化，相关画布结论需要复核",
        )
    return {"ok": True, "evidence": public_evidence_ref(verified), "affected_artifacts": affected}


@router.get("/api/conversations/{conv_id}/files/{filename}/evidence-links")
async def list_canvas_evidence_links(conv_id: str, filename: str, request: Request):
    require_conv_access(request, conv_id)
    fname = _safe_filename(filename)
    if not fname:
        raise HTTPException(status_code=400, detail="文件名无效")
    with _LOCK:
        store = _load_store(conv_id)
        items = _linked_projection(store, fname)
    return {
        "schema": _STORE_SCHEMA,
        "revision": store["revision"],
        "summary": canvas_evidence_summary(conv_id, fname),
        "items": items,
    }


@router.post("/api/conversations/{conv_id}/files/{filename}/evidence-links")
async def link_canvas_evidence(conv_id: str, filename: str, request: Request):
    conv = require_conv_access(request, conv_id)
    fname = _safe_filename(filename)
    if not fname or Path(fname).suffix.lower() not in {".html", ".htm"}:
        raise HTTPException(status_code=415, detail="证据只能关联到 HTML 工作画布")
    target = db.conv_files_dir(conv_id) / fname
    if not target.is_file():
        raise HTTPException(status_code=404, detail="画布文件不存在")
    body = await request.json()
    evidence_id = str((body or {}).get("evidence_id") or "").strip()
    with _LOCK:
        store = _load_store(conv_id)
        evidence = store["refs"].get(evidence_id)
        if not isinstance(evidence, dict):
            raise HTTPException(status_code=404, detail="证据引用不存在")
        try:
            link = build_canvas_evidence_link(
                evidence_id=evidence_id,
                filename=fname,
                block_id=(body or {}).get("block_id") or "canvas-root",
                block_hash=(body or {}).get("block_hash") or _canvas_hash(conv_id, fname),
                label=(body or {}).get("label") or evidence.get("page_title") or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        dedup = any(item.get("link_id") == link["link_id"] for item in store["links"])
        if not dedup:
            store["links"].append(link)
            store["links"] = store["links"][-_LINK_CAP:]
            _save_store(conv_id, store)
    _project_work_runtime(
        conv=conv, conv_id=conv_id, filename=fname, store=store,
        event_type="evidence_linked", summary="网页证据已关联到工作画布",
    )
    return {
        "ok": True, "dedup": dedup, "link": link,
        "evidence": public_evidence_ref(evidence),
        "summary": canvas_evidence_summary(conv_id, fname),
    }


@router.delete("/api/conversations/{conv_id}/files/{filename}/evidence-links/{link_id}")
async def unlink_canvas_evidence(conv_id: str, filename: str, link_id: str, request: Request):
    conv = require_conv_access(request, conv_id)
    fname = _safe_filename(filename)
    with _LOCK:
        store = _load_store(conv_id)
        before = len(store["links"])
        store["links"] = [
            item for item in store["links"]
            if not (item.get("filename") == fname and item.get("link_id") == link_id)
        ]
        changed = len(store["links"]) != before
        if changed:
            _save_store(conv_id, store)
    if changed:
        _project_work_runtime(
            conv=conv, conv_id=conv_id, filename=fname, store=store,
            event_type="evidence_unlinked", summary="网页证据已从工作画布移除",
        )
    return {"ok": True, "removed": changed, "summary": canvas_evidence_summary(conv_id, fname)}


__all__ = [
    "canvas_evidence_summary", "capture_evidence_ref", "link_canvas_evidence",
    "list_canvas_evidence_links", "list_evidence_refs", "router",
    "unlink_canvas_evidence", "verify_evidence",
]
