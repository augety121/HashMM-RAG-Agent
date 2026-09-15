"""V311 基准诊断能力回归测试。

锁三件事：
1. τ² 的 HASHMM_TAU2_PROVIDER 覆盖（deepseek 等模型切 litellm 专属 provider）。
2. smoke 快诊档：每基准 1 题、报告明确标注"分数无统计意义"。
3. 错误可见性不被回退：τ²/SWE/Terminal 的真实错误必须完整进报告
   （历史教训：截 60/70/160 字把真凶砍没了，0 分基准反复跑却无法诊断）。
"""
from pathlib import Path

import pytest

from hashmm.evaluation.benchmarks.sample_stats import (SAMPLE_PRESETS,
                                                       format_breakdown,
                                                       resolve_limit)
from hashmm.evaluation.benchmarks.tau2_full import build_cmd

_BENCH_DIR = Path(__file__).resolve().parent.parent / "hashmm" / "evaluation" / "benchmarks"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HASHMM_TAU2_PROVIDER", "HASHMM_TAU2_STRATEGY",
              "HASHMM_BENCH_SAMPLE", "HASHMM_TAU2_LIMIT"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- τ² provider
def test_tau2_provider_default_openai():
    cmd = build_cmd("/py", "retail", "deepseek-v4-pro", 10, "/log")
    i = cmd.index("--model-provider")
    j = cmd.index("--user-model-provider")
    assert cmd[i + 1] == "openai" and cmd[j + 1] == "openai"


def test_tau2_provider_override(monkeypatch):
    monkeypatch.setenv("HASHMM_TAU2_PROVIDER", "deepseek")
    cmd = build_cmd("/py", "retail", "deepseek-v4-pro", 10, "/log")
    i = cmd.index("--model-provider")
    j = cmd.index("--user-model-provider")
    assert cmd[i + 1] == "deepseek" and cmd[j + 1] == "deepseek"


def test_tau2_strategy_env_still_works(monkeypatch):
    monkeypatch.setenv("HASHMM_TAU2_STRATEGY", "react")
    cmd = build_cmd("/py", "retail", "m", 5, "/log")
    assert cmd[cmd.index("--agent-strategy") + 1] == "react"


def test_tau2_provider_key_injection_present():
    """run() 里必须有 {PROVIDER}_API_KEY/_API_BASE 注入逻辑（litellm 各 provider 读各自变量）。"""
    src = (_BENCH_DIR / "tau2_full.py").read_text(encoding="utf-8")
    assert "_API_KEY" in src and "_API_BASE" in src


# ---------------------------------------------------------------- smoke 档
def test_smoke_preset_one_task_each(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_SAMPLE", "smoke")
    for k in ("tau2", "swebench", "terminal", "webarena", "webvoyager", "gaia"):
        assert resolve_limit(k, 99) == 1


def test_per_bench_limit_beats_preset(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_SAMPLE", "smoke")
    monkeypatch.setenv("HASHMM_TAU2_LIMIT", "7")
    assert resolve_limit("tau2", 99) == 7


def test_webarena_key_in_all_tiers():
    for tier in ("smoke", "quick", "standard", "full"):
        assert "webarena" in SAMPLE_PRESETS[tier]
    assert SAMPLE_PRESETS["full"]["webarena"] == 812   # 官方全集


def test_tiny_sample_marked_as_smoke():
    bd = format_breakdown(0, 1, "swebench")
    assert "无统计意义" in bd["可比性"]


def test_no_preset_falls_back_to_default():
    # V322：默认档改为 standard(50)——默认可比。无该 key 预设值时才回落到调用方 fallback。
    assert resolve_limit("tau2", 42) == 50          # tau2 有 standard 预设 → 50
    import os
    for k in ("HASHMM_ZZZ_LIMIT",):
        os.environ.pop(k, None)
    assert resolve_limit("zzz_unknown", 42) == 42   # 未知基准无预设 → 回落 fallback


# ---------------------------------------------------------------- 错误可见性锁
def test_tau2_error_visibility_locked():
    src = (_BENCH_DIR / "tau2_full.py").read_text(encoding="utf-8")
    assert "首条完整错误" in src            # 0 分时首条错误全文进 breakdown
    assert "e[:300]" in src                # 失败样例 300 字（不是 70）
    assert "harness stderr 尾部" in src     # stderr 真凶可见
    assert "e[:70]" not in src             # 旧截断不得回潮


def test_swebench_error_visibility_locked():
    src = (_BENCH_DIR / "swebench_local.py").read_text(encoding="utf-8")
    assert "msg[:250]" in src              # 环境错误 250 字
    assert "msg[:60]" not in src
    assert src.count("不再吞掉") >= 1       # 全 env_error 的 skip 分支必须带 fails
    assert '"fails": fails[:5]}' in src


def test_terminal_error_visibility_locked():
    src = (_BENCH_DIR / "terminal_local.py").read_text(encoding="utf-8")
    assert "[-800:]" in src                # pytest 输出 800 字
    assert "why[:400]" in src              # 失败原因 400 字
    assert "[-160:]" not in src and "why[:60]" not in src
