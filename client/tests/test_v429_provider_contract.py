"""V429 provider capability negotiation is deterministic and fail-closed."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.regression


def test_provider_contract_accepts_declared_capabilities():
    from hashmm.agent.context_engine import PROVIDER_CONTRACT, negotiate_provider_contract

    result = negotiate_provider_contract(
        {
            "provider": "openai-compatible",
            "model": "test-model",
            "wire_api": "chat_completions",
            "max_input_tokens": 32_000,
            "max_output_tokens": 4_000,
            "supports_tools": True,
            "supports_structured_output": True,
        },
        {
            "supports_tools": True,
            "supports_structured_output": True,
            "max_input_tokens": 16_000,
            "wire_api": "chat_completions",
        },
    )
    assert result["schema"] == PROVIDER_CONTRACT
    assert result["ok"] is True
    assert result["errors"] == []


def test_provider_contract_rejects_missing_or_incompatible_capabilities():
    from hashmm.agent.context_engine import (
        negotiate_provider_contract,
        require_provider_contract,
    )

    result = negotiate_provider_contract(
        {"provider": "deepseek", "wire_api": "chat_completions", "supports_tools": False},
        {"supports_tools": True, "supports_vision": True, "wire_api": "responses"},
    )
    assert result["ok"] is False
    assert {item["code"] for item in result["errors"]} == {
        "missing_capability",
        "wire_api_mismatch",
    }
    with pytest.raises(ValueError, match="provider contract rejected"):
        require_provider_contract(result["provider"], result["requirements"])


def test_context_capsule_publishes_contract_without_source_bodies():
    from hashmm.agent.context_engine import assemble_context, build_context_capsule

    bundle = assemble_context({"retrieval": lambda: "secret source body"})
    capsule = build_context_capsule(
        bundle,
        provider_profile={"provider": "test", "supports_tools": False},
        provider_requirements={"supports_tools": True},
    )
    public = capsule.public()
    assert public["provider_contract"]["ok"] is False
    assert "secret source body" not in str(public)
    assert "secret source body" in capsule.to_prompt()


def test_mesh_decision_exposes_cost_and_rounds_for_user_review():
    from hashmm.agent.mesh import choose_mesh_strategy

    decision = choose_mesh_strategy(
        goal="Compare independent sources",
        roles=[
            {"role": "research", "task": "search sources"},
            {"role": "review", "task": "verify citations"},
        ],
    )
    assert decision["decision_version"] == 2
    assert decision["parallel_net_benefit"] == round(
        decision["parallel_benefit"] - decision["coordination_cost"], 3,
    )
    assert decision["estimated_serial_rounds"] == 2
    assert decision["estimated_parallel_rounds"] in {1, 2}
