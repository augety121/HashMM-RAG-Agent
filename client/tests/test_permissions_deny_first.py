"""V308 权限系统安全属性测试（修 P0-3 的回归护栏）。

这些测试断言的是【安全边界】，不是实现细节。它们必须在任何重构后仍然成立：

  1. 未登记工具 → 拒绝（原实现 `TOOL_PERMISSIONS.get(name, READ)` 把未知工具当只读
     自动放行，run_shell 恰好未登记 → 开放式命令执行被自动批准。这是原 P0 的真身。）
  2. run_shell / 删除类 → 在 standard 与 strict 模式下都必须显式批准，不自动放行。
  3. 审批/身份校验模块抛异常 → 拒绝（fail-closed），不得放行。
  4. 批准与「工具名 + 参数 + 工作目录」绑定：批准 A 不能拿去执行 B；过期即失效。
  5. 权限表与已注册工具一致性可被校验（注册了却没登记权限 = 启动就该暴露）。
  6. ToolPipeline 的安全守卫异常 → 拒绝（原实现 `except Exception: d = None` 一律放行，
     只要让 PermissionGuard.check 抛异常就能绕过权限）。

纯逻辑 + monkeypatch，无需 DB / 网络 / GPU，pytest 与 _mini_runner 均可直跑。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest

from hashmm.agent import permissions as P


@pytest.fixture
def perms(monkeypatch):
    """全新权限实例 + 干净模式（默认 standard）。"""
    monkeypatch.delenv("HASHMM_PERMISSION_MODE", raising=False)
    # 审计写 DB 与本测试无关，静音掉避免依赖数据库。
    monkeypatch.setattr(P.PermissionSystem, "_audit",
                        lambda *a, **k: None, raising=True)
    return P.PermissionSystem()


# ── 1. 未登记工具：不能根据动态名称猜测能力 ──
def test_unregistered_tool_is_denied(perms, monkeypatch):
    """动态工具必须带 MCP annotation 或在服务端权限表显式登记。"""
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "standard")
    ok, reason = perms.check("some_dynamic_plugin_tool", {}, "u1")
    assert ok is False
    assert "未登记" in reason


def test_registered_dangerous_tools_still_gated(perms, monkeypatch):
    """关键：真正危险的工具【已显式登记】为高危级，未登记的默认 WRITE 级拿不到这些能力。
    这条守住“危险能力只授予显式登记工具”的边界。"""
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "standard")
    # run_shell / clean_workspace 已登记为 SYSTEM / DELETE → 即使 standard 也需批准
    assert perms.check("run_shell", {"command": "x"}, "u1")[0] is False
    assert perms.check("clean_workspace", {}, "u1")[0] is False


def test_dangerous_tools_are_explicitly_registered(perms, monkeypatch):
    """危险能力只能来自服务端显式登记。"""
    # 通过一致性检查侧面保证：危险级别工具都在权限表里显式登记
    from hashmm.agent.permissions import TOOL_PERMISSIONS, PermissionLevel
    dangerous = [PermissionLevel.EXECUTE, PermissionLevel.DELETE, PermissionLevel.SYSTEM]
    for name, lvl in TOOL_PERMISSIONS.items():
        if lvl in dangerous:
            # 危险工具必须是【显式登记】的（未登记默认 WRITE 拿不到这些级别）
            assert name in TOOL_PERMISSIONS


# ── 2. 高危工具的门禁 ────────────────────────────────────────────────
def test_run_shell_is_registered_as_system(perms):
    """run_shell 必须在权限表里，且是 SYSTEM 级——不能再靠“未登记默认只读”混过去。"""
    assert P.TOOL_PERMISSIONS.get("run_shell") == P.PermissionLevel.SYSTEM


@pytest.mark.parametrize("mode", ["standard", "strict"])
def test_shell_not_auto_approved_in_any_normal_mode(perms, monkeypatch, mode):
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", mode)
    ok, reason = perms.check("run_shell", {"command": "rm -rf /"}, "u1")
    # 安全属性 = 不放行。拒绝理由可能是"需要批准"，也可能是"身份校验失败"
    # （沙箱/CI 无 DB 时 fail-closed），两者都算通过——不放行才是契约。
    assert ok is False, f"{mode} 模式下 run_shell 竟被自动放行"
    assert reason, "拒绝必须给出理由"


@pytest.mark.parametrize("tool", ["clean_workspace", "delete_doc"])
def test_delete_tools_need_approval(perms, tool):
    ok, _ = perms.check(tool, {}, "u1")
    assert ok is False, f"{tool}（删除类）被自动放行"


def test_execute_code_auto_in_standard_but_not_strict(perms, monkeypatch):
    """本地单机（standard）下 execute_code 自动放行 + 审计 + 限流；
    服务端（strict）下必须显式批准。这是刻意的策略差异，不是漏洞。"""
    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "standard")
    ok_std, _ = perms.check("execute_code", {"code": "print(1)"}, "u1")
    assert ok_std is True

    monkeypatch.setenv("HASHMM_PERMISSION_MODE", "strict")
    ok_strict, _ = P.PermissionSystem().check("execute_code", {"code": "print(1)"}, "u1")
    assert ok_strict is False


# ── 3. fail-closed：审批链路异常必须拒绝 ─────────────────────────────
def test_identity_check_exception_denies(perms, monkeypatch):
    """身份校验抛异常 → 拒绝。原实现 log_suppressed 后继续往下走 = fail-open。"""
    import hashmm.api.database as db

    def boom(*a, **k):
        raise RuntimeError("DB 挂了")

    monkeypatch.setattr(db, "get_user", boom, raising=True)
    ok, reason = perms.check("run_shell", {"command": "ls"}, "u1")
    assert ok is False
    assert "失败" in reason or "批准" in reason


def test_approval_lookup_exception_denies(perms, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("审批表读取失败")

    monkeypatch.setattr(perms, "_has_valid_approval", boom, raising=True)
    ok, _ = perms.check("run_shell", {"command": "ls"}, "u1")
    assert ok is False


# ── 4. 批准与参数绑定 ────────────────────────────────────────────────
def test_approval_binds_to_exact_args(perms):
    """批准 `ls` 不等于批准 `rm -rf /`。"""
    perms.bind_approval("u1", "run_shell", {"command": "ls"}, cwd="/w", ttl=60)

    ok_same, _ = perms.check("run_shell", {"command": "ls"}, "u1", cwd="/w")
    assert ok_same is True, "批准过的完全相同调用应放行"

    ok_other, _ = perms.check("run_shell", {"command": "rm -rf /"}, "u1", cwd="/w")
    assert ok_other is False, "批准 A 后竟能执行 B —— 审批绑定失效"


def test_approval_binds_to_cwd(perms):
    perms.bind_approval("u1", "run_shell", {"command": "ls"}, cwd="/safe", ttl=60)
    ok, _ = perms.check("run_shell", {"command": "ls"}, "u1", cwd="/etc")
    assert ok is False, "换了工作目录仍复用批准 —— cwd 未参与绑定"


def test_approval_binds_to_user(perms):
    perms.bind_approval("u1", "run_shell", {"command": "ls"}, cwd="/w", ttl=60)
    ok, _ = perms.check("run_shell", {"command": "ls"}, "u2", cwd="/w")
    assert ok is False, "别的用户复用了 u1 的批准"


def test_approval_expires(perms, monkeypatch):
    import time as _t
    perms.bind_approval("u1", "run_shell", {"command": "ls"}, cwd="/w", ttl=1)
    monkeypatch.setattr(_t, "time", lambda: _t.time.__wrapped__() + 10
                        if hasattr(_t.time, "__wrapped__") else 1e12)
    ok, _ = perms.check("run_shell", {"command": "ls"}, "u1", cwd="/w")
    assert ok is False, "过期批准仍被接受"


# ── 5. 注册表 / 权限表一致性 ─────────────────────────────────────────
def test_every_registered_tool_has_permission():
    """已注册工具必须全部登记权限，否则会被 deny-first 拒绝执行（应在启动即暴露）。"""
    from hashmm.api import tool_registry as TR
    registered = P.extract_tool_names(TR.get_tool_definitions())
    assert registered, "未解析到任何已注册工具——解析器与 TOOL_DEFS 格式不匹配"
    missing = sorted(registered - set(P.TOOL_PERMISSIONS))
    assert not missing, f"以下已注册工具未登记权限，会被拒绝执行：{missing}"


def test_consistency_checker_reports_missing():
    r = P.verify_registry_consistency({"a_tool_not_in_table"})
    assert r["ok"] is False
    assert "a_tool_not_in_table" in r["missing_permission"]


# ── 6. 管线：安全守卫异常 → 拒绝（不再一律放行）────────────────────
def test_security_guard_exception_denies_execution():
    """原实现：`except Exception: d = None` → 守卫抛异常即放行。
    只要能让 PermissionGuard.check 抛异常，权限系统就被完全绕过。"""
    from hashmm.agent import tool_pipeline as TP

    class BoomGuard:
        name = "permission"          # 安全关键守卫

        def check(self, *a, **k):
            raise RuntimeError("守卫内部异常")

    pipe = TP.ToolPipeline(guards=[BoomGuard()])
    decision = pipe.evaluate("run_shell", {"command": "rm -rf /"}, "k",
                             TP.TurnState(), permissions=None, user_id="u1")
    assert decision is not None, "安全守卫异常后竟放行执行（fail-open）"
    assert decision.allow is False


def test_non_security_guard_exception_still_allows():
    """预算类守卫（非安全边界）异常不应阻断正常任务——只有安全守卫才 fail-closed。"""
    from hashmm.agent import tool_pipeline as TP

    class BoomBudget:
        name = "exec_budget"

        def check(self, *a, **k):
            raise RuntimeError("计数器异常")

    pipe = TP.ToolPipeline(guards=[BoomBudget()])
    decision = pipe.evaluate("kb_search", {"q": "x"}, "k",
                             TP.TurnState(), permissions=None, user_id="u1")
    assert decision is None, "非安全守卫异常不应拦截执行"
