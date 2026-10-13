"""tests/test_llm_judge.py — V103.51 「质量门」离线验证（无需真模型）。

覆盖三层：
  1. 纯函数：prompt 构造、响应解析（含容错）、加权汇总归一化。
  2. score_answer 的降级安全：空答案、无 LLM、不可解析输出都不应误判。
  3. gate 集成：judge 作为「契约红线之上的质量门」AND-gate，且 judge 不可用时不影响判定。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.evaluation.llm_judge import (
    build_judge_prompt, parse_judge_response, aggregate_scores, score_answer,
    RUBRIC_DIMENSIONS, JudgeResult,
)
from hashmm.evaluation.gate import evaluate_case, check_contract


# ── 1. 纯函数 ──────────────────────────────────────────────────────────────
def test_prompt_contains_query_answer_and_sources():
    p = build_judge_prompt("网易2024游戏收入?", "约 X 亿元",
                           sources=[{"filename": "neteng.pdf", "text": "2024 游戏收入..."}],
                           expectation="应给出 2024 实际数字并标注来源")
    assert "网易2024游戏收入?" in p
    assert "约 X 亿元" in p
    assert "neteng.pdf" in p
    assert "期望要点" in p          # expectation block present
    for d in RUBRIC_DIMENSIONS:
        assert d in p               # rubric dimensions all named


def test_parse_clean_json():
    r = parse_judge_response('{"helpfulness":5,"completeness":4,"grounding":5,"coherence":4,"rationale":"好"}')
    assert r is not None
    assert r["dimensions"] == {"helpfulness": 5, "completeness": 4, "grounding": 5, "coherence": 4}
    assert r["rationale"] == "好"


def test_parse_strips_code_fence_and_prose():
    raw = '这是我的判断:\n```json\n{"helpfulness":3,"completeness":3,"grounding":3,"coherence":3,"rationale":"中"}\n```'
    r = parse_judge_response(raw)
    assert r is not None and r["dimensions"]["grounding"] == 3


def test_parse_clamps_out_of_range():
    r = parse_judge_response('{"helpfulness":9,"completeness":0,"grounding":3,"coherence":-2,"rationale":""}')
    assert r["dimensions"]["helpfulness"] == 5   # clamped to max
    assert r["dimensions"]["completeness"] == 1  # clamped to min
    assert r["dimensions"]["coherence"] == 1


def test_parse_missing_dimension_is_failure():
    assert parse_judge_response('{"helpfulness":5,"grounding":5,"coherence":5}') is None  # no completeness
    assert parse_judge_response("not json at all") is None
    assert parse_judge_response("") is None


def test_aggregate_extremes_and_midpoint():
    allfive = {d: 5 for d in RUBRIC_DIMENSIONS}
    allone = {d: 1 for d in RUBRIC_DIMENSIONS}
    allthree = {d: 3 for d in RUBRIC_DIMENSIONS}
    assert abs(aggregate_scores(allfive) - 1.0) < 1e-9
    assert abs(aggregate_scores(allone) - 0.0) < 1e-9
    assert abs(aggregate_scores(allthree) - 0.5) < 1e-9


def test_aggregate_grounding_weighted_highest():
    # grounding=1 (rest=5) should hurt more than coherence=1 (rest=5)
    low_ground = {**{d: 5 for d in RUBRIC_DIMENSIONS}, "grounding": 1}
    low_cohere = {**{d: 5 for d in RUBRIC_DIMENSIONS}, "coherence": 1}
    assert aggregate_scores(low_ground) < aggregate_scores(low_cohere)


# ── 2. score_answer 降级安全 ──────────────────────────────────────────────
def test_empty_answer_scores_zero_but_available():
    r = score_answer("q", "", llm_fn=lambda *_a, **_k: "{}")
    assert r.available is True and r.score == 0.0


def test_no_llm_is_unavailable_not_failure():
    r = score_answer("q", "some answer", llm_fn=None)  # app_state likely has none in test
    assert r.available is False and r.score == 0.0


def test_unparseable_output_is_unavailable():
    r = score_answer("q", "ans", llm_fn=lambda *a, **k: "garbage no json")
    assert r.available is False


def test_good_answer_high_score_with_fake_judge():
    fake = lambda *a, **k: '{"helpfulness":5,"completeness":5,"grounding":5,"coherence":5,"rationale":"perfect"}'
    r = score_answer("q", "great grounded answer", llm_fn=fake)
    assert r.available is True and r.score > 0.95


# ── 3. gate 集成 ────────────────────────────────────────────────────────────
def _fake_high(*a, **k):
    return '{"helpfulness":5,"completeness":5,"grounding":5,"coherence":5,"rationale":"good"}'

def _fake_low(*a, **k):
    return '{"helpfulness":2,"completeness":2,"grounding":1,"coherence":2,"rationale":"weak"}'


def test_gate_judge_only_runs_when_case_opts_in():
    # case WITHOUT judge/rubric → judge not applied even with a working judge_fn
    case = {"id": "c1", "category": "factual", "query": "q", "must_contain_any": ["abc"]}
    res = evaluate_case(case, answer_fn=lambda q: "this contains abc", judge_fn=_fake_high)
    assert res.passed is True
    assert res.judge_applied is False
    assert res.judge_score is None


def test_gate_judge_passes_good_answer():
    case = {"id": "c2", "category": "analytical", "query": "q",
            "must_contain_any": ["abc"], "judge": True, "rubric": "expect abc mentioned"}
    res = evaluate_case(case, answer_fn=lambda q: "answer with abc inside", judge_fn=_fake_high)
    assert res.judge_applied is True
    assert res.judge_score > 0.9
    assert res.passed is True


def test_gate_judge_fails_low_quality_even_if_contract_passes():
    # contract passes (contains 'abc') but judge score below threshold → overall fail
    case = {"id": "c3", "category": "analytical", "query": "q",
            "must_contain_any": ["abc"], "judge": True}
    res = evaluate_case(case, answer_fn=lambda q: "abc but otherwise incoherent garbage",
                        judge_fn=_fake_low)
    assert res.judge_applied is True
    assert res.passed is False
    assert any("judge_score" in r for r in res.reasons)


def test_gate_contract_failure_not_rescued_by_judge():
    # contract FAILS (missing 'abc') → must stay failed regardless of high judge score
    case = {"id": "c4", "category": "analytical", "query": "q",
            "must_contain_any": ["abc"], "judge": True}
    res = evaluate_case(case, answer_fn=lambda q: "no required token here", judge_fn=_fake_high)
    assert res.passed is False
    # judge should not even flip it; reason is the missing contract token
    assert any("must_contain_any" in r for r in res.reasons)


def test_gate_judge_unavailable_does_not_fail_case():
    # judge_fn returns garbage → judge unavailable → passed unchanged (contract decides)
    case = {"id": "c5", "category": "analytical", "query": "q",
            "must_contain_any": ["abc"], "judge": True}
    res = evaluate_case(case, answer_fn=lambda q: "contains abc fine",
                        judge_fn=lambda *a, **k: "not parseable")
    assert res.judge_applied is False
    assert res.passed is True


def test_gate_judge_disabled_via_threshold_flag():
    case = {"id": "c6", "category": "analytical", "query": "q",
            "must_contain_any": ["abc"], "judge": True}
    res = evaluate_case(case, answer_fn=lambda q: "abc here",
                        judge_fn=_fake_low, thresholds={"judge_enabled": False})
    assert res.judge_applied is False
    assert res.passed is True   # judge globally off → low judge score ignored


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
