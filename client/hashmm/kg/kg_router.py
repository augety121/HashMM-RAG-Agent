"""v17 Phase 100 — query router / planner (the first step of the agent layer).

Phases 95/96/97 each have a manual env flag. This classifies the query and picks
the right KG strategy automatically (A-RAG / agentic-RAG style query planning):
  * 全局/总结/对比 → community-global (96)
  * 多跳/关系链   → PPR multi-hop (97)  (+ local)
  * 其它/事实型   → entity-local (95)

Conventions: **default OFF** (env ``HASHMM_KG_AUTO``). When OFF, the pipeline keeps
using the individual flags exactly as before (zero behaviour change). When ON, the
router decides per query. Pure functions; never raises.
"""
from __future__ import annotations

import os

from hashmm.kg.community_retrieval import is_global_query

# Relational / chain markers that signal a multi-hop question.
_MULTIHOP_HINTS = (
    "旗下", "子公司", "母公司", "创办的", "创立的", "成立的", "收购的", "投资的",
    "的创始人", "的CEO", "的董事长", "的总裁", "的产品", "的业务", "谁创办", "谁领导",
    "由谁", "属于哪", "关系", "关联",
)


def kg_auto_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_KG_AUTO")


def _is_multihop(query: str) -> bool:
    q = query or ""
    if any(h in q for h in _MULTIHOP_HINTS):
        return True
    # "A 的 B 的 C" style chains: two or more possessive links
    return q.count("的") >= 2


def classify_query(query: str) -> str:
    """Coarse query type: 'global' | 'multihop' | 'local'. Never raises."""
    try:
        if is_global_query(query):
            return "global"
        if _is_multihop(query):
            return "multihop"
        return "local"
    except Exception:
        return "local"


def route_strategies(query: str) -> dict:
    """Which KG strategies to run for this query. Never raises.
    Returns {'local': bool, 'global': bool, 'ppr': bool}."""
    out = {"local": False, "global": False, "ppr": False}
    try:
        kind = classify_query(query)
        if kind == "global":
            out["global"] = True
        elif kind == "multihop":
            out["ppr"] = True
            out["local"] = True
        else:
            out["local"] = True
    except Exception:
        out["local"] = True
    return out
