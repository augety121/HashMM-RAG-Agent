"""SkillManager — auto-create reusable skills from successful conversations.

Inspired by Hermes Agent's self-evolution: when a complex task gets positive
feedback, extract the "solving pattern" and save it as a Skill. Next time a
similar query arrives, the Skill's prompt template is injected for guidance.

Storage: SQLite table `skills` (created lazily).
"""
from __future__ import annotations

import json
import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evolution.skill")


@dataclass
class Skill:
    id: str
    name: str                          # e.g. "公司财务对比分析"
    description: str                   # when to use this skill
    trigger_patterns: list[str]        # ["对比", "比较", "vs"]
    prompt_template: str               # proven prompt structure
    examples: list[dict] = field(default_factory=list)  # {query, answer_snippet}
    quality_score: float = 0.0         # 0-1, from user feedback
    use_count: int = 0
    created_at: float = 0.0
    last_used: float = 0.0
    # Learned skills are data, not application code.  Their visibility must be
    # explicit so one user's successful conversation can never become another
    # user's system prompt by accident.
    owner_id: str = ""
    scope: str = "quarantined"          # builtin | personal | shared | quarantined
    workspace_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Skill:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SkillManager:
    """Manages the skill lifecycle: create → match → inject → evolve."""

    def __init__(self, db_module=None):
        self._db = db_module
        self._skills: list[Skill] = []
        self._loaded = False

    def _ensure_table(self):
        """Create skills table if not exists."""
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS skills (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        trigger_patterns TEXT,
                        prompt_template TEXT,
                        examples TEXT,
                        quality_score REAL DEFAULT 0,
                        use_count INTEGER DEFAULT 0,
                        created_at REAL,
                        last_used REAL
                    )
                """)
                columns = {str(row[1]) for row in c.execute("PRAGMA table_info(skills)").fetchall()}
                if "owner_id" not in columns:
                    c.execute("ALTER TABLE skills ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
                if "scope" not in columns:
                    c.execute("ALTER TABLE skills ADD COLUMN scope TEXT NOT NULL DEFAULT 'quarantined'")
                if "workspace_id" not in columns:
                    c.execute("ALTER TABLE skills ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''")
                # Only source-controlled built-ins are safe to expose globally.
                # Legacy learned rows have no trustworthy owner provenance and
                # therefore remain quarantined until an administrator reviews
                # and explicitly re-scopes them.
                c.execute(
                    "UPDATE skills SET scope='builtin' "
                    "WHERE id LIKE 'builtin-%' AND (scope='' OR scope='quarantined')"
                )
                c.execute("CREATE INDEX IF NOT EXISTS idx_skills_scope_owner ON skills(scope, owner_id)")
        except Exception as e:
            logger.warning(f"Failed to create skills table: {e}")

    def _load(self):
        """Load all skills from DB."""
        if self._loaded:
            return
        self._ensure_table()
        try:
            with self._db._conn() as c:
                rows = c.execute("SELECT * FROM skills ORDER BY quality_score DESC").fetchall()
            for r in rows:
                d = dict(r)
                d["trigger_patterns"] = json.loads(d.get("trigger_patterns", "[]"))
                d["examples"] = json.loads(d.get("examples", "[]"))
                self._skills.append(Skill.from_dict(d))
        except Exception as _e:
            log_suppressed(logger, _e)
        self._loaded = True

    # ── Creation ──

    def should_create_skill(
        self, query: str, answer: str, feedback: str | None = None,
        strategy: str = "grounded", n_sources: int = 0, owner_id: str = "",
    ) -> bool:
        """Decide whether a conversation is worth extracting as a Skill.

        Conditions:
        1. Answer is substantial (>300 chars, not a simple lookup)
        2. User gave positive feedback (👍)
        3. Strategy was grounded (sufficient evidence)
        4. No existing similar skill
        """
        if not feedback or feedback != "up":
            return False
        if len(answer) < 300:
            return False
        if strategy not in ("grounded", "augmented"):
            return False
        # Check for existing similar skill
        existing = self.match_skills(query, owner_id=owner_id)
        if existing and existing[0].quality_score > 0.7:
            return False
        return True

    def create_from_conversation(
        self, query: str, answer: str, sources: list[dict] | None = None,
        llm_fn: Any = None, owner_id: str = "", workspace_id: str = "",
    ) -> Skill | None:
        """Extract a skill from a successful conversation.

        If llm_fn is available, use LLM to analyze and extract structure.
        Otherwise, use rule-based extraction.
        """
        self._load()
        owner = str(owner_id or "").strip()[:160]
        if not owner:
            logger.warning("[SkillManager] Refused learned skill without an owner")
            return None
        skill_id = uuid.uuid4().hex[:12]
        now = time.time()

        # Rule-based extraction (no LLM needed)
        name = self._extract_skill_name(query)
        triggers = self._extract_triggers(query)
        template = self._build_template(query, answer)

        skill = Skill(
            id=skill_id,
            name=name,
            description=f"自动提取自成功对话: {query[:60]}",
            trigger_patterns=triggers,
            prompt_template=template,
            examples=[{"query": query[:200], "answer_snippet": answer[:300]}],
            quality_score=0.6,  # Start at 0.6, improve with feedback
            use_count=0,
            created_at=now,
            last_used=now,
            owner_id=owner,
            scope="personal",
            workspace_id=str(workspace_id or "").strip()[:160],
        )

        # If LLM available, try to get a better name and description
        if llm_fn and hasattr(llm_fn, 'quick_call'):
            try:
                raw = llm_fn.quick_call(
                    "你是技能提取器。从下面的问答中提取一个可复用的分析技能。"
                    "只返回JSON: {\"name\": \"...\", \"description\": \"...\", \"triggers\": [\"...\", ...]}",
                    f"问：{query[:200]}\n答：{answer[:400]}",
                    max_tok=200,
                )
                parsed = json.loads(raw.strip().strip("```json").strip("```"))
                if parsed.get("name"):
                    skill.name = parsed["name"]
                if parsed.get("description"):
                    skill.description = parsed["description"]
                if parsed.get("triggers"):
                    skill.trigger_patterns = list(set(skill.trigger_patterns + parsed["triggers"]))
            except Exception as _e:
                log_suppressed(logger, _e)

        # Save to DB
        self._save_skill(skill)
        self._skills.append(skill)
        logger.info(f"[SkillManager] Created skill: {skill.name} (triggers={skill.trigger_patterns})")
        return skill

    # ── Matching ──

    def match_skills(self, query: str, owner_id: str = "", workspace_id: str = "") -> list[Skill]:
        """Find skills matching the current query, sorted by relevance."""
        self._load()
        scored: list[tuple[float, Skill]] = []
        q_lower = query.lower()
        owner = str(owner_id or "").strip()
        workspace = str(workspace_id or "").strip()

        for skill in self._skills:
            visible = (
                skill.scope == "builtin"
                or skill.scope == "shared"
                or (skill.scope == "personal" and owner and skill.owner_id == owner)
                or (skill.scope == "workspace" and workspace and skill.workspace_id == workspace)
            )
            if not visible:
                continue
            score = 0.0
            for pattern in skill.trigger_patterns:
                p = (pattern or "").lower().strip()
                if not p:
                    continue
                if " " in p:                      # 多关键词触发：全部出现才算命中（修复空格触发词永不匹配的 bug）
                    kws = [k for k in p.split() if k]
                    if kws and all(k in q_lower for k in kws):
                        score += 1.0
                elif p in q_lower:
                    score += 1.0
            if score > 0:
                score += skill.quality_score * 0.5
                score += min(skill.use_count / 20, 0.3)  # Cap use_count bonus
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:3]]

    def inject_skill_context(
        self, query: str, messages: list[dict], *, owner_id: str = "", workspace_id: str = "",
    ) -> list[dict]:
        """Inject matching skill templates into the system prompt."""
        skills = self.match_skills(query, owner_id=owner_id, workspace_id=workspace_id)
        if not skills:
            return messages

        skill_ctx = "\n\n".join([
            f"## 参考策略：{s.name}\n{s.prompt_template}"
            for s in skills[:2]
        ])

        # Append to first system message
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += f"\n\n{skill_ctx}"

        # Update use stats
        for s in skills[:2]:
            s.use_count += 1
            s.last_used = time.time()
            self._update_skill_stats(s)

        return messages

    def record_use(self, skill_id: str, owner_id: str = ""):
        """V269 单技能使用打点（对齐《How we use skills》：有使用数据才能发现
        "欠触发/热门"技能并针对性改描述）。此前只有 inject_skills_to_messages 计数，
        轻路径（streaming 手工注入 prompt_template）一直漏记——现在两条路径都记。"""
        self._load()
        for s in self._skills:
            if s.id == skill_id:
                if s.scope == "personal" and s.owner_id != str(owner_id or ""):
                    return
                if s.scope == "quarantined":
                    return
                s.use_count += 1
                s.last_used = time.time()
                self._update_skill_stats(s)
                return

    # ── Feedback ──

    def update_quality(self, skill_id: str, feedback: str, *, owner_id: str = "", admin: bool = False) -> bool:
        """Update skill quality based on user feedback."""
        self._load()
        for skill in self._skills:
            if skill.id == skill_id:
                if not admin and not (
                    skill.scope == "personal" and skill.owner_id == str(owner_id or "")
                ):
                    return False
                if feedback == "up":
                    skill.quality_score = min(1.0, skill.quality_score + 0.1)
                elif feedback == "down":
                    skill.quality_score = max(0.0, skill.quality_score - 0.15)
                else:
                    return False
                self._update_skill_stats(skill)
                return True
        return False

    # ── Internal helpers ──

    def _extract_skill_name(self, query: str) -> str:
        """Extract a short skill name from the query."""
        if "对比" in query or "比较" in query:
            return "对比分析"
        if "趋势" in query or "变化" in query:
            return "趋势分析"
        if "营收" in query or "利润" in query or "财务" in query:
            return "财务数据查询"
        if "代码" in query or "实现" in query:
            return "代码生成"
        return query[:20].strip()

    def _extract_triggers(self, query: str) -> list[str]:
        """Extract trigger keywords from query."""
        triggers = []
        kw_map = {
            "对比": ["对比", "比较", "vs", "差异"],
            "趋势": ["趋势", "增长", "变化", "走势"],
            "营收": ["营收", "收入", "利润", "毛利率"],
            "分析": ["分析", "解读", "评估"],
            "代码": ["代码", "实现", "编写", "开发"],
        }
        for key, kws in kw_map.items():
            if key in query:
                triggers.extend(kws)
        return list(set(triggers)) or [query[:10]]

    def _build_template(self, query: str, answer: str) -> str:
        """Build a reusable prompt template from the Q&A pattern."""
        if "对比" in query or "比较" in query:
            return (
                "进行对比分析时，请按以下结构组织回答：\n"
                "1. 概述两者的主要特征\n"
                "2. 逐维度对比（用表格呈现）\n"
                "3. 总结差异和各自优势\n"
                "4. 引用具体数据源"
            )
        if "趋势" in query:
            return (
                "分析趋势时，请按以下结构：\n"
                "1. 当前状态概述\n"
                "2. 历史数据回顾\n"
                "3. 关键转折点分析\n"
                "4. 未来展望"
            )
        return f"参考此类问题的回答模式：先给出核心结论，再展开分析，最后引用来源。"

    def _save_skill(self, skill: Skill):
        """Persist a skill to DB."""
        try:
            with self._db._conn() as c:
                c.execute("""
                    INSERT OR REPLACE INTO skills
                    (id, name, description, trigger_patterns, prompt_template,
                     examples, quality_score, use_count, created_at, last_used,
                     owner_id, scope, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    skill.id, skill.name, skill.description,
                    json.dumps(skill.trigger_patterns, ensure_ascii=False),
                    skill.prompt_template,
                    json.dumps(skill.examples, ensure_ascii=False),
                    skill.quality_score, skill.use_count,
                    skill.created_at, skill.last_used,
                    skill.owner_id, skill.scope, skill.workspace_id,
                ))
        except Exception as e:
            logger.warning(f"Failed to save skill: {e}")

    def _update_skill_stats(self, skill: Skill):
        """Update use_count, last_used, quality_score in DB."""
        try:
            with self._db._conn() as c:
                c.execute("""
                    UPDATE skills SET use_count=?, last_used=?, quality_score=?
                    WHERE id=?
                """, (skill.use_count, skill.last_used, skill.quality_score, skill.id))
        except Exception as _e:
            log_suppressed(logger, _e)

    @staticmethod
    def prompt_hash(prompt: str) -> str:
        return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()

    def update_prompt(
        self,
        skill_id: str,
        prompt: str,
        *,
        owner_id: str = "",
        admin: bool = False,
        expected_hash: str = "",
    ) -> dict:
        """Atomically replace one governed skill prompt.

        Evolution decisions use compare-and-swap semantics: a candidate created
        against an older baseline cannot overwrite a prompt that changed while
        the review dialog was open. The method updates both SQLite and the
        in-process cache only after the guarded write succeeds.
        """
        self._load()
        candidate = str(prompt or "").replace("\x00", "").strip()
        if not candidate or len(candidate) > 12_000:
            return {"state": "invalid"}
        owner = str(owner_id or "")
        target = next((skill for skill in self._skills if skill.id == skill_id), None)
        if target is None:
            return {"state": "missing"}
        mutable = admin or (
            target.scope == "personal" and owner and target.owner_id == owner
        )
        if not mutable:
            return {"state": "missing"}
        current_hash = self.prompt_hash(target.prompt_template)
        if expected_hash and current_hash != str(expected_hash):
            return {"state": "conflict", "current_hash": current_hash}
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT prompt_template FROM skills WHERE id=?", (skill_id,),
            ).fetchone()
            if row is None:
                return {"state": "missing"}
            persisted = str(row["prompt_template"] or "")
            persisted_hash = self.prompt_hash(persisted)
            if expected_hash and persisted_hash != str(expected_hash):
                return {"state": "conflict", "current_hash": persisted_hash}
            conn.execute(
                "UPDATE skills SET prompt_template=? WHERE id=? AND prompt_template=?",
                (candidate, skill_id, persisted),
            )
            if conn.total_changes < 1:
                return {
                    "state": "conflict",
                    "current_hash": self.prompt_hash(
                        str(conn.execute(
                            "SELECT prompt_template FROM skills WHERE id=?", (skill_id,),
                        ).fetchone()["prompt_template"] or "")
                    ),
                }
        target.prompt_template = candidate
        return {
            "state": "updated",
            "previous_hash": current_hash,
            "prompt_hash": self.prompt_hash(candidate),
        }

    def list_skills(
        self, owner_id: str = "", workspace_id: str = "", *, include_all: bool = False,
    ) -> list[dict]:
        """Return only skills visible to a principal unless explicitly auditing."""
        self._load()
        if include_all:
            return [s.to_dict() for s in self._skills]
        owner = str(owner_id or "")
        workspace = str(workspace_id or "")
        return [
            s.to_dict() for s in self._skills
            if s.scope in {"builtin", "shared"}
            or (s.scope == "personal" and owner and s.owner_id == owner)
            or (s.scope == "workspace" and workspace and s.workspace_id == workspace)
        ]

    def get_skill(self, skill_id: str, owner_id: str = "", *, include_all: bool = False) -> Skill | None:
        self._load()
        for skill in self._skills:
            if skill.id != skill_id:
                continue
            if include_all or skill.scope in {"builtin", "shared"}:
                return skill
            if skill.scope == "personal" and skill.owner_id == str(owner_id or ""):
                return skill
            return None
        return None

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill by ID."""
        self._load()
        self._skills = [s for s in self._skills if s.id != skill_id]
        try:
            with self._db._conn() as c:
                c.execute("DELETE FROM skills WHERE id=?", (skill_id,))
            return True
        except Exception:
            return False


# Module singleton
_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    global _manager
    if _manager is None:
        _manager = SkillManager()
    return _manager
