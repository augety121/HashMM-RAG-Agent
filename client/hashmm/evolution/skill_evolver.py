"""Skill Evolver — simplified GEPA for automatic skill prompt optimization.

Hermes Agent uses DSPy + GEPA (Genetic-Pareto Prompt Evolution).
We use a simplified version: LLM analysis → variant generation → A/B testing.

Flow:
  1. Collect feedback for a skill (last 10-20 uses)
  2. LLM analyzes: which answers got 👍? What patterns work?
  3. LLM generates 2-3 prompt variants
  4. A/B test: randomly serve variants, track feedback
  5. After N uses, promote the best variant

Cost: ~¥0.5 per optimization (3 LLM calls)
Trigger: Every 10 uses of a skill
"""
from __future__ import annotations

import json
import random
import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evolution.skill_evolver")


class SkillEvolver:
    """Evolve skill prompts through feedback analysis and A/B testing."""

    # How many uses before triggering evolution
    EVOLVE_THRESHOLD = 10
    # How many A/B test uses before promoting winner
    AB_TEST_ROUNDS = 5

    def __init__(self, db_module=None):
        self._db = db_module

    def _ensure_db(self):
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS skill_variants (
                        id TEXT PRIMARY KEY,
                        skill_id TEXT NOT NULL,
                        prompt_text TEXT NOT NULL,
                        is_active INTEGER DEFAULT 0,
                        is_original INTEGER DEFAULT 0,
                        uses INTEGER DEFAULT 0,
                        positive INTEGER DEFAULT 0,
                        negative INTEGER DEFAULT 0,
                        created_at REAL
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_sv_skill ON skill_variants(skill_id)")
        except Exception as _e:
            log_suppressed(logger, _e)

    def should_evolve(self, skill_id: str, use_count: int) -> bool:
        """Check if a skill should trigger evolution."""
        return (
            use_count > 0
            and use_count % self.EVOLVE_THRESHOLD == 0
        )

    def evolve_skill(self, skill_id: str, skill_name: str, current_prompt: str,
                     llm_fn: Any = None) -> list[str]:
        """Generate prompt variants for A/B testing.

        Returns list of variant prompts (including original).
        """
        self._ensure_db()

        # Check if we already have active variants
        existing = self._get_variants(skill_id)
        if existing and any(not v["is_original"] for v in existing):
            # Already has variants — check if ready to promote
            self._maybe_promote(skill_id)
            return [v["prompt_text"] for v in existing if v["is_active"]]

        # Generate new variants
        variants = [current_prompt]  # Always keep original

        if llm_fn and hasattr(llm_fn, 'quick_call'):
            try:
                raw = llm_fn.quick_call(
                    "你是 Prompt 优化专家。分析以下技能提示词，生成 2 个改进变体。\n"
                    "要求：保持核心意图不变，但尝试不同的结构/措辞/重点。\n"
                    '只返回 JSON: {"variants": ["变体1", "变体2"]}',
                    f"技能名称：{skill_name}\n当前提示词：\n{current_prompt}",
                    max_tok=500,
                )
                raw = raw.strip().strip("```json").strip("```")
                parsed = json.loads(raw)
                for v in parsed.get("variants", [])[:2]:
                    if v and v != current_prompt:
                        variants.append(v)
            except Exception as e:
                logger.warning(f"Skill evolution LLM call failed: {e}")

        # If LLM failed, generate rule-based variants
        if len(variants) == 1:
            variants.append(current_prompt + "\n请确保回答中包含具体数据和引用来源。")
            variants.append("请按以下步骤回答：\n1. 核心结论\n2. 数据支撑\n3. 深度分析\n\n" + current_prompt)

        # Save variants to DB
        import uuid
        for i, v_text in enumerate(variants):
            vid = uuid.uuid4().hex[:12]
            try:
                with self._db._conn() as c:
                    c.execute("""
                        INSERT OR IGNORE INTO skill_variants
                        (id, skill_id, prompt_text, is_active, is_original, uses, positive, negative, created_at)
                        VALUES (?, ?, ?, 1, ?, 0, 0, 0, ?)
                    """, (vid, skill_id, v_text, 1 if i == 0 else 0, time.time()))
            except Exception as _e:
                log_suppressed(logger, _e)

        logger.info(f"[SkillEvolver] Generated {len(variants)} variants for skill '{skill_name}'")
        return variants

    def select_variant(self, skill_id: str) -> str | None:
        """Select a variant for this request (random among active variants)."""
        self._ensure_db()
        variants = self._get_variants(skill_id)
        active = [v for v in variants if v["is_active"]]
        if not active:
            return None
        chosen = random.choice(active)
        # Increment use count
        try:
            with self._db._conn() as c:
                c.execute("UPDATE skill_variants SET uses = uses + 1 WHERE id = ?", (chosen["id"],))
        except Exception as _e:
            log_suppressed(logger, _e)
        return chosen["prompt_text"]

    def record_variant_feedback(self, skill_id: str, prompt_text: str, feedback: str):
        """Record feedback for a specific variant."""
        self._ensure_db()
        col = "positive" if feedback == "up" else "negative"
        try:
            with self._db._conn() as c:
                c.execute(
                    f"UPDATE skill_variants SET {col} = {col} + 1 WHERE skill_id = ? AND prompt_text = ?",
                    (skill_id, prompt_text[:500]),
                )
        except Exception as _e:
            log_suppressed(logger, _e)

    # A challenger must beat the original by at least this margin in win-rate,
    # AND have enough feedback to be trustworthy, before it is promoted. Without
    # this, a noisy 5-sample variant could displace a solid original. This is the
    # "do no harm" guard of the evolution loop (CRAG/self-improvement principle:
    # never promote a regression).
    MIN_FEEDBACK_TO_PROMOTE = 5      # min (positive+negative) on the challenger
    PROMOTE_MARGIN = 0.10            # challenger win-rate must exceed original by ≥10pp

    def _win_rate(self, v: dict) -> float:
        total = v["positive"] + v["negative"]
        # Laplace-smoothed so a 0-feedback variant isn't a spurious 0% or 100%.
        return (v["positive"] + 1) / (total + 2)

    def _maybe_promote(self, skill_id: str):
        """Promote a challenger ONLY if it beats the original by a margin with
        enough feedback; otherwise keep the original (safe, reversible loop).

        Returns the action taken (for logging/tests): 'promoted' | 'kept_original'
        | 'waiting'.
        """
        variants = self._get_variants(skill_id)
        active = [v for v in variants if v["is_active"]]
        if not active:
            return "waiting"

        # All active variants need a minimum number of uses before we judge.
        if not all(v["uses"] >= self.AB_TEST_ROUNDS for v in active):
            return "waiting"

        # Identify the original (baseline). If none is active, fall back to the
        # recorded original among all variants.
        original = next((v for v in active if v.get("is_original")), None)
        if original is None:
            original = next((v for v in variants if v.get("is_original")), None)
        challengers = [v for v in active if not v.get("is_original")]
        if not challengers:
            return "waiting"

        base_wr = self._win_rate(original) if original else 0.5
        # Best challenger by win-rate, but only those with enough feedback.
        eligible = [v for v in challengers
                    if (v["positive"] + v["negative"]) >= self.MIN_FEEDBACK_TO_PROMOTE]
        if not eligible:
            return "waiting"  # not enough evidence yet — keep testing
        eligible.sort(key=self._win_rate, reverse=True)
        best = eligible[0]
        best_wr = self._win_rate(best)

        if best_wr < base_wr + self.PROMOTE_MARGIN:
            # No challenger is meaningfully better → KEEP ORIGINAL (rollback the
            # experiment). Deactivate challengers, ensure original stays active.
            try:
                with self._db._conn() as c:
                    c.execute("UPDATE skill_variants SET is_active = 0 WHERE skill_id = ? AND is_original = 0", (skill_id,))
                    if original:
                        c.execute("UPDATE skill_variants SET is_active = 1 WHERE id = ?", (original["id"],))
                logger.info(
                    f"[SkillEvolver] skill {skill_id}: no challenger beat original "
                    f"(best={best_wr:.0%} vs base={base_wr:.0%}, margin<{self.PROMOTE_MARGIN:.0%}) "
                    f"→ kept original"
                )
            except Exception as e:
                logger.warning(f"Rollback to original failed: {e}")
            return "kept_original"

        # Challenger is significantly better → promote it.
        try:
            with self._db._conn() as c:
                c.execute("UPDATE skill_variants SET is_active = 0 WHERE skill_id = ?", (skill_id,))
                c.execute("UPDATE skill_variants SET is_active = 1 WHERE id = ?", (best["id"],))
            logger.info(
                f"[SkillEvolver] Promoted challenger for skill {skill_id}: "
                f"win_rate={best_wr:.0%} vs original {base_wr:.0%} (uses={best['uses']})"
            )
            from hashmm.evolution.skill_manager import get_skill_manager
            mgr = get_skill_manager()
            for skill in mgr._skills:
                if skill.id == skill_id:
                    skill.prompt_template = best["prompt_text"]
                    mgr._save_skill(skill)
                    break
        except Exception as e:
            logger.warning(f"Promotion failed: {e}")
        return "promoted"

    def _get_variants(self, skill_id: str) -> list[dict]:
        try:
            with self._db._conn() as c:
                rows = c.execute(
                    "SELECT * FROM skill_variants WHERE skill_id = ? ORDER BY created_at",
                    (skill_id,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_evolution_status(self, skill_id: str) -> dict:
        """Get A/B test status for admin panel."""
        variants = self._get_variants(skill_id)
        return {
            "skill_id": skill_id,
            "variant_count": len(variants),
            "variants": [{
                "id": v["id"],
                "is_original": v["is_original"],
                "is_active": v["is_active"],
                "uses": v["uses"],
                "positive": v["positive"],
                "negative": v["negative"],
                "win_rate": v["positive"] / max(v["positive"] + v["negative"], 1),
                "prompt_preview": v["prompt_text"][:100],
            } for v in variants],
        }
    def evolution_overview(self) -> dict:
        """Cross-skill evolution summary for the observability/admin dashboard.

        Auditable view of how the system has self-evolved: how many skills have
        active challengers under test, how their win-rates compare to the
        original baseline. Never raises.
        """
        try:
            self._ensure_db()
            with self._db._conn() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM skill_variants").fetchall()]
        except Exception as _e:
            log_suppressed(logger, _e)
            return {"skills_under_test": 0, "total_variants": 0, "skills": []}

        by_skill: dict = {}
        for r in rows:
            by_skill.setdefault(r["skill_id"], []).append(r)

        skills = []
        for sid, vs in by_skill.items():
            challengers = [v for v in vs if not v.get("is_original")]
            original = next((v for v in vs if v.get("is_original")), None)
            skills.append({
                "skill_id": sid,
                "n_variants": len(vs),
                "n_challengers": len(challengers),
                "original_win_rate": round(self._win_rate(original), 3) if original else None,
                "best_challenger_win_rate": round(max((self._win_rate(v) for v in challengers), default=0), 3),
                "total_uses": sum(v["uses"] for v in vs),
            })
        return {
            "skills_under_test": len([s for s in skills if s["n_challengers"] > 0]),
            "total_variants": len(rows),
            "skills": skills,
        }


# Singleton
_evolver: SkillEvolver | None = None


def get_skill_evolver() -> SkillEvolver:
    global _evolver
    if _evolver is None:
        _evolver = SkillEvolver()
    return _evolver
