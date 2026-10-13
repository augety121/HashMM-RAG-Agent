"""Cross-session task memory (V17 Phase 19).

Marvis-style continuity: remember what the user was working on across sessions
and offer to resume. E.g. if they were analyzing 小米 financials over several
turns and left, next session we can say "上次你在分析小米财报，要继续吗？".

A "task" is a coherent multi-turn thread on a subject. We track:
  - subject (the main entity/topic)
  - status: active / completed / abandoned
  - last_summary (what was covered)
  - turn_count, timestamps

This is deliberately lightweight and rule-based for the subject detection;
the summary can be LLM-generated. No external deps.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.task_memory")

# How long before an active task is considered stale/resumable (seconds)
_RESUME_MIN_AGE = 600          # 10 min: don't nag mid-session
_RESUME_MAX_AGE = 14 * 86400   # 14 days: too old to bother


class TaskMemory:
    def __init__(self, db_module=None):
        self._db = db_module

    def _ensure_db(self):
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS user_tasks (
                        id          TEXT PRIMARY KEY,
                        user_id     TEXT DEFAULT '',
                        subject     TEXT NOT NULL,
                        status      TEXT DEFAULT 'active',
                        summary     TEXT DEFAULT '',
                        turn_count  INTEGER DEFAULT 1,
                        created_at  DOUBLE PRECISION,
                        updated_at  DOUBLE PRECISION
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON user_tasks(user_id)")
        except Exception as e:
            logger.debug(f"task table ensure failed: {e}")

    # ── subject detection (rule-based) ──
    _KNOWN_BRANDS = ("小米", "网易", "苹果", "特斯拉", "腾讯", "阿里巴巴", "阿里",
                     "京东", "字节跳动", "华为", "比亚迪", "美团", "百度",
                     "知识图谱", "大模型", "RAG", "AI")

    def _detect_subject(self, query: str) -> str:
        """Pick the salient subject of a query (company/topic). Empty if none.

        Known brands take priority so '小米的汽车业务' and '小米2024营收' both map
        to '小米' (otherwise the 汽车 suffix regex would split them apart).
        """
        if not query:
            return ""
        # 1) known brands first (longest match wins: 阿里巴巴 before 阿里)
        for kw in sorted(self._KNOWN_BRANDS, key=len, reverse=True):
            if kw in query:
                return kw
        # 2) generic company-like names (trim leading filler)
        best = ""
        for m in re.finditer(r"([\u4e00-\u9fff]{2,6}(?:集团|公司|科技|银行|控股))", query):
            cand = re.sub(r"^(帮我|请|分析|对比|查询|看看|了解|一下)+", "", m.group(1))
            if len(cand) >= 2 and (not best or len(cand) < len(best)):
                best = cand
        if best:
            return best
        # 3) topic keywords
        for kw in ("财报", "营收", "毛利率", "研发"):
            if kw in query:
                return kw
        # 4) ASCII tech term
        m2 = re.search(r"\b([A-Za-z]{3,}(?:[A-Za-z0-9\-]*))\b", query)
        if m2:
            return m2.group(1)
        return ""

    # ── update on each turn ──
    def update(self, user_id: str, query: str, llm_summary: str = "") -> None:
        """Record/advance the user's current task based on this query."""
        if not user_id or user_id == "anonymous":
            return
        subject = self._detect_subject(query)
        if not subject:
            return
        self._ensure_db()
        now = time.time()
        try:
            with self._db._conn() as c:
                row = c.execute(
                    "SELECT id, turn_count FROM user_tasks "
                    "WHERE user_id=? AND subject=? AND status='active'",
                    (user_id, subject),
                ).fetchone()
                if row:
                    c.execute(
                        "UPDATE user_tasks SET turn_count=?, updated_at=?, "
                        "summary=COALESCE(NULLIF(?,''), summary) WHERE id=?",
                        (row["turn_count"] + 1, now, llm_summary, row["id"]),
                    )
                else:
                    c.execute(
                        "INSERT INTO user_tasks (id,user_id,subject,status,summary,"
                        "turn_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], user_id, subject, "active",
                         llm_summary, 1, now, now),
                    )
        except Exception as e:
            logger.debug(f"task update failed: {e}")

    def complete(self, user_id: str, subject: str) -> None:
        self._ensure_db()
        try:
            with self._db._conn() as c:
                c.execute(
                    "UPDATE user_tasks SET status='completed', updated_at=? "
                    "WHERE user_id=? AND subject=? AND status='active'",
                    (time.time(), user_id, subject),
                )
        except Exception as _e:
            log_suppressed(logger, _e)

    # ── proactive resume hint ──
    def get_resume_hint(self, user_id: str, current_query: str = "") -> str:
        """If the user has a recent unfinished task on a DIFFERENT subject than
        the current query, return a short proactive hint. Empty otherwise."""
        if not user_id or user_id == "anonymous":
            return ""
        self._ensure_db()
        now = time.time()
        cur_subject = self._detect_subject(current_query)
        try:
            with self._db._conn() as c:
                rows = c.execute(
                    "SELECT subject, summary, turn_count, updated_at FROM user_tasks "
                    "WHERE user_id=? AND status='active' ORDER BY updated_at DESC LIMIT 5",
                    (user_id,),
                ).fetchall()
        except Exception:
            return ""
        for r in rows:
            age = now - (r["updated_at"] or now)
            if age < _RESUME_MIN_AGE or age > _RESUME_MAX_AGE:
                continue
            if r["subject"] == cur_subject:
                continue  # already on this subject
            if r["turn_count"] < 2:
                continue  # too brief to be a real "task"
            summ = r["summary"] or f"关于「{r['subject']}」的分析"
            return (f"【可继续的任务】用户上次在进行：{summ}（主题：{r['subject']}，"
                    f"已交流{r['turn_count']}轮）。如与当前问题相关，可主动提示用户是否继续。")
        return ""

    def list_tasks(self, user_id: str, limit: int = 20) -> list[dict]:
        self._ensure_db()
        try:
            with self._db._conn() as c:
                rows = c.execute(
                    "SELECT id,subject,status,summary,turn_count,created_at,updated_at "
                    "FROM user_tasks WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


_task_memory: TaskMemory | None = None


def get_task_memory() -> TaskMemory:
    global _task_memory
    if _task_memory is None:
        _task_memory = TaskMemory()
    return _task_memory
