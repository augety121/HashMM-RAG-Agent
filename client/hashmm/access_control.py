"""v17 Phase 30 — document-level access control (multi-tenant ACL).

OWASP LLM02/LLM08: in a multi-tenant RAG system, user A must not retrieve content
from documents only user B may see. Filtering must happen at RETRIEVAL time (before
the context is built), not just on the displayed sources — otherwise restricted
content still reaches the model.

This module provides the policy + a filter applied to retrieval results. It is
backward-compatible: a None / empty ACL means "allow all" (single-tenant default),
so existing behaviour is unchanged until you populate an ACL.
"""
from __future__ import annotations

import fnmatch
from typing import Iterable


class DocumentACL:
    """Maps a principal (user id or role) to the documents it may access.

    rules: {principal: ["*"]            # all docs
                       | ["a.pdf", ...] # exact filenames / doc ids
                       | ["小米*.pdf"]  # glob patterns}
    A principal absent from rules (and no '*' default) sees nothing.
    """

    def __init__(self, rules: dict | None = None, default: list | None = None):
        self.rules = rules or {}
        self.default = default  # patterns applied when principal has no explicit rule

    def allowed_patterns(self, principal: str | None) -> list:
        if principal and principal in self.rules:
            return self.rules[principal]
        return self.default if self.default is not None else []

    def can_access(self, principal: str | None, doc: str) -> bool:
        pats = self.allowed_patterns(principal)
        if pats == ["*"] or "*" in pats:
            return True
        d = doc or ""
        return any(fnmatch.fnmatch(d, p) or d == p for p in pats)


def _doc_of(item) -> str:
    """Extract a filename / doc id from a result object or dict."""
    if isinstance(item, dict):
        return item.get("filename") or item.get("doc_id") or item.get("id") or ""
    return getattr(item, "filename", None) or getattr(item, "doc_id", "") or ""


def filter_results(results: Iterable, acl: DocumentACL | None, principal: str | None):
    """Drop results the principal may not access. acl=None → allow all (no-op)."""
    if acl is None:
        return list(results)
    return [r for r in results if acl.can_access(principal, _doc_of(r))]


def filter_sources(sources: list[dict], acl: DocumentACL | None, principal: str | None) -> list[dict]:
    """ACL-filter the displayed/source dicts (same policy as filter_results)."""
    if acl is None:
        return sources
    return [s for s in sources if acl.can_access(principal, _doc_of(s))]


def load_default_acl(path: str = "data/acl.json") -> DocumentACL | None:
    """Load a multi-tenant ACL from JSON if present, else None (single-tenant, no-op).

    JSON shape: {"rules": {"alice": ["小米*.pdf"], "admin": ["*"]}, "default": []}
    """
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        return DocumentACL(rules=cfg.get("rules"), default=cfg.get("default"))
    except Exception:
        return None
