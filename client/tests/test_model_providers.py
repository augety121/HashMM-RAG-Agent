from __future__ import annotations

from types import SimpleNamespace

import pytest

from hashmm import model_providers as providers
from hashmm.api import model_manager


def test_registry_covers_cloud_local_and_native_protocols():
    items = {item["id"]: item for item in providers.list_provider_specs()}
    required = {
        "openai", "azure_openai", "anthropic", "deepseek", "gemini",
        "qwen", "zhipu", "moonshot", "doubao", "baidu_qianfan",
        "tencent_hunyuan", "minimax", "mistral", "xai", "cohere",
        "groq", "openrouter", "siliconflow", "together", "nvidia_nim",
        "ollama", "lmstudio", "vllm", "custom",
    }
    assert required <= set(items)
    assert items["anthropic"]["wire_apis"] == [providers.WIRE_ANTHROPIC]
    assert providers.WIRE_RESPONSES in items["openai"]["wire_apis"]
    assert items["ollama"]["local"] is True


def test_normalize_alias_endpoint_and_legacy_default():
    azure = providers.normalize_model_config({
        "provider": "azure", "base_url": "https://example.openai.azure.com",
        "model_name": "deployment-a",
    })
    assert azure["provider"] == "azure_openai"
    assert azure["base_url"] == "https://example.openai.azure.com/openai/v1"

    existing_openai = providers.normalize_model_config({
        "provider": "openai", "model_name": "account-model",
    })
    assert existing_openai["wire_api"] == providers.WIRE_CHAT


def test_remote_http_and_unsupported_wire_fail_closed():
    with pytest.raises(providers.ProviderConfigError, match="HTTPS"):
        providers.normalize_model_config({
            "provider": "custom", "base_url": "http://models.example.com/v1",
            "model_name": "demo",
        })
    with pytest.raises(providers.ProviderConfigError, match="不支持协议"):
        providers.normalize_model_config({
            "provider": "deepseek", "wire_api": providers.WIRE_RESPONSES,
            "model_name": "demo",
        })


def test_request_options_keep_core_fields_reserved():
    cfg = providers.normalize_model_config({
        "provider": "openai", "wire_api": providers.WIRE_RESPONSES,
        "model_name": "account-model",
        "config": {
            "wire_api": providers.WIRE_RESPONSES,
            "extra_body": {"model": "attacker-model", "store": False},
        },
    })
    options = providers.request_options(
        cfg, messages=[{"role": "user", "content": "hello"}],
        temperature=0.8, max_tokens=321,
    )
    assert options["model"] == "account-model"
    assert options["max_output_tokens"] == 321
    assert "temperature" not in options
    assert options["extra_body"] == {"store": False}


def test_provider_capability_profile_is_model_scoped_and_fail_closed():
    cfg = providers.normalize_model_config({
        "provider": "openai", "model_name": "text-only-account-model",
        "max_tokens": 2048,
        "config": {
            "supports_tools": False,
            "supports_vision": False,
            "supports_parallel_tools": True,
            "max_input_tokens": 32768,
        },
    })
    profile = providers.provider_capability_profile(cfg)
    assert profile["schema"] == "hashmm.provider-capability.v1"
    assert profile["provider"] == "openai"
    assert profile["model"] == "text-only-account-model"
    assert profile["supports_tools"] is False
    assert profile["supports_parallel_tools"] is False
    assert profile["supports_vision"] is False
    assert profile["max_input_tokens"] == 32768
    assert profile["max_output_tokens"] == 2048

    with pytest.raises(providers.ProviderConfigError, match="工具调用能力"):
        providers.request_options(
            cfg,
            messages=[{"role": "user", "content": "search"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        )


def test_error_classification_is_actionable_and_retry_aware():
    error = SimpleNamespace(status_code=429)
    info = providers.classify_provider_error("qwen", error)
    assert info["code"] == "rate_limit"
    assert info["retryable"] is True
    assert info["provider"] == "qwen"


class _FakeResponses:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([
                SimpleNamespace(type="response.output_text.delta", delta="好"),
                SimpleNamespace(type="response.output_text.delta", delta="的"),
            ])
        usage = SimpleNamespace(input_tokens=7, output_tokens=3)
        tool = SimpleNamespace(
            type="function_call", id="item_1", call_id="call_1",
            name="kb_search", arguments='{"query":"x"}',
        )
        return SimpleNamespace(output_text="", output=[tool], status="completed", usage=usage)


def test_responses_adapter_preserves_agentloop_contract(monkeypatch):
    fake_responses = _FakeResponses()
    fake_client = SimpleNamespace(responses=fake_responses)
    monkeypatch.setattr(model_manager, "_get_client", lambda _url, _key: fake_client)
    fn = model_manager.make_llm_fn_from_model({
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "api_key": "test-only-key", "model_name": "account-model",
        "wire_api": providers.WIRE_RESPONSES, "max_tokens": 500,
    })
    assert fn is not None
    choice = fn.call_with_tools(
        [{"role": "user", "content": "search"}],
        [{"type": "function", "function": {
            "name": "kb_search", "description": "search",
            "parameters": {"type": "object", "properties": {}},
        }}],
    )
    assert choice.finish_reason == "tool_calls"
    assert choice.message.tool_calls[0].function.name == "kb_search"
    assert choice._hashmm_usage == {"prompt_tokens": 7, "completion_tokens": 3}
    sent = fake_responses.calls[-1]
    assert sent["max_output_tokens"] == 500
    assert sent["tools"][0]["name"] == "kb_search"
    assert "messages" not in sent and "input" in sent
    assert "temperature" not in sent
    assert "".join(fn.stream([{"role": "user", "content": "hello"}])) == "好的"
    assert fn.provider == "openai"
    assert fn.provider_profile["model"] == "account-model"
    assert fn.provider_profile["wire_api"] == providers.WIRE_RESPONSES
