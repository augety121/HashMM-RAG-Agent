from __future__ import annotations

from hashmm.agent.operating_contract import (
    OPERATING_CONTRACT_SCHEMA,
    compile_operating_contract,
    normalize_work_method,
    user_operating_projection,
    validate_operating_contract,
)


def _facts(path: str, *, available: bool = True) -> dict:
    rows = {}
    for name in ("structured", "browser", "computer"):
        selected = name == path
        rows[name] = {
            "available": bool(available and selected),
            "configured": bool(available and selected),
            "within_scope": selected,
            "requires_confirmation": name in {"browser", "computer"},
            "reason": "runtime fact",
        }
    rows["manual"] = {
        "available": True,
        "configured": True,
        "within_scope": True,
        "requires_confirmation": True,
        "reason": "manual",
    }
    rows["_meta"] = {
        "schema": "hashmm.capability-facts.v1",
        "requested_path": path,
        "runtime_revision": "runtime-rev",
    }
    return rows


def test_unknown_client_work_method_fails_to_safe_defaults():
    assert normalize_work_method({
        "retrieval": "magic",
        "effort": "unlimited",
        "run_mode": "root-shell",
    }) == {
        "schema": "hashmm.work-method.v1",
        "retrieval": "auto",
        "effort": "standard",
        "run_mode": "auto",
    }


def test_browser_contract_is_descriptive_and_never_grants_authority():
    contract = compile_operating_contract(
        owner_id="owner-a",
        run_id="run-a",
        kind="browser",
        goal="open the supplied page",
        work_method={"run_mode": "browser", "retrieval": "mix"},
        capability_facts=_facts("browser"),
        task_contract={
            "success_criteria": [
                {"check_id": "source", "evidence_required": True},
            ],
        },
    )
    assert contract["schema"] == OPERATING_CONTRACT_SCHEMA
    assert contract["route"]["state"] == "ready"
    assert contract["route"]["placement"] == "desktop_browser"
    assert contract["route"]["authority_granted"] is False
    assert contract["route"]["requires_confirmation"] is True
    assert contract["recovery"]["replay_external_side_effects"] is False
    assert contract["evidence"]["model_prose_is_evidence"] is False
    assert contract["integrations"]["mcp"]["unknown_tools_read_only"] is False
    assert contract["audit"]["arguments_persisted"] is False
    assert validate_operating_contract(contract)["valid"] is True
    assert user_operating_projection(contract)["revision"] == contract["revision"]


def test_unavailable_explicit_route_is_visible_and_falls_back_to_manual():
    contract = compile_operating_contract(
        owner_id="owner-a",
        run_id="run-a",
        kind="computer",
        goal="edit a local file",
        work_method={"run_mode": "computer"},
        capability_facts=_facts("computer", available=False),
        task_contract={},
    )
    assert contract["route"]["state"] == "setup_required"
    assert contract["route"]["fallback"] == "manual"
    projection = user_operating_projection(contract)
    assert projection["route_state"] == "setup_required"
    assert projection["can_resume"] is True
    assert "设备或服务" in projection["route_label"]


def test_contract_revision_detects_tampering():
    contract = compile_operating_contract(
        owner_id="owner-a",
        run_id="run-a",
        kind="team",
        goal="compare two approaches",
        work_method={"run_mode": "deep", "retrieval": "kg", "effort": "max"},
        capability_facts=_facts("structured"),
        task_contract={"success_criteria": [{"check_id": "verify"}]},
    )
    assert contract["collaboration"]["independent_verification_required"] is True
    assert contract["evidence"]["graph_provenance_required"] is True
    contract["route"]["authority_granted"] = True
    result = validate_operating_contract(contract)
    assert result["valid"] is False
    assert "route.authority_granted" in result["errors"]
    assert "revision" in result["errors"]
    projection = user_operating_projection(contract)
    assert projection["route_state"] == "setup_required"
    assert projection["requires_confirmation"] is True


def test_prepare_work_snapshot_puts_contract_on_main_work_path(monkeypatch):
    from hashmm.agent import work_kernel

    monkeypatch.setattr(
        work_kernel,
        "_runtime_index",
        lambda _owner: ({
            "browser_use": {
                "id": "browser_use",
                "state": "ready",
                "wired": True,
                "title": "浏览器",
            },
        }, "runtime-rev", "test"),
    )
    snapshot = work_kernel.prepare_work_snapshot(
        user_id="owner-a",
        run_id="run-a",
        kind="browser",
        title="Research",
        conv_id="conv-a",
        snapshot={"work_method": {"run_mode": "browser", "effort": "max"}},
    )
    manifest = snapshot["run_manifest"]
    assert snapshot["operating_contract"]["schema"] == OPERATING_CONTRACT_SCHEMA
    assert manifest["operating_contract"]["revision"] == snapshot["operating_contract"]["revision"]
    assert snapshot["admission_receipt"]["operating_contract_hash"]
    assert manifest["work_method"]["run_mode"] == "browser"
