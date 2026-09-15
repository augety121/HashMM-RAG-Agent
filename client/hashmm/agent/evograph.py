"""hashmm/agent/evograph.py — 自进化检索循环（V104 P3，吸收 CVPR2026 EvoGraph-R1）。

EvoGraph-R1 把检索建模成 MDP：智能体在"图状态"上反复执行
GraphRetrieve（查）/ WebSearch（扩）/ GraphEdit（改）/ Answer（终止），
每一步把新证据并入一个**持久演进**的状态，支撑多跳推理；其中 INSERT 最关键。

本模块在你既有 CRAG（agent/crag.py：评估 + 改写重试）之上，把"单步纠错"升级成
**有界多轮循环**，并补两件 EvoGraph-R1 的精华：
  1) 跨轮**累积证据池**（而非每轮丢弃重来）—— 持久演进状态；
  2) **GraphEdit/INSERT**：把跨轮发现的新事实**经 evolution_staging 审批闸门**提案
     （人工/规则 approve 后才并入主图），既自进化又**不污染**主图。

铁律：**默认关**（HASHMM_EVOGRAPH=1）、纯函数 + 可注入（search_fn / llm_fn / propose_fn /
extract_fn / web_fn 全部外部传入 → 不依赖 GPU/活索引即可单测）、**永不抛错**、有界轮数。
不改动 live 检索链；作为可选层提供，按"默认关、eval 验证后再上"的项目惯例推进。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.agent.crag import evaluate_retrieval, rewrite_query, STRONG, AMBIGUOUS, WEAK, _text_of

logger = get_logger("hashmm.agent.evograph")

# MDP 动作名（与论文对齐，便于 trace 阅读）
GRAPH_RETRIEVE = "GraphRetrieve"
WEB_SEARCH = "WebSearch"
GRAPH_EDIT = "GraphEdit"      # INSERT 候选事实（走 staging 审批）
ANSWER = "Answer"

_DEFAULT_MAX_ROUNDS = 3
_DEFAULT_MAX_POOL = 20


def evograph_enabled() -> bool:
    return os.environ.get("HASHMM_EVOGRAPH", "0") == "1"


def _key(r: Any) -> str:
    """结果去重键：优先 chunk_id，退化用文本前 100 字。"""
    cid = getattr(r, "chunk_id", "") or (r.get("chunk_id", "") if isinstance(r, dict) else "")
    return str(cid) if cid else _text_of(r)[:100]


def _merge_pool(pool: list, new: list, max_pool: int) -> int:
    """把 new 中尚未在 pool 的结果并入 pool（累积、去重）。返回新增条数。"""
    seen = {_key(r) for r in pool}
    added = 0
    for r in new or []:
        k = _key(r)
        if k and k not in seen:
            pool.append(r)
            seen.add(k)
            added += 1
            if len(pool) >= max_pool:
                break
    return added


def agentic_evolve(
    query: str,
    results: list,
    search_fn: Callable[[str], list],
    *,
    llm_fn: Callable | None = None,
    propose_fn: Callable | None = None,
    extract_fn: Callable[[list, str], list] | None = None,
    web_fn: Callable[[str], list] | None = None,
    max_rounds: int = _DEFAULT_MAX_ROUNDS,
    max_pool: int = _DEFAULT_MAX_POOL,
) -> dict:
    """在初始检索结果上跑有界自进化循环，返回累积证据池 + 决策 trace。永不抛错。

    Args:
        query: 原查询。
        results: 初始检索结果（list[SearchResult] 或 dict）。
        search_fn(q)->list: 重检索（GraphRetrieve）。
        llm_fn: 可选，供 rewrite_query 生成更优改写。
        propose_fn(head, relation, tail, confidence)->Any: GraphEdit/INSERT 的落点；
                 缺省用 evolution_staging.propose（审批闸门）。传入便于单测。
        extract_fn(results, query)->list[(head,relation,tail)]: 从结果抽候选事实供 INSERT；
                 不传则跳过 INSERT（纯检索演进）。
        web_fn(q)->list: 可选 WebSearch 扩展（外部证据）。
    Returns:
        {"results": 累积池, "rounds": n, "label": 最终标签, "actions": [...],
         "proposed": INSERT 提案数, "corrected": bool}
    """
    trace = {"results": list(results or []), "rounds": 0, "label": STRONG,
             "actions": [], "proposed": 0, "corrected": False}
    try:
        pool = trace["results"]
        ev = evaluate_retrieval(pool, query)
        trace["label"] = ev["label"]
        if ev["label"] == STRONG:
            trace["actions"].append(ANSWER)   # 初始即够好 → 直接作答，不折腾（保守）
            return trace

        if propose_fn is None and _staging_available():
            from hashmm.kg.evolution_staging import propose as _es_propose
            propose_fn = lambda h, r, t, confidence=0.6: _es_propose(h, r, t, confidence=confidence)

        rounds = 0
        best_label = ev["label"]
        while rounds < max(1, max_rounds):
            rounds += 1
            # ── GraphRetrieve：改写 + 重检索，新证据累积进池（改写为空则兜底用原查询）──
            improved = False
            for rq in (rewrite_query(query, llm_fn=llm_fn) or [query]):
                try:
                    cand = search_fn(rq) or []
                except Exception as _se:
                    log_suppressed(logger, _se)
                    continue
                added = _merge_pool(pool, cand, max_pool)
                trace["actions"].append(f"{GRAPH_RETRIEVE}({added}+)")
                if added:
                    improved = True
                if len(pool) >= max_pool:
                    break

            # ── WebSearch（可选）：池仍弱时引入外部证据 ──
            cur = evaluate_retrieval(pool, query)
            if cur["label"] == WEAK and web_fn is not None and len(pool) < max_pool:
                try:
                    wres = web_fn(query) or []
                    wadded = _merge_pool(pool, wres, max_pool)
                    trace["actions"].append(f"{WEB_SEARCH}({wadded}+)")
                    if wadded:
                        improved = True
                        cur = evaluate_retrieval(pool, query)
                except Exception as _we:
                    log_suppressed(logger, _we)

            # ── GraphEdit/INSERT：把本轮新证据里的事实经审批闸门提案（不污染主图）──
            if extract_fn is not None and propose_fn is not None:
                try:
                    for (h, rel, t) in (extract_fn(pool, query) or [])[:10]:
                        if h and rel and t:
                            propose_fn(h, rel, t, confidence=0.6)
                            trace["proposed"] += 1
                    if trace["proposed"]:
                        trace["actions"].append(f"{GRAPH_EDIT}(INSERT×{trace['proposed']})")
                except Exception as _pe:
                    log_suppressed(logger, _pe)

            # ── 评估 → 决定继续还是 Answer ──
            cur = evaluate_retrieval(pool, query)
            trace["label"] = cur["label"]
            if cur["label"] == STRONG or not improved:
                break
            best_label = cur["label"]

        trace["rounds"] = rounds
        trace["corrected"] = len(pool) > len(results or [])
        trace["actions"].append(ANSWER)
        return trace
    except Exception as e:  # 永不抛错（铁律）
        log_suppressed(logger, e)
        return trace


def _staging_available() -> bool:
    try:
        from hashmm.kg.evolution_staging import enabled
        return bool(enabled())
    except Exception:
        return False
