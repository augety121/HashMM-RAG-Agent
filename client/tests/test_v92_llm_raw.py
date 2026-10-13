"""V92 测试：/api/llm/tools 决策通道与 CU 落库接线。"""
from pathlib import Path
import types

from hashmm.api import llm_tools_core as llm_raw

ROOT = Path(__file__).resolve().parents[1]


def _fake_client(with_calls: bool):
    class _F:
        name = "run_shell"; arguments = '{"cmd":"dir"}'
    class _TC:
        id = "call_1"; type = "function"; function = _F()
    class _Msg:
        content = "我先看下目录"
        tool_calls = [_TC()] if with_calls else None
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    class _Completions:
        @staticmethod
        def create(**kw):
            assert kw["messages"] and kw["model"]
            return _Resp()
    chat = types.SimpleNamespace(completions=_Completions())
    return types.SimpleNamespace(chat=chat)


def test_run_tools_chat_with_and_without_calls():
    model = {"model_name": "fake-m", "api_key": "k", "base_url": "http://x"}
    out = llm_raw.run_tools_chat(
        [{"role": "user", "content": "hi"}], [{"type": "function"}],
        model_getter=lambda: model, client_factory=lambda m: _fake_client(True))
    assert out["role"] == "assistant" and out["content"] == "我先看下目录"
    assert out["tool_calls"][0]["function"]["name"] == "run_shell"
    assert out["tool_calls"][0]["function"]["arguments"] == '{"cmd":"dir"}'

    out2 = llm_raw.run_tools_chat(
        [{"role": "user", "content": "hi"}], [],
        model_getter=lambda: model, client_factory=lambda m: _fake_client(False))
    assert "tool_calls" not in out2


def test_run_tools_chat_no_model():
    try:
        llm_raw.run_tools_chat([{"role": "user", "content": "x"}], [],
                               model_getter=lambda: None)
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "未配置" in str(e)


def test_routes_wired():
    init_src = (ROOT / "hashmm/api/routes/__init__.py").read_text(encoding="utf-8")
    assert "llm_raw_router" in init_src
    src = (ROOT / "hashmm/api/routes/llm_raw.py").read_text(encoding="utf-8")
    assert '@router.post("/tools")' in src and '@router.post("/cu_save")' in src
    cu_seg = src.split('@router.post("/cu_save")')[1]
    assert "require_conv_access" in cu_seg, "cu_save 必须做会话属主校验"
    assert "run_in_threadpool" in src, "模型调用须走线程池避免阻塞事件循环"


if __name__ == "__main__":
    test_run_tools_chat_with_and_without_calls()
    test_run_tools_chat_no_model()
    test_routes_wired()
    print("llm_raw 自检 OK")
