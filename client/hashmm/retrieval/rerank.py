"""hashmm/retrieval/rerank.py — 结果重排（SAGE 派生，可插拔）。

合并多路/多查询召回后，用一个打分器把更相关的排前面、截 top_k。
- V271 起默认开启（零依赖字面打分微秒级，绝不劣化；置 HASHMM_RERANK=0 可关）。
- 优先 cross-encoder（sentence-transformers，需 HASHMM_RERANK_CROSS=1 且模型可用）；
- 否则退化为零依赖的【字面相关度】打分（CJK 双字 + 拉丁词的重叠比），保证总能用、可单测。
纯逻辑、永不抛错；失败/异常一律保持原顺序（不破坏已有结果）。
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

_CROSS = {"model": None, "tried": False}


def rerank_enabled() -> bool:
    return os.environ.get("HASHMM_RERANK", "1").strip().lower() in ("1", "true", "yes", "on")


def _tokens(s: str) -> set:
    s = str(s or "").lower()
    latin = re.findall(r"[a-z0-9]+", s)
    cjk = re.findall(r"[\u4e00-\u9fff]", s)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return set(latin) | set(cjk) | set(bigrams)


def lexical_score(query: str, text: str) -> float:
    """字面相关度：query 词元中有多少比例命中 text（0..1）。零依赖、确定性。"""
    q = _tokens(query)
    if not q:
        return 0.0
    t = _tokens(text)
    if not t:
        return 0.0
    hit = sum(1 for tok in q if tok in t)
    return hit / len(q)


def _get_cross_encoder():
    if _CROSS["tried"]:
        return _CROSS["model"]
    _CROSS["tried"] = True
    if os.environ.get("HASHMM_RERANK_CROSS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        from sentence_transformers import CrossEncoder  # 重依赖，仅在显式开启时加载
        name = os.environ.get("HASHMM_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        _CROSS["model"] = CrossEncoder(name)
    except Exception:
        _CROSS["model"] = None
    return _CROSS["model"]


def _text_of(item, text_key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(text_key) or item.get("text") or "")
    return str(item or "")


def rerank(query: str, items: list, top_k: int | None = None, *,
           text_key: str = "text", scorer: Optional[Callable[[str, str], float]] = None) -> list:
    """重排 items（dict 或 str 列表），返回新顺序（截 top_k）。永不抛错。"""
    items = list(items or [])
    if len(items) <= 1:
        return items[:top_k] if top_k else items

    use_scorer = scorer
    if use_scorer is None:
        ce = _get_cross_encoder()
        if ce is not None:
            def use_scorer(q, t, _ce=ce):
                try:
                    return float(_ce.predict([[q, t]])[0])
                except Exception:
                    return lexical_score(q, t)
        else:
            use_scorer = lexical_score

    try:
        scored = [(it, float(use_scorer(query, _text_of(it, text_key)))) for it in items]
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [it for it, _ in scored]
    except Exception:
        ranked = items
    return ranked[:top_k] if top_k else ranked
