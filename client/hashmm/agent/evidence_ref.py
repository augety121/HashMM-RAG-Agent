"""Content-addressed browser evidence used by Chat, Canvas and WorkRuntime.

EvidenceRef is deliberately narrower than a browser transcript.  It records a
user/agent-observed quote, a bounded relocation anchor and hashes needed to
detect later page changes.  Remote pages remain untrusted data and none of
their content is interpreted as an instruction.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHEMA = "hashmm.evidence-ref.v1"
LINK_SCHEMA = "hashmm.canvas-evidence-link.v1"
_SECRET_QUERY_KEY = re.compile(
    r"(?:token|key|auth|session|password|passwd|secret|credential|code)", re.I,
)
_SAFE_SELECTOR = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9_-]*(?::nth-of-type\([1-9][0-9]{0,4}\))?"
    r"(?:\s*>\s*[a-zA-Z][a-zA-Z0-9_-]*(?::nth-of-type\([1-9][0-9]{0,4}\))?){0,15}$",
)
_HEX_HASH = re.compile(r"^[a-fA-F0-9]{32,128}$")


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _hash(value: Any, length: int = 64) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def sanitize_evidence_url(value: Any) -> str:
    """Return an http(s) URL without credentials, fragments or secret queries."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return ""
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    query = urlencode([
        (str(key)[:160], str(item)[:500])
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not _SECRET_QUERY_KEY.search(str(key))
    ])
    return urlunsplit((
        parsed.scheme.lower(), f"{host}{port}", parsed.path or "/", query, "",
    ))


def normalize_locator(value: Any, *, exact_fallback: str = "") -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    selector = _text(raw.get("selector"), 800)
    if selector and not _SAFE_SELECTOR.fullmatch(selector):
        selector = ""
    exact = _text(raw.get("exact") or exact_fallback, 2_000)
    prefix = _text(raw.get("prefix"), 240)
    suffix = _text(raw.get("suffix"), 240)
    rect_raw = raw.get("rect") if isinstance(raw.get("rect"), Mapping) else {}
    rect: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        try:
            rect[key] = round(max(-100_000.0, min(float(rect_raw.get(key) or 0), 100_000.0)), 2)
        except (TypeError, ValueError):
            rect[key] = 0.0
    fingerprint_raw = raw.get("fingerprint") if isinstance(raw.get("fingerprint"), Mapping) else {}
    tag = re.sub(r"[^a-z0-9_-]", "", _text(fingerprint_raw.get("tag"), 32).lower())
    role = re.sub(r"[^a-z0-9_-]", "", _text(fingerprint_raw.get("role"), 64).lower())
    aria_label = _text(
        fingerprint_raw.get("aria_label") or fingerprint_raw.get("ariaLabel"), 240,
    )
    ancestor_path = [
        re.sub(r"[^a-z0-9_-]", "", _text(item, 32).lower())
        for item in list(fingerprint_raw.get("ancestor_path") or [])[:16]
    ]
    ancestor_path = [item for item in ancestor_path if item]
    return {
        "selector": selector,
        "exact": exact,
        "prefix": prefix,
        "suffix": suffix,
        "rect": rect,
        "fingerprint": {
            "tag": tag,
            "role": role,
            "aria_label": aria_label,
            "ancestor_path": ancestor_path,
        },
    }


def evidence_content_hash(text: Any) -> str:
    normalized = _text(text, 4_000)
    return _hash({"text": normalized}, 64) if normalized else ""


def build_evidence_ref(
    *,
    conv_id: str,
    raw: Mapping[str, Any],
    captured_at: float | None = None,
) -> dict[str, Any]:
    url = sanitize_evidence_url(raw.get("url"))
    selected = _text(raw.get("selected_text") or raw.get("text_excerpt"), 4_000)
    if not url:
        raise ValueError("evidence URL must be a credential-free http(s) URL")
    if not selected:
        raise ValueError("evidence selection is empty")
    locator = normalize_locator(raw.get("locator"), exact_fallback=selected)
    content_hash = evidence_content_hash(selected)
    browser_session = _text(raw.get("browser_session_id"), 120)
    evidence_id = "ev_" + _hash({
        "conversation": _text(conv_id, 160),
        "url": url,
        "content_hash": content_hash,
        "selector": locator["selector"],
    }, 32)
    screenshot_hash = _text(raw.get("screenshot_hash"), 128)
    if screenshot_hash and not _HEX_HASH.fullmatch(screenshot_hash):
        screenshot_hash = ""
    observed = float(captured_at if captured_at is not None else time.time())
    return {
        "schema": SCHEMA,
        "evidence_id": evidence_id,
        "conversation_id": _text(conv_id, 160),
        "kind": "browser_selection",
        "url": url,
        "page_title": _text(raw.get("page_title") or raw.get("title"), 240),
        "text_excerpt": selected[:1_200],
        "content_hash": content_hash,
        "locator": locator,
        "screenshot_hash": screenshot_hash,
        "browser_session_id": browser_session,
        "captured_at": observed,
        "trust_level": "untrusted_web",
        "freshness": {
            "status": "current",
            "verified_at": observed,
            "observed_content_hash": content_hash,
            "reason": "captured_from_isolated_browser",
        },
        "integrity": {
            "algorithm": "sha256",
            "content_addressed": True,
            "remote_content_is_instruction": False,
            "raw_page_included": False,
        },
    }


def verify_evidence_ref(
    existing: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    verified_at: float | None = None,
) -> dict[str, Any]:
    """Compare a fresh isolated-browser observation with the captured quote."""
    current = dict(existing)
    when = float(verified_at if verified_at is not None else time.time())
    observed_url = sanitize_evidence_url(observation.get("url"))
    observed_text = _text(
        observation.get("selected_text") or observation.get("text_excerpt"), 4_000,
    )
    observed_hash = evidence_content_hash(observed_text)
    expected_hash = _text(current.get("content_hash"), 96)
    if not observed_url or observed_url != current.get("url"):
        status, reason = "unavailable", "url_mismatch"
    elif not observed_hash:
        status, reason = "unavailable", "locator_not_found"
    elif observed_hash == expected_hash:
        status, reason = "current", "content_hash_match"
    else:
        status, reason = "stale", "content_hash_changed"
    previous = current.get("freshness") if isinstance(current.get("freshness"), Mapping) else {}
    current["freshness"] = {
        "status": status,
        "verified_at": when,
        "observed_content_hash": observed_hash,
        "reason": reason,
        "verification_count": max(0, int(previous.get("verification_count") or 0)) + 1,
    }
    return current


def build_canvas_evidence_link(
    *,
    evidence_id: str,
    filename: str,
    block_id: str = "canvas-root",
    block_hash: str = "",
    label: str = "",
    linked_at: float | None = None,
) -> dict[str, Any]:
    eid = _text(evidence_id, 96)
    fname = _text(filename, 240)
    if not eid.startswith("ev_") or not fname:
        raise ValueError("evidence_id and filename are required")
    return {
        "schema": LINK_SCHEMA,
        "link_id": "cel_" + _hash([eid, fname, _text(block_id, 120)], 28),
        "evidence_id": eid,
        "filename": fname,
        "block_id": _text(block_id, 120) or "canvas-root",
        "block_hash": _text(block_hash, 96),
        "label": _text(label, 200),
        "linked_at": float(linked_at if linked_at is not None else time.time()),
        "relation": "supports",
    }


def public_evidence_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded cross-device/UI projection."""
    item = dict(value)
    return {
        key: item[key] for key in (
            "schema", "evidence_id", "conversation_id", "kind", "url",
            "page_title", "text_excerpt", "content_hash", "locator",
            "screenshot_hash", "browser_session_id", "captured_at",
            "trust_level", "freshness", "integrity",
        ) if key in item
    }


__all__ = [
    "SCHEMA", "LINK_SCHEMA", "build_canvas_evidence_link",
    "build_evidence_ref", "evidence_content_hash", "normalize_locator",
    "public_evidence_ref", "sanitize_evidence_url", "verify_evidence_ref",
]
