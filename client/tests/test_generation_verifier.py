from __future__ import annotations

from hashmm.generation.verifier import reflect_and_retry


def test_generation_failure_cannot_be_reported_as_verified():
    def broken_generate(_query, _sources, _critique):
        raise RuntimeError("provider unavailable")

    result = reflect_and_retry("question", broken_generate, max_attempts=2)

    assert result["passed"] is False
    assert result["answer"] == ""
    assert result["issues"] == ["generate_fn raised"]


def test_verifier_failure_keeps_draft_but_fails_closed():
    def generate(_query, _sources, _critique):
        return "draft answer"

    def broken_verify(_query, _answer, _sources):
        raise RuntimeError("verifier unavailable")

    result = reflect_and_retry(
        "question", generate, verify_fn=broken_verify, max_attempts=1,
    )

    assert result["answer"] == "draft answer"
    assert result["passed"] is False
    assert result["issues"] == ["verify_fn raised"]
    assert result["history"] == [{
        "attempt": 1, "passed": False, "issues": ["verify_fn raised"],
    }]
