"""Copy-on-write workspaces for writing sub-agents.

Workers never write directly into a conversation's formal artifact directory.
They receive a server-created branch below ``.agent-branches``. Promotion is
an explicit integrator operation and is not exposed as a model tool.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

BRANCH_SCHEMA = "hashmm.agent-workspace-branch.v1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def _database():
    """Resolve the live database module.

    Some maintenance and test paths reload the database module after changing
    the data root. Holding the earlier module object would split branch
    provisioning from formal-artifact validation.
    """
    package = importlib.import_module("hashmm.api")
    database = getattr(package, "database", None)
    return database if database is not None else importlib.import_module("hashmm.api.database")


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def provision_worker_branch(
    *,
    owner_id: str,
    conversation_id: str,
    run_id: str,
    role: str,
    branch_id: str = "",
) -> dict[str, Any]:
    """Create an empty COW branch for a bounded writing worker."""
    owner = _text(owner_id)
    conversation = _text(conversation_id)
    if not owner or not conversation:
        raise ValueError("worker branch requires owner and conversation")
    safe_branch = _text(branch_id, 80) or f"awb_{uuid.uuid4().hex}"
    if not _SAFE_NAME.fullmatch(safe_branch):
        raise ValueError("invalid worker branch id")
    conversation_root = _database().conv_files_dir(conversation).resolve()
    root = (conversation_root / ".agent-branches" / safe_branch).resolve()
    if not _inside(root, conversation_root):
        raise ValueError("worker branch escaped conversation workspace")
    root.mkdir(parents=True, exist_ok=True)
    return {
        "schema": BRANCH_SCHEMA,
        "branch_id": safe_branch,
        "owner_id": owner,
        "conversation_id": conversation,
        "run_id": _text(run_id, 120),
        "role": _text(role, 40),
        "mode": "copy_on_write",
        "verified": True,
        "integrator_only_merge": True,
        "verifier_read_only": True,
        # Internal authority field. ``public_scope`` removes it.
        "root_path": str(root),
    }


def branch_manifest(branch: dict[str, Any]) -> dict[str, Any]:
    """Return content-addressed candidate files without absolute paths."""
    if not isinstance(branch, dict) or not branch.get("verified"):
        return {"schema": "hashmm.agent-branch-manifest.v1", "files": []}
    root = Path(_text(branch.get("root_path"), 2_048)).resolve()
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _inside(path, root):
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("."):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({
            "path": relative,
            "sha256": digest,
            "size_bytes": path.stat().st_size,
        })
        if len(files) >= 100:
            break
    return {
        "schema": "hashmm.agent-branch-manifest.v1",
        "branch_id": _text(branch.get("branch_id"), 80),
        "files": files,
        "formal_artifacts_modified": False,
    }


def merge_branch(
    branch: dict[str, Any],
    *,
    actor_role: str,
    expected_hashes: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Promote reviewed candidates; only an explicit integrator may call it."""
    if actor_role != "integrator":
        return {"ok": False, "error": "integrator_required"}
    if not isinstance(branch, dict) or not branch.get("verified"):
        return {"ok": False, "error": "invalid_branch"}
    root = Path(_text(branch.get("root_path"), 2_048)).resolve()
    target_root = root.parent.parent
    if (
        branch.get("schema") != BRANCH_SCHEMA
        or branch.get("mode") != "copy_on_write"
        or root.parent.name != ".agent-branches"
        or root.name != _text(branch.get("branch_id"), 80)
        or not _inside(root, target_root)
    ):
        return {"ok": False, "error": "invalid_branch"}
    expected = {
        _text(item.get("path"), 300): _text(item.get("sha256"), 64).lower()
        for item in list(expected_hashes or [])[:100]
        if isinstance(item, dict)
    }
    promoted: list[dict[str, Any]] = []
    for relative, digest in expected.items():
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return {"ok": False, "error": "invalid_candidate_path"}
        source = (root / relative).resolve()
        target = (target_root / relative).resolve()
        if (
            not source.is_file()
            or not _inside(source, root)
            or not _inside(target, target_root)
        ):
            return {"ok": False, "error": "candidate_missing"}
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != digest:
            return {"ok": False, "error": "candidate_hash_changed"}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        promoted.append({"path": relative, "sha256": actual})
    return {
        "ok": True,
        "branch_id": _text(branch.get("branch_id"), 80),
        "promoted": promoted,
        "integrator_only_merge": True,
    }
