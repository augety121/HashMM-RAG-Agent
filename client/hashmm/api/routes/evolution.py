"""Evolution API routes — skills, user model, prompt optimization."""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hashmm.api.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


# ── Skills ──

@router.get("/skills")
async def list_skills(request: Request):
    """List all auto-created skills."""
    from hashmm.evolution.skill_manager import get_skill_manager
    return {"skills": get_skill_manager().list_skills()}


class SkillFeedback(BaseModel):
    skill_id: str
    feedback: str  # "up" or "down"


@router.post("/skills/feedback")
async def skill_feedback(req: SkillFeedback, request: Request):
    """Update skill quality based on feedback."""
    from hashmm.evolution.skill_manager import get_skill_manager
    get_skill_manager().update_quality(req.skill_id, req.feedback)
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

@router.get("/skills/{skill_id}/evolution")
async def skill_evolution_status(skill_id: str, request: Request):
    """Get A/B test status for a skill."""
    from hashmm.evolution.skill_evolver import get_skill_evolver
    return get_skill_evolver().get_evolution_status(skill_id)


@router.post("/skills/{skill_id}/evolve")
async def trigger_skill_evolution(skill_id: str, request: Request):
    """Manually trigger skill evolution (admin only)."""
    require_admin(request)
    from hashmm.evolution.skill_manager import get_skill_manager
    from hashmm.evolution.skill_evolver import get_skill_evolver

    mgr = get_skill_manager()
    skill = None
    for s in mgr._skills:
        if s.id == skill_id:
            skill = s
            break
    if not skill:
        raise HTTPException(404, "Skill not found")

    # Get LLM function
    llm_fn = None
    try:
        from hashmm.api.core.services import ServiceRegistry
        llm_fn = ServiceRegistry.llm_fn
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    evolver = get_skill_evolver()
    variants = evolver.evolve_skill(skill_id, skill.name, skill.prompt_template, llm_fn)
    return {"ok": True, "variants": len(variants)}
