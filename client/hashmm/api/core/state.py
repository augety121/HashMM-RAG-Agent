"""Core state objects — Metrics, SemanticCache, PersistentMemory.

Extracted from server.py to eliminate circular imports and keep
each class testable in isolation. All three are instantiated once
by ServiceRegistry and shared via app_state.

Thread-safety:
  Metrics      — append-only counters, safe without locks for single-writer
  SemanticCache — list-based, tolerable race (worst case: duplicate entry)
  PersistentMemory — disk I/O on a daemon thread, dict mutations from
                     the main thread only (FastAPI is single-threaded per worker)
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.state")

# ═══════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════

class Metrics:
    """Lightweight in-memory metrics collector.

    Designed for a single-process deployment (AutoDL). For multi-process,
    swap in Prometheus client.
    """

    def __init__(self) -> None:
        self.total_queries: int = 0
        self.cache_hits: int = 0
        self.avg_latency_ms: float = 0
        self.total_llm_calls: int = 0
        self.total_retrieve_calls: int = 0
        self.intent_counts: Counter = Counter()
        self.skill_counts: Counter = Counter()
        self._latencies: list[float] = []

    def record(self, latency_ms: float, intent: str,
               skills: list[str], cache_hit: bool = False) -> None:
        self.total_queries += 1
        self._latencies.append(latency_ms)
        if len(self._latencies) > 500:
            self._latencies = self._latencies[-200:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        self.intent_counts[intent] += 1
        for s in skills:
            self.skill_counts[s] += 1
        if cache_hit:
            self.cache_hits += 1

    def to_dict(self) -> dict:
        sorted_lat = sorted(self._latencies) if self._latencies else [0]
        p95_idx = int(len(sorted_lat) * 0.95)
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": f"{self.cache_hits / max(self.total_queries, 1) * 100:.1f}%",
            "avg_latency_ms": round(self.avg_latency_ms),
            "p95_latency_ms": round(sorted_lat[p95_idx]),
            "total_llm_calls": self.total_llm_calls,
            "intents": dict(self.intent_counts.most_common(10)),
            "skills": dict(self.skill_counts.most_common(10)),
        }


# ═══════════════════════════════════════════════════════════════════
# Semantic Cache
# ═══════════════════════════════════════════════════════════════════

class SemanticCache:
    """Embedding-cosine cache for identical/near-identical queries.

    When a user repeats the same question, we can skip retrieval + LLM
    entirely and return the cached answer. Threshold = 0.92 cosine
    (very strict, only near-exact duplicates).
    """

    def __init__(self, threshold: float = 0.92, max_size: int = 200) -> None:
        self.threshold = threshold
        self.max_size = max_size
        self.entries: list[dict] = []

    def lookup(self, q_emb: np.ndarray) -> dict | None:
        if not self.entries:
            return None
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        best_sim, best_entry = 0.0, None
        for e in self.entries:
            sim = float(np.dot(q_norm.flatten(), e["emb"].flatten()))
            if sim > best_sim:
                best_sim, best_entry = sim, e
        return best_entry if best_sim >= self.threshold else None

    def store(self, q_emb: np.ndarray, query: str,
              answer: str, sources: list) -> None:
        q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        self.entries.append({
            "emb": q_norm.flatten(),
            "query": query,
            "answer": answer,
            "sources": sources,
            "ts": time.time(),
        })
        if len(self.entries) > self.max_size:
            self.entries = self.entries[-self.max_size // 2:]


# ═══════════════════════════════════════════════════════════════════
# Persistent Memory
# ═══════════════════════════════════════════════════════════════════

_PERSIST_INTERVAL = 60  # seconds between auto-saves


class PersistentMemory:
    """Cross-session memory with periodic disk persistence.

    Stores:
      sessions  — {sid: {title, created, user_id}}
      history   — {sid: [{role, content, ts}, ...]}
      profiles  — {sid: {topics, intents, expertise, ...}}
      user_profiles — {user_id: {global profile}}
      episodes  — [{ts, ...}]  (audit log)
    """

    def __init__(self, persist_dir: str | Path = "data/agent_state") -> None:
        self.dir = Path(persist_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, dict] = {}
        self.history: dict[str, list] = defaultdict(list)
        self.profiles: dict[str, dict] = {}
        self.episodes: list[dict] = []
        self.user_profiles: dict[str, dict] = {}
        self._load_from_disk()
        self._start_auto_save()

    # ── Disk I/O ──

    def _load_from_disk(self) -> None:
        for name in ["sessions", "profiles", "user_profiles"]:
            p = self.dir / f"{name}.json"
            if p.exists():
                try:
                    setattr(self, name, json.loads(p.read_text()))
                    logger.info(f"[Persist] Loaded {name}: {len(getattr(self, name))} entries")
                except Exception as _e:
                    log_suppressed(logger, _e)
        p = self.dir / "episodes.json"
        if p.exists():
            try:
                self.episodes = json.loads(p.read_text())
            except Exception as _e:
                log_suppressed(logger, _e)
        p = self.dir / "history.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.history = defaultdict(list, data)
            except Exception as _e:
                log_suppressed(logger, _e)

    def save_to_disk(self) -> None:
        try:
            for name in ["sessions", "profiles", "user_profiles"]:
                (self.dir / f"{name}.json").write_text(
                    json.dumps(getattr(self, name), ensure_ascii=False, default=str))
            (self.dir / "episodes.json").write_text(
                json.dumps(self.episodes[-500:], ensure_ascii=False, default=str))
            (self.dir / "history.json").write_text(
                json.dumps(dict(self.history), ensure_ascii=False, default=str))
        except Exception as e:
            logger.warning(f"[Persist] Save failed: {e}")

    def _start_auto_save(self) -> None:
        def _loop():
            while True:
                time.sleep(_PERSIST_INTERVAL)
                self.save_to_disk()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    # ── Session CRUD ──

    def get_or_create_session(self, sid: str, title: str = "",
                              user_id: str = "") -> dict:
        if sid not in self.sessions:
            self.sessions[sid] = {
                "title": title[:30],
                "created": time.time(),
                "user_id": user_id,
            }
        return self.sessions[sid]

    def add_message(self, sid: str, role: str, content: str) -> None:
        self.history[sid].append({
            "role": role,
            "content": content,
            "ts": time.time(),
        })

    def get_history(self, sid: str) -> list:
        return self.history[sid]

    def get_user_sessions(self, user_id: str) -> list:
        return [
            {"id": k, **v}
            for k, v in self.sessions.items()
            if v.get("user_id") == user_id
        ]

    def delete_session(self, sid: str) -> None:
        self.sessions.pop(sid, None)
        self.history.pop(sid, None)

    # ── Profile tracking ──

    def update_profile(self, sid: str, query: str,
                       intent: str, topics: list[str]) -> None:
        if sid not in self.profiles:
            self.profiles[sid] = {
                "topics": {}, "intents": {}, "queries": 0,
                "lang_pref": {}, "expertise": {},
                "prefers_code": 0, "prefers_detail": 0,
                "recent_topics": [], "project_context": "",
            }
        p = self.profiles[sid]
        p["queries"] += 1
        p["intents"][intent] = p["intents"].get(intent, 0) + 1
        for t in topics:
            p["topics"][t] = p["topics"].get(t, 0) + 1
        p["recent_topics"] = (p.get("recent_topics", []) + topics)[-10:]

        lang = "zh" if re.search(r'[\u4e00-\u9fff]', query) else "en"
        p["lang_pref"][lang] = p["lang_pref"].get(lang, 0) + 1

        expert_signals = ["实现", "代码", "架构", "优化", "源码", "论文",
                          "implement", "architecture", "optimize"]
        beginner_signals = ["什么是", "介绍", "入门", "基础",
                            "what is", "explain", "beginner"]
        for s in expert_signals:
            if s in query.lower():
                p["expertise"]["advanced"] = p["expertise"].get("advanced", 0) + 1
        for s in beginner_signals:
            if s in query.lower():
                p["expertise"]["beginner"] = p["expertise"].get("beginner", 0) + 1

        code_signals = ["代码", "code", "实现", ".py", "函数", "class"]
        if any(s in query.lower() for s in code_signals):
            p["prefers_code"] = p.get("prefers_code", 0) + 1
        if len(query) > 50:
            p["prefers_detail"] = p.get("prefers_detail", 0) + 1

        project_kw = re.findall(
            r'(?:项目|project|系统|system|框架|framework)\s*[：:]*\s*(\S+)', query)
        if project_kw:
            p["project_context"] = project_kw[0][:30]

    def get_profile_context(self, sid: str) -> str:
        p = self.profiles.get(sid)
        if not p or p["queries"] < 2:
            return ""
        parts = []
        top = sorted(p["topics"].items(), key=lambda x: -x[1])[:5]
        if top:
            parts.append(f"关注领域：{'、'.join(t for t, _ in top)}")

        adv = p.get("expertise", {}).get("advanced", 0)
        beg = p.get("expertise", {}).get("beginner", 0)
        if adv > beg + 2:
            parts.append("用户是高级研究者，请给出深入、技术性的回答")
        elif beg > adv + 2:
            parts.append("用户偏好基础讲解，请用通俗易懂的语言")

        zh = p.get("lang_pref", {}).get("zh", 0)
        en = p.get("lang_pref", {}).get("en", 0)
        if zh > en * 2:
            parts.append("用户偏好中文回答")
        elif en > zh * 2:
            parts.append("用户偏好英文回答")
        if p.get("prefers_code", 0) > 3:
            parts.append("用户经常需要代码，尽量提供可运行的代码示例")
        if p.get("project_context"):
            parts.append(f"用户正在开发：{p['project_context']}")
        recent = p.get("recent_topics", [])
        if recent:
            parts.append(f"近期关注：{'、'.join(set(recent[-5:]))}")

        if not parts:
            return ""
        return f"[用户画像] {' · '.join(parts)}\n"

    # ── Global user profile (cross-session) ──

    def update_user_profile(self, user_id: str, query: str,
                            intent: str, topics: list[str]) -> None:
        if not user_id or user_id == "anonymous":
            return
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = {
                "total_queries": 0, "topics": {}, "intents": {},
                "lang_pref": {}, "expertise_score": 0,
                "first_seen": time.time(), "last_seen": time.time(),
            }
        up = self.user_profiles[user_id]
        up["total_queries"] += 1
        up["last_seen"] = time.time()
        up["intents"][intent] = up["intents"].get(intent, 0) + 1
        for t in topics:
            up["topics"][t] = up["topics"].get(t, 0) + 1
        lang = "zh" if re.search(r'[\u4e00-\u9fff]', query) else "en"
        up["lang_pref"][lang] = up["lang_pref"].get(lang, 0) + 1

        expert_words = ["实现", "架构", "优化", "implement", "architecture"]
        if any(w in query.lower() for w in expert_words):
            up["expertise_score"] += 1
        basic_words = ["什么是", "介绍", "入门", "what is", "explain"]
        if any(w in query.lower() for w in basic_words):
            up["expertise_score"] -= 1

    def get_user_context(self, user_id: str) -> str:
        up = self.user_profiles.get(user_id)
        if not up or up.get("total_queries", 0) < 3:
            return ""
        parts = []
        top = sorted(up.get("topics", {}).items(), key=lambda x: -x[1])[:5]
        if top:
            parts.append(f"长期关注：{'、'.join(t for t, _ in top)}")
        score = up.get("expertise_score", 0)
        if score > 3:
            parts.append("资深用户，偏好深入技术回答")
        elif score < -3:
            parts.append("偏好通俗易懂的解释")
        total = up.get("total_queries", 0)
        if total > 20:
            parts.append(f"活跃用户（累计 {total} 次对话）")
        if not parts:
            return ""
        return f"[用户历史] {' · '.join(parts)}\n"

    def log_episode(self, **kwargs: Any) -> None:
        self.episodes.append({"ts": time.time(), **kwargs})
        if len(self.episodes) > 2000:
            self.episodes = self.episodes[-1000:]
