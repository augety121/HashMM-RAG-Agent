"""memory/hub — 统一记忆中枢（V249，借鉴 cognee 与 codebase-memory-mcp）。

两个项目的可借鉴点（README 原文可查，不是照搬实现）：
  · cognee：向量+图谱**混合召回**成一条记忆层；「执行后回写」——任务结束把
    结果/教训写回记忆，agent 不再重复同一个错误；时间线可重建。
  · codebase-memory-mcp：把知识做成**持久可查的图**，agent 用一次结构化查询
    代替翻文件（token 省 99%）；不内置 LLM——"正在对话的 agent 就是大脑"。

适配到本项目（做减法，全部踩在既有存储上，零新表零新依赖）：
  本仓库已有四路记忆但互不相通——MemoryService（显著度/衰减/强化）、
  episodic（经验回放 SQLite）、user_memories（用户画像表）、KG（实体图）。
  hub 只做三件事：
    ① recall(uid, q)   —— 四路联邦召回，统一 {kind, text, score, ts, source} 排序返回；
    ② record_outcome() —— 任务/团队执行完把「结果或教训」写回 MemoryService
                          （失败＝教训、importance 高；成功＝可复用打法）——cognee 的回写；
    ③ stats(uid)       —— 各路条数快照。
  每一路独立 try/except 降级为空：单路故障绝不拖垮整个召回（与 /api/feed 同纪律）。
"""
from __future__ import annotations

import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.memory.hub")

_KINDS = ("service", "episodic", "profile", "entity", "layered")


def _svc(uid: str):
    from hashmm.memory.memory_service import MemoryService
    return MemoryService(scope=uid or "default")


# ── ① 联邦召回 ──────────────────────────────────────────────
def recall(uid: str, query: str, kinds: list[str] | None = None, limit: int = 20) -> list[dict]:
    """四路联邦召回，按 score 倒序合并（每路自带 0..1 归一分再统一排序）。"""
    q = (query or "").strip()
    if not q:
        return []
    want = set(kinds) & set(_KINDS) if kinds else set(_KINDS)
    limit = max(1, min(50, int(limit)))
    now = time.time()
    out: list[dict] = []

    # 1) MemoryService：显著度记忆（recall 自带 relevance×salience 排序 + 用即强化）
    if "service" in want:
        try:
            for it in _svc(uid).recall(q, k=min(limit, 10), now=now):
                out.append({
                    "kind": "service", "source": "长期记忆",
                    "text": it.text, "score": round(min(1.0, 0.55 + 0.15 * it.salience(now)), 4),
                    "ts": int(it.last_used or it.created_at or 0),
                    "meta": {"memory_kind": it.kind, "use_count": it.use_count,
                             "entities": list(it.entities or [])[:5]},
                })
        except Exception as e:
            log_suppressed(logger, e)

    # 2) episodic：经验回放（与 /api/feed、/api/evolution 同款 SQL，LIKE 匹配）
    if "episodic" in want:
        try:
            from hashmm.evolution.episodic_memory import get_episodic_memory
            mem = get_episodic_memory()
            mem._ensure_db()
            like = f"%{q[:60]}%"
            with mem._db._conn() as c:
                rows = c.execute(
                    "SELECT * FROM episodes WHERE user_id=? AND (summary LIKE ? OR task LIKE ?) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (uid, like, like, min(limit, 8)),
                ).fetchall()
            for r in rows:
                d = dict(r)
                txt = str(d.get("summary") or d.get("task") or "")[:300]
                if txt:
                    out.append({"kind": "episodic", "source": "经验回放", "text": txt,
                                "score": 0.5, "ts": int(d.get("created_at") or 0),
                                "meta": {"id": d.get("id")}})
        except Exception as e:
            log_suppressed(logger, e)

    # 3) user_memories：用户画像（key/value 词面命中）
    if "profile" in want:
        try:
            from hashmm.api import database as db
            ql = q.lower()
            for r in (db.get_user_memories(uid, limit=200) or []):
                blob = f"{r.get('key','')} {r.get('value','')}"
                if ql in blob.lower():
                    out.append({
                        "kind": "profile", "source": "用户画像",
                        "text": (f"{r.get('key')}：{r.get('value')}" if r.get("key") else str(r.get("value")))[:300],
                        "score": round(0.45 + 0.4 * float(r.get("confidence") or 0.5), 4),
                        "ts": int(r.get("last_used") or 0),
                        "meta": {"id": r.get("id"), "category": r.get("category")},
                    })
        except Exception as e:
            log_suppressed(logger, e)

    # 4) KG 实体（图记忆：命中的实体 + 度数）——与 /api/kg/search 同一取用路径（KGStorage.load）
    if "entity" in want:
        try:
            from hashmm.kg.storage import KGStorage
            kg, _meta = KGStorage().load()
            for ent in (kg.search_entities(q, limit=min(limit, 6)) or []):
                name = str(ent.get("name") or ent.get("id") or "")
                if name:
                    out.append({
                        "kind": "entity", "source": "知识图谱",
                        "text": name + (f"（{ent.get('type')}）" if ent.get("type") else ""),
                        "score": 0.4, "ts": 0,
                        "meta": {k: ent.get(k) for k in ("type", "degree", "description") if ent.get(k)},
                    })
        except Exception as e:
            log_suppressed(logger, e)

    # 5) 分层记忆（V294，移植 TencentDB Agent Memory 的 L1 原子 + L3 画像）——渐进式披露
    if "layered" in want:
        try:
            from hashmm.memory import layered as _lay
            if _lay.enabled():
                for h in _lay.recall(uid, q, limit=min(limit, 10)):
                    out.append({
                        "kind": h.get("kind", "layered"),
                        "source": h.get("source", "分层记忆"),
                        "text": h.get("text", ""),
                        "score": float(h.get("score", 0.5)),
                        "ts": int(h.get("ts", 0)),
                        "meta": h.get("meta", {}),
                    })
        except Exception as e:
            log_suppressed(logger, e)

    out.sort(key=lambda x: (x["score"], x["ts"]), reverse=True)
    return out[:limit]


# ── ② 执行回写（cognee："agent never repeats the same mistake"）──
def record_outcome(uid: str, goal: str, ok: bool, detail: str = "", *, source: str = "任务") -> None:
    """任务/团队执行结束 → 结果或教训写回长期记忆。失败＝教训（importance 高），
    成功＝可复用打法。文本裁剪 + 只在有实质内容时写入。永不抛错。"""
    try:
        g = (goal or "").strip()
        if len(g) < 4:
            return
        d = (detail or "").strip().replace("\n", " ")[:180]
        if ok:
            text = f"【{source}打法】「{g[:80]}」已成功完成" + (f"：{d}" if d else "")
            kind, importance = "pattern", 1.0
        else:
            text = f"【{source}教训】「{g[:80]}」失败" + (f"：{d}" if d else "") + "——下次同类任务先规避此原因"
            kind, importance = "lesson", 1.6
        _svc(uid).remember(text[:400], kind=kind, importance=importance)
    except Exception as e:
        log_suppressed(logger, e)


def remember(uid: str, text: str, *, importance: float = 1.4) -> str:
    """手动置顶记忆（「让它记住」）：写入 MemoryService，importance 偏高不易衰减。"""
    return _svc(uid).remember((text or "").strip()[:400], kind="pinned",
                              importance=max(0.5, min(3.0, importance)))


# ── ③ 快照 ──────────────────────────────────────────────────
def stats(uid: str) -> dict:
    out: dict[str, Any] = {"service": {}, "episodic": 0, "profile": 0, "entities": 0}
    try:
        out["service"] = _svc(uid).stats()
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.evolution.episodic_memory import get_episodic_memory
        mem = get_episodic_memory()
        mem._ensure_db()
        with mem._db._conn() as c:
            out["episodic"] = int(c.execute(
                "SELECT COUNT(*) FROM episodes WHERE user_id=?", (uid,)).fetchone()[0])
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.api import database as db
        out["profile"] = len(db.get_user_memories(uid, limit=500) or [])
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.kg.storage import KGStorage
        st = KGStorage().get_stats() or {}
        out["entities"] = int(st.get("entities", st.get("entity_count", 0)) or 0)
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.memory import layered as _lay
        out["layered"] = _lay.stats(uid)
    except Exception as e:
        log_suppressed(logger, e)
    return out
