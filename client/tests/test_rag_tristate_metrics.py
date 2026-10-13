"""V308 RAG 指标三态测试（修审计"无证据算通过"）。

核心契约：
  · 无引用可核验的回答 → not_evaluable（ratio=None），不得当满分算入通过率均值。
  · 质量看板对 grounded_ratio 求 AVG 时，NULL（not_evaluable）样本被 SQL 自动排除。
  · 证据覆盖率单独统计，不与接地率混算。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest


# ── groundedness 三态 ──
def test_citation_overlap_no_citation_is_not_evaluable():
    from hashmm.generation.groundedness import citation_overlap_check
    # 回答里没有 [N] 引用 → 无从核验
    r = citation_overlap_check("这是一段没有任何引用的话。", [{"text": "some source"}])
    assert r["checked"] == 0
    assert r["ratio"] is None, "无引用时 ratio 必须为 None，不能是 1.0 满分"
    assert r["status"] == "not_evaluable"


def test_citation_overlap_supported_is_passed():
    from hashmm.generation.groundedness import citation_overlap_check
    ans = "公司A营收26.7亿元[1]。"
    src = [{"text": "公司A 2024 营收 26.7 亿元 同比 增长"}]
    r = citation_overlap_check(ans, src)
    assert r["checked"] >= 1
    assert r["status"] in ("passed", "failed")
    assert r["ratio"] is not None


def test_citation_overlap_unsupported_is_failed():
    from hashmm.generation.groundedness import citation_overlap_check
    ans = "巴黎是德国的首都[1]。"
    src = [{"text": "完全无关的一段文字关于烹饪食谱"}]
    r = citation_overlap_check(ans, src)
    assert r["checked"] >= 1
    assert r["status"] == "failed"
    assert r["ratio"] == 0.0


# ── dashboard：not_evaluable 不进通过率分母 ──
@pytest.fixture
def _clean_quality_db(monkeypatch):
    d = tempfile.mkdtemp(prefix="qm_")
    monkeypatch.setenv("HASHMM_DATA_DIR", d)
    monkeypatch.setenv("HASHMM_DB_PATH", os.path.join(d, "qm.sqlite"))
    import importlib
    from hashmm.api import database as db
    importlib.reload(db)
    db.init_db()
    from hashmm.api import quality_monitor as qm
    importlib.reload(qm)
    return qm, db


def test_dashboard_excludes_not_evaluable_from_ratio(_clean_quality_db, monkeypatch):
    qm, db = _clean_quality_db
    monkeypatch.setattr(qm, "_SAMPLE_RATE", 1.0, raising=False)

    # 3 条：2 条可评估（接地 1.0 与 0.0），1 条不可评估（无引用）
    qm.record_sample("u", "q1", "事实[1]。", [{"text": "事实 就 在 这 里"}])          # 接地高
    qm.record_sample("u", "q2", "错误说法[1]。", [{"text": "毫不相干 的 内容"}])       # 接地低
    qm.record_sample("u", "q3", "没有引用的一段话。", [{"text": "some source"}])       # not_evaluable

    d = qm.dashboard(days=7)
    assert d["samples"] == 3
    assert d["evaluable_samples"] == 2, "只有 2 条可评估"
    assert d["not_evaluable_samples"] == 1, "1 条 not_evaluable 被单独计"
    # avg_grounded_ratio 只在 2 条可评估样本上求均值（≈0.5），不被第 3 条拉高/拉低
    assert d["avg_grounded_ratio"] is not None
    assert 0.0 <= d["avg_grounded_ratio"] <= 1.0
    # 证据覆盖率单独报告（3 条都有 source → 1.0）
    assert d["evidence_coverage"] == 1.0


def test_dashboard_all_not_evaluable_ratio_is_none(_clean_quality_db, monkeypatch):
    qm, db = _clean_quality_db
    monkeypatch.setattr(qm, "_SAMPLE_RATE", 1.0, raising=False)
    # 全部无引用 → 全 not_evaluable
    qm.record_sample("u", "q1", "无引用甲。", [{"text": "x"}])
    qm.record_sample("u", "q2", "无引用乙。", [{"text": "y"}])
    d = qm.dashboard(days=7)
    assert d["evaluable_samples"] == 0
    # 没有可评估样本时接地率应为 None（不可评估），而不是假装 1.0
    assert d["avg_grounded_ratio"] is None
