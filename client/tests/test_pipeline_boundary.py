"""tests/test_pipeline_boundary.py — 工具管线边界行为压测（V306）。

工具循环的安全底线全靠 ToolPipeline 的守卫在"超预算/超迭代/来回打转/工具抛异常"这些边界上
正确短路。这类边界最容易藏 off-by-one 和 fail-open/closed 反向的 bug。本套件按 loop.py 里
**真实的计数自增顺序**驱动 TurnState，逐调用断言守卫裁决：
  · 执行预算：exec_calls 在 evaluate【后】自增（post-inc + `>=`）→ 恰好放行 MAX_EXEC_CALLS 次；
  · 检索预算：search_calls 在 evaluate【前】自增（pre-inc + `>`）→ 恰好放行 MAX_SEARCH_CALLS 次；
  · 连续去重 A→A、非连续震荡 A→B→A→B→A；
  · 守卫自身抛异常 → fail-open（放行，harness 故障不放大为拒绝）；
  · pre-hook 短路；权限拒绝；call_key=None 不误伤；
  · 瞬态错误分类（超时/连接/限流 → 可重试）。

纯逻辑、无外部依赖，沙箱可直接跑。
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))

from hashmm.agent import tool_pipeline as TP


def _key(name, args):
    import json
    return (name, json.dumps(args, sort_keys=True, ensure_ascii=False))


# ══════════════════════════════ 执行预算（post-increment + >=）══════════════════════════════
def test_exec_budget_allows_exactly_max():
    """模拟 loop：evaluate 在前、exec_calls += 1 在后。应恰好放行 MAX_EXEC_CALLS 次执行。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    allowed = 0
    for _ in range(TP.MAX_EXEC_CALLS + 3):
        d = p.evaluate("execute_code", {"code": "x"}, _key("execute_code", {"code": "x"}), st)
        if d is None:
            allowed += 1
            st.exec_calls += 1   # loop 在 evaluate 之后自增
        else:
            assert d.guard == "exec_budget", f"超预算应由 exec_budget 拦，实际 {d.guard}"
    assert allowed == TP.MAX_EXEC_CALLS, f"执行预算放行次数错误：{allowed} 应={TP.MAX_EXEC_CALLS}"


def test_exec_budget_denies_after_max():
    p = TP.ToolPipeline()
    st = TP.TurnState()
    st.exec_calls = TP.MAX_EXEC_CALLS   # 已达上限
    d = p.evaluate("execute_code", {}, _key("execute_code", {}), st)
    assert d is not None and d.guard == "exec_budget" and d.allow is False


# ══════════════════════════════ 检索预算（pre-increment + >）══════════════════════════════
def test_search_budget_allows_exactly_max():
    """模拟 loop：search_calls += 1 在前、evaluate 在后。应恰好放行 MAX_SEARCH_CALLS 次检索。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    allowed = 0
    for i in range(TP.MAX_SEARCH_CALLS + 3):
        st.search_calls += 1   # loop 在 evaluate 之前自增
        d = p.evaluate("web_search", {"q": f"q{i}"}, _key("web_search", {"q": f"q{i}"}), st)
        if d is None:
            allowed += 1
        else:
            assert d.guard == "search_budget", f"超检索预算应由 search_budget 拦，实际 {d.guard}"
    assert allowed == TP.MAX_SEARCH_CALLS, f"检索预算放行次数错误：{allowed} 应={TP.MAX_SEARCH_CALLS}"


def test_non_budget_tools_not_limited():
    """非预算类工具（如 create_file）不受 exec/search 预算限制。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    st.exec_calls = 999
    st.search_calls = 999
    for i in range(10):
        d = p.evaluate("create_file", {"filename": f"f{i}.py"}, _key("create_file", {"filename": f"f{i}.py"}), st)
        assert d is None, "非预算工具被误限"


# ══════════════════════════════ 去重 / 震荡 ══════════════════════════════
def test_consecutive_dedup_A_A():
    """同一调用紧跟同一调用 → 去重短路（复用上次结果，不重复执行）。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    k = _key("kb_search", {"q": "同一个"})
    st.last_call_key = k
    st.last_result_text = "上次结果内容"
    d = p.evaluate("kb_search", {"q": "同一个"}, k, st)
    assert d is not None and d.guard == "dedup"
    assert "上次结果" in (d.result or {}).get("message", "")


def test_read_write_read_not_dedup():
    """读→写→读 不应被连续去重误伤（只有相邻完全相同才拦）。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    kr = _key("read_file", {"p": "a"})
    st.last_call_key = kr
    # 中间插入一个不同调用 → last_call_key 变了
    st.last_call_key = _key("write_file", {"p": "a"})
    d = p.evaluate("read_file", {"p": "a"}, kr, st)
    assert d is None, "读写读被去重误伤"


def test_oscillation_ABAB_detected():
    """非连续来回打转 A→B→A→B→A：第 3 次出现的 A 触发震荡短路。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    kA = _key("web_search", {"q": "A"})
    kB = _key("kb_search", {"q": "B"})
    # 模拟 recent_call_keys 已经历 A,B,A,B（loop 在放行后 append）
    st.recent_call_keys = [kA, kB, kA, kB]
    d = p.evaluate("web_search", {"q": "A"}, kA, st)   # 第 3 次 A
    assert d is not None and d.guard == "oscillation", f"震荡未拦，实际 {d}"


def test_none_call_key_never_triggers():
    """call_key=None（参数无法规范化）不应触发 dedup/oscillation。"""
    p = TP.ToolPipeline()
    st = TP.TurnState()
    st.last_call_key = None
    st.recent_call_keys = [None, None, None]
    d = p.evaluate("some_tool", {}, None, st)
    assert d is None, "call_key=None 被误拦"


# ══════════════════════════════ 守卫异常 → fail-open ══════════════════════════════
def test_guard_exception_fails_open():
    """任一守卫 check 抛异常 → evaluate 放行（harness 故障绝不放大为拒绝）。"""
    class BoomGuard:
        name = "boom"

        def check(self, *a, **k):
            raise RuntimeError("guard exploded")

    p = TP.ToolPipeline(guards=[BoomGuard(), TP.ExecBudgetGuard()])
    st = TP.TurnState()
    d = p.evaluate("execute_code", {}, _key("execute_code", {}), st)
    assert d is None, "守卫异常时未 fail-open（错误地拦住了执行）"


def test_pre_hook_short_circuits():
    """pre-tool hook 返回 dict → 作为短路结果（guard='pre_hook'）。"""
    fired = {"n": 0}

    def hook(name, args):
        fired["n"] += 1
        if name == "dangerous":
            return {"status": "denied", "message": "hook 拦截"}
        return None

    TP.register_pre_tool_hook(hook)
    try:
        p = TP.ToolPipeline()
        st = TP.TurnState()
        d = p.evaluate("dangerous", {}, _key("dangerous", {}), st)
        assert d is not None and d.guard == "pre_hook"
        # 普通工具不被 hook 拦
        d2 = p.evaluate("safe", {}, _key("safe", {}), st)
        assert d2 is None
    finally:
        TP.PRE_TOOL_HOOKS.clear()


def test_pre_hook_exception_never_blocks():
    """pre-hook 抛异常绝不拦执行。"""
    def boom(name, args):
        raise ValueError("hook boom")

    TP.register_pre_tool_hook(boom)
    try:
        p = TP.ToolPipeline()
        d = p.evaluate("x", {}, _key("x", {}), TP.TurnState())
        assert d is None, "pre-hook 异常拦住了执行"
    finally:
        TP.PRE_TOOL_HOOKS.clear()


# ══════════════════════════════ 权限 ══════════════════════════════
def test_permission_denied():
    class DenyPerms:
        def check(self, name, args, user_id):
            return (False, "该工具被策略禁用")

    p = TP.ToolPipeline()
    d = p.evaluate("run_shell", {"cmd": "rm"}, _key("run_shell", {"cmd": "rm"}),
                   TP.TurnState(), permissions=DenyPerms(), user_id="u")
    assert d is not None and d.guard == "permission" and d.allow is False


def test_permission_approved_passes():
    class OkPerms:
        def check(self, name, args, user_id):
            return (True, "")

    p = TP.ToolPipeline()
    d = p.evaluate("read_file", {"p": "a"}, _key("read_file", {"p": "a"}),
                   TP.TurnState(), permissions=OkPerms(), user_id="u")
    assert d is None


# ══════════════════════════════ 瞬态错误分类 ══════════════════════════════
def test_transient_error_classification():
    assert TP.is_transient_error(TimeoutError("x")) is True
    assert TP.is_transient_error(ConnectionError("x")) is True
    assert TP.is_transient_error(Exception("Connection reset by peer")) is True
    assert TP.is_transient_error(Exception("HTTP 503 Service Unavailable")) is True
    assert TP.is_transient_error(Exception("rate limit exceeded")) is True
    assert TP.is_transient_error(ValueError("invalid argument")) is False
    assert TP.is_transient_error(KeyError("missing")) is False


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("工具管线边界压测（V306：预算/迭代/守卫异常/震荡/去重）")
    print("=" * 60)
    p = f = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
            p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}")
            f += 1
    print("=" * 60)
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())
