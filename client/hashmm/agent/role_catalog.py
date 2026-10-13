"""Lazy, permission-neutral catalog for third-party Agent personalities."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


_CATALOG_ROOT = Path(__file__).with_name("role_catalog") / "agency-agents"
_INDEX_PATH = _CATALOG_ROOT / "index.json"
_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_MAX_PROMPT_CHARS = 24_000


@lru_cache(maxsize=1)
def agency_roles() -> tuple[dict, ...]:
    """Return public summaries only; no role Markdown is loaded here."""
    try:
        payload = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        rows = payload.get("roles") if isinstance(payload, dict) else []
    except (OSError, ValueError):
        return ()
    result: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        role_id = str(row.get("id") or "")
        relpath = str(row.get("path") or "")
        if not role_id.startswith("agency.") or not relpath:
            continue
        result.append({
            "id": role_id[:160],
            "name": str(row.get("name") or role_id)[:160],
            "skill": str(row.get("skill") or "")[:600],
            "hint": "",
            "category": str(row.get("category") or "other")[:80],
            "source": "agency-agents",
            "license": "MIT",
            "prompt_path": relpath,
            "sha256": str(row.get("sha256") or "")[:64],
        })
    return tuple(result)


@lru_cache(maxsize=32)
def load_role_prompt(role_id: str) -> str:
    """Load one selected prompt, fenced as role guidance rather than authority."""
    role = next((row for row in agency_roles() if row["id"] == role_id), None)
    if not role:
        return ""
    candidate = (_CATALOG_ROOT / str(role["prompt_path"])).resolve()
    try:
        candidate.relative_to(_CATALOG_ROOT.resolve())
        text = candidate.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    text = _FRONT_MATTER.sub("", text, count=1).strip()
    return text[:_MAX_PROMPT_CHARS]


def shortlist_roles(goal: str, *, limit: int = 32) -> list[dict]:
    """Bound auto-routing context while keeping every role manually available."""
    query_tokens = set(re.findall(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", (goal or "").lower()))
    scored: list[tuple[int, str, dict]] = []
    for role in agency_roles():
        haystack = f"{role['name']} {role['skill']} {role['category']}".lower()
        score = sum(3 if token in role["name"].lower() else 1 for token in query_tokens if token in haystack)
        scored.append((score, role["id"], role))
    scored.sort(key=lambda item: (-item[0], item[1]))
    positive = [row for score, _role_id, row in scored if score > 0]
    return positive[:limit]
