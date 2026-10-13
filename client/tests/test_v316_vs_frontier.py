"""V316 回归测试：大厂横向对比（可比性门禁）+ 满分假象警示 + SWE 零网络预置镜像。

用户诉求：拿分数和 2026 大厂做对比图。前提是分数可比——本轮把「可比性」做成对比表
的一等公民，并修掉"τ² 10/10=100% 看着比大厂还高"的误导。全部离线可测。
"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _bench_home(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    for k in ("HASHMM_SWEBENCH_REPO_CACHE", "HASHMM_BENCH_SAMPLE"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- 大厂对比门禁
def test_fake_full_score_stays_out_of_comparable():
    """10/10=100% 必须落进趋势区，不能进正式对比区（防"看着超过大厂"）。"""
    from hashmm.evaluation.benchmarks import vs_frontier as VF
    latest = {"tau2": {"score_pct": 100.0, "passed": 10, "total": 10,
                       "name": "τ²-bench", "model": "deepseek-v4-pro"}}
    cmp = VF.build_comparison(latest)
    assert not cmp["comparable"]
    assert [e["id"] for e in cmp["trend_only"]] == ["tau2"]
    assert "why_not" in cmp["trend_only"][0]


def test_adequate_sample_enters_comparable():
    """50 题达标 → 进正式对比区，并正确判断超过了哪些大厂锚点。"""
    from hashmm.evaluation.benchmarks import vs_frontier as VF
    latest = {"tau2": {"score_pct": 62.0, "passed": 31, "total": 50,
                       "name": "τ²-bench", "model": "m"}}
    cmp = VF.build_comparison(latest)
    assert [e["id"] for e in cmp["comparable"]] == ["tau2"]
    assert "顶尖 agent" in cmp["comparable"][0]["beats"]  # 62 > 60


def test_missing_benches_listed_not_zero():
    """没跑的基准进 missing 区（缺失≠0分）。"""
    from hashmm.evaluation.benchmarks import vs_frontier as VF
    cmp = VF.build_comparison({})
    missing_ids = {e["id"] for e in cmp["missing"]}
    assert {"swebench", "swebench_pro", "osworld"} <= missing_ids


def test_render_markdown_has_three_zones():
    from hashmm.evaluation.benchmarks import vs_frontier as VF
    latest = {"gaia": {"score_pct": 40.0, "passed": 8, "total": 20, "name": "GAIA", "model": "m"}}
    md = VF.render_markdown(VF.build_comparison(latest))
    assert "正式对比区" in md and "趋势区" in md
    assert "不能和大厂比" in md or "不能" in md
    assert "诚实" in md


# ---------------------------------------------------------------- 满分假象警示
def test_high_score_small_sample_warning():
    from hashmm.evaluation.benchmarks.sample_stats import format_breakdown
    bd = format_breakdown(10, 10, "tau2")
    assert any("不可比" in k for k in bd)
    assert any("standard" in str(v) for v in bd.values())


def test_adequate_sample_no_false_warning():
    from hashmm.evaluation.benchmarks.sample_stats import format_breakdown
    bd = format_breakdown(31, 50, "tau2")
    assert not any("看着高" in k for k in bd)


def test_quick_preset_converged():
    from hashmm.evaluation.benchmarks.sample_stats import SAMPLE_PRESETS
    q = SAMPLE_PRESETS["quick"]
    assert q["tau2"] == 5 and q["gaia"] == 10 and q["terminal"] == 5
    # standard/full 保持大厂全集口径
    assert SAMPLE_PRESETS["standard"]["tau2"] == 50
    assert SAMPLE_PRESETS["full"]["swebench_pro"] == 731


# ---------------------------------------------------------------- SWE 零网络预置镜像
def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("hello")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_preload_mirror_zero_network(monkeypatch):
    from hashmm.evaluation.benchmarks import swebench_local as SW
    bh = Path(os.environ["HASHMM_BENCH_HOME"])
    preload = bh / "swebench-repos-preload" / "psf__requests"
    commit = _make_git_repo(preload)
    work = Path(tempfile.mkdtemp()) / "w"
    ok, note = SW.clone_repo("psf/requests", work, commit)
    assert ok and "预置镜像" in note and "零网络" in note
    assert (work / "f.txt").read_text() == "hello"


def test_preload_via_env_var(monkeypatch):
    from hashmm.evaluation.benchmarks import swebench_local as SW
    root = Path(tempfile.mkdtemp())
    commit = _make_git_repo(root / "flask")          # 用短名（repo split）
    monkeypatch.setenv("HASHMM_SWEBENCH_REPO_CACHE", str(root))
    work = Path(tempfile.mkdtemp()) / "w"
    ok, note = SW.clone_repo("pallets/flask", work, commit)
    assert ok and "预置镜像" in note


def test_swe_detect_reports_preload_count():
    from hashmm.evaluation.benchmarks import swebench_local as SW
    d = SW.detect()
    assert "n_preload" in d and d["n_preload"] == 0


def test_install_sh_has_preload_target():
    src = (Path(__file__).resolve().parent.parent / "hashmm" / "evaluation"
           / "benchmarks" / "install.sh").read_text(encoding="utf-8")
    assert "install_swebench_preload" in src
    assert "swebench_preload) install_swebench_preload" in src
    assert "swebench-repos-preload" in src


# ---------------------------------------------------------------- trend latest_runs
def test_latest_runs_extracts_model_and_samples():
    from hashmm.evaluation.benchmarks import trend
    trend.record_run({"id": "tau2", "name": "τ²-bench", "kind": "official",
                      "score_pct": 62.0, "passed": 31, "total": 50,
                      "breakdown": {"脚手架": "脚手架版本=V316 · 模型=deepseek-v4-pro"}})
    lr = trend.latest_runs()
    assert lr["tau2"]["total"] == 50 and lr["tau2"]["passed"] == 31
    assert lr["tau2"]["model"] == "deepseek-v4-pro"


def test_latest_runs_skips_no_score():
    from hashmm.evaluation.benchmarks import trend
    # skip 的跑分不入库 → latest_runs 不含它
    trend.record_run({"id": "osworld", "skip": True, "score_pct": None})
    assert "osworld" not in trend.latest_runs()
