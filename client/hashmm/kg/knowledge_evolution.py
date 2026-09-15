"""Owner/project scoped knowledge--agent co-evolution ledger.

Successful search evidence enters quarantine first.  Promotion requires
provenance, explicit review and an evaluation gate, and creates an immutable
version.  This module never writes ``graph.json`` automatically.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from hashmm.api import database as db


class EvolutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def ensure_schema() -> None:
    with db._conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kg_evolution_candidates (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                head TEXT NOT NULL,
                relation TEXT NOT NULL,
                tail TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_json TEXT NOT NULL,
                source_run_id TEXT NOT NULL DEFAULT '',
                extractor TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                conflict_json TEXT NOT NULL DEFAULT '[]',
                review_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(owner_id, project_id, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_candidates_owner_project_status
                ON kg_evolution_candidates(owner_id, project_id, status, created_at DESC);

            CREATE TABLE IF NOT EXISTS kg_versions (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                parent_version_id TEXT NOT NULL DEFAULT '',
                generation INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                evaluation_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(owner_id, project_id, generation),
                UNIQUE(owner_id, project_id, content_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_versions_owner_project_generation
                ON kg_versions(owner_id, project_id, generation DESC);

            CREATE TABLE IF NOT EXISTS kg_active_versions (
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                activated_by TEXT NOT NULL,
                activated_at REAL NOT NULL,
                PRIMARY KEY(owner_id, project_id),
                FOREIGN KEY(version_id) REFERENCES kg_versions(id)
            );
            """
        )


def _candidate(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "owner_id": str(row["owner_id"]),
        "project_id": str(row["project_id"]), "head": str(row["head"]),
        "relation": str(row["relation"]), "tail": str(row["tail"]),
        "confidence": float(row["confidence"]), "evidence": _load(row["evidence_json"], []),
        "source_run_id": str(row["source_run_id"] or ""), "extractor": str(row["extractor"] or ""),
        "status": str(row["status"]), "conflicts": _load(row["conflict_json"], []),
        "review": _load(row["review_json"], {}), "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
    }


def _safe_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = []
    for item in evidence[:40]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("filename") or "").strip()[:500]
        sha256 = str(item.get("sha256") or item.get("content_hash") or "").strip()[:64]
        if not source or not sha256:
            continue
        safe.append({
            "source": source, "sha256": sha256,
            "page": max(0, int(item.get("page") or 0)),
            "chunk_id": str(item.get("chunk_id") or "")[:160],
            "quote_hash": str(item.get("quote_hash") or "")[:64],
            "retrieved_at": float(item.get("retrieved_at") or time.time()),
        })
    return safe


def propose(*, owner_id: str, project_id: str, head: str, relation: str, tail: str,
            confidence: float, evidence: list[dict[str, Any]], source_run_id: str = "",
            extractor: str = "", threshold: float = 0.75) -> dict[str, Any]:
    ensure_schema()
    owner, project = str(owner_id or "").strip(), str(project_id or "").strip()
    triple = tuple(str(value or "").strip()[:500] for value in (head, relation, tail))
    if not owner or not project or not all(triple):
        raise EvolutionError("invalid_candidate", "owner, project and complete triple are required")
    if float(confidence) < max(0.0, min(float(threshold), 1.0)):
        raise EvolutionError("low_confidence", "candidate confidence is below the quarantine threshold")
    anchors = _safe_evidence(evidence)
    if not anchors:
        raise EvolutionError("evidence_required", "at least one source and SHA-256 evidence anchor is required")
    head, relation, tail = triple
    fingerprint = hashlib.sha256(_json([head, relation, tail]).encode("utf-8")).hexdigest()
    now = time.time()
    with db._conn() as conn:
        duplicate = conn.execute(
            "SELECT * FROM kg_evolution_candidates WHERE owner_id=? AND project_id=? AND fingerprint=?",
            (owner, project, fingerprint),
        ).fetchone()
        if duplicate is not None:
            return _candidate(duplicate)
        active = conn.execute(
            "SELECT v.manifest_json FROM kg_active_versions a JOIN kg_versions v ON v.id=a.version_id "
            "WHERE a.owner_id=? AND a.project_id=?", (owner, project)
        ).fetchone()
        facts = _load(active["manifest_json"], {}).get("facts", []) if active is not None else []
        conflicts = [
            {"head": item.get("head"), "relation": item.get("relation"), "tail": item.get("tail"),
             "candidate_id": item.get("candidate_id")}
            for item in facts if isinstance(item, dict) and item.get("head") == head
            and item.get("relation") == relation and item.get("tail") != tail
        ]
        candidate_id = "kgc_" + uuid.uuid4().hex
        status = "conflict" if conflicts else "pending"
        conn.execute(
            "INSERT INTO kg_evolution_candidates"
            "(id,owner_id,project_id,head,relation,tail,fingerprint,confidence,evidence_json,"
            "source_run_id,extractor,status,conflict_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, owner, project, head, relation, tail, fingerprint, float(confidence),
             _json(anchors), str(source_run_id or "")[:160], str(extractor or "")[:120],
             status, _json(conflicts), now, now),
        )
        return _candidate(conn.execute("SELECT * FROM kg_evolution_candidates WHERE id=?", (candidate_id,)).fetchone())


def list_candidates(owner_id: str, project_id: str, *, status: str = "") -> list[dict[str, Any]]:
    ensure_schema()
    with db._conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM kg_evolution_candidates WHERE owner_id=? AND project_id=? AND status=? "
                "ORDER BY created_at DESC", (owner_id, project_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kg_evolution_candidates WHERE owner_id=? AND project_id=? "
                "ORDER BY created_at DESC", (owner_id, project_id)
            ).fetchall()
    return [_candidate(row) for row in rows]


def review_candidate(owner_id: str, project_id: str, candidate_id: str, *, actor_id: str,
                     decision: str, reason: str = "", resolve_conflict: bool = False) -> dict[str, Any] | None:
    ensure_schema()
    decision = str(decision or "").lower()
    if decision not in {"approve", "reject"}:
        raise EvolutionError("invalid_decision", "decision must be approve or reject")
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM kg_evolution_candidates WHERE id=? AND owner_id=? AND project_id=?",
            (candidate_id, owner_id, project_id),
        ).fetchone()
        if row is None:
            return None
        current = _candidate(row)
        if current["status"] in {"approved", "rejected", "promoted"}:
            return current
        if current["status"] == "conflict" and decision == "approve" and not resolve_conflict:
            raise EvolutionError("conflict_resolution_required", "conflicting knowledge needs explicit resolution")
        status = "approved" if decision == "approve" else "rejected"
        review = {"actor_id": actor_id, "decision": decision, "reason": str(reason or "")[:1000],
                  "resolved_conflict": bool(resolve_conflict), "reviewed_at": time.time()}
        conn.execute(
            "UPDATE kg_evolution_candidates SET status=?,review_json=?,updated_at=? "
            "WHERE id=? AND owner_id=? AND project_id=?",
            (status, _json(review), time.time(), candidate_id, owner_id, project_id),
        )
        return _candidate(conn.execute("SELECT * FROM kg_evolution_candidates WHERE id=?", (candidate_id,)).fetchone())


def active_version(owner_id: str, project_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT v.* FROM kg_active_versions a JOIN kg_versions v ON v.id=a.version_id "
            "WHERE a.owner_id=? AND a.project_id=?", (owner_id, project_id)
        ).fetchone()
    if row is None:
        return None
    return {"id": str(row["id"]), "parent_version_id": str(row["parent_version_id"] or ""),
            "generation": int(row["generation"]), "content_hash": str(row["content_hash"]),
            "manifest": _load(row["manifest_json"], {}),
            "evaluation": _load(row["evaluation_json"], {}), "created_at": float(row["created_at"])}


def promote(owner_id: str, project_id: str, candidate_ids: list[str], *, actor_id: str,
            evaluation: dict[str, Any], expected_parent_version: str = "") -> dict[str, Any]:
    """Create and activate one immutable version after all gates pass."""
    ensure_schema()
    if not candidate_ids:
        raise EvolutionError("candidate_required", "at least one candidate is required")
    if not isinstance(evaluation, dict) or evaluation.get("passed") is not True:
        raise EvolutionError("evaluation_required", "promotion evaluation must explicitly pass")
    current = active_version(owner_id, project_id)
    current_id = str((current or {}).get("id") or "")
    if expected_parent_version != current_id:
        raise EvolutionError("version_conflict", "active knowledge version changed")
    with db._conn() as conn:
        marks = ",".join("?" for _ in candidate_ids)
        rows = conn.execute(
            f"SELECT * FROM kg_evolution_candidates WHERE owner_id=? AND project_id=? "
            f"AND id IN ({marks})", (owner_id, project_id, *candidate_ids)
        ).fetchall()
        if len(rows) != len(set(candidate_ids)):
            raise EvolutionError("candidate_not_found", "candidate not found")
        candidates = [_candidate(row) for row in rows]
        if any(item["status"] != "approved" for item in candidates):
            raise EvolutionError("approval_required", "all candidates must be explicitly approved")
        facts = list(((current or {}).get("manifest") or {}).get("facts") or [])
        by_key = {(item.get("head"), item.get("relation"), item.get("tail")): item
                  for item in facts if isinstance(item, dict)}
        for item in candidates:
            by_key[(item["head"], item["relation"], item["tail"])] = {
                "candidate_id": item["id"], "head": item["head"], "relation": item["relation"],
                "tail": item["tail"], "confidence": item["confidence"],
                "evidence": item["evidence"], "source_run_id": item["source_run_id"],
            }
        manifest = {"schema": "hashmm.kg-version.v1", "owner_id": owner_id,
                    "project_id": project_id, "facts": sorted(by_key.values(), key=lambda x: _json(x))}
        content_hash = hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
        generation = int((current or {}).get("generation") or 0) + 1
        version_id = "kgv_" + uuid.uuid4().hex
        now = time.time()
        conn.execute(
            "INSERT INTO kg_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (version_id, owner_id, project_id, current_id, generation, content_hash,
             _json(manifest), _json(evaluation), actor_id, now),
        )
        conn.execute(
            "INSERT INTO kg_active_versions(owner_id,project_id,version_id,activated_by,activated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(owner_id,project_id) DO UPDATE SET "
            "version_id=excluded.version_id,activated_by=excluded.activated_by,activated_at=excluded.activated_at",
            (owner_id, project_id, version_id, actor_id, now),
        )
        conn.execute(
            f"UPDATE kg_evolution_candidates SET status='promoted',updated_at=? "
            f"WHERE owner_id=? AND project_id=? AND id IN ({marks})",
            (now, owner_id, project_id, *candidate_ids),
        )
    return active_version(owner_id, project_id) or {}


def rollback(owner_id: str, project_id: str, version_id: str, *, actor_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM kg_versions WHERE id=? AND owner_id=? AND project_id=?",
            (version_id, owner_id, project_id),
        ).fetchone()
        if row is None:
            return None
        now = time.time()
        conn.execute(
            "INSERT INTO kg_active_versions(owner_id,project_id,version_id,activated_by,activated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(owner_id,project_id) DO UPDATE SET "
            "version_id=excluded.version_id,activated_by=excluded.activated_by,activated_at=excluded.activated_at",
            (owner_id, project_id, version_id, actor_id, now),
        )
    return active_version(owner_id, project_id)


__all__ = ["EvolutionError", "active_version", "ensure_schema", "list_candidates", "promote",
           "propose", "review_candidate", "rollback"]
