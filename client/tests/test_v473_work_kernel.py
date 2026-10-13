"""V459-V473 Work Kernel, placement and automation regressions."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


pytestmark = pytest.mark.regression


def _fresh_db(tmp_db: str):
    from hashmm.api import database as db

    db.DB_PATH = Path(tmp_db)
    db._pool = None
    db.init_db()
    return db


def _facts() -> dict:
    return {
        "structured": {
            "available": True,
            "configured": True,
            "within_scope": True,
            "reason": "测试运行时已装配",
        },
        "browser": {
            "available": False,
            "configured": False,
            "within_scope": False,
            "reason": "当前任务未要求浏览器",
        },
        "computer": {
            "available": False,
            "configured": False,
            "within_scope": False,
            "reason": "当前任务未要求电脑操作",
        },
        "manual": {
            "available": True,
            "configured": True,
            "within_scope": True,
            "requires_confirmation": True,
        },
        "_meta": {
            "schema": "hashmm.capability-facts.v1",
            "runtime_revision": "test-runtime",
            "source": "test",
            "server_facts_only": True,
        },
    }


def _receipt(run_id: str, *, arguments: dict | None = None) -> dict:
    from hashmm.agent.execution_receipt import build_execution_receipt

    return build_execution_receipt(
        run_id=run_id,
        call_id=f"call-{run_id}",
        tool_name="create_file",
        arguments=arguments or {"filename": "report.md", "content": "verified"},
        result={"status": "ok"},
        execution_scope={"scope_id": "workspace:test"},
        side_effect={
            "class": "local_write",
            "external": False,
            "reversible": True,
        },
        status="completed",
    )


def _run_with_receipt(runtime, user_id: str, source: str, arguments: dict | None = None):
    run_id = f"run-{source}"
    return runtime.create_run(
        user_id=user_id,
        kind="workflow",
        source_id=source,
        run_id=run_id,
        title="生成可核验报告",
        status="completed",
        snapshot={
            "capability_facts": _facts(),
            "run_manifest": {
                "execution_receipts": [_receipt(run_id, arguments=arguments)],
            },
        },
    )


def test_work_admission_compiles_contract_and_explicit_causal_seed(
    tmp_db, monkeypatch,
):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_kernel
    from hashmm.agent import work_runtime as runtime

    user = db.create_user("v473-admission", "pw12345678")
    monkeypatch.setattr(
        work_kernel,
        "collect_capability_facts",
        lambda **_kwargs: _facts(),
    )
    run = runtime.create_run(
        user_id=user["id"],
        kind="artifact",
        source_id="v473-admission-source",
        conv_id="conv-v473",
        title="生成一份带来源的年度报告",
    )
    snapshot = run["snapshot"]
    manifest = snapshot["run_manifest"]
    contract = manifest["task_contract"]
    graph = manifest["causal_work_graph"]

    assert contract["goal"] == "生成一份带来源的年度报告"
    assert contract["run_id"] == run["id"]
    assert contract["conversation_id"] == "conv-v473"
    assert snapshot["admission_receipt"]["server_compiled"] is True
    assert snapshot["admission_receipt"]["model_prose_used_as_fact"] is False
    assert graph["source"] == "task_contract"
    assert graph["model_inferred_edges"] is False
    assert all(
        edge["kind"] == "supports"
        for edge in graph["edges"]
    )
    product = runtime.get_run(run["id"], user["id"])["workspace"]["product"]
    assert product["contract"]["goal"] == contract["goal"]
    assert product["integrity"]["model_prose_is_execution_evidence"] is False


def test_device_selection_is_owner_revision_and_lease_bound(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("v473-device-alice", "pw12345678")
    bob = db.create_user("v473-device-bob", "pw12345678")
    run = runtime.create_run(
        user_id=alice["id"],
        kind="computer",
        source_id="v473-device",
        title="在我的电脑整理文件",
        execution_target={"kind": "waiting_device"},
        snapshot={"capability_facts": _facts()},
    )
    assert runtime.request_execution_device(
        run["id"],
        user_id=bob["id"],
        device_id="desktop-b",
        expected_revision=run["revision"],
    )["error"] == "not_found"
    selected = runtime.request_execution_device(
        run["id"],
        user_id=alice["id"],
        device_id="desktop-a",
        device_label="工作电脑",
        expected_revision=run["revision"],
    )
    assert selected["ok"] is True
    assert selected["execution_target"]["authority_expanded"] is False
    assert runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="remote_device",
        holder_id="desktop-b",
        expected_revision=selected["run"]["revision"],
        idempotency_key="lease-v473-wrong-device",
    )["error"] == "different_device_selected"
    claimed = runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="remote_device",
        holder_id="desktop-a",
        expected_revision=selected["run"]["revision"],
        idempotency_key="lease-v473-selected-device",
        ttl_seconds=15,
    )
    assert claimed["ok"] is True
    with db._conn() as conn:
        conn.execute(
            "UPDATE work_execution_leases SET expires_at=0 WHERE id=?",
            (claimed["lease"]["id"],),
        )
    replay = runtime.acquire_execution_lease(
        run["id"],
        user_id=alice["id"],
        holder_type="remote_device",
        holder_id="desktop-a",
        expected_revision=claimed["run"]["revision"],
        idempotency_key="lease-v473-selected-device",
    )
    assert replay["ok"] is False
    assert replay["error"] == "idempotency_replayed_after_expiry"


def test_workflow_requires_execution_evidence_independent_replay_and_approval(
    tmp_db,
):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_automation
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("v473-workflow-alice", "pw12345678")
    bob = db.create_user("v473-workflow-bob", "pw12345678")
    empty = runtime.create_run(
        user_id=alice["id"],
        kind="workflow",
        source_id="v473-empty",
        title="没有执行证据",
        snapshot={"capability_facts": _facts()},
    )
    assert work_automation.create_workflow_candidate(
        user_id=alice["id"],
        source_run_id=empty["id"],
        name="不可发布",
        idempotency_key="workflow-empty-v473",
    )["error"] == "execution_evidence_required"
    unfinished_id = "run-v473-unfinished"
    unfinished = runtime.create_run(
        user_id=alice["id"],
        kind="workflow",
        source_id="v473-unfinished",
        run_id=unfinished_id,
        title="尚未完成的演示",
        status="running",
        snapshot={
            "capability_facts": _facts(),
            "run_manifest": {
                "execution_receipts": [_receipt(unfinished_id)],
            },
        },
    )
    assert work_automation.create_workflow_candidate(
        user_id=alice["id"],
        source_run_id=unfinished["id"],
        name="不能从运行中的工作发布",
        idempotency_key="workflow-unfinished-v473",
    )["error"] == "completed_run_required"

    source = _run_with_receipt(runtime, alice["id"], "v473-source")
    candidate = work_automation.create_workflow_candidate(
        user_id=alice["id"],
        source_run_id=source["id"],
        name="生成报告",
        idempotency_key="workflow-candidate-v473",
    )
    workflow = candidate["workflow"]
    assert workflow["status"] == "candidate"
    assert "content" not in str(workflow["actions"])
    assert work_automation.verify_workflow_replay(
        user_id=alice["id"],
        workflow_id=workflow["id"],
        replay_run_id=source["id"],
        expected_revision=workflow["revision"],
    )["error"] == "independent_replay_required"
    assert work_automation.verify_workflow_replay(
        user_id=bob["id"],
        workflow_id=workflow["id"],
        replay_run_id=source["id"],
        expected_revision=workflow["revision"],
    )["error"] == "not_found"

    mismatch = _run_with_receipt(
        runtime, alice["id"], "v473-mismatch",
        arguments={"filename": "other.md", "content": "different"},
    )
    assert work_automation.verify_workflow_replay(
        user_id=alice["id"],
        workflow_id=workflow["id"],
        replay_run_id=mismatch["id"],
        expected_revision=workflow["revision"],
    )["error"] == "replay_signature_mismatch"

    replay = _run_with_receipt(runtime, alice["id"], "v473-replay")
    auto_verified = work_automation.create_workflow_candidate(
        user_id=alice["id"],
        source_run_id=replay["id"],
        name="生成报告（第二次演示）",
        idempotency_key="workflow-auto-verified-v473",
    )
    assert auto_verified["workflow"]["status"] == "verified"
    assert auto_verified["workflow"]["replay_run_id"] == source["id"]
    verified = work_automation.verify_workflow_replay(
        user_id=alice["id"],
        workflow_id=workflow["id"],
        replay_run_id=replay["id"],
        expected_revision=workflow["revision"],
    )
    assert verified["workflow"]["status"] == "verified"
    assert work_automation.publish_workflow(
        user_id=alice["id"],
        workflow_id=workflow["id"],
        expected_revision=verified["workflow"]["revision"],
        approved=False,
    )["error"] == "explicit_approval_required"
    published = work_automation.publish_workflow(
        user_id=alice["id"],
        workflow_id=workflow["id"],
        expected_revision=verified["workflow"]["revision"],
        approved=True,
        parameters={"output": "报告"},
    )
    assert published["workflow"]["status"] == "published"
    assert published["workflow"]["integrity"]["raw_arguments_stored"] is False


def test_proactive_items_are_owner_bound_and_never_execute_on_suggestion(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_automation
    from hashmm.agent import work_runtime as runtime

    alice = db.create_user("v473-proactive-alice", "pw12345678")
    bob = db.create_user("v473-proactive-bob", "pw12345678")
    run = runtime.create_run(
        user_id=alice["id"],
        kind="computer",
        source_id="v473-proactive",
        title="等待电脑继续",
        execution_target={"kind": "waiting_device"},
        snapshot={"capability_facts": _facts()},
    )
    items = work_automation.refresh_proactive_items(alice["id"])
    assert len(items) == 1
    item = items[0]
    assert item["run_id"] == run["id"]
    assert item["status"] == "suggested"
    assert item["auto_executes"] is False
    assert work_automation.refresh_proactive_items(bob["id"]) == []
    assert work_automation.decide_proactive_item(
        user_id=bob["id"], item_id=item["id"], decision="approve",
    )["error"] == "not_found"
    assert work_automation.finalize_proactive_item(
        user_id=alice["id"],
        item_id=item["id"],
        status="executed",
        result={"ok": True},
    )["error"] == "approval_required"
    approved = work_automation.decide_proactive_item(
        user_id=alice["id"], item_id=item["id"], decision="approve",
    )
    assert approved["item"]["status"] == "approved"
    finalized = work_automation.finalize_proactive_item(
        user_id=alice["id"],
        item_id=item["id"],
        status="needs_user",
        result={"ok": False, "error": "device_unavailable"},
    )
    assert finalized["ok"] is True
    assert finalized["item"]["status"] == "needs_user"


def test_proactive_attention_budget_is_hard_capped(tmp_db):
    db = _fresh_db(tmp_db)
    from hashmm.agent import work_automation
    from hashmm.agent import work_runtime as runtime

    user = db.create_user("v473-proactive-budget", "pw12345678")
    for index in range(30):
        runtime.create_run(
            user_id=user["id"],
            kind="computer",
            source_id=f"v473-budget-{index}",
            title=f"等待电脑继续 {index}",
            execution_target={"kind": "waiting_device"},
            snapshot={"capability_facts": _facts()},
        )
    items = work_automation.refresh_proactive_items(user["id"], limit=100)
    assert len(items) == 24
    with db._conn() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM proactive_work_items WHERE user_id=? "
            "AND status IN ('suggested','approved','needs_user','failed')",
            (user["id"],),
        ).fetchone()[0]
    assert active == 24


def test_dispatch_completion_requires_the_claimed_device_lease(
    tmp_db, tmp_path, monkeypatch,
):
    _fresh_db(tmp_db)
    fastapi = pytest.importorskip("fastapi")
    HTTPException = fastapi.HTTPException
    from hashmm.api.routes import dispatch as route

    user = {
        "uid": "uid-v473-dispatch",
        "sub": "v473-dispatch@example.com",
        "role": "user",
    }
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(route, "require_auth", lambda _request: user)
    task_id = route.dq.create_task(
        "desktop", "browser_use", {"goal": "读取网页"},
        created_by=user["sub"],
    )
    route._admit_dispatch_work(
        user,
        task_id=task_id,
        kind="browser_use",
        payload={"goal": "读取网页"},
    )

    class _PollRequest:
        query_params = {}

    claimed = asyncio.run(route.poll(
        _PollRequest(),
        runner="desktop",
        device_id="desktop-v473",
        device_name="测试电脑",
        app_version="1.0",
    ))
    task = claimed["task"]
    assert task["execution_authority"] is True
    lease = task["execution_lease"]

    class _CompleteRequest:
        def __init__(self, body):
            self._body = body

        async def json(self):
            return self._body

    with pytest.raises(HTTPException) as missing:
        asyncio.run(route.complete(
            task_id,
            _CompleteRequest({"ok": True, "result": "不能无租约完成"}),
        ))
    assert missing.value.status_code == 409
    assert route.dq.get_task(task_id)["status"] == "claimed"

    response = asyncio.run(route.complete(
        task_id,
        _CompleteRequest({
            "ok": True,
            "result": "已完成",
            "device_id": "desktop-v473",
            "lease_id": lease["id"],
            "lease_generation": lease["generation"],
        }),
    ))
    assert response == {"ok": True}
    assert route.dq.get_task(task_id)["status"] == "done"
