"""P1-3 项目指令文件 + P1-1 安全统一收口 测试。"""
import os
import tempfile

import pytest

import importlib.util as _ilu
pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.security_policy") is None:
    import pytest as _pt
    pytestmark = [pytest.mark.unit, _pt.mark.skip(reason="hashmm.security_policy 未部署（旧版本代码）")]


# ── P1-3 HASHMM.md 项目指令 ──

def test_no_instructions_zero_change(clean_env, monkeypatch, tmp_path):
    """无 HASHMM.md 时注入零变化。"""
    from hashmm import project_instructions as PI
    monkeypatch.setenv("HASHMM_PROJECT_INSTRUCTIONS", str(tmp_path / "nope.md"))
    monkeypatch.chdir(tmp_path)   # cwd 也没有
    assert PI.inject_into_system_prompt("BASE") == "BASE"


def test_instructions_injected(clean_env, monkeypatch, tmp_path):
    """有 HASHMM.md 时内容被追加进 system prompt。"""
    from hashmm import project_instructions as PI
    f = tmp_path / "rules.md"
    f.write_text("# 我的规则\n只用中文回答", encoding="utf-8")
    monkeypatch.setenv("HASHMM_PROJECT_INSTRUCTIONS", str(f))
    out = PI.inject_into_system_prompt("BASE")
    assert "BASE" in out
    assert "项目指令" in out
    assert "只用中文回答" in out


def test_instructions_truncated(clean_env, monkeypatch, tmp_path):
    """超大文件被截断，防 prompt 爆炸。"""
    from hashmm import project_instructions as PI
    f = tmp_path / "big.md"
    f.write_text("x" * 50000, encoding="utf-8")
    monkeypatch.setenv("HASHMM_PROJECT_INSTRUCTIONS", str(f))
    monkeypatch.setenv("HASHMM_PROJECT_INSTRUCTIONS_MAX", "8000")
    import importlib
    from hashmm import project_instructions as PI2
    importlib.reload(PI2)
    instr = PI2.load_project_instructions()
    assert len(instr) <= 8000


# ── P1-1 安全统一收口 ──

def test_safe_tool_auto_approved(clean_env):
    """L1：只读工具自动放行。"""
    from hashmm import security_policy as SP
    d = SP.evaluate("kb_search", {"query": "x"})
    assert d.allowed is True
    assert d.layer == "L1-safe"


def test_sandbox_blocks_dangerous_code(clean_env):
    """L2：execute_code 黑名单拦截危险代码。"""
    from hashmm import security_policy as SP
    d = SP.evaluate("execute_code", {"code": "import subprocess"})
    assert d.allowed is False
    assert d.layer == "L2-sandbox"


def test_render_disabled_rejected(clean_env):
    """L2：render 未启用时拒绝。"""
    from hashmm import security_policy as SP
    d = SP.evaluate("render_design", {"html": "<h1>x</h1>"})
    assert d.allowed is False


def test_high_risk_needs_approval(clean_env, monkeypatch):
    """L3：高风险工具开审批后未批准则拒绝、批准后通过。"""
    from hashmm import security_policy as SP
    monkeypatch.setenv("HASHMM_TOOL_APPROVAL", "1")
    denied = SP.evaluate("create_file", {"filename": "x.txt", "content": "hello world"}, approved=False)
    assert denied.allowed is False
    granted = SP.evaluate("create_file", {"filename": "x.txt", "content": "hello world"}, approved=True)
    assert granted.allowed is True


def test_risk_classification(clean_env):
    """风险分级正确。"""
    from hashmm import security_policy as SP
    assert SP.risk_of("kb_search") == "safe"
    assert SP.risk_of("execute_code") == "high"
    assert SP.risk_of("unknown_tool") == "medium"


def test_summary_three_layers(clean_env):
    """安全概览含三层（对标 Codex）。"""
    from hashmm import security_policy as SP
    s = SP.summary()
    assert len(s["layers"]) == 3
