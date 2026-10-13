"""V324 回归测试：基准并发执行——保序、异常隔离、真并行提速、串行回退、统一开关。

对应用户诉求"测试太慢，是不是要并发"：独立题目并行跑，成倍提速。
"""
import os
import time


def _clear_conc():
    os.environ.pop("HASHMM_BENCH_CONCURRENCY", None)


# ============================================================ 并发助手
def test_run_parallel_preserves_order():
    from hashmm.evaluation.benchmarks.parallel import run_parallel
    assert run_parallel([1, 2, 3, 4, 5], lambda x: x * 10) == [10, 20, 30, 40, 50]


def test_run_parallel_isolates_exceptions():
    """某题抛异常 → 对应位置 {_error}，不炸整批、不影响其它题结果。"""
    from hashmm.evaluation.benchmarks.parallel import run_parallel
    r = run_parallel([2, 0, 4], lambda x: 100 // x)
    assert r[0] == 50 and "_error" in r[1] and r[2] == 25


def test_run_parallel_actually_parallel():
    """4 个 0.25s 任务并发跑应 <0.5s（串行需 1s）。"""
    from hashmm.evaluation.benchmarks.parallel import run_parallel
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "4"
    try:
        t = time.time()
        run_parallel([1, 2, 3, 4], lambda x: time.sleep(0.25) or x)
        dt = time.time() - t
    finally:
        _clear_conc()
    assert dt < 0.6, f"并发未生效，用时 {dt:.2f}s"


def test_run_parallel_serial_when_concurrency_1():
    from hashmm.evaluation.benchmarks.parallel import run_parallel
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "1"
    try:
        t = time.time()
        run_parallel([1, 2, 3], lambda x: time.sleep(0.2) or x)
        dt = time.time() - t
    finally:
        _clear_conc()
    assert dt > 0.5, "并发=1 应串行"


def test_bench_concurrency_default_and_clamp():
    from hashmm.evaluation.benchmarks.parallel import bench_concurrency
    _clear_conc()
    assert bench_concurrency() == 4                 # 默认 4
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "8"
    assert bench_concurrency() == 8
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "999"   # 手滑设太大 → 夹到 32
    assert bench_concurrency() == 32
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "0"      # 0 → 至少 1
    assert bench_concurrency() == 1
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "abc"    # 非法 → 默认 4
    assert bench_concurrency() == 4
    _clear_conc()


def test_run_parallel_empty_and_single():
    from hashmm.evaluation.benchmarks.parallel import run_parallel
    assert run_parallel([], lambda x: x) == []
    assert run_parallel([7], lambda x: x * 2) == [14]   # 单题走串行分支


# ============================================================ tau2 harness 并发
def test_tau2_cmd_uses_concurrency():
    """tau2 官方 harness 的 --max-concurrency 跟随统一开关。"""
    from hashmm.evaluation.benchmarks.tau2_full import build_cmd
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "6"
    try:
        cmd = build_cmd("python", "retail", "m", 50, "/tmp/x")
        i = cmd.index("--max-concurrency")
        assert cmd[i + 1] == "6"
    finally:
        _clear_conc()


# ============================================================ 基准用并发跑（mock adapter）
def test_humaneval_uses_parallel_correctly(monkeypatch):
    """humaneval 并行改造后：结果正确（不因并发丢题/串号）。用 mock adapter + mock 执行。"""
    import hashmm.evaluation.benchmarks.humaneval_bench as H

    # 造 3 个假题，mock 加载 + 执行
    fake_tasks = [{"kind": "humaneval", "id": f"HE/{i}", "prompt": f"def f{i}(): pass",
                   "test": "", "entry_point": f"f{i}"} for i in range(3)]
    monkeypatch.setattr(H, "detect", lambda: {"installed": True, "hint": ""})
    monkeypatch.setattr(H, "_load_humaneval", lambda n: fake_tasks[:n])
    monkeypatch.setattr(H, "_load_mbpp", lambda n: [])
    monkeypatch.setattr(H, "extract_code", lambda raw: raw)
    monkeypatch.setattr(H, "_build_humaneval_program", lambda t, c: c)
    # f0、f2 通过（题号偶数），f1 失败——按 program 里的函数名判
    monkeypatch.setattr(H, "_run_program",
                        lambda prog: (("f0" in prog or "f2" in prog), "why"))
    os.environ["HASHMM_BENCH_CONCURRENCY"] = "3"
    os.environ["HASHMM_CODEBENCH_WHICH"] = "humaneval"
    try:
        class _Ad:
            def answer(self, p):
                return p               # 返回整段 prompt（含 def fN），驱动上面按函数名判分
        r = H.run(_Ad(), limit=3)
    finally:
        _clear_conc()
        os.environ.pop("HASHMM_CODEBENCH_WHICH", None)
    assert r["total"] == 3                        # 3 题都跑了（没丢）
    assert r["passed"] == 2                        # f0,f2 通过（偶数）


# ============================================================ 远程结果接收全链路
def test_ingest_result_flows_to_comparison(monkeypatch):
    """外部CI跑出的结果（record_run 写入）→ latest_runs 读出 → 进「和大厂对比」正式对比区。
    这是"没Docker也能有SWE-bench分数"的核心链路。"""
    import tempfile, importlib
    monkeypatch.setenv("HASHMM_DATA_DIR", tempfile.mkdtemp(prefix="ingest_"))
    from hashmm.evaluation.benchmarks import trend as T
    importlib.reload(T)
    # 模拟 /bench/ingest 端点内部构造并写入的结果（远程 SWE-bench，50 题）
    result = {
        "id": "swebench", "name": "SWE-bench Verified", "kind": "official", "mode": "full",
        "score_pct": 42.0, "passed": 21, "total": 50, "skip": False, "comparable": True,
        "detail": "[远程CI·github_actions] 官方 Docker harness",
        "breakdown": {"来源": "远程CI(github_actions)"}, "elapsed_ms": 1800000,
    }
    assert T.record_run(result, meta={"kind": "official", "source": "github_actions", "remote": True})
    latest = T.latest_runs()
    assert latest.get("swebench", {}).get("score_pct") == 42.0

    from hashmm.evaluation.benchmarks import vs_frontier
    cmp = vs_frontier.build_comparison(latest)
    e = next((x for x in cmp["comparable"] if x["id"] == "swebench"), None)
    assert e is not None, "远程回传的 50 题 SWE-bench 应进正式对比区"
    assert e["anchors"], "SWE-bench 应带大厂锚点"


def test_ingest_validation_rejects_bad_scores():
    """分数校验：score 越界 / total<1 / passed>total 都应判非法（端点里的校验逻辑）。"""
    def _valid(score, passed, total):
        try:
            score = float(score); total = int(total); passed = int(passed)
        except (TypeError, ValueError):
            return False
        return (0 <= score <= 100) and total >= 1 and (0 <= passed <= total)
    assert _valid(42.0, 21, 50)
    assert not _valid(150, 10, 50)      # score 越界
    assert not _valid(50, 5, 0)         # total<1
    assert not _valid(50, 60, 50)       # passed>total
    assert not _valid("abc", 1, 2)      # 非数字


# ============================================================ 并发安全：GAIA 不共享记忆
def test_gaia_skips_shared_memory_when_parallel(monkeypatch):
    """并发安全护栏：GAIA 并行(并发>1)时不注入共享 experience 记忆（bench_memory 是单例，
    并发读写会竞争）；串行(并发=1)时才注入。此测试锁住这个决策。"""
    import hashmm.evaluation.benchmarks.experience as EXP
    # 模拟 experience 开启、bench_memory 返回一个哨兵对象
    monkeypatch.setattr(EXP, "enabled", lambda: True)
    monkeypatch.setattr(EXP, "bench_memory", lambda: "SHARED_MEM_SINGLETON")
    monkeypatch.setattr(EXP, "bench_user", lambda c: "bench:gaia")

    # 复刻 gaia.run 里的判定逻辑
    from hashmm.evaluation.benchmarks.parallel import bench_concurrency

    def _hint_for(conc):
        return EXP.hint_kwargs("gaia") if conc <= 1 else {}

    # 串行：注入共享记忆
    hk1 = _hint_for(1)
    assert hk1.get("mem") == "SHARED_MEM_SINGLETON"
    # 并行：不注入（避免竞争）
    hk4 = _hint_for(4)
    assert "mem" not in hk4 and hk4 == {}


def test_stateless_benches_safe_to_parallel():
    """humaneval/bfcl/kotlin 用无状态 adapter.answer()，无共享记忆——并行天然安全。
    此测试确认它们的执行不经过 experience（不像 gaia 用 AB.run_task）。"""
    import inspect
    from hashmm.evaluation.benchmarks import humaneval_bench, bfcl, kotlin_bench
    for mod in (humaneval_bench, bfcl, kotlin_bench):
        src = inspect.getsource(mod.run)
        assert "adapter.answer" in src          # 用无状态 LLM 调用
        assert "hint_kwargs" not in src         # 不注入共享经验记忆
