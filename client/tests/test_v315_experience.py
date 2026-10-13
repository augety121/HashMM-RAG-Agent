"""V315 回归测试：基准经验闭环（Hermes 方向）+ 脚手架指纹（模型+harness 配对口径）。

全部离线：经验库用临时 sqlite，不碰业务 episodes 库，不需要 LLM。
"""
import os
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_BENCH = _ROOT / "hashmm" / "evaluation" / "benchmarks"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    for k in ("HASHMM_BENCH_EXPERIENCE", "HASHMM_ADAPTIVE_RAG", "HASHMM_RAG_ITERATIVE"):
        monkeypatch.delenv(k, raising=False)


def _mem():
    from hashmm.evaluation.benchmarks import experience as EX
    return EX.bench_memory(db_path=Path(tempfile.mkdtemp()) / "exp.sqlite3")


# ---------------------------------------------------------------- 经验往返
def test_experience_roundtrip_carries_method():
    mem = _mem()
    mem.record(user_id="bench:terminal", query="统计 access.log 每个路径访问次数写入 results.txt",
               query_type="bench", strategy="terminal", outcome="success",
               answer="用 awk 统计后 cat 逐行核对输出格式再收工", reward=1.0)
    hint = mem.get_strategy_hint("bench:terminal", "分析 nginx 日志把每个 URL 计数写到 results.txt")
    assert "有效做法" in hint and "核对" in hint          # 可迁移的是"怎么做"，不是裸策略名
    assert "[好评]" in hint


def test_low_reward_becomes_avoidance_hint():
    mem = _mem()
    mem.record(user_id="bench:t2", query="解析日志生成结果文件", query_type="bench",
               strategy="terminal", outcome="failure",
               answer="没运行验证直接宣称完成", reward=0.0)
    hint = mem.get_strategy_hint("bench:t2", "解析日志并生成结果文件")
    assert "前车之鉴" in hint and "[待改进]" in hint


def test_insight_merges_method_with_rule_tags():
    """规则 insight（策略=X）不再独占——做法摘要在前，规则标签在括号里。"""
    mem = _mem()
    mem.record(user_id="bench:g", query="对比两个库的性能差异", query_type="bench",
               strategy="gaia", outcome="success",
               answer="先 web_search 再 fetch_url 核对原文数字", reward=1.0)
    hint = mem.get_strategy_hint("bench:g", "对比两个数据库的吞吐差异")
    assert "有效做法：先 web_search" in hint and "策略=gaia" in hint


# ---------------------------------------------------------------- 开关与隔离
def test_gate_off_yields_zero_diff(monkeypatch):
    from hashmm.evaluation.benchmarks import experience as EX
    monkeypatch.setenv("HASHMM_BENCH_EXPERIENCE", "0")
    assert EX.enabled() is False
    assert EX.hint_kwargs("terminal") == {}               # 不注入 = run_task 旧签名
    EX.record_outcome("terminal", "x", True)              # 静默不写，不抛


def test_gate_on_kwargs_shape():
    from hashmm.evaluation.benchmarks import experience as EX
    kw = EX.hint_kwargs("swebench")
    assert kw["inject_hints"] is True
    assert kw["user"] == "bench:swebench" and kw["mem"] is not None


def test_experience_db_isolated_from_business():
    from hashmm.evaluation.benchmarks import experience as EX
    db = EX._FileEpisodeDB()
    assert db.path.name == "bench-experience.sqlite3"     # 独立库，不碰业务 episodes
    assert "hashmm-benchmarks" in str(db.path) or os.environ["HASHMM_BENCH_HOME"] in str(db.path)


def test_record_outcome_uses_official_verdict():
    from hashmm.evaluation.benchmarks import experience as EX
    EX.record_outcome("gaia", "某论文被引数", True, detail="web_search 后 fetch_url 核对")
    hint = EX.bench_memory().get_strategy_hint("bench:gaia", "查论文被引次数")
    assert "有效做法" in hint and "fetch_url" in hint


# ---------------------------------------------------------------- 三基准接线
def test_three_benches_wired_with_gate():
    for f in ("terminal_local.py", "swebench_local.py", "gaia.py"):
        src = (_BENCH / f).read_text(encoding="utf-8")
        assert "from . import experience as _EXP" in src, f
        assert "_EXP.hint_kwargs(" in src and "_EXP.record_outcome(" in src, f


def test_swe_records_after_anti_hack_verdict():
    """SWE 回录必须用【反作弊后的最终判定】，不是裸 tests_pass。"""
    src = (_BENCH / "swebench_local.py").read_text(encoding="utf-8")
    assert "_EXP.record_outcome(bench_key" in src
    assert "bool(_tests_pass and touched_source(_changed))" in src


def test_terminal_records_after_official_pytest():
    src = (_BENCH / "terminal_local.py").read_text(encoding="utf-8")
    i_run = src.index("run_official_tests(work, t[\"dir\"])")
    i_rec = src.index('_EXP.record_outcome("terminal"')
    assert i_run < i_rec                                   # 判分之后才知道真相


# ---------------------------------------------------------------- 脚手架指纹
def test_harness_fingerprint_fields():
    from hashmm.evaluation.benchmarks.runner import harness_fingerprint
    fp = harness_fingerprint()
    assert fp["脚手架版本"].startswith("V") and fp["工具数"] > 0
    assert fp["自适应RAG"] == "开" and fp["迭代检索"] == "开" and fp["经验闭环"] == "开"
    assert "browser" in fp["能力模块"] and "rag" in fp["能力模块"]


def test_harness_fingerprint_reflects_switches(monkeypatch):
    from hashmm.evaluation.benchmarks.runner import harness_fingerprint
    monkeypatch.setenv("HASHMM_BENCH_EXPERIENCE", "0")
    monkeypatch.setenv("HASHMM_ADAPTIVE_RAG", "0")
    fp = harness_fingerprint()
    assert fp["经验闭环"] == "关" and fp["自适应RAG"] == "关"


def test_runner_attaches_harness_to_result():
    src = (_BENCH / "runner.py").read_text(encoding="utf-8")
    assert 'out["harness"] = harness_fingerprint(llm_fn)' in src
    assert '"脚手架"' in src                                # breakdown 里也带一行


def test_selftest_experience_card_registered():
    src = (_ROOT / "hashmm" / "api" / "routes" / "selftest.py").read_text(encoding="utf-8")
    assert "_t_bench_experience" in src and "基准经验闭环" in src
    # id 不能以 bench_ 开头——中枢按该前缀收集"外部基准勾选项"，会把自检卡误收进去
    assert '"id": "exp_loop"' in src and '"id": "bench_experience"' not in src


# ---------------------------------------------------------------- recall 缺陷回归锁
def test_chinese_text_similarity():
    from hashmm.evolution.episodic_memory import _text_sim
    assert _text_sim("解析日志生成结果文件", "解析日志并生成结果文件") > 0.7
    assert _text_sim("完全不同的问题", "解析日志生成结果文件") < 0.2
    assert _text_sim("", "x") == 0.0


def test_failed_experience_is_recallable_as_avoidance():
    """reward=0 的失败经验必须能召回（旧版被 reward 惩罚挡在准入门外，永不出现）。"""
    mem = _mem()
    mem.record(user_id="bench:t", query="解析日志生成结果文件", query_type="bench",
               strategy="terminal", outcome="failure",
               answer="没运行验证直接宣称完成", reward=0.0)
    eps = mem.recall("bench:t", "解析日志并生成结果文件")
    assert len(eps) == 1                                   # 准入门只看相关性
    hint = mem.get_strategy_hint("bench:t", "解析日志并生成结果文件")
    assert "[待改进]" in hint and "前车之鉴" in hint       # reward=0 的显式失败不再被标"中性"


def test_high_reward_still_ranks_first():
    mem = _mem()
    mem.record(user_id="bench:t", query="解析日志生成结果文件", query_type="bench",
               strategy="terminal", outcome="failure", answer="没验证就收工", reward=0.0)
    mem.record(user_id="bench:t", query="解析日志生成结果文件", query_type="bench",
               strategy="terminal", outcome="success", answer="用 awk 统计后 cat 核对", reward=1.0)
    eps = mem.recall("bench:t", "解析日志并生成结果文件", limit=2)
    assert eps[0]["reward"] == 1.0
    assert eps[0]["relevance_score"] > eps[1]["relevance_score"]
    hint = mem.get_strategy_hint("bench:t", "解析日志并生成结果文件")
    assert "[好评]" in hint and "[待改进]" in hint          # 正反两面都注入
