"""KG-based suggestion generator — zero LLM calls.

Generates follow-up questions based on KG entities and relations
found during retrieval. Much faster than LLM-generated suggestions
(0ms vs 1-2s) and more relevant (based on actual data, not guesses).

Usage:
    from hashmm.kg.suggestions import generate_suggestions
    suggestions = generate_suggestions(query, answer, entities, relations)
    # → ["小米集团的研发投入变化趋势？", "智能手机出货量的具体数据？", ...]
"""
from __future__ import annotations

import re


def generate_suggestions(
    query: str,
    answer: str,
    entities: list[dict] | None = None,
    relations: list[dict] | None = None,
    sources: list[dict] | None = None,
    max_suggestions: int = 3,
) -> list[str]:
    """Generate follow-up suggestions from KG data.

    Priority order:
    1. Entities mentioned in answer but not in query → "Tell me more about X"
    2. Relations found → "What's the connection between X and Y"
    3. Query pattern-based → domain-aware follow-ups
    4. Source-based → "What else is in document X"

    Returns up to max_suggestions suggestions.
    """
    suggestions: list[str] = []
    entities = entities or []
    relations = relations or []
    sources = sources or []
    query_lower = query.lower()

    # ── Strategy 1: Entities in KG but not in query ──
    for e in entities[:6]:
        name = e.get("name", "")
        if not name or len(name) < 2:
            continue
        if name in query:
            continue
        etype = e.get("type", "")

        if etype == "ORG":
            suggestions.append(f"{name}的经营情况如何？")
        elif etype == "PRODUCT":
            suggestions.append(f"{name}的市场表现怎样？")
        elif etype == "TECHNOLOGY":
            suggestions.append(f"{name}的发展现状和应用前景？")
        elif etype == "METRIC":
            suggestions.append(f"{name}的具体数据和变化趋势？")
        elif etype == "PERSON":
            suggestions.append(f"{name}在公司中的角色和贡献？")
        else:
            suggestions.append(f"能详细介绍一下{name}吗？")

        if len(suggestions) >= max_suggestions:
            return suggestions[:max_suggestions]

    # ── Strategy 2: Relations → connection questions ──
    for r in relations[:4]:
        head = r.get("head", "")
        tail = r.get("tail", "")
        rel = r.get("relation", "")
        if not head or not tail:
            continue
        if head in query and tail in query:
            continue  # Already asked about both

        if head in query:
            suggestions.append(f"{head}和{tail}之间有什么关系？")
        elif tail in query:
            suggestions.append(f"{tail}和{head}之间有什么关系？")
        elif rel:
            suggestions.append(f"{head}的{rel}情况如何？")

        if len(suggestions) >= max_suggestions:
            return suggestions[:max_suggestions]

    # ── Strategy 3: Query pattern-based ──
    pattern_suggestions = []
    if any(w in query_lower for w in ["营收", "收入", "revenue"]):
        pattern_suggestions.append("利润和毛利率的变化情况如何？")
    if any(w in query_lower for w in ["对比", "比较", "vs"]):
        pattern_suggestions.append("还有哪些可比较的维度？")
    if any(w in query_lower for w in ["研发", "r&d", "技术"]):
        pattern_suggestions.append("研发投入占营收的比重是多少？")
    if any(w in query_lower for w in ["战略", "规划", "布局"]):
        pattern_suggestions.append("未来的发展方向和重点是什么？")
    if any(w in query_lower for w in ["风险", "挑战"]):
        pattern_suggestions.append("应对这些风险的策略是什么？")

    for s in pattern_suggestions:
        if s not in suggestions and len(suggestions) < max_suggestions:
            suggestions.append(s)

    # ── Strategy 4: Source document-based ──
    if len(suggestions) < max_suggestions and sources:
        filenames = set()
        for s in sources:
            fn = s.get("filename", "")
            if fn and fn not in filenames:
                filenames.add(fn)
        for fn in list(filenames)[:2]:
            short_name = fn.replace(".pdf", "").replace(".docx", "")
            s = f"{short_name}中还有哪些重要信息？"
            if s not in suggestions and len(suggestions) < max_suggestions:
                suggestions.append(s)

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:max_suggestions]
