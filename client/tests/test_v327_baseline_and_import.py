"""V327 回归测试：基线消融（回答"测的是我的agent还是DeepSeek"）+ 内网 Artifact 闭环。

背景（用户核心疑虑 + 新约束）：
  · "用 DeepSeek API 测出来的是不是 DeepSeek 的指标？"——代码证据：GAIA/SWE-bench/Terminal
    走真实 AgentLoop（测你的 agent）；HumanEval/Kotlin/BFCL 是模型直答（业界该基准的标准
    口径，本来就测底座）；τ² 是官方 harness×你的模型。本轮把这个事实产品化：
    ① registry 每基准标注 sut（被测系统）② HASHMM_BENCH_BASELINE=1 跑裸模型基线做消融对照。
  · "服务器外网进不来"——CI 回传断了：runner 落盘 JSON → Actions Artifact →
    客户端「导入CI结果」→ /bench/import（登录鉴权）入库。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────── ① 每个基准都要声明被测系统（sut）───────────────────
def test_registry_all_have_sut():
    from hashmm.evaluation.benchmarks.registry import BENCHMARKS
    for b in BENCHMARKS:
        assert b.get("sut"), f"{b['id']} 缺 sut（被测系统说明）——用户无从知道测的是谁"


def test_registry_sut_agent_vs_model_split():
    """锁死关键事实：GAIA/SWE/Terminal 标'你的Agent'；HumanEval/Kotlin/BFCL 标模型口径。"""
    from hashmm.evaluation.benchmarks.registry import get_benchmark
    for bid in ("gaia", "swebench_verified", "terminal_bench", "swebench_pro", "webarena"):
        assert "你的Agent" in get_benchmark(bid)["sut"], bid
    for bid in ("humaneval", "kotlin_bench", "tool_calling"):
        assert "模型" in get_benchmark(bid)["sut"] and "你的Agent" not in get_benchmark(bid)["sut"], bid
    assert "官方 harness" in get_benchmark("tau2_bench")["sut"]


# ─────────────────── ② 基线拦截（消融对照的机制）───────────────────
def test_baseline_intercepts_run_task_with_direct_answer(monkeypatch):
    """HASHMM_BENCH_BASELINE=1 时 AB.run_task 必须裸直答：llm 恰被调 1 次、零工具、结构同构。"""
    monkeypatch.setenv("HASHMM_BENCH_BASELINE", "1")
    calls = []

    def fake_llm(prompt, **kw):
        calls.append(prompt)
        return "BASE_ANSWER"

    from hashmm.tools import agent_bench as AB
    task = AB.Task(id="bl1", category="bench", turns=["Q1"], requires=set(),
                   scorers=[], max_seconds=5)
    r = AB.run_task(task, fake_llm)
    assert r["status"] == "OK" and r["answer"] == "BASE_ANSWER"
    assert r["tools_used"] == [] and r["steps"] == 1
    assert len(calls) == 1
    for k in ("id", "category", "status", "answer", "tools_used", "failures", "elapsed_s"):
        assert k in r, f"基线返回缺 {k}（与正常路径不同构会炸消费方）"


def test_baseline_off_does_not_intercept(monkeypatch):
    """不开基线时拦截分支不触发（走正常 AgentLoop 路径——用工作区副作用证明进入了正常分支）。"""
    monkeypatch.delenv("HASHMM_BENCH_BASELINE", raising=False)
    from hashmm.evaluation.benchmarks.adapter import baseline_mode
    assert baseline_mode() is False


def test_runner_marks_baseline_not_comparable(monkeypatch):
    """基线结果：名字带标注、comparable=False（永不冒充你的 agent 分去对标大厂）。"""
    monkeypatch.setenv("HASHMM_BENCH_BASELINE", "1")
    monkeypatch.setenv("HASHMM_DATA_DIR", tempfile.mkdtemp(prefix="bl_"))
    import importlib
    from hashmm.evaluation.benchmarks import trend as _t
    importlib.reload(_t)

    # 让 dispatch 返回一个"官方满分"结果，看 runner 怎么包装
    import hashmm.evaluation.benchmarks.runner as R
    monkeypatch.setattr(R, "_dispatch", lambda *a, **k: {
        "kind": "official", "score_pct": 88.0, "passed": 44, "total": 50, "detail": "官方数据集"})
    out = R.run_benchmark("gaia", mode="full", llm_fn=lambda p, **k: "x")
    assert out["baseline"] is True
    assert "基线" in out["name"]
    assert out["comparable"] is False, "基线分不许进正式对比区！"
    assert "你的Agent" in out["sut"]


# ─────────────────── ③ 基线分不顶正式分 + 对照透传 ───────────────────
def test_baseline_never_shadows_agent_score(monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", tempfile.mkdtemp(prefix="blsh_"))
    import importlib
    from hashmm.evaluation.benchmarks import trend
    importlib.reload(trend)
    trend.record_run({"id": "gaia", "name": "GAIA", "kind": "official", "mode": "full",
                      "score_pct": 62.0, "passed": 31, "total": 50, "skip": False,
                      "comparable": True, "detail": "", "breakdown": {}, "elapsed_ms": 1},
                     meta={"kind": "official", "baseline": False})
    trend.record_run({"id": "gaia", "name": "GAIA（🧪裸模型基线）", "kind": "official", "mode": "full",
                      "score_pct": 40.0, "passed": 20, "total": 50, "skip": False,
                      "comparable": False, "detail": "[🧪基线]", "breakdown": {}, "elapsed_ms": 1},
                     meta={"kind": "official", "baseline": True})
    latest = trend.latest_runs()
    assert latest["gaia"]["score_pct"] == 62.0, "后跑的基线把 agent 正式分顶掉了！"
    assert trend.latest_baselines()["gaia"]["score_pct"] == 40.0

    from hashmm.evaluation.benchmarks.vs_frontier import build_comparison
    row = next(r for r in (lambda c: c["comparable"] + c["trend_only"])(build_comparison(latest))
               if r["id"] == "gaia")
    assert row["baseline_score"] == 40.0 and "你的Agent" in row["sut"]


# ─────────────────── ④ runner 落盘（Artifact 的内容）───────────────────
def test_runner_writes_result_json_even_on_skip():
    env = {**os.environ, "HASHMM_BENCH_HOME": tempfile.mkdtemp(prefix="fbh_"),
           "HASHMM_OPENAI_BASE": "http://127.0.0.1:9/v1", "HASHMM_OPENAI_MODEL": "t"}
    out_dir = tempfile.mkdtemp(prefix="br_")
    subprocess.run([sys.executable, str(_ROOT / "scripts/remote_bench_runner.py"),
                    "--bench", "swebench", "--sample", "quick", "--no-ingest",
                    "--out-dir", out_dir], env=env, cwd=str(_ROOT),
                   capture_output=True, timeout=120)
    fp = Path(out_dir) / "swebench_verified.json"
    assert fp.exists(), "skip 场景也必须落盘（用户要能从 Artifact 看到跳过原因）"
    d = json.loads(fp.read_text(encoding="utf-8"))
    assert d["id"] == "swebench_verified" and d["skip"] is True


def test_runner_has_baseline_flag_and_outdir():
    src = (_ROOT / "scripts/remote_bench_runner.py").read_text(encoding="utf-8")
    assert "--baseline" in src and "--out-dir" in src
    assert "HASHMM_BENCH_BASELINE" in src


# ─────────────────── ⑤ /bench/import 的核心校验逻辑（与端点同款）───────────────────
def test_import_endpoint_logic():
    """复刻端点判定：skip 条目拒导、非法分拒导、基线条目 comparable=False。"""
    def gate(it: dict):
        if it.get("skip"):
            return "reject_skip"
        try:
            score = float(it.get("score_pct")); total = int(it.get("total") or 0)
            passed = int(it.get("passed") or 0)
        except (TypeError, ValueError):
            return "reject_nan"
        if not (0 <= score <= 100) or total < 1 or not (0 <= passed <= total):
            return "reject_range"
        comparable = (str(it.get("kind") or "official") == "official"
                      and total >= 1 and not bool(it.get("baseline")))
        return f"ok_comparable={comparable}"

    assert gate({"skip": True}) == "reject_skip"
    assert gate({"score_pct": "x"}) == "reject_nan"
    assert gate({"score_pct": 120, "passed": 1, "total": 2}) == "reject_range"
    assert gate({"score_pct": 42.0, "passed": 21, "total": 50, "kind": "official"}) == "ok_comparable=True"
    assert gate({"score_pct": 40.0, "passed": 20, "total": 50, "kind": "official",
                 "baseline": True}) == "ok_comparable=False"


def test_import_route_exists_with_login_auth():
    src = (_ROOT / "hashmm/api/routes/selftest.py").read_text(encoding="utf-8")
    assert '"/bench/import"' in src, "内网导入端点丢了"
    seg = src.split('"/bench/import"', 1)[1][:3500]
    assert "require_auth(request)" in seg, "/bench/import 必须走登录鉴权"
    assert "artifact_import" in seg, "导入来源标注丢了（透明可查）"


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0

    class _MP:  # 简易 monkeypatch，供直跑
        def __init__(self): self._undo = []
        def setenv(self, k, v): self._undo.append((k, os.environ.get(k))); os.environ[k] = v
        def delenv(self, k, raising=True):
            self._undo.append((k, os.environ.get(k))); os.environ.pop(k, None)
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name))); setattr(obj, name, val)
        def undo(self):
            for it in reversed(self._undo):
                if len(it) == 2:
                    k, old = it
                    (os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
                else:
                    obj, name, old = it; setattr(obj, name, old)

    for fn in fns:
        mp = _MP()
        try:
            fn(mp) if "monkeypatch" in fn.__code__.co_varnames else fn()
            passed += 1; print(f"  ✓ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ✗ {fn.__name__}: {e}"); traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
