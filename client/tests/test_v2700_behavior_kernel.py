from __future__ import annotations

from hashmm.agent.behavior_kernel import (
    BEHAVIOR_SCHEMA,
    build_behavior_contract,
    reference_profile,
    render_behavior_prompt,
)
from hashmm.release import MINIMUM_COMPATIBLE


def test_reference_profile_is_derived_and_does_not_redistribute_prompts():
    profile = reference_profile()
    assert profile["mode"] == "derived-rules-only"
    assert profile["source_prompts_embedded"] is False
    assert profile["hidden_reasoning_requested"] is False
    assert len(profile["references"]) == 3
    assert all(len(item["sha256"]) == 64 for item in profile["references"])


def test_contract_cannot_add_tools_outside_effective_set():
    contract = build_behavior_contract(
        [{"type": "function", "function": {"name": "read_file"}}, "web_search"],
        attachment_scope=["receipt-2", "receipt-1"],
    )
    assert contract["schema"] == BEHAVIOR_SCHEMA
    assert contract["effective_tools"] == ["read_file", "web_search"]
    flattened = {name for names in contract["tool_families"].values() for name in names}
    assert flattened == {"read_file", "web_search"}
    assert contract["attachment_scope"] == ["receipt-1", "receipt-2"]


def test_prompt_requires_public_evidence_not_hidden_reasoning():
    contract = build_behavior_contract(
        ["update_todo", "edit_file", "web_search"],
        attachment_scope=["upload-a"],
        approval_mode="explicit",
    )
    prompt = render_behavior_prompt(contract)
    assert "hidden chain-of-thought" in prompt
    assert "completion receipt" in prompt
    assert "Inspect the request-scoped attachments" in prompt
    assert "retrieve current evidence" in prompt
    assert "update_todo" in prompt
    assert "Approval mode: explicit" in prompt


def test_behavior_contract_is_deterministic_for_equivalent_inputs():
    left = build_behavior_contract(["web_search", "read_file", "web_search"])
    right = build_behavior_contract(["read_file", "web_search"])
    assert left == right


def test_remote_bootstrap_minimum_is_manifest_driven():
    from pathlib import Path

    route = (
        Path(__file__).resolve().parents[1]
        / "hashmm"
        / "api"
        / "routes"
        / "remote_signal.py"
    ).read_text("utf-8")
    assert MINIMUM_COMPATIBLE["desktop"] == "16.0.0"
    assert 'MINIMUM_COMPATIBLE.get("desktop")' in route
    assert '"13.2.0"' not in route


def test_release_manifest_reader_falls_back_to_package_resource(monkeypatch):
    import json
    from pathlib import Path

    import hashmm.release as release

    expected = json.dumps(release.release_manifest()).encode("utf-8")
    monkeypatch.setattr(release, "manifest_path", lambda: Path("missing-release-manifest.json"))
    monkeypatch.setattr(release.pkgutil, "get_data", lambda package, name: expected)
    assert json.loads(release._read_manifest_text())["release"] == "V2802"


def test_agent_loop_injects_contract_from_effective_tools_only():
    from hashmm.agent.loop import AgentLoop

    allowed = {
        "type": "function",
        "function": {"name": "read_file", "description": "read", "parameters": {"type": "object"}},
    }
    loop = AgentLoop(
        llm_fn=object(),
        tools=[allowed],
        user_id="behavior-user",
        conv_id="behavior-conv",
        attachment_scope=["receipt-a"],
    )
    loop._active_tools = list(loop.tools)
    system = loop._build_messages("inspect the upload", [], "")[0]["content"]
    assert "[HashMM execution contract]" in system
    assert "Effective tools: read_file" in system
    assert "Inspect the request-scoped attachments" in system
    assert loop._behavior_contract["effective_tools"] == ["read_file"]
