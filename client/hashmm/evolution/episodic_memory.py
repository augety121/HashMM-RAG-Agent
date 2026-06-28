"""Episodic Memory — remembers specific interaction outcomes for future decisions.

Hermes Agent's three-layer memory:
  Layer 1: Working memory (current conversation context) — already exists
  Layer 2: Semantic memory (user_profiles + interests) — v10 UserModel
  Layer 3: Episodic memory (this file) — specific success/failure experiences

When a user asks something similar to a past interaction, episodic memory
provides "last time we used X strategy and it worked" guidance.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evolution.episodic")


class EpisodicMemory:
    """Stores and retrieves interaction episodes for strategy selection."""

    def __init__(self, db_module=None):
        self._db = db_module

    def _ensure_db(self):
        if not self._db:
            from hashmm.api import database as db
            self._db = db
        try:
            with self._db._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS episodes (
                        id TEXT PRIMARY KEY,
                        user_id TEXT DEFAULT '',
                        query TEXT NOT NULL,
                        query_type TEXT DEFAULT '',
                        strategy TEXT DEFAULT '',
                        outcome TEXT DEFAULT 'unknown',
                        feedback TEXT DEFAULT '',
                        key_entities TEXT DEFAULT '[]',
                        key_insight TEXT DEFAULT '',
                        answer_length INTEGER DEFAULT 0,
                        elapsed_ms INTEGER DEFAULT 0,
                        reward REAL DEFAULT 0.0,
                        created_at REAL
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome)")
                # V103.90: 对**已存在**的旧表（真机已有数据）幂等补 reward 列——
                # CREATE TABLE IF NOT EXISTS 不会给旧表加列，必须显式 ALTER（用 PRAGMA 探测，
                # 已存在则跳过）。这样升级不破坏既有 episodes 数据。
                try:
                    _cols = {r[1] for r in c.execute("PRAGMA table_info(episodes)").fetchall()}
                    if "reward" not in _cols:
                        c.execute("ALTER TABLE episodes ADD COLUMN reward REAL DEFAULT 0.0")
                        logger.info("[EpisodicMemory] migrated: added reward column")
                except Exception as _me:
                    log_suppressed(logger, _me)
        except Exception as _e:
            log_suppressed(logger, _e)

    # ── Reward（奖励驱动：把"答得好不好"量化为标量，驱动召回/淘汰）──

    @staticmethod
    def compute_reward(
        *,
        confidence: float | None = None,
        faithfulness_ratio: float | None = None,
        grounded: bool | None = None,
        n_sources: int = 0,
        top_score: float = 0.0,
        answer: str = "",
        feedback: str = "",
        outcome: str = "",
        task_type: str = "",
    ) -> float:
        """把一回合的多种质量信号融合成 0~1 的标量奖励（纯函数，无 IO，可单测）。

        设计：以"中性 0.5"为基线，各信号在其上加减；最后裁剪到 [0,1]。
        优先用强信号（用户显式 up/down、忠实度接地率、self-RAG 置信度），
        弱信号（来源数/相关度/答案长度/错误措辞）作微调。对标 MemoPilot/JitRL
        "用下游奖励驱动记忆"的思路——这里的 reward 即下游奖励的轻量代理。
        """
        # 强信号优先：用户显式反馈直接定调。
        if feedback == "up":
            base = 0.92
        elif feedback == "down":
            base = 0.12
        else:
            base = 0.5

        # 忠实度接地率（Task 1 产物）——最贴近"答得对不对"，权重最高。
        if faithfulness_ratio is not None:
            base = base * 0.45 + float(faithfulness_ratio) * 0.55

        # self-RAG 置信度。
        if confidence is not None:
            base = base * 0.7 + float(confidence) * 0.3

        # 接地与否（布尔门控）。
        if grounded is True:
            base += 0.05
        elif grounded is False:
            base -= 0.08

        # 弱信号微调（来源 / 相关度 / 长度 / 错误措辞）。
        if n_sources > 0 and top_score > 1.0:
            base += 0.05
        elif n_sources == 0 and task_type not in ("code_task", "direct_task", "greeting"):
            base -= 0.05
        alen = len(answer or "")
        if alen and alen < 40:
            base -= 0.08            # 太短，多半没答好
        elif alen > 200:
            base += 0.03
        if any(p in (answer or "")[:200] for p in ("抱歉", "无法回答", "未能", "出错", "Error", "失败")):
            base -= 0.10
        if outcome == "success":
            base += 0.04
        elif outcome in ("error", "failed"):
            base -= 0.10

        return max(0.0, min(1.0, round(base, 4)))

    # ── Record ──

    def record(
        self,
        user_id: str,
        query: str,
        query_type: str = "",
        strategy: str = "",
        outcome: str = "unknown",
        feedback: str = "",
        answer: str = "",
        elapsed_ms: int = 0,
        *,
        episode_id: str | None = None,
        reward: float | None = None,
        confidence: float | None = None,
        faithfulness_ratio: float | None = None,
        grounded: bool | None = None,
        retrieval_mode: str = "",
        n_sources: int = 0,
        top_score: float = 0.0,
        answer_length: int | None = None,
    ):
        """记录一次交互 episode（完成后调用）。

        V103.90: 修复历史 bug——调用方（streaming._run_evolution_safe）一直传
        ``episode_id/retrieval_mode/n_sources/top_score/answer_length`` 等 kwarg，
        而旧签名不接受，导致每次 record 抛 TypeError 被静默吞掉、**episode 从未真正写入**。
        现接受这些参数并落地。同时引入奖励驱动：未显式给 ``reward`` 时按 compute_reward
        从可用信号自动算出并持久化，供 recall 优先召回高 reward、decay 优先淘汰低 reward。
        质量门：env ``HASHMM_EPISODE_MIN_REWARD`` 可设入库下限（默认 0.0=全记，
        因 recall 已对低分降权；需要更激进时可调高，把坏经验直接挡在库外）。
        """
        self._ensure_db()
        import uuid
        import os as _os

        if reward is None:
            reward = self.compute_reward(
                confidence=confidence, faithfulness_ratio=faithfulness_ratio,
                grounded=grounded, n_sources=n_sources, top_score=top_score,
                answer=answer, feedback=feedback, outcome=outcome, task_type=query_type,
            )
        # 经验质量门（可选，默认不挡）。
        try:
            _floor = float(_os.environ.get("HASHMM_EPISODE_MIN_REWARD", "0") or 0)
        except Exception:
            _floor = 0.0
        if reward < _floor:
            return

        entities = self._extract_entities(query)
        insight = self._generate_insight(query, strategy, outcome, feedback)
        alen = answer_length if answer_length is not None else len(answer)
        eid = (episode_id or uuid.uuid4().hex[:12])

        try:
            with self._db._conn() as c:
                c.execute("""
                    INSERT INTO episodes
                    (id, user_id, query, query_type, strategy, outcome, feedback,
                     key_entities, key_insight, answer_length, elapsed_ms, reward, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    eid, user_id, query[:500],
                    query_type, strategy, outcome, feedback,
                    json.dumps(entities, ensure_ascii=False),
                    insight, alen, elapsed_ms, float(reward), time.time(),
                ))
        except Exception as e:
            logger.warning(f"Failed to record episode: {e}")

    # ── Recall ──

    def recall(self, user_id: str, query: str, limit: int = 3) -> list[dict]:
        """Find relevant past episodes for strategy guidance.

        Matching logic:
        1. Same user + overlapping entities → high relevance
        2. Similar query type → medium relevance
        3. Positive feedback episodes ranked higher
        """
        self._ensure_db()
        entities = self._extract_entities(query)

        try:
            with self._db._conn() as c:
                # Get recent episodes for this user
                rows = c.execute("""
                    SELECT * FROM episodes
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (user_id,)).fetchall()

            if not rows:
                return []

            # Score each episode by relevance
            scored = []
            for row in rows:
                d = dict(row)
                score = 0.0

                # Entity overlap
                ep_entities = json.loads(d.get("key_entities", "[]"))
                overlap = set(entities) & set(ep_entities)
                score += len(overlap) * 2.0

                # Keyword overlap
                q_words = set(query.lower().split())
                ep_words = set(d["query"].lower().split())
                word_overlap = len(q_words & ep_words)
                score += min(word_overlap * 0.3, 2.0)

                # Positive feedback bonus
                if d.get("feedback") == "up":
                    score += 1.5
                elif d.get("feedback") == "down":
                    score -= 1.0

                # V103.90: 奖励驱动——高 reward 经验更值得复用，低 reward 降权。
                # reward 已归一到 0~1（旧行缺省 0，等价中性偏低，不会喧宾夺主）。
                try:
                    rwd = float(d.get("reward") or 0.0)
                except Exception:
                    rwd = 0.0
                score += (rwd - 0.5) * 2.0          # 0.5 中性=0；1.0→+1.0；0.0→-1.0

                # Recency bonus (last 24h)
                age_hours = (time.time() - d.get("created_at", 0)) / 3600
                if age_hours < 24:
                    score += 0.5

                if score > 0.5:
                    d["relevance_score"] = score
                    d["reward"] = rwd
                    scored.append(d)

            scored.sort(key=lambda x: x["relevance_score"], reverse=True)
            return scored[:limit]

        except Exception as e:
            logger.warning(f"Episode recall failed: {e}")
            return []

    def get_strategy_hint(self, user_id: str, query: str) -> str:
        """Generate a strategy hint from episodic memory for the system prompt.

        V103.90: 奖励感知——高 reward 经验作正面参考、低 reward 作前车之鉴，
        让"越用越强"既学好经验也避坑。
        """
        episodes = self.recall(user_id, query, limit=2)
        if not episodes:
            return ""

        hints = []
        for ep in episodes:
            insight = ep.get("key_insight", "")
            strategy = ep.get("strategy", "")
            try:
                rwd = float(ep.get("reward") or 0.0)
            except Exception:
                rwd = 0.0
            # 标记优先看显式反馈，其次看 reward 高低。
            if ep.get("feedback") == "up" or rwd >= 0.7:
                mark = "✅"
            elif ep.get("feedback") == "down" or (0 < rwd < 0.35):
                mark = "⚠️"
            else:
                mark = "—"
            if insight:
                hints.append(f"{mark} 类似问题经验：{insight}")
            elif strategy:
                tip = "（效果好，可复用）" if mark == "✅" else "（上次效果欠佳，建议调整）" if mark == "⚠️" else ""
                hints.append(f"{mark} 上次用了「{strategy}」策略{tip}")

        if hints:
            return "【历史经验】\n" + "\n".join(hints)
        return ""

    def update_reward(self, user_id: str, reward: float, *, episode_id: str | None = None,
                      only_if_lower: bool = False):
        """更新某 episode 的 reward（不传 id 则更新该用户最近一条）。

        用可移植子查询定位最近一条（不依赖 ``UPDATE ... ORDER BY LIMIT`` 这种需特定
        编译选项的语法——后者在部分 SQLite 构建上直接报错）。供异步 self-eval 在拿到
        更充分的质量信号后回填 reward。

        ``only_if_lower=True``：仅当新值更低时才更新（保守校准——自评只能"发现更多问题"
        而非给没接地的答案虚高评分，避免覆盖掉记录时基于忠实度算出的更强 reward）。
        """
        self._ensure_db()
        try:
            with self._db._conn() as c:
                if episode_id:
                    if only_if_lower:
                        c.execute("UPDATE episodes SET reward = ? WHERE id = ? "
                                  "AND COALESCE(reward,0) > ?",
                                  (float(reward), episode_id, float(reward)))
                    else:
                        c.execute("UPDATE episodes SET reward = ? WHERE id = ?",
                                  (float(reward), episode_id))
                else:
                    cond = "AND COALESCE(reward,0) > ?" if only_if_lower else ""
                    params = ([float(reward), user_id, float(reward)] if only_if_lower
                              else [float(reward), user_id])
                    c.execute(f"""
                        UPDATE episodes SET reward = ?
                        WHERE id = (
                            SELECT id FROM episodes WHERE user_id = ? {cond}
                            ORDER BY created_at DESC LIMIT 1
                        )
                    """, params)
        except Exception as _e:
            log_suppressed(logger, _e)

    # ── Decay ──

    def decay(self, max_age_days: int = 90, max_entries: int = 500,
              protect_reward: float = 0.7):
        """Remove old episodes to prevent unbounded growth.

        Called periodically (e.g. daily via startup or cron).

        V103.90: 奖励感知淘汰——① 删旧时同时保护"高 reward"经验（不止保护 up 反馈）；
        ② 超额裁剪时**优先淘汰低 reward**（而非单纯按时间），把好经验留下来。
        """
        self._ensure_db()
        cutoff = time.time() - max_age_days * 86400
        try:
            with self._db._conn() as c:
                # Remove old, low-value entries (保护 up 反馈 与 高 reward 经验)
                deleted_old = c.execute(
                    "DELETE FROM episodes WHERE created_at < ? AND feedback != 'up' "
                    "AND COALESCE(reward, 0) < ?",
                    (cutoff, protect_reward)
                ).rowcount

                # Cap total entries——超额时优先删"低 reward、且较旧"的（综合排序保留高价值）。
                c.execute("""
                    DELETE FROM episodes WHERE id IN (
                        SELECT id FROM episodes
                        ORDER BY (CASE WHEN feedback='up' THEN 1 ELSE 0 END) DESC,
                                 COALESCE(reward, 0) DESC,
                                 created_at DESC
                        LIMIT -1 OFFSET ?
                    )
                """, (max_entries,))

                if deleted_old:
                    logger.info(f"[EpisodicMemory] Decayed {deleted_old} old low-reward episodes")
        except Exception as _e:
            log_suppressed(logger, _e)

    def snapshot(self) -> int:
        """把当前 episodes 复制到备份表 ``episodes_backup``（覆盖式），返回备份条数。

        奖励驱动有"学到错经验越用越偏"的风险——快照 + rollback 是必备的安全阀：
        升级/批量学习前先 snapshot()，发现退化用 rollback() 一键回到上个好状态。
        """
        self._ensure_db()
        try:
            with self._db._conn() as c:
                c.execute("DROP TABLE IF EXISTS episodes_backup")
                c.execute("CREATE TABLE episodes_backup AS SELECT * FROM episodes")
                n = c.execute("SELECT COUNT(*) FROM episodes_backup").fetchone()[0]
            logger.info(f"[EpisodicMemory] snapshot saved: {n} episodes")
            return int(n)
        except Exception as _e:
            log_suppressed(logger, _e)
            return 0

    def rollback(self) -> int:
        """从 ``episodes_backup`` 恢复 episodes（覆盖当前），返回恢复条数；无备份则不动。"""
        self._ensure_db()
        try:
            with self._db._conn() as c:
                has = c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes_backup'"
                ).fetchone()
                if not has:
                    logger.warning("[EpisodicMemory] rollback skipped: no snapshot")
                    return 0
                c.execute("DELETE FROM episodes")
                c.execute("INSERT INTO episodes SELECT * FROM episodes_backup")
                n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            logger.info(f"[EpisodicMemory] rolled back to snapshot: {n} episodes")
            return int(n)
        except Exception as _e:
            log_suppressed(logger, _e)
            return 0

    # ── Stats ──

    def stats(self) -> dict:
        self._ensure_db()
        try:
            with self._db._conn() as c:
                total = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                positive = c.execute("SELECT COUNT(*) FROM episodes WHERE feedback='up'").fetchone()[0]
                negative = c.execute("SELECT COUNT(*) FROM episodes WHERE feedback='down'").fetchone()[0]
            return {"total": total, "positive": positive, "negative": negative}
        except Exception:
            return {"total": 0, "positive": 0, "negative": 0}

    # ── Helpers ──

    def _extract_entities(self, text: str) -> list[str]:
        entities = []
        for m in re.finditer(r'([\u4e00-\u9fff]{2,6}(?:集团|公司|科技|汽车|银行))', text):
            entities.append(m.group(1))
        for m in re.finditer(r'(营收|利润|毛利率|研发|市值|出货量|增长率)', text):
            entities.append(m.group(1))
        for m in re.finditer(r'(20\d{2})', text):
            entities.append(m.group(1))
        return list(set(entities))

    def _generate_insight(
        self, query: str, strategy: str, outcome: str, feedback: str
    ) -> str:
        """Rule-based insight generation (no LLM needed)."""
        parts = []
        if "对比" in query or "比较" in query:
            parts.append("对比分析类问题")
        if "趋势" in query or "变化" in query:
            parts.append("趋势分析")
        if strategy:
            parts.append(f"策略={strategy}")
        if feedback == "up":
            parts.append("用户满意")
        elif feedback == "down":
            parts.append("需改进")
        return "，".join(parts) if parts else ""


# Singleton
_memory: EpisodicMemory | None = None


def get_episodic_memory() -> EpisodicMemory:
    global _memory
    if _memory is None:
        _memory = EpisodicMemory()
    return _memory
