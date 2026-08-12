"""V390: causal evidence fabric joins context, actions and durable work."""
from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.unit


def _receipt(*, tool: str = "create_file", idem: str = "run-1:call-1") -> dict:
    from hashmm.agent.execution_receipt import build_execution_receipt

    return build_execution_receipt(
        run_id="run-1",
        call_id="call-1",
        tool_name=tool,
        arguments={
            "path": "C:/private/customer/result.md",
            "content": "credential-like private body",
        },
        result={
            "status": "ok",
            "file": {
                "filename": "C:/private/customer/result.md",
                "content_hash": "sha256:artifact",
            },
        },
        started_at=10,
        finished_at=10.25,
        execution_scope={"scope_id": "scope-1"},
        side_effect={
            "class": "local_write",
            "external": False,
            "reversible": True,
            "compensation": "restore_workspace_revision",
        },
        artifacts=[{
            "filename": "C:/private/customer/result.md",
            "content_hash": "sha256:artifact",
        }],
        idempotency_key=idem,
    )


def test_execution_receipt_is_content_addressed_bounded_and_argument_free():
    from hashmm.agent.execution_receipt import (
        SCHEMA,
        build_execution_receipt,
        validate_execution_receipt,
    )

    receipt = _receipt()
    encoded = json.dumps(receipt, ensure_ascii=False)

    assert receipt["schema"] == SCHEMA
    assert validate_execution_receipt(receipt) == {"valid": True, "errors": []}
    assert "C:/private/customer" not in encoded
    assert "credential-like private body" not in encoded
    assert receipt["action"]["arguments_hash"]
    assert receipt["artifacts"][0]["name"] == "result.md"
    assert receipt["integrity"]["raw_arguments_included"] is False

    tampered = json.loads(encoded)
    tampered["outcome"]["status"] = "failed"
    assert "content_hash" in validate_execution_receipt(tampered)["errors"]

    unsafe_external = build_execution_receipt(
        run_id="run-1",
        call_id="call-2",
        tool_name="send_email",
        arguments={"to": "private@example.test"},
        result={"status": "ok"},
        side_effect={"class": "external_write", "external": True, "reversible": False},
    )
    assert "idempotency_ref" in validate_execution_receipt(unsafe_external)["errors"]


def test_context_capsule_keeps_bodies_transient_but_reproducible():
    from hashmm.agent.context_engine import (
        CAPSULE_CONTRACT,
        assemble_context,
        build_context_capsule,
    )

    bundle = assemble_context(
        {
            "profile": lambda: "Prefer concise answers",
            "retrieval": lambda: "private retrieved body",
        },
        order=["profile", "retrieval"],
        global_budget=2_000,
    )
    first = build_context_capsule(
        bundle,
        active_goal="Verify the report",
        criteria=[{"check_id": "citations", "label": "Citations resolve"}],
        provider_profile={
            "provider": "openai-compatible",
            "model": "test-model",
            "max_input_tokens": 32_000,
            "supports_tools": True,
        },
        conversation_revision="message-7",
    )
    second = build_context_capsule(
        bundle,
        active_goal="Verify the report",
        criteria=[{"check_id": "citations", "label": "Citations resolve"}],
        provider_profile={
            "provider": "openai-compatible",
            "model": "test-model",
            "max_input_tokens": 32_000,
            "supports_tools": True,
        },
        conversation_revision="message-7",
    )

    public = first.public()
    assert public["schema"] == CAPSULE_CONTRACT
    assert public["fingerprint"] == second.public()["fingerprint"]
    assert public["source_bodies_included"] is False
    assert "private retrieved body" not in json.dumps(public, ensure_ascii=False)
    assert "private retrieved body" in first.to_prompt()
    assert {item["trust"] for item in public["sections"]} >= {
        "owner_instruction", "untrusted_data",
    }


def test_changed_source_selectively_invalidates_downstream_claims():
    from hashmm.agent.causal_work_graph import build_causal_work_graph
    from hashmm.agent.task_evidence_graph import build_task_evidence_graph

    evidence = build_task_evidence_graph(
        run_id="run-causal",
        goal="Produce a grounded answer",
        sources=[{
            "id": 1, "source_id": "source-1", "chunk_id": "chunk-1",
            "doc_id": "doc-1", "filename": "facts.md",
        }],
        groundings={
            "claims": [{
                "id": "claim-1",
                "claim_span": {"text": "Revenue increased"},
                "status": "supported",
                "citations": [1],
                "evidence": [{"source_index": 1}],
            }],
        },
    )
    first = build_causal_work_graph(
        run_id="run-causal",
        evidence_graph=evidence,
        source_snapshots=[{
            "source_id": "source-1", "chunk_id": "chunk-1",
            "doc_id": "doc-1", "filename": "facts.md", "text": "version A",
        }],
        observed_at=100,
    )
    second = build_causal_work_graph(
        run_id="run-causal",
        evidence_graph=evidence,
        source_snapshots=[{
            "source_id": "source-1", "chunk_id": "chunk-1",
            "doc_id": "doc-1", "filename": "facts.md", "text": "version B",
        }],
        previous_graph=first,
        observed_at=200,
    )

    stale = {
        item["kind"] for item in second["nodes"] if item["status"] == "stale"
    }
    assert second["status"] == "stale"
    assert second["invalidation"]["changed_semantic_keys"] == [
        "source_snapshot:source-1",
    ]
    assert {"source", "claim", "goal"} <= stale
    assert second["integrity"]["model_inferred_edges"] == 0


def test_manifest_completion_gate_and_cross_device_projection_share_receipts():
    from hashmm.agent.work_runtime import project_run_manifest
    from hashmm.evaluation.run_manifest import build_run_manifest

    receipt = _receipt()
    manifest = build_run_manifest(
        run_id="run-1",
        task_type="code_task",
        execution_mode="agent_loop",
        user_goal="Create and verify a report",
        answer_text="Delivered result.md",
        stop_reason="completed",
        plan_items=[{"text": "Create report", "status": "completed"}],
        tool_steps=[{
            "id": "call-1", "call_id": "call-1",
            "tool": "create_file", "status": "done",
            "receipt": receipt,
        }],
        execution_receipts=[receipt],
        artifacts=[{"filename": "result.md", "exists": True}],
        context_lifecycle={
            "contract": "hashmm.context-engine.v2",
            "generation": 2,
            "context_capsule": {
                "schema": "hashmm.context-capsule.v1",
                "fingerprint": "capsule-1",
                "generation": 2,
                "sections": [],
                "rendered_hash": "rendered-1",
                "rendered_chars": 20,
                "source_bodies_included": False,
            },
        },
    )
    projected = project_run_manifest(manifest)
    encoded = json.dumps(projected, ensure_ascii=False)

    assert manifest["completion_gate"]["status"] == "verified"
    assert manifest["causal_work_graph"]["summary"]["receipts"] == 1
    assert projected["execution_receipts"][0]["receipt_id"] == receipt["receipt_id"]
    assert projected["context_lifecycle"]["context_capsule"]["fingerprint"] == "capsule-1"
    assert "credential-like private body" not in encoded
    assert "C:/private/customer" not in encoded

    invalid = json.loads(json.dumps(receipt))
    invalid["outcome"]["status"] = "failed"
    rejected = build_run_manifest(
        run_id="run-2",
        task_type="code_task",
        execution_mode="agent_loop",
        user_goal="Create and verify a report",
        answer_text="Claimed complete",
        stop_reason="completed",
        plan_items=[{"text": "Create report", "status": "completed"}],
        tool_steps=[{"tool": "create_file", "status": "done", "receipt": invalid}],
        execution_receipts=[invalid],
    )
    assert rejected["completion_gate"]["status"] != "verified"
    assert rejected["causal_work_graph"]["status"] == "invalid"


def test_durable_loop_requires_and_persists_one_receipt_per_tool_action():
    from hashmm.agent import loop_engine
    from hashmm.agent.task_evidence_graph import build_task_evidence_graph
    from hashmm.agent.task_method import build_task_contract

    receipt = _receipt(tool="read_file")
    contract = loop_engine._ensure_execution_receipt_criterion(build_task_contract(
        user_goal="Inspect the real file",
        task_type="long_task",
        execution_mode="durable_agent_loop",
        requires_plan=False,
    ))
    loop = {
        "id": "run-1",
        "task_contract": contract,
        "execution_receipts": [],
        "execution_scope": {"scope_id": "scope-1"},
    }
    attempt = {
        "result": "Observed the file",
        "tools": [{"name": "read_file", "status": "done", "receipt": receipt}],
        "execution_receipts": [receipt],
        "files": [],
        "todo": [],
    }
    verified = loop_engine._verify_task_contract(
        loop, attempt, score=90, evidence_ok=True,
    )
    receipt_check = next(
        item for item in verified["checks"]
        if item["check_id"] == "execution_receipt_integrity"
    )
    assert receipt_check["status"] == "passed"

    evidence = build_task_evidence_graph(
        run_id="run-1",
        goal="Inspect the real file",
        task_contract=contract,
        execution_scope=loop["execution_scope"],
        tool_steps=attempt["tools"],
        verification=verified,
    )
    causal = loop_engine._loop_causal_work_graph(
        loop, evidence, attempt, previous=None,
    )
    assert causal["status"] == "ready"
    assert causal["summary"]["receipts"] == 1

    missing = dict(attempt)
    missing["execution_receipts"] = []
    refused = loop_engine._verify_task_contract(
        loop, missing, score=90, evidence_ok=True,
    )
    assert "execution_receipt_integrity" in refused["failed_required"]


def test_manifest_fails_closed_when_tool_steps_have_no_or_unbound_receipts():
    from hashmm.evaluation.run_manifest import build_run_manifest

    missing = build_run_manifest(
        run_id="run-missing-receipt",
        task_type="direct_task",
        execution_mode="react_agent",
        user_goal="Inspect the file",
        answer_text="Inspection finished",
        stop_reason="completed",
        tool_steps=[{
            "call_id": "call-missing",
            "tool_name": "read_file",
            "tool": "READ",
            "status": "done",
        }],
    )
    receipt_check = next(
        item for item in missing["verification"]["checks"]
        if item["id"] == "execution_receipt_integrity"
    )
    assert receipt_check["status"] == "failed"
    assert receipt_check["evidence"]["missing"] == 1
    assert missing["completion_gate"]["status"] != "verified"

    wrong_tool = _receipt(tool="read_file")
    mismatched = build_run_manifest(
        run_id="run-1",
        task_type="direct_task",
        execution_mode="react_agent",
        user_goal="Create the file",
        answer_text="File created",
        stop_reason="completed",
        tool_steps=[{
            "call_id": "call-1",
            "tool_name": "create_file",
            "tool": "FILE",
            "status": "done",
            "receipt": wrong_tool,
        }],
        execution_receipts=[wrong_tool],
    )
    mismatch_check = next(
        item for item in mismatched["verification"]["checks"]
        if item["id"] == "execution_receipt_integrity"
    )
    assert mismatch_check["status"] == "failed"
    assert mismatch_check["evidence"]["mismatched_call_ids"] == ["call-1"]

    wrong_run = build_run_manifest(
        run_id="another-run",
        task_type="direct_task",
        execution_mode="react_agent",
        user_goal="Create the file",
        answer_text="File created",
        stop_reason="completed",
        tool_steps=[{
            "call_id": "call-1", "tool_name": "create_file",
            "tool": "FILE", "status": "done", "receipt": _receipt(),
        }],
        execution_receipts=[_receipt()],
    )
    wrong_run_check = next(
        item for item in wrong_run["verification"]["checks"]
        if item["id"] == "execution_receipt_integrity"
    )
    assert wrong_run_check["status"] == "failed"
    assert wrong_run_check["evidence"]["wrong_run_call_ids"] == ["call-1"]

    duplicate = build_run_manifest(
        run_id="run-1",
        task_type="direct_task",
        execution_mode="react_agent",
        user_goal="Create the file twice",
        answer_text="File created",
        stop_reason="completed",
        tool_steps=[
            {"call_id": "call-1", "tool": "create_file", "status": "done"},
            {"call_id": "call-1", "tool": "create_file", "status": "done"},
        ],
        execution_receipts=[_receipt(), _receipt()],
    )
    duplicate_check = next(
        item for item in duplicate["verification"]["checks"]
        if item["id"] == "execution_receipt_integrity"
    )
    assert duplicate_check["status"] == "failed"
    assert duplicate_check["evidence"]["duplicate_step_call_ids"] == ["call-1"]
    assert duplicate_check["evidence"]["duplicate_receipt_call_ids"] == ["call-1"]


def test_react_compatibility_path_uses_governed_executor_and_emits_receipt():
    from hashmm.agent.execution_receipt import validate_execution_receipt
    from hashmm.agent.execution_scope import build_root_scope
    from hashmm.react_agent import ReactAgent

    class LLM:
        def __init__(self):
            self.responses = iter(["<<SEARCH: quarterly report>>", "Finished"])

        def chat(self, _messages):
            return next(self.responses)

    governed_calls = []

    def governed(tool, args, context):
        governed_calls.append((tool, dict(args), bool(context.get("execution_scope"))))
        return {"status": "ok", "message": "one grounded result"}

    def raw_callback(_args, _context):
        raise AssertionError("raw registry callback bypassed the governed executor")

    scope = build_root_scope(
        owner_id="owner-1",
        conversation_id="conv-1",
        run_id="react-run-1",
        allowed_tools=["kb_search"],
        approval_mode="read_only",
        network_mode="deny",
    )
    agent = ReactAgent(
        llm_fn=LLM(),
        tool_exec_fn=governed,
        kb_search_fn=raw_callback,
        exec_context={
            "user_id": "owner-1",
            "conv_id": "conv-1",
            "execution_scope": scope,
        },
    )
    events = list(agent.run_streaming("inspect", [{"role": "system", "content": "base"}]))
    tool_event = next(data for kind, data in events if kind == "tool")

    assert governed_calls == [("kb_search", {"query": "quarterly report"}, True)]
    assert tool_event["tool_name"] == "kb_search"
    assert tool_event["call_id"] == "react-call-1"
    assert tool_event["receipt"]["run_id"] == "react-run-1"
    assert validate_execution_receipt(tool_event["receipt"])["valid"] is True


def test_react_compatibility_path_fails_closed_without_governed_executor(monkeypatch):
    from hashmm.agent.execution_scope import build_root_scope
    from hashmm.react_agent import ReactAgent

    raw_calls = []
    scope = build_root_scope(
        owner_id="owner-1", conversation_id="conv-1", run_id="react-run-2",
        allowed_tools=["kb_search", "execute_code"], approval_mode="read_only",
    )
    agent = ReactAgent(
        llm_fn=lambda _prompt: "",
        kb_search_fn=lambda *_args: raw_calls.append(True) or {"status": "ok"},
        exec_context={"execution_scope": scope},
    )
    search = agent._exec_search("private evidence")
    assert search.success is False
    assert raw_calls == []
    assert "统一权限执行器" in search.output

    monkeypatch.setenv("HASHMM_AGENT_HITL", "1")
    import hashmm.agent_safety as safety
    monkeypatch.setattr(
        safety, "requires_approval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("policy unavailable")),
    )
    executed = []
    guarded = ReactAgent(
        llm_fn=lambda _prompt: "",
        tool_exec_fn=lambda *_args: executed.append(True) or {"status": "ok"},
        exec_context={"execution_scope": scope},
    )
    denied = guarded._exec_code("print('must not run')")
    assert denied.success is False
    assert executed == []
    assert denied.receipt["outcome"]["status"] == "denied"


def test_team_manifest_preserves_missing_worker_receipts_as_failed_actions():
    from hashmm.agent.team import _team_run_manifest

    manifest = _team_run_manifest({
        "team_id": "team-receipt-gap",
        "uid": "owner-1",
        "goal": "Research and verify",
        "conv_id": "conv-team",
        "mode": "parallel",
        "status": "done",
        "created": 1,
        "roles": [{
            "role": "research", "task": "Inspect source", "state": "ok",
            "tool_calls": 1,
        }],
        "execution_receipts": [],
        "evidence": {"sources": [], "groundings": {}},
    }, "Delivered", "completed", "test-model")
    receipt_check = next(
        item for item in manifest["verification"]["checks"]
        if item["id"] == "execution_receipt_integrity"
    )
    assert receipt_check["status"] == "failed"
    assert receipt_check["evidence"]["missing"] == 1
    assert manifest["completion_gate"]["can_claim_verified"] is False
