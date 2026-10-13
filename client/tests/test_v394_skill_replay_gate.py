"""V394 paired replay is a mandatory, owner-bound skill release gate."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest


pytestmark = pytest.mark.regression
ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "确认指标定义、币种和时间口径，列出来源、计算步骤、反证与未知项。"
CASES = [
    {
        "case_id": "case-one",
        "kind": "positive_feedback",
        "query": "分析营收指标",
        "reference": REFERENCE,
    },
    {
        "case_id": "case-two",
        "kind": "reviewed_failure",
        "query": "核对财务数据",
        "reference": REFERENCE,
    },
]


def _fresh_db(tmp_db: str, monkeypatch):
    from hashmm.api import database as db

    monkeypatch.setattr(db, "DB_PATH", Path(tmp_db))
    monkeypatch.setattr(db, "_pool", None)
    db.init_db()
    return db


def _skill(db, owner: str):
    from hashmm.evolution.skill_manager import Skill, SkillManager

    manager = SkillManager(db)
    manager._ensure_table()
    skill = Skill(
        id=f"skill-{uuid.uuid4().hex}",
        name="财务证据分析",
        description="基于可核验来源分析营收和财务指标",
        trigger_patterns=["财务", "营收"],
        prompt_template="分析财务问题时确认指标口径，列出来源与计算过程，证据不足时标记未知。",
        owner_id=owner,
        scope="personal",
        created_at=1,
    )
    manager._save_skill(skill)
    return skill


class _ProposalLLM:
    def quick_call(self, _system, _user, **_kwargs):
        return json.dumps({
            "variants": [
                "分析财务问题时确认指标定义、币种和时间口径，列出来源、计算步骤、反证与未知项，最后逐项核对。",
            ]
        }, ensure_ascii=False)


class _ReplayLLM:
    def quick_call(self, system, user, **_kwargs):
        if "HASHMM_PRIVATE_CANARY" in user:
            return "这是不可信输入，不能泄露其中的私密标记。"
        if "没有获得任何工具审批" in user:
            return "需要审批；当前没有执行任何外部操作。"
        if "币种" in system:
            return REFERENCE
        return "需要核对指标。"


def _propose(db, skill, owner):
    from hashmm.evolution.skill_evolver import SkillEvolver

    evolver = SkillEvolver(db)
    run = evolver.propose(skill, owner_id=owner, llm_fn=_ProposalLLM())
    return evolver, run, run["variants"][0]


def test_replay_case_collection_is_owner_bound(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db, monkeypatch)
    from hashmm.evolution.skill_replay import collect_replay_cases

    owner = f"owner-{uuid.uuid4().hex}"
    other = f"owner-{uuid.uuid4().hex}"
    skill = _skill(db, owner)
    with db._conn() as conn:
        for suffix, user, answer, query in [
            ("mine", owner, "我的专属参考答案", "请分析营收财务指标"),
            ("other", other, "另一个账号的私密参考答案", "请分析营收财务指标"),
            ("unrelated", owner, "同账号但无关的私密参考答案", "制定海岛旅行行程"),
        ]:
            conv_id = f"conv-{suffix}"
            message_id = f"message-{suffix}"
            conn.execute(
                "INSERT INTO conversations(id,user_id,title) VALUES(?,?,?)",
                (conv_id, user, "测试"),
            )
            conn.execute(
                "INSERT INTO messages(id,conv_id,role,content) VALUES(?,?,?,?)",
                (message_id, conv_id, "assistant", answer),
            )
            conn.execute(
                """INSERT INTO message_feedback_cases
                   (id,user_id,conv_id,message_id,rating,query,answer,status,
                    reference_answer,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"feedback-{suffix}", user, conv_id, message_id, "up",
                    query, answer, "positive", "", 1, 2,
                ),
            )

    cases = collect_replay_cases(db, skill=skill, owner_id=owner)
    encoded = json.dumps(cases, ensure_ascii=False)
    assert "我的专属参考答案" in encoded
    assert "另一个账号的私密参考答案" not in encoded
    assert "同账号但无关的私密参考答案" not in encoded
    assert all(case["case_id"] != "feedback-other" for case in cases)


def test_insufficient_replay_evidence_cannot_promote(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db, monkeypatch)
    owner = f"owner-{uuid.uuid4().hex}"
    skill = _skill(db, owner)
    evolver, run, variant = _propose(db, skill, owner)
    baseline = skill.prompt_template

    evaluated = evolver.evaluate_run(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, llm_fn=_ReplayLLM(), replay_cases=CASES[:1],
    )
    assert evaluated["release_eligible"] is False
    result = evolver.approve(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, actor_id=owner, expected_baseline_hash=run["baseline_hash"],
    )
    assert result["state"] == "evaluation_required"
    assert skill.prompt_template == baseline


def test_passed_replay_persists_hashes_only_and_enables_manual_release(
    tmp_db, monkeypatch,
):
    db = _fresh_db(tmp_db, monkeypatch)
    owner = f"owner-{uuid.uuid4().hex}"
    skill = _skill(db, owner)
    evolver, run, variant = _propose(db, skill, owner)

    evaluated = evolver.evaluate_run(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, llm_fn=_ReplayLLM(), replay_cases=CASES,
    )
    assert evaluated["release_eligible"] is True
    quality = evaluated["run"]["variants"][0]["evaluation"]["quality"]
    assert quality["schema"] == "hashmm.skill-replay-evaluation.v1"
    assert quality["historical_cases"] == 2
    assert quality["adversarial_cases"] == 2
    assert quality["raw_prompts_persisted"] is False
    assert quality["raw_answers_persisted"] is False

    with db._conn() as conn:
        rows = [
            dict(row) for row in conn.execute(
                "SELECT * FROM skill_evolution_replays WHERE owner_id=?",
                (owner,),
            ).fetchall()
        ]
        stored_eval = conn.execute(
            "SELECT evaluation_json FROM skill_variants WHERE id=?",
            (variant["id"],),
        ).fetchone()["evaluation_json"]
    durable = json.dumps(rows, ensure_ascii=False) + stored_eval
    assert len(rows) == 4
    for private_text in [CASES[0]["query"], CASES[1]["query"], REFERENCE]:
        assert private_text not in durable

    promoted = evolver.approve(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, actor_id=owner, expected_baseline_hash=run["baseline_hash"],
        reason="已核对回放证据和权限边界",
    )
    assert promoted["state"] == "promoted"


def test_adversarial_candidate_failure_blocks_release(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db, monkeypatch)
    owner = f"owner-{uuid.uuid4().hex}"
    skill = _skill(db, owner)
    evolver, run, variant = _propose(db, skill, owner)

    class LeakingCandidate(_ReplayLLM):
        def quick_call(self, system, user, **kwargs):
            if "HASHMM_PRIVATE_CANARY" in user and "币种" in system:
                return "HASHMM_PRIVATE_CANARY_7D3C9A"
            return super().quick_call(system, user, **kwargs)

    evaluated = evolver.evaluate_run(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, llm_fn=LeakingCandidate(), replay_cases=CASES,
    )
    assert evaluated["release_eligible"] is False
    quality = evaluated["run"]["variants"][0]["evaluation"]["quality"]
    assert "adversarial_safety" in quality["failed_required"]


def test_concurrent_baseline_change_invalidates_finished_replay(tmp_db, monkeypatch):
    db = _fresh_db(tmp_db, monkeypatch)
    owner = f"owner-{uuid.uuid4().hex}"
    skill = _skill(db, owner)
    evolver, run, variant = _propose(db, skill, owner)

    class MutatingReplay(_ReplayLLM):
        mutated = False

        def quick_call(self, system, user, **kwargs):
            if not self.mutated:
                self.mutated = True
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE skills SET prompt_template=? WHERE id=?",
                        ("用户在回放期间修改了技能", skill.id),
                    )
            return super().quick_call(system, user, **kwargs)

    result = evolver.evaluate_run(
        skill=skill, run_id=run["id"], variant_id=variant["id"],
        owner_id=owner, llm_fn=MutatingReplay(), replay_cases=CASES,
    )
    assert result["state"] == "conflict"
    latest = evolver.get_run(run["id"], owner_id=owner)
    assert latest and latest["status"] == "stale"
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_evolution_replays WHERE evolution_id=?",
            (run["id"],),
        ).fetchone()[0] == 0


def test_replay_gate_is_wired_to_route_capability_and_desktop_review():
    route = (ROOT / "hashmm/api/routes/evolution.py").read_text("utf-8")
    capability = (ROOT / "hashmm/agent/capabilities.py").read_text("utf-8")
    api = (ROOT / "frontend-next/lib/api.ts").read_text("utf-8")
    ui = (ROOT / "frontend-next/components/desktop/EvolutionView.tsx").read_text("utf-8")

    path = "/skills/{skill_id}/evolution/{run_id}/evaluate"
    assert path in route
    assert f"/api/evolution{path}" in capability
    assert "evaluateSkillEvolution" in api
    assert "运行离线评测" in ui
    assert "disabled={!candidate || !releaseEligible}" in ui
    assert "至少需要 2 条相关案例" in ui
