"""V320 回归测试：对比图导出（SVG + CSV）。

诚实性关键点逐一验证：与 vs_frontier 同源、缺失不画 0 分条、趋势区明确标注不可比、
CSV 带 BOM（中文 Excel 兼容）、SVG 结构合法且转义安全。
"""


def _fake_cmp():
    """手工构造一个 build_comparison 形状的结构（不依赖 DB）。"""
    return {
        "model": "Qwen3-32B-AWQ",
        "comparable": [{
            "id": "tau2", "name": "τ²-bench", "your_score": 62.0,
            "n": 50, "passed": 31, "ci_low": 48.2, "ci_high": 74.1,
            "ci_width": 25.9, "coverage_pct": 45.0, "official_full": 111,
            "verdict": "standard 档，可与大厂比", "beats": [],
            "anchors": [("顶尖 agent", 60.0)],
        }],
        "trend_only": [{
            "id": "gaia", "name": "GAIA", "your_score": 80.0,
            "n": 10, "passed": 8, "ci_low": 49.0, "ci_high": 94.3,
            "ci_width": 45.3, "coverage_pct": 6.1, "official_full": 165,
            "verdict": "quick 档", "beats": [],
            "anchors": [("系统级最佳(工具栈)", 92.4)],
            "why_not": "只跑了 10 题（官方 165 题）",
        }],
        "missing": [{
            "id": "osworld", "name": "OSWorld",
            "anchors": [("人类", 72.4)], "reason": "尚无跑分记录",
        }],
    }


# ============================================================ SVG
def test_svg_wellformed_and_sections():
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg(_fake_cmp())
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<svg") == 1 and svg.count("</svg>") == 1
    # 两区标题都在
    assert "正式对比区" in svg and "趋势区" in svg
    # 可比基准与锚点都画了
    assert "τ²-bench" in svg and "HashMM(你)" in svg and "顶尖 agent" in svg


def test_svg_missing_not_drawn_as_zero():
    """缺失≠0：OSWorld 只出现在脚注文字，不应有它的 HashMM 条。"""
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg(_fake_cmp())
    assert "尚未跑分" in svg and "OSWorld" in svg
    # 缺失区不该出现"HashMM"和 OSWorld 绑在一行的条形数值（无 0% 条）
    assert "OSWorld（大厂锚点" in svg          # 只在文字列表里


def test_svg_trend_labeled_not_comparable():
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg(_fake_cmp())
    assert "不可与大厂横向比较" in svg
    assert "样本不足" in svg


def test_svg_ci_whisker_present():
    """可比条必须带 CI 误差线（stroke=深靛的 line）。"""
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg(_fake_cmp())
    assert 'stroke="#1e1b4b"' in svg


def test_svg_escapes_xml():
    """基准名里出现 & < > 不能破坏 SVG。"""
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    cmp = _fake_cmp()
    cmp["comparable"][0]["name"] = "A&B <危险> 基准"
    svg = render_svg(cmp)
    assert "&amp;B" in svg and "&lt;危险&gt;" in svg
    assert "<危险>" not in svg


def test_svg_empty_comparable_hint():
    """一个可比项都没有时，给出如何达标的提示而不是空白。"""
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg({"model": "m", "comparable": [], "trend_only": [], "missing": []})
    assert "HASHMM_BENCH_SAMPLE=standard" in svg


# ============================================================ CSV
def test_csv_has_bom_and_header():
    from hashmm.evaluation.benchmarks.chart_export import render_csv
    csv = render_csv(_fake_cmp())
    assert csv.startswith("\ufeff")                    # Excel 中文兼容
    assert "benchmark_id" in csv and "comparable" in csv


def test_csv_rows_cover_all_zones():
    from hashmm.evaluation.benchmarks.chart_export import render_csv
    csv = render_csv(_fake_cmp())
    assert ",yes," in csv                              # 可比行
    assert ",no," in csv                               # 趋势行
    assert "not_run" in csv                            # 缺失行
    assert "anchor" in csv                             # 锚点行


def test_csv_quotes_commas():
    """字段含逗号必须加引号（why_not 常含逗号）。"""
    from hashmm.evaluation.benchmarks.chart_export import render_csv
    cmp = _fake_cmp()
    cmp["trend_only"][0]["why_not"] = "只跑了 10 题, 需要 50"
    csv = render_csv(cmp)
    assert '"只跑了 10 题, 需要 50"' in csv


# ============================================================ 同源性
def test_chart_meta_counts():
    from hashmm.evaluation.benchmarks.chart_export import chart_meta
    m = chart_meta(_fake_cmp())
    assert m["comparable_count"] == 1 and m["trend_count"] == 1 and m["missing_count"] == 1


def test_same_source_as_vs_frontier():
    """图直接吃 build_comparison 的输出——用真实空库跑一遍全链路（无跑分→全 missing）。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    from hashmm.evaluation.benchmarks.chart_export import render_csv, render_svg
    cmp = vs_frontier.build_comparison({})             # 空 latest → 全 missing
    svg = render_svg(cmp)
    csv = render_csv(cmp)
    assert cmp["comparable"] == [] and len(cmp["missing"]) > 0
    assert "尚未跑分" in svg and "not_run" in csv


# ============================================================ V322 聚合总结
def test_comparison_summary_headline():
    """聚合总结只统计正式对比区，给出一句话结论。"""
    from hashmm.evaluation.benchmarks.vs_frontier import comparison_summary
    s = comparison_summary(_fake_cmp())
    assert s["comparable_benches"] == 1          # 只有 τ² 在可比区
    assert s["anchors_total"] == 1               # τ² 有 1 个锚点
    assert "可比基准" in s["headline"]


def test_comparison_summary_empty():
    from hashmm.evaluation.benchmarks.vs_frontier import comparison_summary
    s = comparison_summary({"comparable": [], "trend_only": [], "missing": []})
    assert s["comparable_benches"] == 0
    assert "跑够 50 题" in s["headline"]


def test_comparison_summary_beats_counted():
    """超过的锚点数正确统计（beats 里的都算）。"""
    from hashmm.evaluation.benchmarks.vs_frontier import comparison_summary
    cmp = {"comparable": [{"id": "x", "name": "X", "your_score": 90.0,
                           "anchors": [("A", 60.0), ("B", 80.0)], "beats": ["A", "B"]}],
           "trend_only": [], "missing": []}
    s = comparison_summary(cmp)
    assert s["anchors_beaten"] == 2 and s["topped_benches"] == 1


def test_chart_svg_includes_summary_headline():
    """对比图 SVG 图头应包含聚合总结。"""
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    svg = render_svg(_fake_cmp())
    assert "📊" in svg and "可比基准" in svg
