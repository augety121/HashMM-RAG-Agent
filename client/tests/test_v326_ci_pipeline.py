"""V326 回归测试：CI（GitHub Actions）跑 Docker 基准的链路修复。

背景（本版修的三个真 bug）：
  1. remote_bench_runner 的 _BENCH_ID_MAP 把 swebench→"swebench"、terminal→"terminal"，
     但注册表里是 "swebench_verified"/"terminal_bench" → run_benchmark 返回「未知基准」，
     而工作流默认输入就是 swebench —— 最常见的一次 CI 运行完全跑不出分。
  2. swebench_full / terminal_full（有 Docker 时的官方 harness 路径，正是 CI 走的路径）
     不调用 resolve_limit，硬编码 limit=5 → CI 的 --sample standard/full 被忽略，恒跑 3~5 题，
     分数永远进不了可比区。
  3. SAMPLE_PRESETS 缺 osworld 键（osworld 接线后会静默回落代码默认值）。

这些测试锁死修复，防回潮。纯离线、不依赖 fastapi/Docker。
"""
import importlib.util
import sys
from pathlib import Path

try:
    import pytest  # noqa: F401  （mini_runner/真机 pytest 都有；直接 python 跑本文件时不需要）
except ImportError:
    pytest = None

_ROOT = Path(__file__).resolve().parent.parent


def _load_runner_module():
    """按文件路径独立加载 scripts/remote_bench_runner.py（它不是包，不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "remote_bench_runner", _ROOT / "scripts" / "remote_bench_runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ─────────────────────────── ① CI 短名映射必须全部命中注册表 ───────────────────────────
def test_ci_bench_map_all_resolve_to_registry():
    """_BENCH_ID_MAP 的每个值都必须是注册表里的真实 bench_id（否则 CI 直接「未知基准」）。"""
    from hashmm.evaluation.benchmarks.registry import get_benchmark
    m = _load_runner_module()
    for short, bid in m._BENCH_ID_MAP.items():
        assert get_benchmark(bid) is not None, \
            f"CI 短名 {short} 映射到 {bid}，但注册表里没有——CI 会整趟白跑"


def test_ci_bench_map_covers_ci_benches():
    """_CI_BENCHES 里的每个短名都要有映射（否则 KeyError 崩 runner）。"""
    m = _load_runner_module()
    for short in m._CI_BENCHES:
        assert short in m._BENCH_ID_MAP, f"CI 短名 {short} 没有映射"


def test_ci_map_swebench_terminal_specifically():
    """钉死本次修复的两个具体值（swebench→swebench_verified，terminal→terminal_bench）。"""
    m = _load_runner_module()
    assert m._BENCH_ID_MAP["swebench"] == "swebench_verified"
    assert m._BENCH_ID_MAP["terminal"] == "terminal_bench"


# ─────────────────────────── ② Docker 基准的 sample 传导 ───────────────────────────
def test_swebench_full_respects_forced_sample():
    """swebench_full 源码必须调用 resolve_limit（CI --sample 才能生效）。"""
    src = (_ROOT / "hashmm/evaluation/benchmarks/swebench_full.py").read_text(encoding="utf-8")
    assert "resolve_limit" in src, "swebench_full 不再走 resolve_limit——CI --sample 会被忽略"
    assert 'resolve_limit("swebench"' in src


def test_terminal_full_respects_forced_sample():
    src = (_ROOT / "hashmm/evaluation/benchmarks/terminal_full.py").read_text(encoding="utf-8")
    assert "resolve_limit" in src, "terminal_full 不再走 resolve_limit——CI --sample 会被忽略"
    assert 'resolve_limit("terminal"' in src


def test_forced_sample_flows_to_docker_bench_keys():
    """端到端：set_forced_sample 后，swebench/terminal 的 resolve_limit 出对应档位题量。"""
    from hashmm.evaluation.benchmarks.sample_stats import (
        SAMPLE_PRESETS, clear_forced_sample, resolve_limit, set_forced_sample)
    try:
        for preset in ("quick", "standard", "full"):
            set_forced_sample(preset)
            assert resolve_limit("swebench", 5) == SAMPLE_PRESETS[preset]["swebench"]
            assert resolve_limit("terminal", 5) == SAMPLE_PRESETS[preset]["terminal"]
    finally:
        clear_forced_sample()


# ─────────────────────────── ③ 预设表完整性 ───────────────────────────
def test_sample_presets_have_osworld():
    """osworld 键补齐（full=369 官方全集）。"""
    from hashmm.evaluation.benchmarks.sample_stats import SAMPLE_PRESETS
    for preset in ("smoke", "quick", "standard", "full"):
        assert "osworld" in SAMPLE_PRESETS[preset], f"{preset} 档缺 osworld 键"
    assert SAMPLE_PRESETS["full"]["osworld"] == 369
    assert SAMPLE_PRESETS["standard"]["osworld"] == 50


def test_sample_presets_keys_consistent_across_tiers():
    """四档预设的键集合必须一致（缺键=某档静默回落代码默认值，就是 osworld 踩过的坑）。"""
    from hashmm.evaluation.benchmarks.sample_stats import SAMPLE_PRESETS
    keysets = {tier: set(d) for tier, d in SAMPLE_PRESETS.items()}
    base = keysets["standard"]
    for tier, ks in keysets.items():
        assert ks == base, f"{tier} 档键集合与 standard 不一致：差 {base ^ ks}"


# ─────────────────────────── ④ install.sh 结构回归 ───────────────────────────
def test_install_sh_no_duplicate_case_branch():
    """case 里不允许重复的目标分支（bash 只匹配第一个，重复=死代码+将来行为陷阱）。

    ★ V332 修正解析粒度：'bash 只匹配第一个'的规则只在**单个 case…esac 语句内**成立。
    install.sh 现在合法地有多个 case 块（①安装分发 ②CHECK_TARGETS 映射 ③标签美化），
    nodocker/all 在不同块里各出现一次是正确结构；旧版全文件计数把它误报成重复。
    """
    src = (_ROOT / "hashmm/evaluation/benchmarks/install.sh").read_text(encoding="utf-8")
    import re
    from collections import Counter
    dups: list[str] = []
    for block in re.findall(r"\bcase\b.*?\besac\b", src, flags=re.S):
        branches = re.findall(r"^\s*(nodocker|all)\)", block, flags=re.M)
        dups += [k for k, v in Counter(branches).items() if v > 1]
    assert not dups, f"同一个 case 语句内有重复分支: {dups}"


def test_install_sh_tau2_selfcheck_runs_python():
    """τ² 自检必须真跑 venv 的 python（不是只查文件存在——那是 V325 用户踩到的假绿）。"""
    src = (_ROOT / "hashmm/evaluation/benchmarks/install.sh").read_text(encoding="utf-8")
    assert 'tau2-venv/bin/python" -c "import fastapi, litellm"' in src, \
        "τ² venv 自检退化回了文件存在性检查（假绿会回潮）"


# ─────────────────────────── ⑤ 远程来源透传（ingest → 对比表可见）───────────────────────────
def test_remote_source_flows_to_comparison(tmp_path, monkeypatch):
    """CI 回传的分数必须能在对比表看出来源（remote=True + source），兑现"透明可查"。"""
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    import importlib
    from hashmm.evaluation.benchmarks import trend
    importlib.reload(trend)   # 让 _db_path 读到新的 HASHMM_DATA_DIR
    result = {
        "id": "swebench", "name": "SWE-bench Verified", "kind": "official", "mode": "full",
        "score_pct": 42.0, "passed": 21, "total": 50, "skip": False, "comparable": True,
        "detail": "[远程CI·github_actions] Pass@1 42.0%",
        "breakdown": {"来源": "远程CI(github_actions)"}, "elapsed_ms": 3600000,
    }
    assert trend.record_run(result, meta={"kind": "official", "source": "github_actions",
                                          "remote": True, "elapsed_ms": 3600000})
    rec = trend.latest_runs().get("swebench")
    assert rec and rec["remote"] is True and rec["source"] == "github_actions"

    from hashmm.evaluation.benchmarks.vs_frontier import build_comparison
    cmp = build_comparison(trend.latest_runs())
    rows = cmp["comparable"] + cmp["trend_only"]
    row = next((r for r in rows if r["id"] == "swebench"), None)
    assert row is not None, "对比表没有该条目"
    assert row["remote"] is True and row["source"] == "github_actions"


if __name__ == "__main__":
    # 允许直接 python 跑（沙箱无 pytest 时 mini_runner 会接管）
    sys.path.insert(0, str(_ROOT))   # conftest/pytest 之外直跑也能 import hashmm
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  ✓ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
