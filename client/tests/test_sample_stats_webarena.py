"""样本量统计 + WebArena 官方判分（V310）回归测试。纯逻辑，无网络/无 Chromium 依赖。"""
import os

import pytest

from hashmm.evaluation.benchmarks import sample_stats as SS
from hashmm.evaluation.benchmarks import webarena as WA


# ═══════════ 样本量 / Wilson 置信区间 ═══════════

def test_wilson_zero_of_three_not_certain():
    """核心：0/3 绝不能被当成"确定的 0%"。Wilson 区间上界必须显著 > 0。"""
    lo, hi = SS.wilson_interval(0, 3)
    assert lo == 0.0
    assert hi > 40, f"0/3 上界应反映样本太小，实际 {hi}"


def test_wilson_zero_of_full_is_tight():
    """跑满官方全集的 0% 才是真的低：区间收窄。"""
    lo, hi = SS.wilson_interval(0, 115)
    assert hi < 5, f"0/115 上界应很小，实际 {hi}"


def test_wilson_empty_is_no_info():
    lo, hi = SS.wilson_interval(0, 0)
    assert (lo, hi) == (0.0, 100.0)   # 完全无信息


def test_wilson_interval_widths_shrink_with_n():
    """样本越大区间越窄（单调）。"""
    w3 = SS.wilson_interval(1, 3)
    w30 = SS.wilson_interval(12, 30)
    w300 = SS.wilson_interval(120, 300)
    width = lambda t: t[1] - t[0]
    assert width(w3) > width(w30) > width(w300)


def test_comparability_verdicts():
    assert "不可比" in SS.comparability(0, 3, "swebench")["verdict"]
    assert "纵向" in SS.comparability(0, 10, "tau2")["verdict"]
    assert "可比" in SS.comparability(60, 120, "swebench")["verdict"]


def test_comparability_coverage():
    c = SS.comparability(3, 3, "swebench")
    assert c["official_full"] == 500
    assert c["coverage_pct"] == 0.6


def test_resolve_limit_priority(monkeypatch):
    # 单项环境变量 > 预设 > 默认档(standard)。V322：默认不再是调用方 fallback，而是 standard(50)。
    monkeypatch.delenv("HASHMM_BENCH_SAMPLE", raising=False)
    monkeypatch.delenv("HASHMM_SWEBENCH_LIMIT", raising=False)
    assert SS.resolve_limit("swebench", 3) == 50          # 默认档 standard（默认可比）
    monkeypatch.setenv("HASHMM_BENCH_SAMPLE", "full")
    assert SS.resolve_limit("swebench", 3) == 500         # 预设 full
    monkeypatch.setenv("HASHMM_SWEBENCH_LIMIT", "7")
    assert SS.resolve_limit("swebench", 3) == 7           # 单项覆盖


def test_resolve_limit_standard_preset(monkeypatch):
    monkeypatch.delenv("HASHMM_TAU2_LIMIT", raising=False)
    monkeypatch.setenv("HASHMM_BENCH_SAMPLE", "standard")
    assert SS.resolve_limit("tau2", 10) == 50


def test_format_breakdown_keys():
    bd = SS.format_breakdown(0, 3, "swebench")
    assert "样本量" in bd and "95%置信区间" in bd and "可比性" in bd
    assert "0.6%" in bd["样本量"]        # 覆盖率


# ═══════════ WebArena 官方判分 ═══════════

def test_exact_match():
    assert WA.eval_string_match("  The Answer 42 ", {"exact_match": "the answer 42"})[0] is True
    assert WA.eval_string_match("41", {"exact_match": "42"})[0] is False


def test_must_include():
    assert WA.eval_string_match("Total $42.50 shipped", {"must_include": ["42.50", "shipped"]})[0] is True
    assert WA.eval_string_match("Total $42.50", {"must_include": ["42.50", "shipped"]})[0] is False


def test_fuzzy_match_returns_none():
    """fuzzy_match 需 LLM 裁判 → 返回 None（不评），绝不当通过。"""
    ok, why = WA.eval_string_match("anything", {"fuzzy_match": ["x"]})
    assert ok is None and "裁判" in why


def test_url_match_normalization():
    assert WA.eval_url_match("http://h:9999/f/aww/", "http://h:9999/f/aww")[0] is True
    assert WA.eval_url_match("https://h/x", "http://h/x")[0] is True          # scheme 无关
    assert WA.eval_url_match("http://h/s?b=2&a=1", "http://h/s?a=1&b=2")[0] is True  # query 乱序
    assert WA.eval_url_match("http://h/a", "http://h/b")[0] is False


def test_placeholder_resolution():
    urls = {"SHOPPING": "http://1.2.3.4:7770"}
    assert WA.resolve_placeholders("去 __SHOPPING__/cart", urls) == "去 http://1.2.3.4:7770/cart"


def test_detect_without_urls(monkeypatch):
    for k in WA.SITE_ENVS:
        monkeypatch.delenv(f"HASHMM_WEBARENA_{k}", raising=False)
        monkeypatch.delenv(k, raising=False)
    det = WA.detect()
    assert det["installed"] is False   # 没配站点 → 不可跑（但会给指引，不是死跳过）


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
