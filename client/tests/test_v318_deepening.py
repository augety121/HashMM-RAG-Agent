"""V318 回归测试：四块深化——Context Engine 全局预算、引用归因检测、就绪度诊断。

（桌面端敏感文件读取确认在 desktop/tests-node/test_computeruse.js 中）
"""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _bench_home(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())


# ============================================================ Context Engine 全局预算
def test_global_budget_evicts_low_priority():
    from hashmm.agent.context_engine import assemble_context
    providers = {
        "profile": lambda: "画像" * 300,
        "memory": lambda: "记忆" * 400,
        "skill": lambda: "技能" * 300,
        "episodic": lambda: "经验" * 300,
        "user_model": lambda: "偏好" * 200,
        "task": lambda: "任务" * 200,
    }
    b = assemble_context(providers, global_budget=2000)
    obs = b.observability()
    assert b.total_chars <= 2000 and obs["global_evicted"]
    # 高优先级保住，低优先级被淘汰
    kept = {s["key"] for s in obs["sources"] if s["hit"]}
    assert "profile" in kept and "memory" in kept
    assert "task" in obs["evicted_sources"]


def test_global_budget_zero_no_eviction():
    from hashmm.agent.context_engine import assemble_context
    providers = {"profile": lambda: "x" * 3000, "memory": lambda: "y" * 3000}
    b = assemble_context(providers, global_budget=0)
    assert not b.observability()["global_evicted"]
    assert b.observability()["hit_count"] == 2


def test_global_budget_within_no_eviction():
    from hashmm.agent.context_engine import assemble_context
    b = assemble_context({"profile": lambda: "短", "memory": lambda: "也短"},
                         global_budget=5000)
    assert not b.observability()["global_evicted"]


def test_evicted_source_not_in_text():
    from hashmm.agent.context_engine import assemble_context
    b = assemble_context({
        "profile": lambda: "画像画像画像" * 200,
        "task": lambda: "任务内容标记XYZ" * 100,
    }, global_budget=800)
    txt = b.profile_text()
    # task 优先级最低，应被淘汰，其内容不在拼装文本
    if "task" in b.observability()["evicted_sources"]:
        assert "标记XYZ" not in txt


# ============================================================ 引用校验归因检测
def test_attribution_without_citation_is_risk():
    from hashmm.retrieval.citation_guard import check_citations
    r = check_citations("研究表明这方法有效。据权威报告，市场巨大。", n_sources=3)
    assert r.level == "risk" and r["hard_facts"] == 2


def test_attribution_with_citation_ok():
    from hashmm.retrieval.citation_guard import check_citations
    assert check_citations("研究表明这方法有效[1]。", 3).ok


def test_english_attribution():
    from hashmm.retrieval.citation_guard import check_citations
    assert check_citations("Studies show this improves performance.", 3).level == "risk"
    assert check_citations("According to reports, growth is strong.", 3).level == "risk"


def test_ranking_hard_fact():
    from hashmm.retrieval.citation_guard import check_citations
    r = check_citations("该公司排名第一，遥遥领先。", n_sources=2)
    assert r["hard_facts"] >= 1


def test_existing_number_detection_intact():
    from hashmm.retrieval.citation_guard import check_citations
    assert check_citations("营收6600亿元[1]，利润率32%[2]。", 3).ok
    assert check_citations("营收增长35%，用户2.3亿人。", 3).level == "risk"
    assert check_citations("这家公司主营游戏，地位稳固。", 3).ok  # 纯定性不误报


# ============================================================ 就绪度诊断
def test_readiness_four_states():
    from hashmm.evaluation.benchmarks.vs_frontier import readiness_diagnosis
    latest = {
        "tau2": {"score_pct": 62.0, "passed": 31, "total": 50},   # 可比
        "gaia": {"score_pct": 40.0, "passed": 4, "total": 10},    # 样本不足
    }
    by_id = {d["id"]: d for d in readiness_diagnosis(latest)}
    assert by_id["tau2"]["state"] == "comparable"
    assert by_id["gaia"]["state"] == "need_more_samples"
    assert by_id["swebench"]["state"] == "not_run"
    assert by_id["mcp_atlas"]["state"] == "need_env"


def test_readiness_all_runnable_parity_consistent():
    from hashmm.evaluation.benchmarks.vs_frontier import readiness_diagnosis
    diag = readiness_diagnosis({})
    runnable = [d for d in diag if d["state"] != "need_env"]
    assert all(d["parity_consistent"] for d in runnable)
    assert len(runnable) >= 7
