"""Keyword Extractor — extract HL/LL keywords from user queries.

High-Level (HL) keywords: concepts, themes, trends → search RelationVDB
Low-Level (LL) keywords: entities, products, metrics → search EntityVDB

Short/simple queries skip LLM and use regex (saves 1-2s latency).
Results are cached by query hash.
"""
from __future__ import annotations

import json
import re
import hashlib
from collections import OrderedDict

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.keyword_extractor")

_PROMPT = """分析用户查询，提取两类关键词（用于知识库检索）：

高层关键词（HL）：宏观概念、主题、趋势、分析维度
低层关键词（LL）：具体实体名称、产品名、财务指标、技术术语

用户查询：{query}

直接输出 JSON，不要解释：
{{"hl": ["关键词1", "关键词2"], "ll": ["关键词3", "关键词4"]}}"""

# Regex patterns for fast extraction (no LLM needed)
_COMPANY_RE = re.compile(
    r'([\u4e00-\u9fff]{2,8}(?:集团|公司|控股|银行|保险|证券|科技|汽车|电子|通信))'
)
_METRIC_RE = re.compile(
    r'(营[业收]收入|净利润|毛利[润率]|研发[费投]入|总资产|净资产|'
    r'市值|股价|市盈率|负债率|现金流|ROE|ROA|EPS|'
    r'出货量|用户数|月活|DAU|MAU|GMV)'
)
_PRODUCT_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6}(?:手机|汽车|平台|系统|芯片|处理器|大模型|AI))'
)

# LRU cache for keyword extraction results
_CACHE_MAX = 256
_cache: OrderedDict[str, dict] = OrderedDict()


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


def extract_keywords_regex(query: str) -> dict:
    """Fast regex-based keyword extraction (0ms, no LLM)."""
    ll = []
    for pattern in [_COMPANY_RE, _METRIC_RE, _PRODUCT_RE]:
        ll.extend(pattern.findall(query))
    # Deduplicate preserving order
    seen = set()
    ll_unique = []
    for kw in ll:
        if kw not in seen:
            seen.add(kw)
            ll_unique.append(kw)
    return {"hl": [], "ll": ll_unique[:6]}


def extract_keywords_llm(query: str, llm_fn) -> dict | None:
    """LLM-based keyword extraction. Returns None on failure."""
    try:
        prompt = _PROMPT.format(query=query)
        if hasattr(llm_fn, 'quick_call'):
            raw = llm_fn.quick_call("你是关键词提取专家", prompt, max_tok=150)
        else:
            raw = llm_fn(prompt)

        # Parse JSON from response (handle markdown code blocks)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        hl = result.get("hl", [])
        ll = result.get("ll", [])

        # Validate
        if not isinstance(hl, list) or not isinstance(ll, list):
            return None
        hl = [str(k).strip() for k in hl if k and len(str(k).strip()) > 1][:5]
        ll = [str(k).strip() for k in ll if k and len(str(k).strip()) > 1][:6]

        return {"hl": hl, "ll": ll}
    except Exception as e:
        logger.debug(f"LLM keyword extraction failed: {e}")
        return None


def extract_keywords(query: str, llm_fn=None, history: list[dict] | None = None) -> dict:
    """Extract HL/LL keywords with smart routing.

    - Short queries with clear entities → regex only (0ms)
    - Complex queries → LLM extraction with cache
    - Failure → fallback to regex

    Returns:
        {"hl": ["concept1", ...], "ll": ["entity1", ...]}
    """
    # Check cache
    key = _cache_key(query)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    # Fast path: regex extraction first
    regex_result = extract_keywords_regex(query)

    # Decide if LLM extraction is needed
    use_llm = (
        llm_fn is not None
        and len(query) > 15  # Short queries don't need LLM
        and (
            len(regex_result["ll"]) < 2  # Few entities found by regex
            or any(kw in query for kw in ["对比", "比较", "趋势", "战略", "分析",
                                           "为什么", "如何", "影响", "关系"])
        )
    )

    if use_llm:
        # F11: keyword extraction is a LOCAL_TASK → route to local model when
        # enabled. No-op when off (route_llm returns the same llm_fn).
        _kw_fn = llm_fn
        try:
            from hashmm.llm_router import route_llm, record_routing
            _routed, _backend = route_llm("keyword", llm_fn)
            _kw_fn = _routed or llm_fn
            record_routing("keyword", _backend)
        except Exception:
            pass  # nosem: observability-fallback
        llm_result = extract_keywords_llm(query, _kw_fn)
        if llm_result:
            # Merge: LLM HL + (LLM LL ∪ regex LL)
            merged_ll = list(dict.fromkeys(llm_result["ll"] + regex_result["ll"]))[:6]
            result = {"hl": llm_result["hl"], "ll": merged_ll}
            logger.info(f"Keywords (LLM): HL={result['hl']}, LL={result['ll']}")
        else:
            result = regex_result
            logger.info(f"Keywords (regex fallback): LL={result['ll']}")
    else:
        result = regex_result
        if result["ll"]:
            logger.info(f"Keywords (regex): LL={result['ll']}")

    # Cache
    _cache[key] = result
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)

    return result
