"""Claim-level grounding ledger for Chat/RAG answers.

This module turns a flat ``answer + sources`` pair into an auditable contract:
every detected factual claim keeps its exact character span, explicit citation
IDs, and the source metadata used to assess the link.  The assessment is a
deterministic citation/lexical proxy; it deliberately does **not** claim that a
linked source proves real-world truth.

It also normalizes numbered tool evidence.  Agent tools traditionally restart
their source list at ``[1]`` for every search, which makes citations ambiguous
after multiple searches.  ``renumber_numbered_evidence`` assigns turn-global
IDs before the result is shown to the model.
"""
from __future__ import annotations

import re
from typing import Iterable

from hashmm.evaluation import faithfulness as _fa


LEDGER_VERSION = 1
MAX_CLAIMS = 80
MAX_CLAIM_CHARS = 240

_NUMBERED_BLOCK = re.compile(r"(?m)^\[(\d{1,3})\][ \t]+")
_CITATION = re.compile(r"(?<![A-Za-z0-9_\]])\[(\d{1,3})\](?!\()")
_PACKED_HEAD = re.compile(
    r"^\[(\d{1,3})\]\s+\[(.*?)\s+p\.([^\]]+)\]\s*"
    r"(?:\(相关度:([^)]+)\))?\s*(.*)$",
    re.S,
)
_DEEP_HEAD = re.compile(r"^\[(\d{1,3})\]\s+\(([^)]+)\)\s*(.*)$", re.S)
_GENERIC_HEAD = re.compile(r"^\[(\d{1,3})\]\s+([^\n]*)(?:\n(.*))?$", re.S)
_MARKDOWN_PREFIX = re.compile(r"(?:#{1,6}\s+|[-*+]\s+|\d+[.、)]\s+|>\s*)")


def _source_text(source) -> str:
    if isinstance(source, str):
        return source.strip()
    if not isinstance(source, dict):
        return str(source or "").strip()
    return str(
        source.get("text")
        or source.get("content")
        or source.get("snippet")
        or source.get("body")
        or ""
    ).strip()


def _safe_int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_sources(sources: Iterable | None, *, start_id: int = 1) -> list[dict]:
    """Normalize heterogeneous retrieval records while preserving full text."""
    out: list[dict] = []
    for offset, raw in enumerate(sources or []):
        src = raw if isinstance(raw, dict) else {"text": str(raw or "")}
        citation_id = _safe_int(src.get("citation_id"), start_id + offset)
        if citation_id < 1:
            citation_id = start_id + offset
        filename = str(src.get("filename") or src.get("file") or src.get("source") or "")
        source_id = src.get("source_id") or src.get("id") or f"source-{citation_id}"
        chunk_id = src.get("chunk_id") or ""
        doc_id = src.get("doc_id") or ""
        out.append({
            "citation_id": citation_id,
            "source_id": str(source_id),
            "chunk_id": str(chunk_id),
            "doc_id": str(doc_id),
            "filename": filename,
            "page": _safe_int(src.get("page"), -1),
            "section": str(src.get("section") or src.get("loc") or ""),
            "score": _safe_float(src.get("score", src.get("_rrf", 0.0))),
            "method": str(src.get("method") or src.get("via") or ""),
            "modality": str(src.get("modality") or "text"),
            "text": _source_text(src),
        })
    return out


def public_sources(sources: Iterable | None, *, text_limit: int = 220) -> list[dict]:
    """Return the bounded source contract persisted and sent to the UI."""
    records = normalize_sources(sources)
    records.sort(key=lambda s: s["citation_id"])
    return [
        {
            "id": s["citation_id"],
            "rank": s["citation_id"],
            "source_id": s["source_id"],
            "chunk_id": s["chunk_id"],
            "doc_id": s["doc_id"],
            "filename": s["filename"],
            "page": s["page"],
            "section": s["section"],
            "score": s["score"],
            "method": s["method"],
            "modality": s["modality"],
            "text": s["text"][: max(0, text_limit)],
        }
        for s in records
    ]


def parse_numbered_sources(text: str) -> list[dict]:
    """Extract source records from ``[N] ...`` tool-output blocks."""
    value = str(text or "")
    marks = list(_NUMBERED_BLOCK.finditer(value))
    parsed: list[dict] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(value)
        block = value[mark.start():end].strip()
        citation_id = _safe_int(mark.group(1), len(parsed) + 1)
        filename, page, score, body = "", -1, 0.0, ""
        packed = _PACKED_HEAD.match(block)
        deep = _DEEP_HEAD.match(block) if packed is None else None
        generic = _GENERIC_HEAD.match(block) if packed is None and deep is None else None
        if packed:
            filename = packed.group(2).strip()
            page = _safe_int(packed.group(3), -1)
            score = _safe_float(packed.group(4), 0.0)
            body = packed.group(5).strip()
        elif deep:
            filename = deep.group(2).strip()
            body = deep.group(3).strip()
        elif generic:
            filename = generic.group(2).strip()
            body = (generic.group(3) or "").strip()
        if body:
            parsed.append({
                "citation_id": citation_id,
                "filename": filename,
                "page": page,
                "score": score,
                "text": body,
                "method": "agent_tool",
            })
    return normalize_sources(parsed)


def renumber_numbered_evidence(text: str, offset: int) -> tuple[str, list[dict]]:
    """Shift one tool result's local citations into a turn-global namespace."""
    parsed = parse_numbered_sources(text)
    if not parsed:
        return str(text or ""), []
    local_ids = {s["citation_id"] for s in parsed}
    shift = max(0, int(offset or 0))

    def repl(match: re.Match) -> str:
        number = _safe_int(match.group(1), -1)
        return f"[{number + shift}]" if number in local_ids else match.group(0)

    shifted_text = _CITATION.sub(repl, str(text or "")) if shift else str(text or "")
    for source in parsed:
        source["citation_id"] += shift
        source["source_id"] = f"agent-source-{source['citation_id']}"
    return shifted_text, parsed


def _masked_answer(answer: str) -> str:
    """Mask code while retaining string offsets and newlines."""
    chars = list(answer or "")
    for pattern in (re.compile(r"```.*?```", re.S), re.compile(r"`[^`\n]*`")):
        for match in pattern.finditer(answer or ""):
            for i in range(match.start(), match.end()):
                if chars[i] != "\n":
                    chars[i] = " "
    return "".join(chars)


_NON_SOURCE_CLAIM_PREFIXES = (
    "回到核心问题", "请你", "请告诉我", "给我一个方向", "我需要你",
    "你希望我", "你可以", "如果你愿意",
)
_RUNTIME_OBSERVATION_MARKERS = (
    "工具调用", "系统跳过", "本轮未获得", "上一轮", "当前任务",
    "当前会话", "运行时", "执行层面", "服务连接",
)


def _is_source_groundable_claim(text: str) -> bool:
    """Exclude interaction/runtime narration from external-source auditing.

    The ledger verifies claims against retrieved documents.  A question,
    request for clarification, or observation about HashMM's own execution is
    not a document-grounded factual claim and must not depress source coverage.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return False
    if value.endswith(("?", "？")):
        return False
    if value.startswith(_NON_SOURCE_CLAIM_PREFIXES):
        return False
    if any(marker in value for marker in _RUNTIME_OBSERVATION_MARKERS):
        return False
    return True


def iter_claim_spans(answer: str):
    """Yield exact ``(start, end, text)`` spans for detected factual claims."""
    raw = str(answer or "")
    masked = _masked_answer(raw)
    start = 0
    boundaries = re.finditer(r"[。！？；!?](?=\s|$|\n)|\.(?=\s|$|\n)|\n+", masked)
    for boundary in list(boundaries) + [None]:
        if boundary is None:
            end, next_start = len(raw), len(raw)
        elif boundary.group(0).startswith("\n"):
            end, next_start = boundary.start(), boundary.end()
        else:
            end, next_start = boundary.end(), boundary.end()
        left, right = start, end
        while left < right and raw[left].isspace():
            left += 1
        while right > left and raw[right - 1].isspace():
            right -= 1
        prefix = _MARKDOWN_PREFIX.match(raw[left:right])
        if prefix:
            left += prefix.end()
        text = raw[left:right].strip()
        if (
            text
            and len(text) <= 4000
            and _is_source_groundable_claim(text)
            and _fa.is_factual_claim(text)
        ):
            yield left, right, text
        start = next_start


def _evidence_ref(source: dict, overlap: float) -> dict:
    return {
        "source_index": source["citation_id"],
        "source_id": source["source_id"],
        "chunk_id": source["chunk_id"],
        "doc_id": source["doc_id"],
        "filename": source["filename"],
        "page": source["page"],
        "section": source["section"],
        "score": source["score"],
        "overlap": round(overlap, 3),
    }


def build_grounding_ledger(answer: str, sources: Iterable | None) -> dict:
    """Build a bounded, JSON-safe claim-to-evidence ledger.

    ``supported`` means an explicit valid citation plus deterministic evidence
    overlap (and strict hard-fact matching).  It is not a real-world truth or
    semantic-entailment guarantee; the limitation is included in the payload.
    """
    records = normalize_sources(sources)
    by_id = {s["citation_id"]: s for s in records}
    claims: list[dict] = []
    invalid_ids: set[int] = set()
    seen_claims: set[str] = set()
    counts = {"supported": 0, "inferred": 0, "unsupported": 0, "invalid_citation": 0}
    truncated = False

    for start, end, text in iter_claim_spans(answer):
        normalized_claim = " ".join(text.lower().split())
        if not normalized_claim or normalized_claim in seen_claims:
            continue
        if len(claims) >= MAX_CLAIMS:
            truncated = True
            break
        seen_claims.add(normalized_claim)
        claim_index = len(claims)
        cited = _fa.extract_citations(text)
        invalid = [number for number in cited if number not in by_id]
        valid = [number for number in cited if number in by_id]
        invalid_ids.update(invalid)
        evidence: list[dict] = []
        status = "inferred"
        reason = "factual_claim_without_explicit_citation"

        if invalid:
            status = "invalid_citation"
            reason = "citation_id_not_present_in_this_turn"
        elif valid:
            cited_sources = [by_id[number] for number in valid]
            cited_texts = [s["text"] for s in cited_sources if s["text"]]
            overlap = _fa.lexical_support(text, cited_texts)
            evidence = [_evidence_ref(source, _fa.lexical_support(text, [source["text"]]))
                        for source in cited_sources]
            report = _fa.audit_faithfulness(text, cited_sources, strict_numbers=True)
            if report.unsupported or report.uncited or not cited_texts:
                status = "unsupported"
                reason = "cited_evidence_failed_deterministic_support_check"
            else:
                status = "supported"
                reason = "explicit_citation_and_deterministic_support_proxy"
                if overlap <= 0:
                    status = "unsupported"
                    reason = "cited_evidence_has_no_lexical_overlap"

        counts[status] += 1
        claims.append({
            "id": f"claim-{claim_index + 1}",
            "claim_span": {"start": start, "end": end, "text": text[:MAX_CLAIM_CHARS]},
            "status": status,
            "citations": cited,
            "invalid_citations": invalid,
            "evidence": evidence,
            "reason": reason,
        })

    total = len(claims)
    supported = counts["supported"]
    ratio = round(supported / total, 4) if total else None
    if not records or not total:
        status = "not_evaluable"
    elif supported == total:
        status = "passed"
    else:
        status = "failed"
    return {
        "version": LEDGER_VERSION,
        "status": status,
        "validation_method": "explicit_citation_plus_deterministic_support_proxy",
        "semantic_entailment_verified": False,
        "disclaimer": "证据关联用于可追溯审计，不等同于事实真伪或语义蕴含证明。",
        "total_factual_claims": total,
        "supported_claims": supported,
        "inferred_claims": counts["inferred"],
        "unsupported_claims": counts["unsupported"],
        "invalid_citation_claims": counts["invalid_citation"],
        "invalid_citations": sorted(invalid_ids),
        "coverage_ratio": ratio,
        "review_required": bool(total and supported != total),
        "truncated": truncated,
        "claims": claims,
    }


__all__ = [
    "LEDGER_VERSION",
    "build_grounding_ledger",
    "iter_claim_spans",
    "normalize_sources",
    "parse_numbered_sources",
    "public_sources",
    "renumber_numbered_evidence",
]
