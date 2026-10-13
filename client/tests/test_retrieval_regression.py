"""检索主链 回归测试（needs_data）。

这是改主检索链前的安全网（铁律：动主检索链前必须先有回归测试）。
无真实 data/ 时自动跳过（不失败）。固定 query → 验证召回结构与命中。
"""
import pytest

pytestmark = [pytest.mark.regression, pytest.mark.needs_data]


def test_bridge_contract_structure():
    """kb_search_bridge 返回结构稳定：results / num_results 必有。"""
    from hashmm.retriever_bridge import kb_search_bridge
    r = kb_search_bridge({"query": "小米营收", "top_k": 3})
    assert "results" in r
    assert "num_results" in r
    assert isinstance(r["results"], list)
    # 每条结果有 content 字段（契约）
    for item in r["results"]:
        assert "content" in item


def test_recall_xiaomi_revenue():
    """回归：'小米2024营收'必须召回到结果（防止改坏检索后召回归零）。"""
    from hashmm.retriever_bridge import kb_search_bridge
    r = kb_search_bridge({"query": "小米2024营收", "top_k": 5})
    assert r["num_results"] > 0, "核心 query 召回为 0 —— 检索链可能被改坏"


def test_topk_respected():
    """top_k 上限被遵守。"""
    from hashmm.retriever_bridge import kb_search_bridge
    r = kb_search_bridge({"query": "营收", "top_k": 3})
    assert len(r["results"]) <= 3


def test_empty_query_safe():
    """空 query 不抛错，返回空结果。"""
    from hashmm.retriever_bridge import kb_search_bridge
    r = kb_search_bridge({"query": "", "top_k": 3})
    assert r["num_results"] == 0
