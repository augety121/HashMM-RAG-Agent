"""自进化 KG —— 待审区（staging）。

MASTER_PLAN 第 4 步前沿差异化之一。让 agent 在回答中发现的高置信新事实，能**安全地**
沉淀进知识图谱 —— 但绝不自动写：

  1. 候选事实先进**独立待审区**（staging，单独的 JSON 文件，**不是 graph.json**）；
  2. **置信阈值**：低于阈值的候选直接拒收（默认 0.75）；
  3. **冲突检测**：与现有图谱里"同 head + 同 relation 但不同 tail"的事实冲突时，标记
     `conflict` 待人工裁决，不会悄悄覆盖已有知识；
  4. **显式审批**：只有人工/规则 `approve` 后，才产出一条可并入的 Relation；`reject` 丢弃。

铁律：默认关（`HASHMM_KG_EVOLUTION`），永不自动写真实图谱、永不抛错、关闭时零影响。
即使开启，本模块也只写自己的 staging 文件；是否把已 approve 的事实并入主图，由你显式
调用 `approved_relations()` 拿到结果、再走你既有的建图/写图流程决定。

用法：
    from hashmm.kg import evolution_staging as ES
    ES.propose("小米", "2024营收", "3659亿元", confidence=0.9,
               source="chat:xxx", existing_graph=kg)   # 提交候选（带冲突检测）
    ES.list_pending()                                   # 看待审
    ES.approve(fact_id)                                 # 批准
    ES.reject(fact_id)                                  # 拒绝
    rels = ES.approved_relations()                      # 拿已批准的，交给你的写图流程
"""
from __future__ import annotations

import json
import os
import time
import hashlib
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.evolution_staging")

DEFAULT_CONFIDENCE_THRESHOLD = 0.75


def enabled() -> bool:
    return os.environ.get("HASHMM_KG_EVOLUTION", "0").strip().lower() in {"1", "true", "yes", "on"}


def _staging_path() -> Path:
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    p = data_dir / "kg" / "staging"
    p.mkdir(parents=True, exist_ok=True)
    return p / "pending_facts.json"


def _load() -> list[dict]:
    try:
        p = _staging_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as _e:
        log_suppressed(logger, _e)
    return []


def _save(facts: list[dict]) -> bool:
    try:
        _staging_path().write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


def _fact_id(head: str, relation: str, tail: str) -> str:
    return hashlib.md5(f"{head}|{relation}|{tail}".encode("utf-8")).hexdigest()[:12]


def _existing_tails(existing_graph: Any, head: str, relation: str) -> list[str]:
    """从现有图谱里找 head 出发、同 relation 的已有 tail（用于冲突检测）。
    existing_graph 可为 None / KnowledgeGraph / networkx 图 / dict(node-link)；都安全兼容。"""
    tails: list[str] = []
    if existing_graph is None:
        return tails
    try:
        g = getattr(existing_graph, "graph", existing_graph)  # KnowledgeGraph.graph 或本身
        # networkx DiGraph
        if hasattr(g, "out_edges"):
            for _h, t, data in g.out_edges(head, data=True):
                if data.get("relation") == relation:
                    tails.append(t)
            return tails
        # dict node-link
        if isinstance(g, dict) and "links" in g:
            for e in g["links"]:
                if e.get("source") == head and e.get("relation") == relation:
                    tails.append(e.get("target"))
    except Exception as _e:
        log_suppressed(logger, _e)
    return tails


def propose(head: str, relation: str, tail: str, *,
            confidence: float = 0.0,
            source: str = "",
            existing_graph: Any = None,
            threshold: float | None = None) -> dict:
    """提交一个候选事实到待审区。返回 {status, fact_id, ...}。永不抛错、永不写主图。

    status 取值：
      - "rejected_low_confidence"：置信度低于阈值，未收录；
      - "duplicate"：待审区已有同一事实；
      - "exists_in_graph"：图谱里已有完全相同的事实，无需新增；
      - "conflict"：与图谱已有事实冲突（同 head+relation 不同 tail），收录并标记待裁决；
      - "pending"：正常收录，待审。
    """
    if not enabled():
        return {"status": "disabled", "fact_id": None}
    head, relation, tail = (str(head).strip(), str(relation).strip(), str(tail).strip())
    if not (head and relation and tail):
        return {"status": "invalid", "fact_id": None}

    th = DEFAULT_CONFIDENCE_THRESHOLD if threshold is None else threshold
    if confidence < th:
        return {"status": "rejected_low_confidence", "fact_id": None,
                "confidence": confidence, "threshold": th}

    fid = _fact_id(head, relation, tail)
    facts = _load()
    if any(f.get("id") == fid for f in facts):
        return {"status": "duplicate", "fact_id": fid}

    existing = _existing_tails(existing_graph, head, relation)
    if tail in existing:
        return {"status": "exists_in_graph", "fact_id": fid}

    conflict = bool(existing)  # 同 head+relation 已有别的 tail → 冲突
    rec = {
        "id": fid, "head": head, "relation": relation, "tail": tail,
        "confidence": round(float(confidence), 4),
        "source": source,
        "status": "conflict" if conflict else "pending",
        "conflicts_with": existing if conflict else [],
        "created_at": time.time(),
    }
    facts.append(rec)
    _save(facts)
    logger.info(f"[KGEvolution] proposed {head}-{relation}->{tail} "
                f"(conf={confidence}, {'CONFLICT' if conflict else 'pending'})")
    return {"status": rec["status"], "fact_id": fid, "conflicts_with": rec["conflicts_with"]}


def list_pending(include_conflicts: bool = True) -> list[dict]:
    """列出待审事实。"""
    facts = _load()
    return [f for f in facts
            if f.get("status") in (("pending", "conflict") if include_conflicts else ("pending",))]


def approve(fact_id: str) -> bool:
    """批准一个候选（标记 approved）。批准后可用 approved_relations() 取出并入主图。"""
    facts = _load()
    hit = False
    for f in facts:
        if f.get("id") == fact_id:
            f["status"] = "approved"
            f["approved_at"] = time.time()
            hit = True
    if hit:
        _save(facts)
    return hit


def reject(fact_id: str) -> bool:
    """拒绝一个候选（从待审区移除）。"""
    facts = _load()
    n0 = len(facts)
    facts = [f for f in facts if f.get("id") != fact_id]
    if len(facts) != n0:
        _save(facts)
        return True
    return False


def approved_relations() -> list[dict]:
    """返回所有已批准、尚未并入的事实（dict 形式）。

    这里**只返回数据**，不替你写图 —— 是否并入主图由调用方走既有建图流程决定，
    从而保证"自动发现"和"写入图谱"之间始终隔着一道人工闸门。
    """
    return [f for f in _load() if f.get("status") == "approved"]


def stats() -> dict:
    facts = _load()
    from collections import Counter
    c = Counter(f.get("status") for f in facts)
    return {"total": len(facts), "by_status": dict(c), "enabled": enabled()}


def clear_all() -> bool:
    """清空待审区（测试 / 重置用）。"""
    return _save([])
