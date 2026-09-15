"""Regression tests for quality gates that must not report false positives."""

from __future__ import annotations

from hashmm.agent import acceptance
from hashmm.api import retrieval


def test_acceptance_checker_failure_withholds_pass(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("checker unavailable")

    monkeypatch.setitem(acceptance._CHECKERS, "writing", boom)
    ok, missing, description = acceptance.check("doc_task", "写一份报告", "draft")

    assert ok is False
    assert missing
    assert "无法确认" in missing[0]
    assert description


def test_answer_validator_failure_withholds_pass(monkeypatch):
    monkeypatch.setattr(retrieval, "_get_llm_fn", lambda: object())
    monkeypatch.setattr(
        retrieval,
        "_call_llm",
        lambda _prompt: (_ for _ in ()).throw(RuntimeError("validator down")),
    )

    ok, reason = retrieval.validate_answer("问题", "这是一个足够长的回答。" * 8)

    assert ok is False
    assert "不可用" in reason
