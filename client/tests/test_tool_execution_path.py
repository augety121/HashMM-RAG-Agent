"""S1-3 前置回归测试：锁住 tool_registry 工具执行主路径的当前行为。

在把 security_policy.evaluate() 接入执行主路径【之前】先锁住基线，确保接入后：
- 默认配置下行为完全不变（安全工具/普通工具照常执行）。
- 只有显式开启审批（HASHMM_TOOL_APPROVAL=1）且高风险工具未批准时才拒绝。
"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.api.tool_registry") is None:
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="tool_registry 未部署")]


@pytest.fixture
def reg():
    """V50 修复：本文件多个测试用 register_executor 覆写了 create_file/kb_search 等
    【真实工具】的 executor 且从不恢复——单文件跑没事，全量跑会污染后续所有依赖
    真实 executor 的测试（已实际坑到 test_v50_edit_loop）。这里快照+还原。"""
    from hashmm.api import tool_registry as TR
    _snapshot = dict(getattr(TR, "_EXECUTORS", {}) or {})
    yield TR
    reg_table = getattr(TR, "_EXECUTORS", None)
    if isinstance(reg_table, dict):
        reg_table.clear()
        reg_table.update(_snapshot)


def test_known_tool_executes(reg, clean_env):
    """注册的工具正常执行（默认配置零拦截）。"""
    reg.register_executor("_t_echo", lambda a, c: f"echo:{a.get('x')}")
    out = reg.execute_tool("_t_echo", {"x": "hi"}, {})
    assert "echo:hi" in out


def test_unknown_tool_errors(reg, clean_env):
    """未知工具返回错误字符串（不抛异常）。"""
    out = reg.execute_tool("_t_does_not_exist", {}, {})
    assert "unknown tool" in out.lower() or "Error" in out


def test_structured_preserves_dict(reg, clean_env):
    """execute_tool_structured 保留原始 dict（file 字段不被压平）。"""
    reg.register_executor("_t_doc", lambda a, c: {"status": "ok", "file": {"filename": "x.pptx"}})
    d = reg.execute_tool_structured("_t_doc", {}, {})
    assert isinstance(d, dict) and "file" in d


def test_safe_tool_not_blocked_by_default(reg, clean_env):
    """默认配置下，安全工具（kb_search 名义）执行不被拦截。"""
    reg.register_executor("kb_search", lambda a, c: "results")
    out = reg.execute_tool("kb_search", {"query": "x"}, {})
    assert "results" in out


def test_high_risk_tool_runs_without_approval_flag(reg, clean_env):
    """默认（未开审批）下，即使是高风险工具名也照常执行 —— 接入后这条必须仍成立（零变化）。"""
    reg.register_executor("_t_write", lambda a, c: "written")
    out = reg.execute_tool("_t_write", {}, {})
    assert "written" in out


def test_approval_blocks_high_risk_when_enabled(reg, monkeypatch, clean_env):
    """S1-3 接入后：开启审批 + 高风险工具未批准 → 拒绝。"""
    monkeypatch.setenv("HASHMM_TOOL_APPROVAL", "1")
    reg.register_executor("create_file", lambda a, c: "created")
    out = reg.execute_tool("create_file", {"filename": "x.txt", "content": "hi"}, {})
    assert "安全策略" in out or "审批" in out


def test_approval_allows_when_approved(reg, monkeypatch, clean_env):
    """S1-3：开启审批 + 已批准 → 执行。"""
    monkeypatch.setenv("HASHMM_TOOL_APPROVAL", "1")
    reg.register_executor("create_file", lambda a, c: "created")
    out = reg.execute_tool("create_file", {"filename": "x.txt", "content": "hi"}, {"approved": True})
    assert "created" in out


def test_safe_tool_bypasses_approval(reg, monkeypatch, clean_env):
    """S1-3：即使开启审批，安全工具（L1）仍直接放行。"""
    monkeypatch.setenv("HASHMM_TOOL_APPROVAL", "1")
    reg.register_executor("kb_search", lambda a, c: "results")
    out = reg.execute_tool("kb_search", {"query": "x"}, {})
    assert "results" in out
