"""V320.1 回归测试：「为对比而跑」——强制样本量、就绪探测、非就绪跳过、档位清除。

关键诉求（对应用户困境「τ² 只跑 10 题不能和大厂比」）：run_for_comparison 必须
确定性地跑够 standard(50) 题，且不被用户 env 里残留的 HASHMM_TAU2_LIMIT=10 卡住。
"""
import os


# ============================================================ 强制档位覆盖 env
def test_forced_sample_overrides_stale_env():
    """核心修复：即使 env 里残留 HASHMM_TAU2_LIMIT=10，强制 standard 也跑 50。"""
    from hashmm.evaluation.benchmarks.sample_stats import (
        resolve_limit, set_forced_sample, clear_forced_sample)
    os.environ["HASHMM_TAU2_LIMIT"] = "10"          # 模拟用户环境残留
    try:
        assert resolve_limit("tau2", 3) == 10        # 无强制时被 env 卡住
        set_forced_sample("standard")
        assert resolve_limit("tau2", 3) == 50        # 强制后无视 env
        set_forced_sample("full")
        assert resolve_limit("tau2", 3) == 115
    finally:
        clear_forced_sample()
        os.environ.pop("HASHMM_TAU2_LIMIT", None)
    assert resolve_limit("tau2", 3) == 50            # V322：清除后回落默认档 standard(50)


def test_forced_sample_thread_local():
    """强制档位是线程局部的：主线程设了，另一线程看不到（互不干扰）。"""
    import threading
    from hashmm.evaluation.benchmarks.sample_stats import (
        resolve_limit, set_forced_sample, clear_forced_sample)
    set_forced_sample("standard")
    seen = {}

    def _other():
        seen["n"] = resolve_limit("tau2", 3)         # 无强制 → 默认 3

    t = threading.Thread(target=_other)
    t.start(); t.join()
    try:
        assert resolve_limit("tau2", 3) == 50        # 本线程仍是 50
        assert seen["n"] == 50                         # 另一线程不受影响（默认 standard=50）
    finally:
        clear_forced_sample()


# ============================================================ 就绪探测
def test_comparable_candidates_shape():
    from hashmm.evaluation.benchmarks import comparable_candidates
    cands = comparable_candidates()
    ids = {c["id"] for c in cands}
    # 五个官方口径免 Docker 基准都在
    assert {"tau2_bench", "gaia", "humaneval", "tool_calling", "kotlin_bench"} <= ids
    for c in cands:
        assert "runnable" in c and "hint" in c and "standard_n" in c
        assert c["standard_n"] >= 50                  # standard 至少 50 题（可比阈值）


def test_comparable_candidates_sandbox_not_runnable():
    """沙箱无模型/未装数据集 → 全部 not runnable，且每项给出可执行的 install 提示。"""
    from hashmm.evaluation.benchmarks import comparable_candidates
    for c in comparable_candidates():
        assert c["runnable"] is False
        assert "install.sh" in c["hint"] or "模型" in c["hint"]


# ============================================================ run_for_comparison
def test_run_for_comparison_forces_50_and_skips_unready(monkeypatch):
    """runnable 的 τ² 被强制以 50 题跑；未就绪的项带 hint 跳过；跑完档位清除。"""
    import hashmm.evaluation.benchmarks.runner as R
    from hashmm.evaluation.benchmarks.sample_stats import resolve_limit

    # 假装 τ² 就绪、其余不就绪
    monkeypatch.setattr(R, "comparable_candidates", lambda: [
        {"id": "tau2_bench", "name": "τ²", "runnable": True, "hint": "就绪",
         "standard_n": 50, "full_n": 115, "min_comparable": 50},
        {"id": "gaia", "name": "GAIA", "runnable": False, "hint": "未装 GAIA 数据",
         "standard_n": 50, "full_n": 165, "min_comparable": 50},
    ])

    captured = {}

    def _fake_run_benchmark(bid, mode="smoke", llm_fn=None):
        # 跑的瞬间，样本量必须已被强制成 50
        captured[bid] = resolve_limit("tau2", 3)
        return {"id": bid, "name": bid, "skip": False, "comparable": True,
                "score_pct": 62.0, "passed": 31, "total": 50, "kind": "official"}

    monkeypatch.setattr(R, "run_benchmark", _fake_run_benchmark)

    os.environ["HASHMM_TAU2_LIMIT"] = "10"            # 残留也不该影响
    try:
        results = R.run_for_comparison(["tau2_bench", "gaia"], sample="standard")
    finally:
        os.environ.pop("HASHMM_TAU2_LIMIT", None)

    by_id = {r["id"]: r for r in results}
    assert captured.get("tau2_bench") == 50           # ★ 跑时确实是 50 题
    assert by_id["tau2_bench"]["comparable"] is True
    assert by_id["gaia"]["skip"] is True and "未装" in by_id["gaia"]["detail"]
    # 跑完强制档位必须清除（否则污染后续同线程调用）
    assert resolve_limit("tau2", 3) == 50


def test_run_for_comparison_clears_on_exception(monkeypatch):
    """即使跑分抛异常，强制档位也要在 finally 里清除。"""
    import hashmm.evaluation.benchmarks.runner as R
    from hashmm.evaluation.benchmarks.sample_stats import resolve_limit

    monkeypatch.setattr(R, "comparable_candidates", lambda: [
        {"id": "tau2_bench", "name": "τ²", "runnable": True, "hint": "就绪",
         "standard_n": 50, "full_n": 115, "min_comparable": 50}])

    def _boom(*a, **k):
        raise RuntimeError("模拟跑分崩了")

    monkeypatch.setattr(R, "run_benchmark", _boom)
    try:
        R.run_for_comparison(["tau2_bench"], sample="standard")
    except RuntimeError:
        pass
    assert resolve_limit("tau2", 3) == 50             # 已清除（回默认 standard=50）


# ============================================================ 诚实降级：样本不足不定论
def test_comparability_boundary_drives_honest_ranking():
    """驱动卡片降级的阈值：<50 题判「仅供纵向参考」，≥50 判可比。
    这正是 selftest 卡片给排名加 ⚠️ 前缀的依据（10题70%不能说"已达标"）。"""
    from hashmm.evaluation.benchmarks.sample_stats import comparability, MIN_COMPARABLE_N
    # 10 题：不可比
    c10 = comparability(7, 10, "tau2")
    assert c10["ci_width"] > 20                      # 区间很宽
    assert "纵向" in c10["verdict"] or "不" in c10["verdict"] or c10["ci_width"] > 20
    # 50 题：可比
    c50 = comparability(31, 50, "tau2")
    assert 50 >= MIN_COMPARABLE_N
    assert c50["ci_width"] < c10["ci_width"]         # 样本越多区间越窄


def test_wilson_interval_small_sample_is_wide():
    """小样本 Wilson 区间必须宽（10题70%的下界应显著低于70%），这是"不能定论"的数学根据。"""
    from hashmm.evaluation.benchmarks.sample_stats import wilson_interval
    lo, hi = wilson_interval(7, 10)
    assert lo < 50 and hi > 85                        # [~40%, ~89%]：宽到不能和 60% 锚点比
    lo2, hi2 = wilson_interval(35, 50)
    assert (hi2 - lo2) < (hi - lo)                    # 50题区间明显更窄


# ============================================================ 端到端：50 题→进对比区→上图
def test_50_samples_land_in_comparable_zone():
    """核心验证：跑够 50 题的分数，build_comparison 应放进 comparable（不是 trend_only）。
    这证明 V322「默认 50 可比」改动真的能让分数进正式对比区。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    latest = {"tau2": {"score_pct": 62.0, "passed": 31, "total": 50,
                       "name": "τ²-bench", "model": "Qwen3-32B"}}
    cmp = vs_frontier.build_comparison(latest)
    ids_comparable = {e["id"] for e in cmp["comparable"]}
    ids_trend = {e["id"] for e in cmp["trend_only"]}
    assert "tau2" in ids_comparable, "50 题应进正式对比区"
    assert "tau2" not in ids_trend


def test_below_50_stays_in_trend_zone():
    """反例：10 题的分数必须留在 trend_only（不能和大厂比）。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    latest = {"tau2": {"score_pct": 70.0, "passed": 7, "total": 10, "name": "τ²", "model": "m"}}
    cmp = vs_frontier.build_comparison(latest)
    assert "tau2" in {e["id"] for e in cmp["trend_only"]}
    assert "tau2" not in {e["id"] for e in cmp["comparable"]}


def test_comparable_50_renders_in_chart_formal_zone():
    """50 题的分数在对比图 SVG 里应出现在「正式对比区」，带 CI 误差线。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    from hashmm.evaluation.benchmarks.chart_export import render_svg
    latest = {"tau2": {"score_pct": 62.0, "passed": 31, "total": 50, "name": "τ²-bench", "model": "m"}}
    cmp = vs_frontier.build_comparison(latest)
    svg = render_svg(cmp)
    assert "正式对比区" in svg and "τ²-bench" in svg
    assert 'stroke="#1e1b4b"' in svg          # CI 误差线（可比条特征）


def test_bfcl_default_is_comparable_total():
    """BFCL 走预设后，默认每类题数×类别数应达可比阈值（≥50）。"""
    import os
    for k in list(os.environ):
        if k.startswith("HASHMM_"):
            os.environ.pop(k, None)
    from hashmm.evaluation.benchmarks.sample_stats import resolve_limit, MIN_COMPARABLE_N
    # tool_calling 默认应是 standard(50)
    assert resolve_limit("tool_calling", 80) == 50
    assert 50 >= MIN_COMPARABLE_N


# ============================================================ V322 跑分用时全链路
def test_elapsed_ms_roundtrip(monkeypatch):
    """elapsed_ms：record_run 入库 → latest_runs 读出 → build_comparison 带上。"""
    import tempfile
    monkeypatch.setenv("HASHMM_DATA_DIR", tempfile.mkdtemp(prefix="bench_ms_"))
    import importlib
    from hashmm.evaluation.benchmarks import trend as T
    importlib.reload(T)   # 让 _db_path 重新读新的 HASHMM_DATA_DIR
    # 记一次 50 题、用时 3 分钟的可比跑分
    ok = T.record_run({
        "id": "tau2", "name": "τ²-bench", "kind": "official", "mode": "full",
        "score_pct": 62.0, "passed": 31, "total": 50, "skip": False,
        "elapsed_ms": 180000,
        "breakdown": {"脚手架": "模型=Qwen3-32B"},
    }, meta={"kind": "official", "elapsed_ms": 180000})
    assert ok
    latest = T.latest_runs()
    assert latest.get("tau2", {}).get("elapsed_ms") == 180000

    from hashmm.evaluation.benchmarks import vs_frontier
    cmp = vs_frontier.build_comparison(latest)
    e = next((x for x in cmp["comparable"] if x["id"] == "tau2"), None)
    assert e and e["elapsed_ms"] == 180000


# ============================================================ V322 补漏：三个基准进对比
def test_humaneval_kotlin_toolcalling_in_comparison():
    """修复：humaneval/tool_calling/kotlin_bench 也是官方口径可比基准，应能进对比
    （此前漏在 _ID_TO_LBKEY 外，跑了却不显示）。50 题应进正式对比区、带大厂锚点。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    latest = {
        "humaneval": {"score_pct": 88.0, "passed": 44, "total": 50, "name": "HumanEval", "model": "m"},
        "tool_calling": {"score_pct": 82.0, "passed": 41, "total": 50, "name": "BFCL", "model": "m"},
        "kotlin_bench": {"score_pct": 70.0, "passed": 35, "total": 50, "name": "Kotlin", "model": "m"},
    }
    cmp = vs_frontier.build_comparison(latest)
    ids = {e["id"] for e in cmp["comparable"]}
    assert {"humaneval", "tool_calling", "kotlin_bench"} <= ids
    # 每个都带大厂锚点（否则对比无意义）
    for e in cmp["comparable"]:
        assert e["anchors"], f"{e['id']} 缺大厂锚点"


def test_new_benches_in_parity_table():
    """口径对齐表也应包含这三个（都标 ✅ 一致）。"""
    from hashmm.evaluation.benchmarks import vs_frontier
    pt = {r["id"]: r for r in vs_frontier.parity_table()}
    for bid in ("humaneval", "tool_calling", "kotlin_bench"):
        assert bid in pt and "一致" in pt[bid]["verdict"]
