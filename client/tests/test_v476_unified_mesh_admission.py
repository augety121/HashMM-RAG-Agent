from __future__ import annotations


def test_mesh_admission_is_deterministic_and_builds_dependencies():
    from hashmm.agent.mesh import admit_mesh_work

    roles = [
        {"id": "draft", "role": "writer", "task": "编辑同一份报告"},
        {"id": "review", "role": "reviewer", "task": "审核并修改同一份报告"},
    ]
    first = admit_mesh_work(
        goal="完成报告", roles=roles, requested_mode="auto", adapter="test",
    )
    replay = admit_mesh_work(
        goal="完成报告", roles=roles, requested_mode="auto", adapter="test",
    )
    assert first["schema"] == "hashmm.agent-mesh-decision.v1"
    assert first["admission_schema"] == "hashmm.agent-mesh-admission.v1"
    assert first["resolved_mode"] == "pipeline"
    assert first["tasks"][1]["depends_on"] == ["draft"]
    assert first["admission_hash"] == replay["admission_hash"]


def test_execution_scope_can_narrow_but_not_expand_mesh():
    from hashmm.agent.mesh import admit_mesh_work

    scope = {
        "allow_subagents": True,
        "budgets": {"max_workers": 1},
    }
    admission = admit_mesh_work(
        goal="并行调研两个市场",
        roles=[
            {"role": "research", "task": "调研市场 A"},
            {"role": "research", "task": "调研市场 B"},
        ],
        requested_mode="parallel",
        execution_scope=scope,
        require_delegation=True,
    )
    assert admission["admitted"] is True
    assert admission["resolved_mode"] == "pipeline"
    assert "parallel_budget_insufficient" in admission["reason_codes"]

    denied = admit_mesh_work(
        goal="调研",
        roles=[{"role": "research", "task": "调研 A"}],
        execution_scope={"allow_subagents": False, "budgets": {"max_workers": 3}},
        require_delegation=True,
    )
    assert denied["admitted"] is False
    assert denied["denial_code"] == "subagents_not_in_execution_scope"


def test_legacy_orchestrator_projects_the_same_mesh_contract():
    from hashmm.agent.orchestrator import SubAgentOrchestrator

    orchestrator = SubAgentOrchestrator()
    plan = orchestrator.plan("对比小米和腾讯的2024年营收")
    summary = orchestrator.plan_summary(plan)
    admission = summary["mesh_admission"]
    assert admission is summary["team"]["mesh_admission"]
    assert admission["adapter"] == "legacy_orchestrator"
    assert summary["resolved_mode"] == admission["resolved_mode"]
    assert len(admission["tasks"]) == len(plan.subtasks)


def test_legacy_agentic_rag_obeys_mesh_denial():
    from hashmm.agent.subagents import orchestrate

    searches: list[str] = []

    def search(query):
        searches.append(query)
        return [{"filename": "evidence.pdf", "text": query}]

    result = orchestrate(
        "小米与腾讯财务对比",
        search,
        lambda query, reports: "完成",
        execution_scope={
            "allow_subagents": False,
            "budgets": {"max_workers": 4},
        },
    )
    assert result["mesh_admission"]["admitted"] is False
    assert result["subqueries"] == ["小米与腾讯财务对比"]
    assert searches == ["小米与腾讯财务对比"]
