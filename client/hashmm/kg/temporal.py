"""时序 / 双时态 KG —— 查询期给关系标注时间，支持"截至某时 / 跨期对比"。

定位（MASTER_PLAN 第 4 步前沿差异化之一，通用化做法）：很多企业知识天然带时间
（"2024 年营收""Q1 利润""2023 年 CEO 是谁"）。本模块**不改建图主链、不改 graph.json
里任何一条已有关系**，而是在**查询期**从关系的 ``relation`` / ``description`` /
``source_ids`` 文本里抽取时间，给关系动态标注 ``valid_from`` / ``valid_to``，
并支持：
  - **时间点查询**：截至 T 时有效的关系（as-of T）；
  - **区间查询**：落在 [start, end] 的关系；
  - **跨期对比**：把关系按年份/季度分桶，便于"2023 vs 2024"。

为什么放查询期而不写进图：你的 KG 已达标、graph.json 是真实数据，加字段重建有风险且
收益递减。查询期标注是纯增量、可随时关、零破坏 —— 抽不出时间的关系一律视为"始终有效"，
绝不因为没有时间就被过滤掉（保持与现状完全一致的兜底行为）。

设计（遵守铁律：默认关 / 纯函数 / 永不抛错 / 关闭零变化）：
  - 仅当 ``HASHMM_KG_TEMPORAL=1`` 时，调用方才会用本模块过滤；默认关时检索结果不变。
  - 纯 Python + 正则，无新依赖。

用法：
    from hashmm.kg import temporal as T
    rels = [...]  # 图谱查出来的边 dict 列表，每个含 relation/description/source_ids
    rels = T.annotate_relations(rels)          # 给每条加 valid_from/valid_to（抽不到则 None）
    asof = T.filter_as_of(rels, year=2024)     # 截至 2024 年有效的
    buckets = T.bucket_by_period(rels)         # {2023:[...], 2024:[...]} 便于对比
"""
from __future__ import annotations

import os
import re
from typing import Any

# 年份：2019~2099（四位数，避免误抓金额/编号里的数字时再叠加上下文约束）
_YEAR_RE = re.compile(r"(20[1-9]\d)\s*年?")
# 季度
_QUARTER_RE = re.compile(r"Q([1-4])|第?([一二三四1-4])季度")
_Q_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def enabled() -> bool:
    return os.environ.get("HASHMM_KG_TEMPORAL", "0").strip().lower() in {"1", "true", "yes", "on"}


def _text_of(rel: dict) -> str:
    """关系里所有可能含时间线索的文本拼起来，供抽取。"""
    parts = [str(rel.get("relation", "")), str(rel.get("description", ""))]
    sids = rel.get("source_ids") or []
    if isinstance(sids, (list, tuple)):
        parts.extend(str(s) for s in sids)
    # source/target 名字本身也可能带年份（如"小米2024营收"）
    parts.append(str(rel.get("source", "")))
    parts.append(str(rel.get("target", "")))
    return " ".join(parts)


def extract_years(text: str) -> list[int]:
    """从文本抽出所有出现的年份，去重升序。"""
    out = sorted({int(m.group(1)) for m in _YEAR_RE.finditer(text or "")})
    return out


def extract_quarter(text: str) -> int | None:
    m = _QUARTER_RE.search(text or "")
    if not m:
        return None
    g = m.group(1) or m.group(2)
    if g is None:
        return None
    return _Q_CN.get(str(g))


def relation_validity(rel: dict) -> tuple[int | None, int | None]:
    """推断一条关系的 (valid_from, valid_to)，以年份表示（int），抽不到则 (None, None)。

    规则（保守）：
      - 文本里只有一个年份 → valid_from = valid_to = 该年（点事件）；
      - 多个年份 → valid_from=最小, valid_to=最大（区间）；
      - 没有年份 → (None, None)，调用方应视为"始终有效"。
    """
    try:
        years = extract_years(_text_of(rel))
        if not years:
            return (None, None)
        return (years[0], years[-1])
    except Exception:
        return (None, None)


def annotate_relations(relations: list[dict]) -> list[dict]:
    """给每条关系**就地新增** valid_from/valid_to/quarter（不删改原字段）。永不抛错。

    抽不到时间的关系字段为 None —— 表示"始终有效"，不会在后续过滤里被误删。
    """
    if not isinstance(relations, list):
        return relations
    for r in relations:
        if not isinstance(r, dict):
            continue
        try:
            vf, vt = relation_validity(r)
            r.setdefault("valid_from", vf)
            r.setdefault("valid_to", vt)
            r.setdefault("quarter", extract_quarter(_text_of(r)))
        except Exception:
            r.setdefault("valid_from", None)
            r.setdefault("valid_to", None)
            r.setdefault("quarter", None)
    return relations


def filter_as_of(relations: list[dict], year: int) -> list[dict]:
    """截至 year 有效的关系（as-of 查询）。

    规则：valid_from 为 None（始终有效）一律保留；否则要求 valid_from <= year。
    valid_to 不做上限裁剪（关系一旦成立通常持续有效，除非数据明确终止）—— 这样
    "截至 2024 年谁是 CEO"不会因为关系只标了 2020 就被漏掉。
    """
    anns = annotate_relations(relations)
    out = []
    for r in anns:
        vf = r.get("valid_from")
        if vf is None or vf <= year:
            out.append(r)
    return out


def filter_between(relations: list[dict], start: int, end: int) -> list[dict]:
    """valid_from 落在 [start, end] 的关系；无时间的关系保留（始终有效）。"""
    anns = annotate_relations(relations)
    lo, hi = min(start, end), max(start, end)
    out = []
    for r in anns:
        vf = r.get("valid_from")
        if vf is None or (lo <= vf <= hi):
            out.append(r)
    return out


def bucket_by_period(relations: list[dict]) -> dict[int, list[dict]]:
    """按 valid_from 年份分桶，便于跨期对比（"2023 vs 2024"）。

    无年份的关系归到键 0（"未标注时间"），调用方可选择忽略或并入每个桶。
    """
    anns = annotate_relations(relations)
    buckets: dict[int, list[dict]] = {}
    for r in anns:
        vf = r.get("valid_from")
        key = int(vf) if isinstance(vf, int) else 0
        buckets.setdefault(key, []).append(r)
    return buckets


def query_years(query: str) -> list[int]:
    """从用户 query 里抽年份，用于判断这是否是个时序问题（>=1 个年份即可能是）。"""
    return extract_years(query)


def is_temporal_query(query: str) -> bool:
    """query 是否带明显时序意图（含年份，或"截至/趋势/对比/历年"等词）。"""
    if query_years(query):
        return True
    return any(w in (query or "") for w in ("截至", "趋势", "历年", "逐年", "同比", "环比", "至今"))
