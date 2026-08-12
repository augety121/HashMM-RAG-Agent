"""Regression tests for the V338 declarative Hook execution boundary."""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def isolated_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    from hashmm import hooks

    hooks.reset_hooks()
    hooks.install_default_hooks()
    yield tmp_path
    hooks.reset_hooks()
    hooks.install_default_hooks()


def test_block_rule_runs_at_central_tool_boundary(isolated_rules):
    from hashmm import hooks
    from hashmm.agent import tool_pipeline
    from hashmm.agent import user_hooks

    assert user_hooks.save_hooks([{
        "name": "禁止命令",
        "when": {"tools": ["run_shell"]},
        "action": "block",
        "message": "此工作区不允许命令执行",
        "enabled": True,
    }])
    before_pipeline = list(tool_pipeline.PRE_TOOL_HOOKS)
    user_hooks.register()
    user_hooks.register()  # startup retries must not duplicate the policy

    decision = hooks.run_pre_tool_hooks("run_shell", {"command": "echo ok"}, {})
    assert decision.allow is False
    assert decision.hook == "user_declarative"
    assert "此工作区不允许命令执行" in decision.reason
    assert sum(name == "user_declarative" for name, _ in hooks._PRE_HOOKS) == 1
    assert tool_pipeline.PRE_TOOL_HOOKS == before_pipeline


def test_confirm_rule_is_a_deterministic_denial_until_user_retries(isolated_rules):
    from hashmm import hooks
    from hashmm.agent import user_hooks

    assert user_hooks.save_hooks([{
        "name": "写入前确认",
        "when": {"tools": ["write_file"]},
        "action": "confirm",
        "message": "需要人工确认",
    }])
    user_hooks.register()
    decision = hooks.run_pre_tool_hooks("write_file", {"path": "a.txt"}, {})
    assert decision.allow is False
    assert decision.require_approval is True
    assert "需要人工确认" in decision.reason


def test_corrupt_rule_config_fails_closed(isolated_rules):
    from hashmm import hooks
    from hashmm.agent import user_hooks

    data_dir = isolated_rules / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "user_hooks.json").write_text("{broken", encoding="utf-8")
    user_hooks.register()

    decision = hooks.run_pre_tool_hooks("read_file", {"path": "a.txt"}, {})
    assert decision.allow is False
    assert "读取失败" in decision.reason


def test_invalid_regex_is_rejected_without_replacing_existing_rules(isolated_rules):
    from hashmm.agent import user_hooks

    assert user_hooks.save_hooks([{
        "name": "现有规则",
        "when": {"tools": ["read_file"]},
        "action": "notify",
    }])
    original = json.loads((isolated_rules / "data" / "user_hooks.json").read_text(encoding="utf-8"))
    assert user_hooks.save_hooks([{
        "name": "坏正则",
        "when": {"pattern": "["},
        "action": "block",
    }]) is False
    after = json.loads((isolated_rules / "data" / "user_hooks.json").read_text(encoding="utf-8"))
    assert after == original


@pytest.mark.parametrize("pattern", ["(a+)+$", "(a|aa)+$", "a*a*a*X", "a?a?a?a?a?X", "a{1,999}X"])
def test_backtracking_prone_regex_subset_is_rejected(isolated_rules, pattern):
    from hashmm.agent import user_hooks

    assert user_hooks.save_hooks([{
        "name": "高回溯风险",
        "when": {"pattern": pattern},
        "action": "block",
    }]) is False


def test_simple_pattern_matches_nested_arguments(isolated_rules):
    from hashmm.agent import user_hooks

    assert user_hooks.save_hooks([{
        "name": "拦截危险子参数",
        "when": {"pattern": r"rm\s+-rf"},
        "action": "block",
    }])
    result = user_hooks.evaluate_user_hooks(
        "custom_tool", {"options": {"argv": ["rm", "-rf", "workspace"]}}
    )
    assert result and result["status"] == "denied"


def test_invalid_hook_decision_type_fails_closed(isolated_rules):
    from hashmm import hooks

    hooks.register_pre_hook("broken_contract", lambda *_: {"allow": True})
    decision = hooks.run_pre_tool_hooks("read_file", {}, {})
    assert decision.allow is False
    assert decision.hook == "broken_contract"


def test_tool_entry_fails_closed_when_hook_layer_crashes(isolated_rules, monkeypatch):
    from hashmm import hooks
    from hashmm.api import tool_registry

    called = []
    monkeypatch.setitem(tool_registry._EXECUTORS, "test_hook_boundary", lambda args, ctx: called.append(True))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("hook engine unavailable")

    monkeypatch.setattr(hooks, "run_pre_tool_hooks", _boom)
    result = tool_registry._execute_tool_core("test_hook_boundary", {}, {})
    assert "Hook 安全检查异常" in result
    assert called == []
