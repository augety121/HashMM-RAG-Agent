"""P2-1 模块归类（聚合入口）测试。

验证：新整洁入口可用 + 旧路径完全不破（零破坏归类）。
注：若运行的代码版本尚未包含聚合入口模块（hashmm.security / hashmm.retrieval_group），
则跳过而非失败——避免"测试版本新于被测代码版本"时误报。
"""
import importlib.util

import pytest

pytestmark = pytest.mark.unit

_HAS_SECURITY = importlib.util.find_spec("hashmm.security") is not None
_HAS_RETRIEVAL_GROUP = importlib.util.find_spec("hashmm.retrieval_group") is not None

_skip_sec = pytest.mark.skipif(not _HAS_SECURITY, reason="hashmm.security 聚合入口未部署（旧版本代码）")
_skip_ret = pytest.mark.skipif(not _HAS_RETRIEVAL_GROUP, reason="hashmm.retrieval_group 未部署（旧版本代码）")


@_skip_sec
def test_security_group_entry():
    """hashmm.security 聚合入口暴露关键安全函数。"""
    from hashmm.security import (
        detect_prompt_injection, scan_chunk_for_injection, evaluate, risk_of,
    )
    assert callable(detect_prompt_injection)
    assert callable(evaluate)


@_skip_ret
def test_retrieval_group_entry():
    """hashmm.retrieval_group 聚合入口暴露关键检索函数。"""
    from hashmm.retrieval_group import kb_search_bridge, QueryPlanner
    assert callable(kb_search_bridge)


@_skip_sec
def test_old_paths_unbroken():
    """旧 import 路径完全不破，且与新入口指向同一对象（零破坏）。"""
    from hashmm.security import evaluate as new_eval
    from hashmm.security_policy import evaluate as old_eval
    assert new_eval is old_eval

    from hashmm.security import detect_prompt_injection as new_dpi
    from hashmm.prompt_safety import detect_prompt_injection as old_dpi
    assert new_dpi is old_dpi


@_skip_sec
def test_group_modules_listing():
    """分组可列出归入的模块（便于发现）。"""
    from hashmm import security
    assert "security_policy" in security.modules()
    if _HAS_RETRIEVAL_GROUP:
        from hashmm import retrieval_group
        assert "retriever_bridge" in retrieval_group.modules()
