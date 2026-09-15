"""Revisioned, idempotent Canvas operation log for cross-device editing."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_auth, require_conv_access

router = APIRouter(prefix="/api/canvas", tags=["canvas-ops"])
_lock = threading.RLock()
_ALLOWED = {"insert", "replace", "delete", "move", "format", "comment", "evidence_link", "accept_patch"}
_table_ready = False


def _ensure_table(conn) -> None:
    global _table_ready
    if _table_ready:
        return
    with _lock:
        if _table_ready:
            return
        conn.execute(
            "CREATE TABLE IF NOT EXISTS canvas_documents ("
            "conv_id TEXT NOT NULL,filename TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,"
            "state_json TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(conv_id,filename))"
        )
        _table_ready = True


def _safe_filename(value: str) -> str:
    name = str(value or "").strip()
    if not name or name != Path(name).name or name in {".", ".."} or "\x00" in name:
        raise HTTPException(400, "文件名无效")
    return name[:240]


def _path(conv_id: str, filename: str) -> Path:
    root = db.conv_files_dir(conv_id) / ".canvas-ops"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{filename}.json"


def _read(path: Path) -> dict:
    if not path.exists():
        return {"schema": "hashmm.canvas-ops.v1", "revision": 0, "ops": [], "snapshots": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"schema": "hashmm.canvas-ops.v1", "revision": 0, "ops": [], "snapshots": []}
    except Exception:
        raise HTTPException(503, "画布操作日志暂时不可读")


def _write(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _load_state(conn, conv_id: str, filename: str) -> tuple[dict, bool]:
    _ensure_table(conn)
    row = conn.execute(
        "SELECT revision,state_json FROM canvas_documents WHERE conv_id=? AND filename=?",
        (conv_id, filename),
    ).fetchone()
    if row:
        try:
            state = json.loads(row["state_json"] or "{}")
        except Exception as exc:
            raise HTTPException(503, "画布数据库状态不可读") from exc
        if not isinstance(state, dict):
            raise HTTPException(503, "画布数据库状态无效")
        state["revision"] = int(row["revision"] or 0)
        return state, True
    # One-way compatibility import: old local JSON remains untouched until a
    # successful database write makes the shared store authoritative.
    return _read(_path(conv_id, filename)), False


def _store_state(conn, conv_id: str, filename: str, state: dict, *, expected: int, existed: bool) -> bool:
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    revision = int(state.get("revision") or 0)
    now = time.time()
    if existed:
        result = conn.execute(
            "UPDATE canvas_documents SET revision=?,state_json=?,updated_at=? "
            "WHERE conv_id=? AND filename=? AND revision=?",
            (revision, encoded, now, conv_id, filename, expected),
        )
        return bool(result.rowcount)
    try:
        conn.execute(
            "INSERT INTO canvas_documents(conv_id,filename,revision,state_json,updated_at) VALUES(?,?,?,?,?)",
            (conv_id, filename, revision, encoded, now),
        )
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


@router.get("/ops")
async def canvas_ops(request: Request, conv_id: str, filename: str, after_revision: int = 0):
    require_auth(request)
    require_conv_access(request, conv_id)
    name = _safe_filename(filename)
    with db._conn() as conn:
        state, _ = _load_state(conn, conv_id, name)
    floor = max(0, int(after_revision or 0))
    return {
        "schema": "hashmm.canvas-ops.v1", "conv_id": conv_id, "filename": name,
        "revision": int(state.get("revision") or 0), "storage": "database",
        "ops": [op for op in (state.get("ops") or []) if int(op.get("revision") or 0) > floor],
        "snapshots": list(state.get("snapshots") or [])[-20:],
    }


@router.post("/ops")
async def append_canvas_op(request: Request):
    user = require_auth(request)
    body = await request.json()
    conv_id = str(body.get("conv_id") or "").strip()
    require_conv_access(request, conv_id)
    name = _safe_filename(str(body.get("filename") or ""))
    op_id = str(body.get("op_id") or body.get("client_mutation_id") or "").strip()[:160]
    kind = str(body.get("kind") or "").strip()
    if not op_id or kind not in _ALLOWED:
        raise HTTPException(400, "op_id 和受支持的 kind 为必填")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    # Operation payload is bounded and contains deltas/anchors only, not full
    # attachment bytes or credentials.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise HTTPException(413, "画布操作超过 64 KiB 上限")
    expected = int(body.get("expected_revision") or 0)
    with db._conn() as conn:
        state, existed = _load_state(conn, conv_id, name)
        for existing in state.get("ops") or []:
            if existing.get("op_id") == op_id:
                return {"ok": True, "duplicate": True, "revision": existing["revision"], "op": existing}
        current = int(state.get("revision") or 0)
        if expected != current:
            raise HTTPException(409, detail={"code": "canvas_revision_conflict", "expected": expected, "actual": current})
        revision = current + 1
        op = {"op_id": op_id, "revision": revision, "kind": kind, "payload": payload,
              "actor_id": str(user.get("uid") or ""), "created_at": time.time()}
        state.setdefault("ops", []).append(op)
        state["revision"] = revision
        # Bound the live log.  Older history is represented by a content-addressed
        # snapshot receipt written by the normal Canvas version chain.
        if len(state["ops"]) > 2000:
            state["ops"] = state["ops"][-1500:]
        if not _store_state(conn, conv_id, name, state, expected=current, existed=existed):
            latest, _ = _load_state(conn, conv_id, name)
            duplicate = next((item for item in latest.get("ops") or [] if item.get("op_id") == op_id), None)
            if duplicate:
                return {"ok": True, "duplicate": True, "revision": duplicate["revision"], "op": duplicate}
            raise HTTPException(409, detail={"code": "canvas_revision_conflict", "expected": expected,
                                             "actual": int(latest.get("revision") or 0)})
    return {"ok": True, "duplicate": False, "revision": revision, "op": op}


@router.post("/ops/snapshot")
async def record_canvas_snapshot(request: Request):
    user = require_auth(request)
    body = await request.json()
    conv_id = str(body.get("conv_id") or "").strip()
    require_conv_access(request, conv_id)
    name = _safe_filename(str(body.get("filename") or ""))
    content_hash = str(body.get("content_hash") or "").lower().strip()
    if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
        raise HTTPException(400, "content_hash 必须是 SHA-256")
    with db._conn() as conn:
        state, existed = _load_state(conn, conv_id, name)
        if int(body.get("revision") or -1) != int(state.get("revision") or 0):
            raise HTTPException(409, "快照版本已过期")
        receipt = {"snapshot_id": uuid.uuid4().hex, "revision": state["revision"],
                   "content_hash": content_hash, "actor_id": str(user.get("uid") or ""),
                   "created_at": time.time()}
        state.setdefault("snapshots", []).append(receipt)
        state["snapshots"] = state["snapshots"][-50:]
        current = int(state.get("revision") or 0)
        if not _store_state(conn, conv_id, name, state, expected=current, existed=existed):
            raise HTTPException(409, "快照版本已过期")
    return {"ok": True, "snapshot": receipt}
