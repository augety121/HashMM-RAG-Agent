"""Evolution API routes — skills, user model, prompt optimization."""
from __future__ import annotations
import asyncio
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hashmm.api.auth import get_current_user, require_admin, require_auth
from hashmm.api import database as db

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


# ── Skills ──

@router.get("/skills")
async def list_skills(request: Request):
    """List built-in and principal-owned skills without cross-user leakage."""
    user = get_current_user(request)
    uid = str((user or {}).get("uid") or "")
    from hashmm.evolution.skill_manager import get_skill_manager
    return {"skills": get_skill_manager().list_skills(owner_id=uid)}


class SkillFeedback(BaseModel):
    skill_id: str
    feedback: str  # "up" or "down"


@router.post("/skills/feedback")
async def skill_feedback(req: SkillFeedback, request: Request):
    """Update skill quality based on feedback."""
    user = get_current_user(request)
    uid = str((user or {}).get("uid") or "")
    if not uid:
        raise HTTPException(401, "Authentication required")
    from hashmm.evolution.skill_manager import get_skill_manager
    ok = get_skill_manager().update_quality(
        req.skill_id, req.feedback, owner_id=uid,
    )
    if not ok:
        # Missing and non-owned skills intentionally share one response.
        raise HTTPException(404, "Skill not found")
    return {"ok": True}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request):
    """Delete a skill."""
    require_admin(request)
    from hashmm.evolution.skill_manager import get_skill_manager
    ok = get_skill_manager().delete_skill(skill_id)
    if not ok:
        raise HTTPException(404, "Skill not found")
    return {"ok": True}


# ── User Model ──

@router.get("/profile")
async def get_my_profile(request: Request):
    """Get current user's profile."""
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    from hashmm.evolution.user_model import get_user_model
    return {"profile": get_user_model().get_profile(uid)}


@router.get("/profile/stats")
async def profile_stats(request: Request):
    """Aggregate user model statistics (admin)."""
    require_admin(request)
    from hashmm.evolution.user_model import get_user_model
    return get_user_model().get_stats()


# ── Prompt Optimizer ──

@router.get("/prompt/analysis")
async def prompt_analysis(request: Request):
    """Analyze prompt feedback patterns (admin)."""
    require_admin(request)
    from hashmm.evolution.prompt_optimizer import get_prompt_optimizer
    return {
        "analysis": get_prompt_optimizer().analyze_feedback(),
        "suggestions": get_prompt_optimizer().suggest_improvements(),
    }


class PromptFeedbackReq(BaseModel):
    task_type: str
    feedback: str  # "up" or "down"
    query: str = ""


@router.post("/prompt/feedback")
async def record_prompt_feedback(req: PromptFeedbackReq, request: Request):
    """Record prompt feedback for optimization."""
    from hashmm.evolution.prompt_optimizer import get_prompt_optimizer
    get_prompt_optimizer().record_feedback(
        prompt_template="",  # Could pass actual template
        task_type=req.task_type,
        feedback=req.feedback,
        query=req.query,
    )
    return {"ok": True}


# ── Episodic Memory ──

@router.get("/episodes")
async def list_episodes(request: Request, limit: int = 20):
    """List recent episodic memories."""
    user = get_current_user(request)
    uid = user["uid"] if user else "anonymous"
    from hashmm.evolution.episodic_memory import get_episodic_memory
    mem = get_episodic_memory()
    mem._ensure_db()
    try:
        with mem._db._conn() as c:
            rows = c.execute(
                "SELECT * FROM episodes WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (uid, limit)
            ).fetchall()
        return {"episodes": [dict(r) for r in rows]}
    except Exception:
        return {"episodes": []}


@router.get("/episodes/stats")
async def episode_stats(request: Request):
    """Episodic memory statistics."""
    from hashmm.evolution.episodic_memory import get_episodic_memory
    return get_episodic_memory().stats()


# ── Skill Evolution ──


def _skill_for_evolution(request: Request, skill_id: str):
    """Return a mutable skill without revealing whether another owner has it."""
    user = require_auth(request)
    uid = str(user.get("uid") or user.get("sub") or "")
    is_admin = str(user.get("role") or "") == "admin"
    from hashmm.evolution.skill_manager import get_skill_manager

    manager = get_skill_manager()
    skill = manager.get_skill(
        skill_id, owner_id=uid, include_all=is_admin,
    )
    if skill is None:
        raise HTTPException(404, "Skill not found")
    if not is_admin and not (
        skill.scope == "personal" and skill.owner_id == uid
    ):
        # Built-in/shared skills are readable but only an administrator may
        # change their production prompt.
        raise HTTPException(404, "Skill not found")
    return user, uid, skill


class SkillEvolutionProposal(BaseModel):
    source_run_ids: list[str] = Field(default_factory=list)


class SkillEvolutionDecision(BaseModel):
    variant_id: str = ""
    expected_baseline_hash: str = ""
    reason: str = ""

@router.get("/skills/{skill_id}/evolution")
async def skill_evolution_status(skill_id: str, request: Request):
    """Get the owner-bound candidate, decision and rollback history."""
    user, uid, _skill = _skill_for_evolution(request, skill_id)
    from hashmm.evolution.skill_evolver import get_skill_evolver
    return get_skill_evolver().get_evolution_status(
        skill_id, owner_id=uid, include_prompt=True,
    )


@router.post("/skills/{skill_id}/evolve")
async def trigger_skill_evolution(
    skill_id: str, req: SkillEvolutionProposal, request: Request,
):
    """Generate isolated candidates; never switch the production skill."""
    user, uid, skill = _skill_for_evolution(request, skill_id)
    from hashmm.evolution.skill_evolver import get_skill_evolver

    llm_fn = None
    try:
        from hashmm.api.core.services import ServiceRegistry
        llm_fn = ServiceRegistry.llm_fn
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    try:
        run = get_skill_evolver().propose(
            skill,
            owner_id=uid,
            llm_fn=llm_fn,
            source_run_ids=req.source_run_ids,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.audit(
        uid, str(user.get("username") or uid),
        "skill_evolution_proposed",
        f"skill={skill_id};run={run.get('id')};candidates={len(run.get('variants') or [])}",
    )
    return {"ok": True, "run": run}


@router.post("/skills/{skill_id}/evolution/{run_id}/approve")
async def approve_skill_evolution(
    skill_id: str, run_id: str, req: SkillEvolutionDecision, request: Request,
):
    user, uid, skill = _skill_for_evolution(request, skill_id)
    if not req.variant_id:
        raise HTTPException(400, "必须选择一个候选版本")
    from hashmm.evolution.skill_evolver import get_skill_evolver

    result = get_skill_evolver().approve(
        skill=skill, run_id=run_id, variant_id=req.variant_id,
        owner_id=uid, actor_id=uid, reason=req.reason,
        expected_baseline_hash=req.expected_baseline_hash,
    )
    state = str(result.get("state") or "")
    if state == "missing":
        raise HTTPException(404, "演进记录不存在")
    if state == "conflict":
        raise HTTPException(409, "技能已发生变化，请重新生成候选")
    if state == "evaluation_required":
        raise HTTPException(409, "必须先通过历史任务回放、对抗测试和成本门禁")
    if state in {"unsafe", "terminal"}:
        raise HTTPException(409, "候选未通过门禁或演进记录已经结束")
    db.audit(
        uid, str(user.get("username") or uid),
        "skill_evolution_promoted",
        f"skill={skill_id};run={run_id};variant={req.variant_id}",
    )
    return {"ok": True, **result}


@router.post("/skills/{skill_id}/evolution/{run_id}/evaluate")
async def evaluate_skill_evolution(
    skill_id: str, run_id: str, req: SkillEvolutionDecision, request: Request,
):
    """Run paired replay using server-owned cases; client cannot submit cases."""
    user, uid, skill = _skill_for_evolution(request, skill_id)
    if not req.variant_id:
        raise HTTPException(400, "必须选择一个候选版本")
    from hashmm.evolution.skill_evolver import get_skill_evolver

    llm_fn = None
    try:
        from hashmm.api.core.services import ServiceRegistry
        llm_fn = ServiceRegistry.llm_fn
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
    if not llm_fn or not hasattr(llm_fn, "quick_call"):
        raise HTTPException(409, "当前模型服务不支持离线技能回放")
    result = await asyncio.to_thread(
        get_skill_evolver().evaluate_run,
        skill=skill,
        run_id=run_id,
        variant_id=req.variant_id,
        owner_id=uid,
        llm_fn=llm_fn,
    )
    state = str(result.get("state") or "")
    if state == "missing":
        raise HTTPException(404, "演进记录不存在")
    if state == "conflict":
        raise HTTPException(409, "技能在回放期间发生变化，本次结果已作废")
    if state in {"unsafe", "terminal"}:
        raise HTTPException(409, "候选不可评测或演进记录已经结束")
    db.audit(
        uid, str(user.get("username") or uid),
        "skill_evolution_evaluated",
        (
            f"skill={skill_id};run={run_id};variant={req.variant_id};"
            f"eligible={bool(result.get('release_eligible'))}"
        ),
    )
    return {"ok": True, **result}


@router.post("/skills/{skill_id}/evolution/{run_id}/reject")
async def reject_skill_evolution(
    skill_id: str, run_id: str, req: SkillEvolutionDecision, request: Request,
):
    user, uid, skill = _skill_for_evolution(request, skill_id)
    from hashmm.evolution.skill_evolver import get_skill_evolver

    result = get_skill_evolver().reject(
        skill=skill, run_id=run_id, owner_id=uid, actor_id=uid,
        reason=req.reason,
    )
    if result.get("state") == "missing":
        raise HTTPException(404, "演进记录不存在")
    if result.get("state") == "terminal":
        raise HTTPException(409, "演进记录已经结束")
    db.audit(
        uid, str(user.get("username") or uid),
        "skill_evolution_rejected", f"skill={skill_id};run={run_id}",
    )
    return {"ok": True, **result}


@router.post("/skills/{skill_id}/evolution/{run_id}/rollback")
async def rollback_skill_evolution(
    skill_id: str, run_id: str, req: SkillEvolutionDecision, request: Request,
):
    user, uid, skill = _skill_for_evolution(request, skill_id)
    from hashmm.evolution.skill_evolver import get_skill_evolver

    result = get_skill_evolver().rollback(
        skill=skill, run_id=run_id, owner_id=uid, actor_id=uid,
        reason=req.reason,
    )
    state = str(result.get("state") or "")
    if state == "missing":
        raise HTTPException(404, "演进记录不存在")
    if state == "conflict":
        raise HTTPException(409, "线上技能已再次变化，不能覆盖式回滚")
    if state == "terminal":
        raise HTTPException(409, "当前演进记录不可回滚")
    db.audit(
        uid, str(user.get("username") or uid),
        "skill_evolution_rolled_back", f"skill={skill_id};run={run_id}",
    )
    return {"ok": True, **result}


# ── Governed knowledge--agent co-evolution ────────────────────────────────

def _owned_evolution_project(request: Request, project_id: str) -> tuple[dict, str]:
    user = require_auth(request)
    owner = str(user.get("uid") or "")
    if not project_id or db.get_project_for_user(project_id, owner) is None:
        # Missing and foreign IDs intentionally share one result.
        raise HTTPException(404, "Project not found")
    return user, owner


@router.get("/kg/{project_id}/candidates")
async def kg_evolution_candidates(project_id: str, request: Request, status: str = ""):
    _user, owner = _owned_evolution_project(request, project_id)
    from hashmm.kg import knowledge_evolution as evolution
    return {"schema": "hashmm.kg-candidate-feed.v1",
            "items": evolution.list_candidates(owner, project_id, status=status)}


@router.post("/kg/{project_id}/candidates")
async def kg_evolution_propose(project_id: str, request: Request):
    user, owner = _owned_evolution_project(request, project_id)
    body = await request.json()
    from hashmm.kg import knowledge_evolution as evolution
    try:
        candidate = evolution.propose(
            owner_id=owner, project_id=project_id,
            head=str(body.get("head") or ""), relation=str(body.get("relation") or ""),
            tail=str(body.get("tail") or ""), confidence=float(body.get("confidence") or 0),
            evidence=body.get("evidence") if isinstance(body.get("evidence"), list) else [],
            source_run_id=str(body.get("source_run_id") or ""),
            extractor=str(body.get("extractor") or ""),
        )
    except evolution.EvolutionError as exc:
        raise HTTPException(409 if exc.code in {"low_confidence"} else 422, exc.code) from exc
    db.audit(owner, str(user.get("username") or owner), "kg_candidate_quarantined",
             f"project={project_id};candidate={candidate['id']};status={candidate['status']}")
    return {"ok": True, "candidate": candidate, "main_graph_modified": False}


@router.post("/kg/{project_id}/candidates/{candidate_id}/review")
async def kg_evolution_review(project_id: str, candidate_id: str, request: Request):
    user, owner = _owned_evolution_project(request, project_id)
    body = await request.json()
    from hashmm.kg import knowledge_evolution as evolution
    try:
        candidate = evolution.review_candidate(
            owner, project_id, candidate_id, actor_id=owner,
            decision=str(body.get("decision") or ""), reason=str(body.get("reason") or ""),
            resolve_conflict=bool(body.get("resolve_conflict")),
        )
    except evolution.EvolutionError as exc:
        raise HTTPException(409 if exc.code == "conflict_resolution_required" else 422, exc.code) from exc
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    db.audit(owner, str(user.get("username") or owner), "kg_candidate_reviewed",
             f"project={project_id};candidate={candidate_id};status={candidate['status']}")
    return {"ok": True, "candidate": candidate, "main_graph_modified": False}


@router.get("/kg/{project_id}/versions/active")
async def kg_evolution_active(project_id: str, request: Request):
    _user, owner = _owned_evolution_project(request, project_id)
    from hashmm.kg import knowledge_evolution as evolution
    return {"active": evolution.active_version(owner, project_id)}


@router.post("/kg/{project_id}/versions")
async def kg_evolution_promote(project_id: str, request: Request):
    user, owner = _owned_evolution_project(request, project_id)
    body = await request.json()
    from hashmm.kg import knowledge_evolution as evolution
    try:
        version = evolution.promote(
            owner, project_id,
            [str(item) for item in body.get("candidate_ids", []) if str(item).strip()],
            actor_id=owner,
            evaluation=body.get("evaluation") if isinstance(body.get("evaluation"), dict) else {},
            expected_parent_version=str(body.get("expected_parent_version") or ""),
        )
    except evolution.EvolutionError as exc:
        raise HTTPException(409, exc.code) from exc
    db.audit(owner, str(user.get("username") or owner), "kg_version_activated",
             f"project={project_id};version={version.get('id')};hash={version.get('content_hash')}")
    return {"ok": True, "version": version, "automatic_main_graph_write": False}


@router.post("/kg/{project_id}/versions/{version_id}/rollback")
async def kg_evolution_rollback(project_id: str, version_id: str, request: Request):
    user, owner = _owned_evolution_project(request, project_id)
    from hashmm.kg import knowledge_evolution as evolution
    version = evolution.rollback(owner, project_id, version_id, actor_id=owner)
    if version is None:
        raise HTTPException(404, "Version not found")
    db.audit(owner, str(user.get("username") or owner), "kg_version_rolled_back",
             f"project={project_id};version={version_id}")
    return {"ok": True, "version": version, "automatic_main_graph_write": False}
