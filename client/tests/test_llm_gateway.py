"""V103.48 — LLM 网关基座（多 provider 故障转移 + 配置档切换）单测。

对标 9Router（多 provider 智能转发 + 失败自动切换）与 CC Switch（多套 Key 配置一键切换）。
用假 llm_fn 模拟 provider 失败，验证故障转移、流式起步切换、粘滞、配置档存切删。
"""
import pytest

pytestmark = pytest.mark.unit


class _FakeFn:
    """假 provider：fail=True 时所有调用抛 429。"""
    def __init__(self, name, fail=False, toks=None):
        self.name = name
        self.fail = fail
        self.model_name = name
        self.toks = toks or ["回答", "片段"]

    def stream(self, messages, temp_override=None):
        if self.fail:
            raise Exception("429 rate limit exceeded")
        for t in self.toks:
            yield t

    def call_with_tools(self, messages, tools=None, tool_choice="auto"):
        if self.fail:
            raise Exception("503 service unavailable")
        class _M:
            content = "ok from " + self.name
            tool_calls = None
        class _C:
            message = _M()
        return _C()


def _node(name, fn):
    from hashmm.llm_gateway import ProviderNode
    n = ProviderNode(name, {})
    n._fn = fn
    return n


def test_stream_failover_primary_to_fallback():
    from hashmm.llm_gateway import FailoverLLM
    gw = FailoverLLM([_node("primary", _FakeFn("primary", fail=True)),
                      _node("fallback", _FakeFn("fallback", toks=["这是", "备援"]))])
    out = "".join(gw.stream([{"role": "user", "content": "hi"}]))
    assert out == "这是备援"


def test_call_with_tools_failover():
    from hashmm.llm_gateway import FailoverLLM
    gw = FailoverLLM([_node("p", _FakeFn("p", fail=True)),
                      _node("f", _FakeFn("f"))])
    r = gw.call_with_tools([{"role": "user", "content": "hi"}])
    assert r.message.content == "ok from f"


def test_sticky_active_node():
    """成功节点应被记住（粘滞），减少后续无谓切换。"""
    from hashmm.llm_gateway import FailoverLLM
    gw = FailoverLLM([_node("p", _FakeFn("p", fail=True)),
                      _node("f", _FakeFn("f"))])
    gw.call_with_tools([{"role": "user", "content": "x"}])
    assert gw._active_idx == 1


def test_mid_stream_break_does_not_silently_switch():
    """已吐出 token 后断开 → 不静默切换（避免答案拼接错乱），异常上抛。"""
    from hashmm.llm_gateway import FailoverLLM, ProviderNode

    class _Half:
        model_name = "half"
        def stream(self, messages, temp_override=None):
            yield "已经"
            yield "吐了"
            raise Exception("500 mid-stream")

    n1 = ProviderNode("half", {}); n1._fn = _Half()
    gw = FailoverLLM([n1, _node("backup", _FakeFn("backup"))])
    got = ""
    raised = False
    try:
        for t in gw.stream([{"role": "user", "content": "x"}]):
            got += t
    except Exception:
        raised = True
    assert got == "已经吐了" and raised


def test_failover_error_classification():
    from hashmm.llm_gateway import _is_failover_error
    assert _is_failover_error(Exception("429 too many"))
    assert _is_failover_error(Exception("Connection timeout"))
    assert _is_failover_error(Exception("503 overloaded"))
    assert not _is_failover_error(Exception("invalid json schema"))
    assert not _is_failover_error(Exception("missing required field"))


def test_profiles_crud(tmp_path, monkeypatch):
    """配置档增/查/切/删（CC Switch 的核心动作），落盘到临时路径。"""
    import hashmm.llm_gateway as G
    monkeypatch.setattr(G, "_PROFILES_PATH", tmp_path / "llm_profiles.json")

    assert G.save_profile("主用", [{"name": "ds", "provider": "deepseek",
                                    "model": "deepseek-chat", "api_key": "k1,k2",
                                    "base_url": "https://api.deepseek.com/v1"}],
                          note="主力", set_active=True)
    assert G.save_profile("离线", [{"name": "ollama", "provider": "ollama",
                                    "model": "qwen2.5:7b",
                                    "base_url": "http://localhost:11434/v1"}])
    d = G.list_profiles()
    assert d["active"] == "主用"
    assert set(d["profiles"].keys()) == {"主用", "离线"}

    assert G.switch_profile("离线")
    assert G.list_profiles()["active"] == "离线"

    assert not G.switch_profile("不存在的档")  # 切换到不存在的档应失败

    assert G.delete_profile("离线")
    assert G.list_profiles()["active"] == "主用"  # 删掉 active 会回退到剩余档


def test_build_failover_llm_empty_returns_none():
    from hashmm.llm_gateway import build_failover_llm
    # 空列表 / 全是假值（None、空 dict）→ 没有有效节点 → None
    assert build_failover_llm([]) is None
    assert build_failover_llm([None, {}]) is None
    # 至少一个非空配置 → 构建出实例
    assert build_failover_llm([{"name": "x", "model": "m"}]) is not None
