"""V332 回归测试：CI 全基准矩阵 + 双端并发 + 技能包目录命名根因修复。

本轮四件事，每件都有对应断言锁死：
  ① CI runner 接入服务器 6 个免 Docker 基准（gaia/tau2/humaneval/bfcl/kotlin/webvoyager），
     短名→registry 真实 bench_id 全量映射；别名 nodocker/all 与 workflow、install.sh 同词汇，
     三处词汇漂移由本文件的交叉断言拦截（V326 的"短名映射错→全跳过"就是漂移事故）。
  ② 桌面端 run_for_comparison 基准级并发（此前串行）：结果保序、sample 档位在工作线程内
     生效（thread-local 坑）、进度回调收敛到 total、真的用了多线程。
  ③ webvoyager 逐题并发（真浏览器模式强制串行——共享 Chromium 内核非线程安全）；
     terminal_full 总时长 HASHMM_TB_TIMEOUT 可调（CI 上 50 题不再被 2h 默认值掐死）。
  ④ 内置技能包安装目录一律用 ASCII 源目录名（中文目录名曾在 zip 跨平台传输时产生
     cp437 乱码——V332 清理的那批）；展示名仍取 SKILL.md frontmatter。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _load_runner_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "remote_bench_runner", _ROOT / "scripts/remote_bench_runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ─────────────── ① CI 基准矩阵：映射齐全 + 三处词汇防漂移 ───────────────
def test_all_ci_short_names_map_to_real_registry_ids():
    """每个 CLI 短名都必须映射到注册表里真实存在的 bench_id（V326 教训的全矩阵版）。"""
    m = _load_runner_module()
    from hashmm.evaluation.benchmarks.registry import get_benchmark
    assert m._CI_BENCHES == set(m._BENCH_ID_MAP), "短名集合与映射表键不一致"
    for short, bid in m._BENCH_ID_MAP.items():
        assert get_benchmark(bid) is not None, f"{short} 映射到不存在的 bench_id {bid}"


def test_server_six_benches_now_in_ci():
    """服务器上免 Docker 的 6 个基准必须都进了 CI 清单（本轮整合的主体）。"""
    m = _load_runner_module()
    for b in ("gaia", "tau2", "humaneval", "bfcl", "kotlin", "webvoyager"):
        assert b in m._CI_BENCHES, f"{b} 没接进 CI"
    assert m._BENCH_ID_MAP["bfcl"] == "tool_calling"
    assert m._BENCH_ID_MAP["tau2"] == "tau2_bench"
    assert m._BENCH_ID_MAP["kotlin"] == "kotlin_bench"


def test_alias_expansion_dedup_and_order():
    m = _load_runner_module()
    out = m.expand_benches(["nodocker", "gaia", "swebench"])
    assert out[0] == "humaneval" and "gaia" in out and out.count("gaia") == 1, out
    assert set(m.expand_benches(["all"])) == m._CI_BENCHES
    assert m.expand_benches(["swebench", "swebench"]) == ["swebench"]


def test_workflow_and_runner_share_vocabulary():
    """workflow 的别名展开是 bash 字符串替换，必须与 runner._ALIASES 一字不差。"""
    m = _load_runner_module()
    wf = (_ROOT / ".github/workflows/docker-benchmarks.yml").read_text(encoding="utf-8")
    for short in sorted(m._CI_BENCHES) + ["nodocker"]:
        assert short in wf, f"workflow 缺基准词汇 {short}"
    # bash 替换串必须逐词等于 _ALIASES（防止一边改了另一边忘）
    nodocker_line = next(line for line in wf.splitlines() if "//nodocker/" in line)
    expanded = nodocker_line.split("/nodocker/", 1)[1].rstrip('}"').strip('}').strip()
    assert expanded.split() == m._ALIASES["nodocker"], \
        f"workflow nodocker 展开 {expanded.split()} ≠ runner {m._ALIASES['nodocker']}"


def test_search_key_gating_is_optional_skip_not_failure():
    """gaia/webvoyager 缺搜索 key：runner 必须合成 skip 落盘、给出确切 Secret 名，
    且该路径不设 rc=1（与 webarena/osworld 的可选外部环境同语义）。"""
    m = _load_runner_module()
    assert m._NEEDS_SEARCH_KEY == {"gaia", "webvoyager"}
    assert m._NEEDS_SEARCH_KEY <= m._CI_BENCHES
    for k in ("HASHMM_SERPER_API_KEY",):
        assert k in m._SEARCH_KEY_ENVS
    old = {k: os.environ.pop(k, None) for k in m._SEARCH_KEY_ENVS}
    try:
        assert m._search_key_present() is False
        os.environ["HASHMM_SERPER_API_KEY"] = "x" * 20
        assert m._search_key_present() is True
    finally:
        os.environ.pop("HASHMM_SERPER_API_KEY", None)
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v
    src = (_ROOT / "scripts/remote_bench_runner.py").read_text(encoding="utf-8")
    assert "缺联网搜索 key" in src and "HASHMM_SERPER_API_KEY" in src
    gate = src.split("_NEEDS_SEARCH_KEY and not _search_key_present()", 1)[1]
    gate = gate.split("jobs.append", 1)[0]
    assert "rc = 1" not in gate, "缺搜索 key 被当成失败了——应是可选外部环境的正常 SKIP"
    assert "continue" in gate and "json.dumps" in gate, "skip 结果没有落盘成 Artifact"


def test_workflow_yaml_valid_and_v332_knobs():
    import yaml
    wf_text = (_ROOT / ".github/workflows/docker-benchmarks.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(wf_text)
    assert "bench" in data.get("jobs", {}), "workflow 缺 bench job"
    inputs = data[True]["workflow_dispatch"]["inputs"] if True in data else \
        data["on"]["workflow_dispatch"]["inputs"]     # PyYAML 会把 on 解析成 True
    for k in ("benches", "sample", "baseline", "bench_workers", "concurrency"):
        assert k in inputs, f"workflow 缺输入 {k}"
    assert "HASHMM_TB_TIMEOUT" in wf_text, "Terminal 超时预算没进 workflow"
    assert "actions/cache" in wf_text, "BENCH_HOME 数据集缓存丢了——每次都要重下"


# ─────────────── ② 桌面端 run_for_comparison 并发行为 ───────────────
def test_run_for_comparison_parallel_order_sample_progress():
    from hashmm.evaluation.benchmarks import runner as R
    from hashmm.evaluation.benchmarks import sample_stats as S

    ids = list(R._COMPARABLE_CAPABLE)          # tau2/gaia/humaneval/tool_calling/kotlin
    assert len(ids) >= 3
    seen_threads: set[int] = set()
    seen_sample: dict[str, int] = {}
    orig_run, orig_cands = R.run_benchmark, R.comparable_candidates

    def fake_run(bid, mode="full", llm_fn=None, limit=3):
        seen_threads.add(threading.get_ident())
        # thread-local 强制档位必须在工作线程可见（V332 的坑位断言）
        seen_sample[bid] = S.resolve_limit("gaia", 7)
        time.sleep(0.15)
        return {"id": bid, "name": bid, "kind": "official", "skip": False, "score_pct": 1.0}

    def fake_cands():
        return [{"id": b, "name": b, "runnable": True, "hint": ""} for b in ids]

    R.run_benchmark, R.comparable_candidates = fake_run, fake_cands
    progress_calls: list[tuple[int, int]] = []
    try:
        t0 = time.time()
        out = R.run_for_comparison(ids, sample="full", llm_fn=None, parallel=3,
                                   progress=lambda d, t, b: progress_calls.append((d, t)))
        elapsed = time.time() - t0
    finally:
        R.run_benchmark, R.comparable_candidates = orig_run, orig_cands

    assert [r["id"] for r in out] == ids, "并发后结果必须保序"
    assert len(seen_threads) >= 2, "parallel=3 却只用了一个线程——没并发"
    full_gaia = S.SAMPLE_PRESETS["full"]["gaia"]
    assert all(v == full_gaia for v in seen_sample.values()), \
        f"工作线程没看到强制 full 档（{seen_sample}）——thread-local 又漏设了"
    assert progress_calls and progress_calls[-1][0] == progress_calls[-1][1] == len(ids), \
        "进度回调没收敛到 done==total"
    assert elapsed < 0.15 * len(ids), f"耗时 {elapsed:.2f}s 接近串行——并发没生效"
    # 主线程档位必须被清干净（工作线程内 set/clear，不污染调用方）
    assert S.resolve_limit("gaia", 7) == S.SAMPLE_PRESETS["standard"]["gaia"]


def test_run_for_comparison_serial_fallback_and_unready_skip():
    from hashmm.evaluation.benchmarks import runner as R
    ids = R._COMPARABLE_CAPABLE[:2]
    orig_run, orig_cands = R.run_benchmark, R.comparable_candidates
    R.run_benchmark = lambda bid, mode="full", llm_fn=None, limit=3: {"id": bid, "skip": False}
    R.comparable_candidates = lambda: [
        {"id": ids[0], "name": ids[0], "runnable": True, "hint": ""},
        {"id": ids[1], "name": ids[1], "runnable": False, "hint": "未装数据"},
    ]
    try:
        out = R.run_for_comparison(ids, sample="standard", parallel=1)
    finally:
        R.run_benchmark, R.comparable_candidates = orig_run, orig_cands
    assert out[0]["id"] == ids[0] and not out[0].get("skip")
    assert out[1]["id"] == ids[1] and out[1]["skip"] and "未装数据" in out[1]["detail"]


def test_selftest_route_passes_parallel_through():
    src = (_ROOT / "hashmm/api/routes/selftest.py").read_text(encoding="utf-8")
    assert 'body.get("parallel")' in src, "路由没接收 parallel 参数"
    assert "parallel=parallel" in src, "路由没把 parallel 传给 run_for_comparison"


# ─────────────── ③ webvoyager 并发 / terminal 超时预算 ───────────────
def test_webvoyager_parallel_but_browser_serial():
    src = (_ROOT / "hashmm/evaluation/benchmarks/webvoyager.py").read_text(encoding="utf-8")
    assert "run_parallel" in src, "webvoyager 还是串行 for 循环"
    assert re.search(r"concurrency\s*=\s*None if not use_browser else 1", src), \
        "真浏览器模式必须强制串行（共享 Chromium 内核非线程安全）"


def test_terminal_full_timeout_env_tunable():
    src = (_ROOT / "hashmm/evaluation/benchmarks/terminal_full.py").read_text(encoding="utf-8")
    assert 'os.environ.get("HASHMM_TB_TIMEOUT", "7200")' in src, "TB 总时长不可调"
    assert "timeout=tb_timeout" in src, "tb run 没用上可调超时"
    assert "已达 HASHMM_TB_TIMEOUT=" in src, "超时被杀时的提示文本丢了"
    assert "{_hint}" in src, "提示没有插进 skip detail（用户看不到该调哪个变量）"


def test_bench_workers_cap_raised_with_hard_ceiling():
    m = _load_runner_module()
    src = (_ROOT / "scripts/remote_bench_runner.py").read_text(encoding="utf-8")
    assert "min(int(args.bench_workers), 6)" in src, "基准级并发上限应为 6（含硬上限防打挂 runner）"
    assert m is not None


# ─────────────── ④ 技能包目录命名根因 ───────────────
def test_builtin_skill_pack_installs_under_ascii_src_name(tmp_path=None, monkeypatch=None):
    """内置包安装目录 = 源目录名（ASCII），即使 SKILL.md 的 name 是中文；
    用户上传的包保持原逻辑（slug 允许中文）。"""
    import tempfile
    td_ctx = tempfile.TemporaryDirectory() if tmp_path is None else None
    base = Path(td_ctx.name) if td_ctx else tmp_path
    try:
        from hashmm.agent.skill_packs import SkillPackManager
        src = base / "builtin-src" / "data-analysis"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(
            "---\nname: 数据分析\ndescription: 中文展示名\n---\n正文", encoding="utf-8")
        mgr = SkillPackManager(root=base / "packs")
        p1 = mgr.install_from_dir(src, "builtin")
        assert p1.id == "data-analysis", f"内置包目录应为 ASCII 源名，实际 {p1.id}"
        assert p1.name == "数据分析", "展示名必须仍取 frontmatter（中文不受影响）"
        assert (base / "packs" / "data-analysis" / "SKILL.md").is_file()
        p2 = mgr.install_from_dir(src, "upload")       # 用户导入路径不变
        assert p2.id.startswith("数据分析"), f"用户导入应保留中文 slug，实际 {p2.id}"
    finally:
        if td_ctx:
            td_ctx.cleanup()


def test_shipped_skill_pack_dirs_are_ascii_and_registry_synced():
    """随包数据区的技能包目录已英文化，registry.json 键与目录一致且带 src 字段。"""
    import json
    root = _ROOT / "data/skill_packs"
    if not root.is_dir():
        return
    dirs = {p.name for p in root.iterdir() if p.is_dir()}
    for d in dirs:
        assert d.isascii(), f"数据区仍有非 ASCII 技能包目录：{d}（zip 跨平台会再出乱码）"
    reg = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    assert set(reg) == dirs, f"registry 键 {set(reg)} 与目录 {dirs} 不同步"
    for k, v in reg.items():
        assert v.get("src"), f"registry[{k}] 缺 src（内置播种去重依赖它）"


if __name__ == "__main__":
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
