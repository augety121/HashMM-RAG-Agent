"""PromptOptimizer — feedback-driven prompt improvement.

Collects user feedback (👍/👎) correlated with system prompt patterns,
then suggests prompt modifications to improve response quality.

This is a simplified DSPy-inspired approach: no gradient descent,
just statistical analysis of which prompt patterns get better feedback.
"""
from __future__ import annotations

import json
import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evolution.prompt_opt")


class PromptOptimizer:
    """Analyze feedback to suggest prompt improvements."""

    def __init__(self, db_module=None):
        self._db = db_module

    def _ensure_db(self):
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prompt_hash TEXT,
                        task_type TEXT,
                        feedback TEXT,
                        query_snippet TEXT,
                        created_at REAL
                    )
                """)
        except Exception as _e:
            log_suppressed(logger, _e)

    def record_feedback(
        self,
        prompt_template: str,
        task_type: str,
        feedback: str,
        query: str,
    ):
        """Record a feedback data point."""
        self._ensure_db()
        prompt_hash = str(hash(prompt_template[:200]))
        try:
            with self._db._conn() as c:
                c.execute("""
                    INSERT INTO prompt_feedback (prompt_hash, task_type, feedback, query_snippet, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (prompt_hash, task_type, feedback, query[:100], time.time()))
        except Exception as _e:
            log_suppressed(logger, _e)

    def analyze_feedback(self, days: int = 7) -> dict:
        """Analyze recent feedback patterns."""
        self._ensure_db()
        cutoff = time.time() - days * 86400
        try:
            with self._db._conn() as c:
                rows = c.execute("""
                    SELECT task_type, feedback, COUNT(*) as cnt
                    FROM prompt_feedback
                    WHERE created_at > ?
                    GROUP BY task_type, feedback
                    ORDER BY task_type, feedback
                """, (cutoff,)).fetchall()

            analysis: dict[str, dict] = {}
            for r in rows:
                tt = r["task_type"]
                if tt not in analysis:
                    analysis[tt] = {"up": 0, "down": 0}
                analysis[tt][r["feedback"]] = r["cnt"]

            # Calculate satisfaction rate per task type
            for tt, counts in analysis.items():
                total = counts["up"] + counts["down"]
                analysis[tt]["satisfaction"] = (
                    round(counts["up"] / total, 2) if total > 0 else 0
                )
                analysis[tt]["total"] = total

            return analysis
        except Exception:
            return {}

    def suggest_improvements(self) -> list[dict]:
        """Return actionable suggestions based on feedback analysis."""
        analysis = self.analyze_feedback()
        suggestions = []

        for task_type, stats in analysis.items():
            if stats.get("total", 0) < 5:
                continue  # Not enough data
            satisfaction = stats.get("satisfaction", 1.0)
            if satisfaction < 0.6:
                suggestions.append({
                    "task_type": task_type,
                    "satisfaction": satisfaction,
                    "total_feedback": stats["total"],
                    "suggestion": f"{task_type} 的满意度仅 {satisfaction:.0%}，建议检查该任务类型的 system prompt。",
                })

        return suggestions


# Module singleton
_optimizer: PromptOptimizer | None = None


def get_prompt_optimizer() -> PromptOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = PromptOptimizer()
    return _optimizer
