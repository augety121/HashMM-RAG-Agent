from types import SimpleNamespace

from hashmm import model_providers as providers
from hashmm import model_runtime as runtime
from hashmm.api import model_manager


def test_runtime_admits_smallest_execution_shape():
    direct = runtime.plan_runtime("解释一下什么是上下文窗口", requested_mode="standard")
    assert direct.execution == runtime.EXEC_DIRECT
    assert direct.user_mode == "auto"
    assert not direct.allow_parallel_agents

    work = runtime.plan_runtime(
        "分别调研三个方案，然后生成文件并验证结果",
        requested_mode="max",
    )
    assert work.execution == runtime.EXEC_MULTI_AGENT
    assert work.allow_parallel_agents
    assert work.max_parallel_agents == 3

    fast = runtime.plan_runtime("创建并运行这个程序", requested_mode="fast")
    assert fast.execution == runtime.EXEC_AGENT
    assert fast.max_iterations == 5
    assert not fast.allow_planning


def test_deep_mode_is_not_a_hard_native_reasoning_requirement():
    plan = runtime.plan_runtime("深入分析这份资料", requested_mode="deep", has_file_context=True)
    req = runtime.requirements_for(plan)
    assert req.reasoning is False
    assert runtime.requirements_for(plan, require_reasoning=True).reasoning is True


def test_context_budget_uses_declared_window_but_caps_cost_by_mode():
    profile = {"max_input_tokens": 200_000}
    assert runtime.context_input_budget(profile, user_mode="fast") == 8_000
    assert runtime.context_input_budget(profile, user_mode="auto") == 24_000
    assert runtime.context_input_budget(profile, user_mode="deep") == 64_000
    assert runtime.context_input_budget({}, user_mode="deep") == 8_000
    assert runtime.context_input_budget(
        {"max_input_tokens": 1_000}, user_mode="auto"
    ) == 2_048


def test_url_requires_governed_tool_execution_without_multi_agent():
    plan = runtime.plan_runtime(
        "Read https://example.com and summarize it",
        requested_mode="auto",
    )
    assert plan.requires_tools is True
    assert plan.execution in {runtime.EXEC_AGENT, runtime.EXEC_WORKFLOW}
    assert plan.allow_parallel_agents is False


def test_local_provider_can_be_selected_without_fake_api_key():
    assert providers.provider_credentials_ready({
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_name": "local-account-model",
        "api_key": "",
    })
    assert not providers.provider_credentials_ready({
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_name": "account-model",
        "api_key": "",
    })


def test_capability_check_is_fail_closed_for_required_features():
    result = runtime.check_compatibility(
        {"supports_tools": True, "supports_streaming": True},
        runtime.ModelRequirements(tools=True, vision=True),
    )
    assert not result.ok
    assert result.missing == ("vision",)


def test_rank_uses_explicit_tier_not_vendor_model_name():
    req = runtime.ModelRequirements(streaming=True)
    rows = [
        {
            "id": "default", "provider": "deepseek", "model_name": "opaque-a",
            "is_default": 1, "config": {"routing_tier": "auto"},
        },
        {
            "id": "deep", "provider": "openai", "model_name": "opaque-b",
            "config": {"routing_tier": "deep", "supports_reasoning": True},
            "wire_api": "responses",
        },
    ]
    ranked = runtime.rank_model_configs(rows, req, user_mode="deep")
    assert [row["id"] for row in ranked] == ["deep", "default"]


def test_fast_mode_prefers_local_when_other_routing_signals_are_equal():
    req = runtime.ModelRequirements(streaming=True)
    rows = [
        {
            "id": "remote", "provider": "openai", "model_name": "opaque-a",
            "config": {"routing_tier": "auto"},
        },
        {
            "id": "local", "provider": "ollama", "model_name": "opaque-b",
            "config": {"routing_tier": "auto"},
        },
    ]
    ranked = runtime.rank_model_configs(rows, req, user_mode="fast")
    assert [row["id"] for row in ranked] == ["local", "remote"]


def test_usage_normalizes_cache_and_reasoning_without_double_counting():
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=40,
        total_tokens=140,
        input_tokens_details=SimpleNamespace(cached_tokens=80),
        output_tokens_details=SimpleNamespace(reasoning_tokens=25),
    )
    record = runtime.normalize_usage(usage)
    assert record.total_tokens == 140
    assert record.cached_input_tokens == 80
    assert record.reasoning_tokens == 25


def test_openai_responses_reasoning_policy_is_opt_in():
    base = {
        "provider": "openai", "wire_api": "responses",
        "model_name": "account-visible-id",
    }
    no_claim = providers.request_options(
        providers.normalize_model_config(base),
        messages=[{"role": "user", "content": "hello"}],
        runtime_mode="deep",
    )
    assert "reasoning" not in no_claim

    opted = providers.normalize_model_config({
        **base,
        "config": {
            "wire_api": "responses",
            "supports_reasoning": True,
            "text_verbosity": "high",
        },
    })
    request = providers.request_options(
        opted,
        messages=[{"role": "user", "content": "hello"}],
        runtime_mode="deep",
    )
    assert request["reasoning"] == {"effort": "high"}
    assert request["text"] == {"verbosity": "high"}


def test_deepseek_reasoning_policy_and_extra_body_are_merged():
    cfg = providers.normalize_model_config({
        "provider": "deepseek",
        "model_name": "account-visible-id",
        "config": {
            "supports_reasoning": True,
            "extra_body": {"custom_trace": "enabled"},
        },
    })
    request = providers.request_options(
        cfg,
        messages=[{"role": "user", "content": "hello"}],
        runtime_mode="deep",
    )
    assert request["extra_body"]["reasoning_effort"] == "high"
    assert request["extra_body"]["custom_trace"] == "enabled"


def test_model_request_router_never_uses_another_users_credential(monkeypatch):
    rows = [
        {"id": "global", "is_default": 1, "created_by": "admin"},
        {"id": "alice", "is_default": 0, "created_by": "alice"},
        {"id": "bob", "is_default": 0, "created_by": "bob"},
    ]
    full = {
        row["id"]: {
            **row,
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model_name": row["id"],
            "api_key": "test-only",
            "config": {"routing_tier": "auto"},
        }
        for row in rows
    }
    seen = []
    monkeypatch.setattr(model_manager.db, "list_models", lambda: rows)
    monkeypatch.setattr(model_manager.db, "get_model", lambda model_id: full[model_id])
    model_manager.invalidate_model_catalog_cache()
    monkeypatch.setattr(
        model_manager,
        "make_llm_fn_from_model",
        lambda cfg: seen.append(cfg["id"]) or (lambda _prompt: "ok"),
    )
    fn, cfg, reason = model_manager.get_llm_for_request(
        runtime.ModelRequirements(streaming=True),
        preferred_model_id="bob",
        owner_subject="alice",
    )
    assert fn is not None
    assert cfg["id"] in {"global", "alice"}
    assert "bob" not in seen


def test_model_discovery_returns_only_provider_reported_ids(monkeypatch):
    page = SimpleNamespace(data=[
        SimpleNamespace(id="model-z"),
        SimpleNamespace(id="model-a"),
        SimpleNamespace(id="model-a"),
        SimpleNamespace(id=""),
    ])
    fake_client = SimpleNamespace(models=SimpleNamespace(list=lambda: page))
    monkeypatch.setattr(model_manager, "_get_client", lambda _url, _key: fake_client)
    result = model_manager.discover_provider_models({
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "test-only",
        "model_name": "_discovery_placeholder_",
    })
    assert result["ok"]
    assert result["models"] == ["model-a", "model-z"]


def test_model_discovery_does_not_fake_azure_deployment_ids():
    result = model_manager.discover_provider_models({
        "provider": "azure_openai",
        "base_url": "https://example.openai.azure.com",
        "api_key": "test-only",
        "model_name": "_discovery_placeholder_",
    })
    assert result["ok"] and result["supported"] is False
    assert result["models"] == []


def test_mainstream_provider_catalog_is_available_without_model_guessing():
    ids = {item["id"] for item in providers.list_provider_specs()}
    assert {
        "openai", "azure_openai", "anthropic", "deepseek", "gemini",
        "qwen", "zhipu", "moonshot", "doubao", "baidu_qianfan",
        "tencent_hunyuan", "minimax", "mistral", "xai", "cohere",
        "groq", "openrouter", "siliconflow", "together", "nvidia_nim",
        "aws_bedrock", "oci_genai",
        "perplexity", "fireworks", "cerebras", "github_models", "sambanova",
        "ollama", "lmstudio", "vllm", "custom",
    } <= ids


def test_hyperscaler_openai_compatible_endpoints_are_explicit_and_bounded():
    bedrock = providers.normalize_model_config({
        "provider": "bedrock",
        "base_url": "https://bedrock-mantle.us-east-1.api.aws",
        "api_key": "test-only",
        "model_name": "account-visible-id",
        "wire_api": "responses",
    })
    assert bedrock["provider"] == "aws_bedrock"
    assert bedrock["base_url"] == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert bedrock["wire_api"] == "responses"
    assert bedrock["provider_spec"].model_discovery is False

    oci = providers.normalize_model_config({
        "provider": "oci",
        "base_url": "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        "api_key": "test-only",
        "model_name": "account-visible-id",
        "wire_api": "responses",
    })
    assert oci["provider"] == "oci_genai"
    assert oci["base_url"].endswith("/openai/v1")
    assert oci["provider_spec"].model_discovery is False
