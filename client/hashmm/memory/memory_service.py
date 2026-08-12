"""v17 Phase 78 — memory service layer (轴 C, general-purpose).

2026 treats memory as a *service layer* between the agent and the data: agents
make orders-of-magnitude more data requests than humans, so memory must be
salience-ranked (what to surface), time-decayed (what to forget), reinforced on
use, entity-linked (graph memory), and recallable across sessions so relevant
long-term facts/preferences get pulled into the current context.

This is complementary to the existing episodic/working/semantic-cache + evolution
memories — it adds the unified salience/decay/recall service those lack.

- **Salience** = importance × recency-decay(half-life) × frequency(use_count).
- **Recall** ranks stored memories by relevance × (1 + salience); recalling an
  item **reinforces** it (use_count++, last_used=now) so useful memories persist.
- **Graph memory**: each memory can be tagged with entities; ``recall_by_entity``
  retrieves an entity's long-term facts (reuses the KG idea without rebuilding it).
- Relevance is model-free by default (lexical overlap) but an embedding scorer is
  injectable. File-backed per scope (user/tenant). Never raises.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)

_DAY = 86400.0
_KEY_RE = re.compile(r"[^A-Za-z0-9_.\-]")

# V211 差距三：记忆策略的三条启发式（都保守——宁可漏记也别乱记乱翻）。
# ① 何时该翻记忆：任务是否依赖"我这个人的偏好/历史/习惯"。纯知识/翻译/算题不依赖 → 不翻。
_RECALL_TRIGGER = re.compile(
    r"(我的|我们|上次|之前|以前|我(通常|一般|平时|习惯|喜欢|偏好)|记得|按我|照我|"
    r"我(叫|是谁)|我的名字|我的(项目|团队|公司|风格|口味)|继续(上|之前)|接着(上|之前))")
# ② 什么值得写入长期记忆：显式的偏好/身份/长期事实表述。
_WRITE_WORTHY = re.compile(
    r"(记住|记一下|帮我记|以后(都|记得|请)|我(叫|是|喜欢|偏好|习惯|讨厌|不喜欢|不吃|忌口)|"
    r"我的(名字|邮箱|电话|地址|生日|团队|公司|项目|职位|角色)(是|叫|为)|"
    r"我(通常|一般|平时|默认)(用|喜欢|选|要))")
# ③ 一次性/无价值，别写：闲聊、即时任务、礼貌用语。
_TRIVIAL = re.compile(
    r"^(你好|谢谢|多谢|好的|嗯|哦|ok|thanks?|hi|hello|在吗|测试|test)\b|"
    r"(帮我(查|搜|算|写|翻译|总结|生成)|现在|马上|立刻)")


def is_write_worthy(text: str) -> bool:
    """判断这句话是否值得写入长期记忆（显式偏好/身份/长期事实=值得；闲聊/一次性任务=不值得）。"""
    t = (text or "").strip()
    if len(t) < 3 or _TRIVIAL.search(t):
        return False
    return bool(_WRITE_WORTHY.search(t))


def memory_service_enabled() -> bool:
    """Whether memory recall is injected into the live context (default OFF)."""
    return os.environ.get("HASHMM_MEMORY_SERVICE", "0").strip().lower() in ("1", "true", "yes", "on")


def _half_life_days() -> float:
    try:
        return max(0.1, float(os.environ.get("HASHMM_MEMORY_HALFLIFE_DAYS", "30")))
    except Exception:
        return 30.0


def _tokens(text: str) -> set:
    text = (text or "").lower()
    toks = set(re.findall(r"[a-z0-9]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk) - 1):
        toks.add(cjk[i] + cjk[i + 1])  # CJK bigrams
    return toks


def _lexical_relevance(query: str, text: str) -> float:
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)   # fraction of query tokens covered → [0,1]


def _text_jaccard(a: str, b: str) -> float:
    """对称的词级 Jaccard 相似度——用于近重复记忆的合并判定。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class MemoryItem:
    id: str
    text: str
    kind: str = "fact"            # fact | preference | episodic | ...
    importance: float = 1.0
    entities: list = field(default_factory=list)
    created_at: float = 0.0
    last_used: float = 0.0
    use_count: int = 0

    def salience(self, now: float, half_life_days: Optional[float] = None) -> float:
        hl = (half_life_days if half_life_days is not None else _half_life_days()) * _DAY
        age = max(0.0, now - (self.last_used or self.created_at))
        recency = 0.5 ** (age / hl) if hl > 0 else 1.0
        freq = 1.0 + 0.3 * math.log1p(max(0, self.use_count))
        return max(0.0, self.importance) * recency * freq


class MemoryService:
    def __init__(self, scope: str = "default", base_dir: str | Path | None = None,
                 relevance_fn: Callable[[str, str], float] | None = None):
        self.scope = _KEY_RE.sub("_", str(scope))[:120] or "default"
        base = base_dir or os.environ.get("HASHMM_MEMORY_DIR", "data/memory")
        self.base = Path(base)
        self.relevance_fn = relevance_fn or _lexical_relevance
        self.items: dict[str, MemoryItem] = {}
        try:
            self.base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_suppressed(logger, e)
        self._load()

    # ── persistence ──
    def _path(self) -> Path:
        return self.base / f"{self.scope}.memory.json"

    def _load(self):
        try:
            p = self._path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                for d in data:
                    self.items[d["id"]] = MemoryItem(**d)
        except Exception as e:
            log_suppressed(logger, e)

    def _save(self):
        try:
            self._path().write_text(
                json.dumps([asdict(i) for i in self.items.values()], ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:
            log_suppressed(logger, e)

    # ── write ──
    def remember(self, text: str, *, kind: str = "fact", importance: float = 1.0,
                 entities: list | None = None, now: float | None = None) -> str:
        try:
            now = now if now is not None else time.time()
            mid = uuid.uuid4().hex[:16]
            self.items[mid] = MemoryItem(
                id=mid, text=text, kind=kind, importance=float(importance),
                entities=list(entities or []), created_at=now, last_used=now, use_count=0)
            self._save()
            return mid
        except Exception as e:
            log_suppressed(logger, e)
            return ""

    # ── read (with reinforcement) ──
    def should_recall(self, query: str) -> bool:
        """V211 差距三：需不需要翻记忆。只有任务依赖"我这个人"时才翻——纯知识/翻译/算题不翻，省噪声不干扰。

        无记忆可翻时直接 False。命中 recall-trigger（我的/上次/我通常…）才翻。
        """
        if not self.items:
            return False
        return bool(_RECALL_TRIGGER.search(query or ""))

    def remember_pref(self, field_key: str, value: str, *, now: float | None = None) -> str:
        """V211 差距三：写入/更新一条"结构化偏好"，冲突以新覆旧。

        同一 field_key（如"下载目录""称呼""语气偏好"）只保留最新值——
        用户上周说"喜欢简洁"、这周说"要详细"，听这周的。旧的同字段记忆被本次覆盖。
        """
        try:
            now = now if now is not None else time.time()
            key = (field_key or "").strip()
            if not key:
                return ""
            # 删除同字段旧记忆（entities 里带 pref:<key> 标识）
            tag = f"pref:{key}"
            self.items = {mid: it for mid, it in self.items.items() if tag not in (it.entities or [])}
            mid = uuid.uuid4().hex[:16]
            self.items[mid] = MemoryItem(
                id=mid, text=f"{key}：{value}", kind="preference", importance=1.5,
                entities=[tag], created_at=now, last_used=now, use_count=0)
            self._save()
            return mid
        except Exception as e:
            log_suppressed(logger, e)
            return ""

    def recall(self, query: str, *, k: int = 5, now: float | None = None,
               reinforce: bool = True) -> list[MemoryItem]:
        try:
            now = now if now is not None else time.time()
            scored = []
            for it in self.items.values():
                rel = self.relevance_fn(query, it.text)
                if rel <= 0:
                    continue
                scored.append((rel * (1.0 + it.salience(now)), it))
            scored.sort(key=lambda x: x[0], reverse=True)
            top = [it for _, it in scored[:k]]
            if reinforce:
                for it in top:
                    it.use_count += 1
                    it.last_used = now
                if top:
                    self._save()
            return top
        except Exception as e:
            log_suppressed(logger, e)
            return []

    def recall_by_entity(self, entity: str, *, k: int = 5, now: float | None = None) -> list[MemoryItem]:
        try:
            now = now if now is not None else time.time()
            hits = [it for it in self.items.values() if entity in (it.entities or [])]
            hits.sort(key=lambda it: it.salience(now), reverse=True)
            return hits[:k]
        except Exception as e:
            log_suppressed(logger, e)
            return []

    # ── maintenance ──
    def dedup_merge(self, *, sim_threshold: float = 0.85, now: float | None = None) -> int:
        """合并近重复记忆：同 kind 且文本 Jaccard > 阈值的视为同一条，保留 salience 更高者作为
        "代表"，把其余合并进它——use_count 相加、importance 取大、created_at 取早、last_used 取晚、
        entities 取并集，然后丢弃被合并者。减少记忆膨胀与重复召回（与检索去冗同理）。返回合并掉的条数。"""
        try:
            now = now if now is not None else time.time()
            items = list(self.items.values())
            items.sort(key=lambda it: it.salience(now), reverse=True)   # 让代表是更显著的那条
            kept: list = []
            merged = 0
            for it in items:
                dup = None
                for k in kept:
                    if k.kind == it.kind and _text_jaccard(k.text, it.text) > sim_threshold:
                        dup = k
                        break
                if dup is None:
                    kept.append(it)
                    continue
                # 合并 it → dup
                dup.use_count += it.use_count
                dup.importance = max(dup.importance, it.importance)
                dup.created_at = min(dup.created_at or it.created_at, it.created_at or dup.created_at)
                dup.last_used = max(dup.last_used, it.last_used)
                seen = set(dup.entities or [])
                for e in (it.entities or []):
                    if e not in seen:
                        dup.entities.append(e)
                        seen.add(e)
                merged += 1
            if merged:
                self.items = {it.id: it for it in kept}
                self._save()
            return merged
        except Exception as e:
            log_suppressed(logger, e)
            return 0

    def consolidate(self, *, min_salience: float = 0.05, max_items: int = 1000,
                    now: float | None = None, merge_dups: bool = True) -> int:
        """Forget low-salience memories and cap total count (keep most salient).
        v: 先合并近重复记忆（merge_dups），再按 salience 过滤+限量。Returns 丢弃/合并的总数。"""
        try:
            now = now if now is not None else time.time()
            before = len(self.items)
            if merge_dups:
                self.dedup_merge(now=now)   # 先去重合并，避免冗余记忆占用名额
            kept = [it for it in self.items.values() if it.salience(now) >= min_salience]
            kept.sort(key=lambda it: it.salience(now), reverse=True)
            kept = kept[:max_items]
            self.items = {it.id: it for it in kept}
            self._save()
            return before - len(self.items)
        except Exception as e:
            log_suppressed(logger, e)
            return 0

    def context_block(self, query: str, *, k: int = 5, now: float | None = None) -> str:
        """Render the top recalled memories as a context block for prompt injection
        (cross-session personalization). Empty when nothing relevant.

        V211 差距三：先过 needs_memory 门——任务不依赖历史偏好/事实时，直接不注入（省噪声、不干扰）。
        """
        if not self.should_recall(query):
            return ""
        mems = self.recall(query, k=k, now=now)
        if not mems:
            return ""
        lines = [f"- [{m.kind}] {m.text}" for m in mems]
        return "已知的长期记忆（供参考，可能与当前问题相关）：\n" + "\n".join(lines)

    def stats(self) -> dict:
        return {"scope": self.scope, "count": len(self.items)}
