from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


pytestmark = pytest.mark.unit


def test_broker_fails_closed_when_no_isolation_backend(monkeypatch):
    from hashmm.agent import sandbox as sb

    monkeypatch.setattr(sb.sys, "platform", "win32")
    monkeypatch.setattr(sb.shutil, "which", lambda _name: None)
    monkeypatch.delenv("HASHMM_SANDBOX_DOCKER_IMAGE", raising=False)
    monkeypatch.delenv("HASHMM_ALLOW_UNSANDBOXED_EXEC", raising=False)
    broker = sb.SandboxBroker("auto")
    assert broker.status()["available"] is False
    with pytest.raises(sb.SandboxUnavailable):
        broker.run([sys.executable, "-c", "print(1)"], sb.SandboxPolicy(Path.cwd()))


def test_unsafe_compatibility_is_explicit_and_strips_credentials(tmp_path, monkeypatch):
    from hashmm.agent.sandbox import SandboxBroker, SandboxPolicy

    monkeypatch.setenv("HASHMM_ALLOW_UNSANDBOXED_EXEC", "1")
    monkeypatch.setenv("HASHMM_TEST_API_KEY", "must-not-leak")
    code = (
        "import os, pathlib; "
        "print(os.environ.get('HASHMM_TEST_API_KEY', 'absent')); "
        "pathlib.Path('inside.txt').write_text('ok')"
    )
    result = SandboxBroker("unsafe").run(
        [sys.executable, "-c", code], SandboxPolicy(tmp_path, timeout=10)
    )
    assert result.returncode == 0
    assert result.backend == "unsafe" and result.isolated is False
    assert result.stdout.strip() == "absent"
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "ok"


def test_bwrap_plan_is_read_only_root_writable_workspace_and_default_no_network(tmp_path):
    from hashmm.agent.sandbox import SandboxBroker, SandboxPolicy, _safe_environment

    policy = SandboxPolicy(tmp_path).normalized()
    argv = SandboxBroker._bwrap_command(
        "/usr/bin/bwrap", ["python", "task.py"], policy, _safe_environment(policy)
    )
    joined = "\0".join(argv)
    assert "--ro-bind\0/\0/" in joined
    assert f"--bind\0{tmp_path}\0{tmp_path}" in joined
    assert "--unshare-all" in argv
    assert "--share-net" not in argv
    assert "--clearenv" in argv


def test_docker_plan_never_pulls_and_uses_hardening_flags(tmp_path):
    from hashmm.agent.sandbox import SandboxBroker, SandboxPolicy, _safe_environment

    policy = SandboxPolicy(tmp_path).normalized()
    argv = SandboxBroker._docker_command(
        "hashmm-sandbox:locked", ["python", "/workspace/task.py"],
        policy, _safe_environment(policy),
    )
    assert "pull" not in argv
    assert argv[0:3] == ["docker", "run", "--rm"]
    assert "--read-only" in argv
    assert "no-new-privileges" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert argv[argv.index("--network") + 1] == "none"


def test_execution_scope_missing_is_rejected():
    from hashmm.agent.execution_scope import check_execution_scope

    ok, reason = check_execution_scope(
        None, "kb_search", {"query": "x"}, user_id="u", conversation_id="c"
    )
    assert ok is False
    assert "缺少" in reason

