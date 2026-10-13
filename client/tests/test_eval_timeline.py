"""Eval 保真 + Agent 运行时间线 测试。

覆盖：held-out 稳定切分、overfit_gap 计算、timeline phase 映射、step_id 单调。
"""
import pytest

pytestmark = pytest.mark.unit


def test_holdout_split_is_stable():
    """held-out 切分用稳定哈希：同一 case_id 永远落同一桶（可复现）。"""
    from hashmm.evaluation import holdout
    b1 = holdout._stable_bucket("case_42")
    b2 = holdout._stable_bucket("case_42")
    assert b1 == b2
    assert 0 <= b1 < 1000


def test_holdout_split_distributes():
    """不同 case 分散到不同桶（不是全挤一个）。"""
    from hashmm.evaluation import holdout
    buckets = {holdout._stable_bucket(f"case_{i}") for i in range(50)}
    assert len(buckets) > 10   # 50 个 case 至少落进 >10 个桶


def test_timeline_phase_mapping():
    """node → phase 映射稳定，未知 node 归 other（永不抛错）。"""
    from hashmm.api import run_timeline as RT
    # 已知 node 应映射到 5 大阶段之一
    p = RT.phase_of("retrieve")
    assert isinstance(p, str) and p
    # 未知 node 归 other
    assert RT.phase_of("__nonexistent_node__") == "other"


def test_timeline_step_id_monotonic():
    """step_id 在一次请求内单调递增。"""
    from hashmm.api import run_timeline as RT
    RT.reset()
    ids = [RT._next_step_id() for _ in range(5)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 5   # 严格递增无重复
