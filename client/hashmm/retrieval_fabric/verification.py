from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import EvidenceRecord

_TRACKING = {"fbclid", "gclid", "ref", "ref_src", "spm"}


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))
    except Exception:
        return ""


def evidence_id(url: str, title: str, snippet: str) -> str:
    material = canonical_url(url) or f"{title}\0{snippet[:300]}"
    return "ev_" + hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:24]


def _published_epoch(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.isdigit():
            raw = float(text)
            return raw / 1000 if raw > 10_000_000_000 else raw
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def verify_and_dedupe(records: list[EvidenceRecord], *, freshness_days: int | None = None) -> list[EvidenceRecord]:
    """Deterministically dedupe and annotate evidence.

    Verification means source corroboration and timestamp checks only. It is not
    a factual-truth verdict and callers must preserve that distinction.
    """
    merged: dict[str, EvidenceRecord] = {}
    for record in records:
        record.url = canonical_url(record.url)
        if record.url:
            record.domain = (urlsplit(record.url).hostname or "").lower()
        key = record.url or record.id
        if key in merged:
            current = merged[key]
            current.corroborating_providers = sorted(set(current.corroborating_providers + [record.provider]))
            if len(record.snippet) > len(current.snippet):
                current.snippet = record.snippet
            continue
        merged[key] = record

    now = time.time()
    values = list(merged.values())
    normalized_titles: dict[str, set[str]] = {}
    for item in values:
        terms = " ".join(re.findall(r"[\w\u4e00-\u9fff]+", item.title.lower()))[:160]
        if terms:
            normalized_titles.setdefault(terms, set()).add(item.provider)
    for item in values:
        published = _published_epoch(item.published_at)
        if published is not None:
            age_days = max(0, int((now - published) / 86400))
            item.freshness = "fresh" if freshness_days is None or age_days <= freshness_days else "stale"
            item.metadata["age_days"] = age_days
            if item.freshness == "stale":
                item.warnings.append("published_before_freshness_window")
        terms = " ".join(re.findall(r"[\w\u4e00-\u9fff]+", item.title.lower()))[:160]
        peers = normalized_titles.get(terms, set()) - {item.provider}
        item.corroborating_providers = sorted(set(item.corroborating_providers) | peers)
        if item.corroborating_providers:
            item.verification_status = "corroborated_listing"
        elif item.url and item.snippet:
            item.verification_status = "single_source"
        else:
            item.verification_status = "insufficient_metadata"
            item.warnings.append("missing_url_or_snippet")
    return values
