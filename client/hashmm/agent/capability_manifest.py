"""User-facing capability manifests over the executable runtime inventory."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


MANIFEST_SCHEMA = "hashmm.capability-manifest.v1"


_SIDE_EFFECTS = {
    "rag": "read",
    "web_research": "network",
    "image_search": "read",
    "browser_use": "external",
    "computer_use": "execute",
    "artifacts": "write",
    "multiagent": "execute",
    "memory": "write",
    "canvas": "write",
    "long_tasks": "execute",
    "mcp": "unknown",
    "hooks": "external",
    "execution_sandbox": "execute",
    "graph_rag": "read",
}


def manifest_from_runtime(
    capability: Mapping[str, Any],
    *,
    dependencies: Iterable[str] = (),
) -> dict[str, Any]:
    cap_id = str(capability.get("id") or "")[:120]
    state = str(capability.get("state") or "unavailable")[:32]
    side_effect = _SIDE_EFFECTS.get(cap_id, "unknown")
    approval = "none" if side_effect in {"none", "read"} else "policy"
    return {
        "schema": MANIFEST_SCHEMA,
        "id": cap_id,
        "title": str(capability.get("title") or cap_id)[:160],
        "description": str(capability.get("description") or "")[:500],
        "availability": str(capability.get("availability") or state)[:32],
        "health": (
            "healthy" if capability.get("production_ready")
            else "degraded" if capability.get("wired")
            else "unavailable"
        ),
        "side_effect": side_effect,
        "approval": approval,
        "dependencies": sorted({str(item)[:120] for item in dependencies if str(item)}),
        "entrypoints": [str(item)[:160] for item in list(capability.get("entrypoints") or [])[:16]],
        "surfaces": dict(capability.get("surfaces") or {}),
        "progressive_disclosure": {
            "default_visible": bool(capability.get("production_ready")),
            "show_setup_when_unavailable": True,
            "show_technical_details": False,
        },
        "truth": {
            "wired": bool(capability.get("wired")),
            "enabled": bool(capability.get("enabled")),
            "reason": str(capability.get("reason") or "")[:400],
        },
    }


def attach_manifests(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    ids = {str(item.get("id") or "") for item in capabilities}
    graph: dict[str, list[str]] = {}
    for item in capabilities:
        cap_id = str(item.get("id") or "")
        dependencies: list[str] = []
        if cap_id in {"multiagent", "long_tasks"} and "execution_sandbox" in ids:
            dependencies.append("execution_sandbox")
        if cap_id == "graph_rag" and "rag" in ids:
            dependencies.append("rag")
        if cap_id in {"browser_use", "computer_use"} and "hooks" in ids:
            dependencies.append("hooks")
        item["manifest"] = manifest_from_runtime(item, dependencies=dependencies)
        graph[cap_id] = dependencies
    return {"schema": MANIFEST_SCHEMA, "dependencies": graph}
