"""Preconditioned, atomic workspace edit transactions with rollback receipts."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping


TRANSACTION_SCHEMA = "hashmm.edit-transaction.v1"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(root: Path, relative: Any) -> Path:
    raw = str(relative or "").replace("\\", "/").lstrip("/")
    if not raw or raw in {".", ".."}:
        raise ValueError("edit path is empty")
    lexical = root / raw
    cursor = root
    # Check the lexical chain before resolve(); otherwise resolve() hides the
    # symlink which was used to reach the final target.
    for part in Path(raw).parts:
        if part in {"", "."}:
            continue
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symbolic-link edit target is not allowed")
    target = lexical.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("edit path escaped workspace") from exc
    return target


def apply_edit_transaction(
    root: str | Path,
    edits: Iterable[Mapping[str, Any]],
    *,
    transaction_id: str = "",
) -> dict[str, Any]:
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError("workspace root does not exist")
    txid = "".join(char for char in (transaction_id or f"tx_{uuid.uuid4().hex}")
                   if char.isalnum() or char in "_.-")[:120]
    prepared: list[dict[str, Any]] = []
    for raw in list(edits)[:64]:
        path = _resolve(base, raw.get("path"))
        before = path.read_bytes() if path.exists() else b""
        expected = str(raw.get("expected_hash") or "").lower()
        if expected and _hash(before) != expected:
            raise ValueError(f"edit precondition failed: {path.relative_to(base).as_posix()}")
        content = raw.get("content")
        if not isinstance(content, str):
            raise ValueError("edit content must be text")
        after = content.encode("utf-8")
        prepared.append({
            "path": path,
            "relative": path.relative_to(base).as_posix(),
            "before": before,
            "after": after,
        })
    if not prepared:
        raise ValueError("edit transaction is empty")

    journal = base / ".hashmm" / "transactions" / txid
    journal.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": TRANSACTION_SCHEMA,
        "transaction_id": txid,
        "state": "prepared",
        "created_at": time.time(),
        "files": [],
    }
    for index, item in enumerate(prepared):
        backup = journal / f"{index:03d}.before"
        backup.write_bytes(item["before"])
        manifest["files"].append({
            "path": item["relative"],
            "before_hash": _hash(item["before"]),
            "after_hash": _hash(item["after"]),
            "existed": item["path"].exists(),
            "backup": backup.name,
        })
    (journal / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    written: list[Path] = []
    try:
        for item in prepared:
            item["path"].parent.mkdir(parents=True, exist_ok=True)
            temp = item["path"].with_name(f".{item['path'].name}.{txid}.tmp")
            temp.write_bytes(item["after"])
            os.replace(temp, item["path"])
            written.append(item["path"])
    except Exception:
        for index, item in enumerate(prepared):
            if item["path"] not in written:
                continue
            if manifest["files"][index]["existed"]:
                os.replace(journal / manifest["files"][index]["backup"], item["path"])
            else:
                item["path"].unlink(missing_ok=True)
        raise
    manifest["state"] = "applied"
    manifest["applied_at"] = time.time()
    (journal / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest


def rollback_edit_transaction(root: str | Path, transaction_id: str) -> dict[str, Any]:
    base = Path(root).resolve()
    txid = "".join(char for char in str(transaction_id or "")
                   if char.isalnum() or char in "_.-")[:120]
    journal = base / ".hashmm" / "transactions" / txid
    manifest_path = journal / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("edit transaction was not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") != "applied":
        raise ValueError("edit transaction is not rollbackable")
    # Refuse to erase edits made after this transaction.
    for row in manifest.get("files") or []:
        target = _resolve(base, row.get("path"))
        current = target.read_bytes() if target.exists() else b""
        if _hash(current) != row.get("after_hash"):
            raise ValueError(f"rollback precondition failed: {row.get('path')}")
    for row in manifest.get("files") or []:
        target = _resolve(base, row.get("path"))
        if row.get("existed"):
            os.replace(journal / str(row.get("backup")), target)
        else:
            target.unlink(missing_ok=True)
    manifest["state"] = "rolled_back"
    manifest["rolled_back_at"] = time.time()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest
