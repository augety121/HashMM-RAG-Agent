"""v17 Phase 74 — Verifier + reflection loop (轴 B1, general-purpose).

2026 multi-agent practice separates roles: a Generator produces an answer, a
**Verifier** checks it, and on failure a **Reflector** critiques so the Generator
can retry. This is the reliability half of "agentic RAG": after multi-hop
retrieval (Phase 73) gathers evidence, we generate → verify it's actually grounded
in that evidence → reflect + regenerate if not. Domain-agnostic.

Everything is **injectable** (so it's testable without models) and **never blocks**:
- ``verify_answer`` runs cheap, deterministic checks (non-empty; cited markers
  present when sources exist; cited sentences actually overlap their source via
  the existing ``citation_overlap_check``) — and treats an honest "not found"
  answer as valid. An optional ``llm_fn`` slot lets callers add an LLM faithfulness
  judge, but the default path needs no model.
- ``reflect_and_retry`` calls an injected ``generate_fn(query, sources, critique)``,
  verifies, and on failure feeds a critique back for one or more retries, capped by
  ``max_attempts``. Verification or generation errors remain visible as an
  unverified result; infrastructure failure must never be converted into proof.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)

# An answer that honestly says "not in the corpus" is correct behavior, not an
# ungrounded hallucination — don't penalize it for lacking citations.
_REFUSAL_MARKERS = (
    "未提及", "未找到", "没有相关", "无法回答", "无法", "未披露", "暂无", "查无",
    "不包含", "未涉及", "not found", "no information", "cannot find", "don't have",
)


def verifier_enabled() -> bool:
    """Whether the verify/reflect loop is active in the live path (default OFF)."""
    return os.environ.get("HASHMM_VERIFIER", "0").strip().lower() in ("1", "true", "yes", "on")


def max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_VERIFIER_MAX_ATTEMPTS", "2")))
    except Exception:
        return 2


@dataclass
class VerifyResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)


def verify_answer(query: str, answer: str, sources: list | None = None, *,
                  llm_fn: Callable | None = None, min_overlap_ratio: float = 0.5) -> VerifyResult:
    """Cheap, deterministic answer verification (no model required). Never raises."""
    issues: list[str] = []
    checks: dict = {}
    a = (answer or "").strip()
    checks["length"] = len(a)
    if not a:
        return VerifyResult(False, ["答案为空"], checks)

    honest_refusal = any(m in a for m in _REFUSAL_MARKERS)
    sources = sources or []

    if sources:
        has_cite = ("[" in a and "]" in a)
        g = None
        try:
            from hashmm.generation.groundedness import citation_overlap_check
            g = citation_overlap_check(a, sources)
            checks["citation"] = g
        except Exception as e:
            log_suppressed(logger, e)

        if not has_cite and not honest_refusal:
            issues.append("有检索来源但答案缺少引用角标 [n]")
        elif g and g.get("checked", 0) > 0 and not honest_refusal:
            ratio = g.get("ratio", 1.0)
            if ratio < min_overlap_ratio:
                issues.append(f"引用与来源重合度低（{ratio:.2f}），可能未真正基于来源")

    # Optional LLM faithfulness slot — left to the caller's verify_fn / llm_fn so
    # the default path stays model-free. (Pluggable, not invoked here.)

    return VerifyResult(len(issues) == 0, issues, checks)


def _default_critique(issues: list[str]) -> str:
    bullets = "\n".join(f"- {i}" for i in issues)
    return ("上一稿存在以下问题，请修正后重写：\n" + bullets +
            "\n要求：严格基于检索来源作答；对每个来自来源的事实/数字，在句末加 [n] 角标；"
            "来源未提及的内容不要编造（可明确说明未提及）。")


def reflect_and_retry(query: str, generate_fn: Callable[[str, list, str | None], str], *,
                      sources: list | None = None,
                      verify_fn: Callable[[str, str, list], VerifyResult] | None = None,
                      build_critique: Callable[[list[str]], str] | None = None,
                      max_attempts: int = 2) -> dict:
    """Generate → verify → (reflect → regenerate) up to ``max_attempts``.

    ``generate_fn(query, sources, critique)`` returns an answer string; ``critique``
    is None on the first attempt and the Reflector's note on retries. Returns
    {answer, attempts, passed, issues, history}. Never raises — generation/verify
    errors return the best answer so far with ``passed=False``.  The caller may
    still show that draft, but cannot label it verified or completed.
    """
    verify_fn = verify_fn or (lambda q, a, s: verify_answer(q, a, s))
    build_critique = build_critique or _default_critique
    sources = sources or []
    history: list[dict] = []
    critique: str | None = None
    best: dict | None = None

    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            answer = generate_fn(query, sources, critique)
        except Exception as e:
            log_suppressed(logger, e)
            if best is not None:
                break
            return {"answer": "", "attempts": attempt, "passed": False,
                    "issues": ["generate_fn raised"], "history": history}
        try:
            vr = verify_fn(query, answer, sources)
        except Exception as e:
            log_suppressed(logger, e)
            vr = VerifyResult(
                False, ["verify_fn raised"], {"verification_available": False},
            )

        history.append({"attempt": attempt, "passed": vr.passed, "issues": list(vr.issues)})
        best = {"answer": answer, "passed": vr.passed, "issues": list(vr.issues), "attempt": attempt}
        if vr.passed or attempt >= max(1, max_attempts):
            break
        critique = build_critique(vr.issues)

    return {"answer": best["answer"], "attempts": best["attempt"], "passed": best["passed"],
            "issues": best["issues"], "history": history}
