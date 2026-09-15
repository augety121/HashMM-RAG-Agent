"""UserModel — progressive user profiling for personalization.

Tracks user interests, expertise level, style preferences, and usage patterns.
Updates incrementally after each conversation — no batch processing needed.

Storage: SQLite table `user_profiles` (JSON blob per user).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evolution.user_model")

# Professional finance terms that indicate expertise
_FINANCE_TERMS = {
    "PE", "ROE", "ROA", "EBITDA", "自由现金流", "EPS", "市盈率",
    "毛利率", "净利率", "资产负债率", "现金流", "DCF", "WACC",
    "beta", "alpha", "夏普比率", "年化收益",
}

# Technical terms
_TECH_TERMS = {
    "transformer", "attention", "embedding", "fine-tune", "RLHF",
    "gradient", "loss function", "batch size", "learning rate",
    "卷积", "反向传播", "预训练",
}


class UserModel:
    """Progressive user profile — gets smarter with every interaction."""

    def __init__(self, db_module=None):
        self._db = db_module
        self._cache: dict[str, dict] = {}

    def _ensure_db(self):
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        user_id TEXT PRIMARY KEY,
                        profile TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL
                    )
                """)
        except Exception as _e:
            log_suppressed(logger, _e)

    def _load_profile(self, user_id: str) -> dict:
        if user_id in self._cache:
            return self._cache[user_id]
        self._ensure_db()
        try:
            with self._db._conn() as c:
                row = c.execute(
                    "SELECT profile FROM user_profiles WHERE user_id=?",
                    (user_id,)
                ).fetchone()
            if row:
                profile = json.loads(row["profile"])
                self._cache[user_id] = profile
                return profile
        except Exception as _e:
            log_suppressed(logger, _e)
        return {}

    def _save_profile(self, user_id: str, profile: dict):
        self._cache[user_id] = profile
        self._ensure_db()
        try:
            with self._db._conn() as c:
                c.execute("""
                    INSERT OR REPLACE INTO user_profiles (user_id, profile, updated_at)
                    VALUES (?, ?, ?)
                """, (user_id, json.dumps(profile, ensure_ascii=False), time.time()))
        except Exception as e:
            logger.warning(f"Failed to save user profile: {e}")
        try:  # 云同步用户设置/画像
            from hashmm.api import supabase_sync
            if supabase_sync.enabled():
                supabase_sync.push_settings(user_id, profile)
        except Exception:
            pass

    # ── Update from conversation ──

    def update_from_conversation(
        self,
        user_id: str,
        query: str,
        answer: str = "",
        feedback: str | None = None,
        task_type: str = "",
    ):
        """Incrementally update user profile after each conversation turn."""
        if not user_id or user_id == "anonymous":
            return

        profile = self._load_profile(user_id)

        # 1. Track interest entities
        interests = profile.setdefault("interests", {})
        entities = self._extract_entities(query)
        for entity, etype in entities:
            key = f"{etype}:{entity}"
            interests[key] = interests.get(key, 0) + 1

        # v11: Time-based interest decay — reduce stale interests
        last_decay = profile.get("_last_decay", 0)
        now = time.time()
        if now - last_decay > 86400:  # Decay once per day
            decay_factor = 0.95  # 5% daily decay
            for k in list(interests.keys()):
                interests[k] = round(interests[k] * decay_factor, 2)
                if interests[k] < 0.5:
                    del interests[k]  # Remove forgotten interests
            profile["_last_decay"] = now

        # Keep top 50
        if len(interests) > 50:
            sorted_items = sorted(interests.items(), key=lambda x: x[1], reverse=True)
            profile["interests"] = dict(sorted_items[:50])

        # 2. Infer expertise level
        expertise = profile.setdefault("expertise", {})
        q_lower = query.lower()
        if any(term.lower() in q_lower for term in _FINANCE_TERMS):
            expertise["finance"] = min(1.0, expertise.get("finance", 0) + 0.15)
        if any(term.lower() in q_lower for term in _TECH_TERMS):
            expertise["tech"] = min(1.0, expertise.get("tech", 0) + 0.15)

        # v11: Expertise decay (slower than interests — expertise is sticky)
        for domain in list(expertise.keys()):
            if domain not in ("finance", "tech"):
                continue
            # Only decay if user hasn't shown this expertise recently
            if domain == "finance" and not any(t.lower() in q_lower for t in _FINANCE_TERMS):
                expertise[domain] = max(0, expertise[domain] - 0.02)
            if domain == "tech" and not any(t.lower() in q_lower for t in _TECH_TERMS):
                expertise[domain] = max(0, expertise[domain] - 0.02)

        # 3. Track style preferences from feedback
        prefs = profile.setdefault("preferences", {})
        if feedback == "up":
            if len(answer) > 500:
                prefs["detail_level"] = prefs.get("detail_level", 0) + 0.1
            elif len(answer) < 200:
                prefs["detail_level"] = prefs.get("detail_level", 0) - 0.1
            prefs["detail_level"] = max(-1, min(1, prefs.get("detail_level", 0)))

        # 4. Track task type distribution
        task_dist = profile.setdefault("task_distribution", {})
        if task_type:
            task_dist[task_type] = task_dist.get(task_type, 0) + 1

        # 5. Update activity
        profile["last_active"] = time.time()
        profile["total_queries"] = profile.get("total_queries", 0) + 1

        self._save_profile(user_id, profile)

    # ── Personalization prompt ──

    def get_personalization_prompt(self, user_id: str) -> str:
        """Generate a personalization prompt fragment for the system prompt."""
        if not user_id or user_id == "anonymous":
            return ""

        profile = self._load_profile(user_id)
        if not profile:
            return ""

        parts = []

        # Expertise-based language adjustment
        expertise = profile.get("expertise", {})
        if expertise.get("finance", 0) > 0.5:
            parts.append("用户是金融专业人士，可以使用专业术语（PE/ROE/EBITDA等），不需要解释基础概念。")
        if expertise.get("tech", 0) > 0.5:
            parts.append("用户具有技术背景，可以使用专业术语。")

        # Detail level preference
        prefs = profile.get("preferences", {})
        detail = prefs.get("detail_level", 0)
        if detail > 0.3:
            parts.append("用户偏好详细深入的分析，请提供完整的数据支撑和多角度论述。")
        elif detail < -0.3:
            parts.append("用户偏好简洁精炼的回答，请直击要点。")

        # Top interests
        interests = profile.get("interests", {})
        if interests:
            top = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:5]
            top_names = [k.split(":", 1)[1] if ":" in k else k for k, _ in top]
            parts.append(f"用户主要关注：{', '.join(top_names)}")

        if not parts:
            return ""
        return "【用户画像】\n" + "\n".join(parts)

    # ── Entity extraction (rule-based, fast) ──

    def _extract_entities(self, text: str) -> list[tuple[str, str]]:
        """Extract entities from text using regex patterns."""
        entities = []
        # Company names (Chinese)
        for m in re.finditer(r'([\u4e00-\u9fff]{2,6}(?:集团|公司|科技|汽车|电子|银行|保险))', text):
            entities.append((m.group(1), "ORG"))
        # Metrics
        for m in re.finditer(r'(营收|利润|毛利率|研发|市值|股价|出货量|增长率|净利润)', text):
            entities.append((m.group(1), "METRIC"))
        # Tech concepts
        for m in re.finditer(r'(Transformer|BERT|GPT|CNN|RNN|ResNet|YOLO|RAG|LLM)', text, re.I):
            entities.append((m.group(1), "TECH"))
        return entities

    # ── Admin API ──

    def get_profile(self, user_id: str) -> dict:
        """Return the full profile dict (for admin view)."""
        return self._load_profile(user_id)

    def get_stats(self) -> dict:
        """Return aggregate user model statistics."""
        self._ensure_db()
        try:
            with self._db._conn() as c:
                total = c.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
                recent = c.execute(
                    "SELECT COUNT(*) FROM user_profiles WHERE updated_at > ?",
                    (time.time() - 86400 * 7,)
                ).fetchone()[0]
            return {"total_profiles": total, "active_7d": recent}
        except Exception:
            return {"total_profiles": 0, "active_7d": 0}


# Module singleton
_model: UserModel | None = None


def get_user_model() -> UserModel:
    global _model
    if _model is None:
        _model = UserModel()
    return _model
