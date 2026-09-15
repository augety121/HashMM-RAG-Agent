"""v17 Phase 30 — document-level access control (multi-tenant ACL).

OWASP LLM02/LLM08: in a multi-tenant RAG system, user A must not retrieve content
from documents only user B may see. Filtering must happen at RETRIEVAL time (before
the context is built), not just on the displayed sources — otherwise restricted
content still reaches the model.

This module provides the policy + a filter applied to retrieval results.  An
explicit ACL is authoritative.  When no ACL file exists, authenticated user
traffic is still owner-scoped; legacy rows without an owner fail closed.  Only
an explicitly privileged administrator path may request an unscoped view.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from typing import Iterable


class ACLConfigurationError(RuntimeError):
    """The configured document ACL exists but cannot be trusted or parsed."""


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


def filter_principal_documents(
    items: Iterable,
    *,
    principal: str,
    is_admin: bool = False,
    acl: DocumentACL | None = None,
):
    """Filter document-like rows for one authenticated principal.

    Administrators retain the global operational view.  For ordinary users an
    explicit ACL is authoritative; without one, only rows carrying the exact
    ``owner_id`` are visible.  Legacy rows with no owner therefore fail closed
    instead of becoming shared tenant data by accident.
    """
    rows = list(items)
    if is_admin:
        return rows
    if acl is not None:
        return [row for row in rows if acl.can_access(principal, _doc_of(row))]

    def _owner_of(row) -> str:
        if isinstance(row, dict):
            return str(row.get("owner_id") or "")
        return str(getattr(row, "owner_id", "") or "")

    owner = str(principal or "")
    return [row for row in rows if owner and _owner_of(row) == owner]


def allowed_source_ids(
    metadata: Iterable[dict],
    *,
    principal: str,
    is_admin: bool = False,
    acl: DocumentACL | None = None,
) -> set[str] | None:
    """Return graph source identifiers visible to a principal.

    ``None`` means the administrator may inspect the full graph.  A set (which
    may be empty) is a mandatory filter for ordinary users.
    """
    if is_admin:
        return None
    visible = filter_principal_documents(
        metadata, principal=principal, is_admin=False, acl=acl,
    )
    source_ids: set[str] = set()
    for row in visible:
        for key in ("chunk_id", "doc_id", "filename"):
            value = str(row.get(key) or "").strip()
            if value:
                source_ids.add(value)
    return source_ids


def load_default_acl(path: str = "data/acl.json") -> DocumentACL | None:
    """Load a multi-tenant ACL from JSON if present, else None (single-tenant, no-op).

    JSON shape: {"rules": {"alice": ["小米*.pdf"], "admin": ["*"]}, "default": []}
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("ACL root must be an object")
        rules = cfg.get("rules")
        default = cfg.get("default")
        if rules is not None and not isinstance(rules, dict):
            raise ValueError("ACL rules must be an object")
        if default is not None and not isinstance(default, list):
            raise ValueError("ACL default must be an array")
        for principal, patterns in (rules or {}).items():
            if not isinstance(principal, str) or not isinstance(patterns, list):
                raise ValueError("each ACL rule must map a principal to an array")
            if any(not isinstance(pattern, str) for pattern in patterns):
                raise ValueError("ACL patterns must be strings")
        if any(not isinstance(pattern, str) for pattern in (default or [])):
            raise ValueError("ACL default patterns must be strings")
        return DocumentACL(rules=rules, default=default)
    except ACLConfigurationError:
        raise
    except Exception as exc:
        # A present but malformed ACL must never silently become "allow all".
        # Callers catch this typed error and fail the retrieval closed.
        raise ACLConfigurationError(f"invalid document ACL: {type(exc).__name__}") from exc


def resolve_acl_scope(
    principal: str | None,
    path: str = "data/acl.json",
) -> tuple[DocumentACL | None, list[str] | None, str]:
    """Return the ACL, allowed patterns and a stable cache-scope fingerprint.

    ``allowed=None`` means that no ACL file is configured (legacy single-tenant
    mode).  ``allowed=[]`` is deliberately different: an ACL exists and this
    principal may retrieve no documents.
    """
    acl = load_default_acl() if path == "data/acl.json" else load_default_acl(path)
    allowed = None if acl is None else list(acl.allowed_patterns(principal))
    identity = json.dumps(
        {
            "principal": str(principal or ""),
            "allowed": sorted(str(item) for item in (allowed or [])),
            "scoped": allowed is not None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return acl, allowed, fingerprint


def normalize_document_scope(document_scope: Iterable[str] | None) -> list[str] | None:
    """Return the bounded, deterministic document selection from request state.

    ``None`` means that the user did not select a document scope.  An explicitly
    selected scope is server-owned request state; model-generated tool arguments
    may narrow it further but may never widen it.
    """
    values = list(dict.fromkeys(
        str(item).strip()[:260]
        for item in (document_scope or [])
        if str(item).strip()
    ))[:40]
    return values or None


def effective_document_scope(
    *,
    acl: DocumentACL | None,
    allowed: list[str] | None,
    principal: str | None,
    document_scope: Iterable[str] | None,
) -> list[str] | None:
    """Intersect the user's selection with the tenant ACL.

    A selected filename is treated as an exact document capability.  When a
    tenant ACL exists, only selected names admitted by that ACL survive.  With
    no explicit selection, the tenant ACL remains the effective allow-list.
    """
    selected = normalize_document_scope(document_scope)
    if selected is None:
        return None if allowed is None else list(allowed)
    if acl is None:
        return selected
    return [doc for doc in selected if acl.can_access(principal, doc)]


def retrieval_scope_fingerprint(
    *,
    principal: str | None,
    allowed: list[str] | None,
    document_scope: Iterable[str] | None,
) -> str:
    """Stable cache namespace for tenant ACL plus the selected document set."""
    selected = normalize_document_scope(document_scope)
    identity = json.dumps(
        {
            "principal": str(principal or ""),
            "allowed": None if allowed is None else sorted(str(x) for x in allowed),
            "documents": None if selected is None else sorted(selected),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def enhance_with_acl(chat_rag, query: str, messages: list[dict], *,
                     principal: str | None,
                     document_scope: Iterable[str] | None = None,
                     **kwargs):
    """Run ChatRetrieval with pre-context ACL filtering and defence in depth."""
    acl, allowed, _tenant_fingerprint = resolve_acl_scope(principal)
    selected = normalize_document_scope(document_scope)
    effective = effective_document_scope(
        acl=acl,
        allowed=allowed,
        principal=principal,
        document_scope=selected,
    )
    fingerprint = retrieval_scope_fingerprint(
        principal=principal,
        allowed=allowed,
        document_scope=selected,
    )
    call_kwargs = dict(kwargs)
    if effective is not None:
        call_kwargs["allowed_docs"] = effective
    if allowed is None:
        # No tenant ACL still requires exact owner isolation.  A selected
        # document set is an additional intersection, never a replacement.
        owner_id = str(principal or "")
        if owner_id:
            call_kwargs["owner_id"] = owner_id
        elif selected is not None:
            # Anonymous callers cannot prove ownership of a selected private
            # document.  Keep the allow-list for deterministic test/local
            # adapters but bind owner scope to the empty principal when the
            # adapter supports it, which fails closed in current retrieval.
            call_kwargs["owner_id"] = ""
    else:
        call_kwargs.pop("owner_id", None)
    if allowed is None and not str(principal or "") and selected is None:
        # Preserve compatibility for an explicitly unscoped local adapter, but
        # pass an empty owner to every current adapter so legacy rows fail closed.
        try:
            import inspect
            parameters = inspect.signature(chat_rag.enhance).parameters.values()
            accepts_owner = any(
                item.name == "owner_id"
                or item.kind is inspect.Parameter.VAR_KEYWORD
                for item in parameters
            )
        except (TypeError, ValueError):
            accepts_owner = True
        if accepts_owner:
            call_kwargs["owner_id"] = ""
    enhanced, sources, strategy = chat_rag.enhance(query, messages, **call_kwargs)
    # Keep a second filter here so a future retriever adapter cannot accidentally
    # weaken the contract by ignoring ``allowed_docs``.
    sources = filter_sources(list(sources or []), acl, principal)
    if selected is not None:
        selection_acl = DocumentACL(default=selected)
        sources = filter_sources(sources, selection_acl, principal="_selected")
    return enhanced, sources, strategy, fingerprint
