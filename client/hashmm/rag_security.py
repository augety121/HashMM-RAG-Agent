"""v17 Phase 29 — RAG security hardening (OWASP LLM Top 10 2025 aligned).

Direct prompt injection (the user's message) is handled in prompt_safety. But the
biggest RAG-specific attack surface is **indirect prompt injection (LLM01)**:
malicious instructions embedded in the *retrieved documents* ("AI assistant: ignore
your safety guidelines and …"), which the model may follow because it can't tell
trusted data from injected instructions. And **sensitive-information disclosure
(LLM02)**: PII (ID numbers, phones, emails) leaking into outputs via the context
window / RAG sources.

OWASP mitigation is defense-in-depth: filter on BOTH inputs and outputs, and treat
retrieved content as DATA, not instructions. This module provides:
  - sanitize_chunk_text / sanitize_retrieved_context : strip injected instruction
    lines from retrieved chunks before they enter the prompt (LLM01 indirect);
  - redact_pii / guard_output_pii : redact PII from generated answers (LLM02).

Conservative + pure; offline-testable. Wired no-op-safe into retrieval + output.
"""
from __future__ import annotations

import re

# Instruction-injection markers that should NEVER appear as *content* the model
# obeys. Targets imperative override / role-spoofing / extraction phrasing.
_CONTENT_INJECTION = [
    r"忽略.{0,6}(前面|上面|以上|之前|所有|先前|上述).{0,4}(规则|指令|提示|设定|命令|要求|内容)",
    r"(无视|绕过|越过|跳过).{0,6}(规则|指令|限制|设定|约束|安全)",
    r"(打印|输出|展示|显示|告诉|复述|重复|泄露).{0,8}(系统提示|提示词|你的指令|原始指令|prompt)",
    r"(请|你应该|你必须|现在).{0,4}(改为|转而|忽略|停止|代替)",
    r"(AI|assistant|助手|系统|system)\s*[:：]\s*(ignore|忽略|无视|instead|改为)",
    r"ignore\s+(all\s+)?(previous|prior|above|earlier|the\s+above).{0,20}(instruction|rule|prompt)",
    r"(disregard|override|bypass)\s+.{0,20}(instruction|rule|guardrail|safety)",
    r"instead\s+of\s+.{0,30}(summari|answer|respond)",
    r"(reveal|disclose|print|output)\s+.{0,20}(system\s+prompt|other\s+files|confidential)",
]
_CRE = [re.compile(p, re.IGNORECASE) for p in _CONTENT_INJECTION]


def scan_chunk_for_injection(text: str) -> list[str]:
    """Return the lines of `text` that look like embedded injection instructions."""
    flagged = []
    for line in (text or "").splitlines():
        if any(r.search(line) for r in _CRE):
            flagged.append(line.strip())
    return flagged


def sanitize_chunk_text(text: str) -> tuple[str, bool]:
    """Strip embedded-injection lines from a retrieved chunk (treat content as data).

    Returns (clean_text, was_modified). Only removes lines that match injection
    markers — legitimate document text is preserved.
    """
    if not text:
        return text, False
    kept, removed = [], False
    for line in text.splitlines():
        if any(r.search(line) for r in _CRE):
            removed = True
            continue
        kept.append(line)
    clean = "\n".join(kept).strip()
    if removed and not clean:
        clean = "[该来源含被过滤的可疑指令内容]"
    return (clean, True) if removed else (text, False)


def sanitize_retrieved_context(chunks: list[dict], text_key: str = "text") -> tuple[list[dict], list]:
    """Sanitize a list of retrieved chunk dicts. Returns (clean_chunks, flagged_ids)."""
    out, flagged = [], []
    for c in (chunks or []):
        clean, modified = sanitize_chunk_text(c.get(text_key, ""))
        if modified:
            flagged.append(c.get("id", c.get("chunk_id", "")))
        out.append({**c, text_key: clean})
    return out, flagged


# ── LLM02: PII output guard ──
# Specific patterns to avoid false positives on financial figures.
_PII = [
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),          # 18-digit mainland ID
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),             # 11-digit mobile
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
]


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact clear PII (ID / phone / email). Returns (redacted_text, types_found).

    Deliberately narrow so revenue/financial figures (commas, 亿/万 units) are not
    touched — only bare 18-digit IDs, 11-digit mobiles, and emails.
    """
    if not text:
        return text, []
    found, out = [], text
    for label, rx in _PII:
        if rx.search(out):
            found.append(label)
            out = rx.sub(f"[已隐去{label}]", out)
    return out, found


def guard_output_pii(answer: str) -> str:
    """Defense-in-depth output filter: never emit PII that slipped through. Safe no-op
    when the answer has none."""
    redacted, _ = redact_pii(answer or "")
    return redacted


# ── LLM02 (ingestion side): scan/redact documents BEFORE indexing ──
def scan_document_for_pii(text: str) -> dict:
    """Scan a document (pre-ingest) for PII. Returns {has_pii, counts, types}.

    Use at ingestion to FLAG documents containing personal data so an admin can
    decide whether to redact, restrict, or skip — keeping PII out of the index.
    """
    counts: dict = {}
    for label, rx in _PII:
        hits = rx.findall(text or "")
        if hits:
            counts[label] = len(hits)
    return {"has_pii": bool(counts), "types": list(counts.keys()), "counts": counts}


def redact_document(text: str) -> tuple[str, dict]:
    """Redact PII from a document before indexing. Returns (clean_text, scan_report)."""
    report = scan_document_for_pii(text)
    redacted, _ = redact_pii(text or "")
    return redacted, report
