#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KG 建图后的【连通性修复 + 清理】——默认关 / 永不抛错 / 不调 LLM / 不重建。

为什么需要：本地 Qwen 抽出的图很精确但【边少】。数学上 连通块数 ≥ 实体数 − 关系数，
所以 551 实体 / 298 关系 必然碎成 ≥253 块（实测 280）。多跳/PPR/社区检索在碎图上收益有限。
本模块在【已存的图】上原地改善，秒~分钟级，**不必再跑 5 小时 LLM 抽取**：

  · add_cooccurrence_edges(kg, ...)：把出现在【同一来源块】里的实体用轻量 `co_occurs_with`
    边连起来（并查集：只在能合并两个连通块时加边 → 最少的边、最大的连通收益）。
    库内开关 env ``HASHMM_KG_COOCCUR_EDGES``。
  · drop_generic_concepts(kg, ...)：删掉泛化/噪声 CONCEPT 节点（“…委员会成员/…方向/…成员”
    且低度数）——它们既不合并也不连通，只会让图更碎。env ``HASHMM_KG_DROP_GENERIC_CONCEPTS``。

约定：默认关、纯函数、永不抛错、零新依赖（只用 networkx）。
CLI：python -m hashmm.kg.kg_connectivity   （载入已存图 → 清理+补边 → 打印前后连通块 → 保存）
"""
from __future__ import annotations

import os
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.connectivity")

# 泛化 CONCEPT 节点的后缀（保守：只删 CONCEPT 类型 + 低度数 + 命中这些后缀的）
_GENERIC_CONCEPT_SUFFIXES = (
    "委员会成员", "委员会", "成员", "方向", "板块", "业务方向",
    "部门", "职位", "岗位", "事项", "工作", "方面",
)
# 精确匹配的泛化概念停用表（默认不启用）。只收【明显是角色/法律形态/治理机构】、
# 几乎不会承载属性事实的通用词；**刻意不含**净利润/营收/毛利率等可能挂着量化属性事实的指标词。
_GENERIC_CONCEPT_STOPLIST = frozenset({
    # 角色/头衔
    "首席执行官", "首席财务官", "首席运营官", "首席技术官", "总裁", "副总裁",
    "董事长", "副董事长", "董事", "监事", "经理", "总经理", "高级管理人员",
    "执行董事", "非执行董事", "独立非执行董事", "主要经营决策者", "经营者",
    # 法律形态
    "外商独资企业", "有限公司", "股份有限公司", "有限责任公司", "子公司", "母公司",
    "关联方", "分公司", "控股股东",
    # 治理机构
    "薪酬委员会", "提名委员会", "审计委员会", "董事会", "股东大会", "监事会",
})
_CONCEPT_TYPES = {"CONCEPT", "概念", "concept"}


def cooccur_edges_enabled() -> bool:
    return os.environ.get("HASHMM_KG_COOCCUR_EDGES", "").strip().lower() in {"1", "true", "yes", "on"}


def drop_generic_enabled() -> bool:
    return os.environ.get("HASHMM_KG_DROP_GENERIC_CONCEPTS", "").strip().lower() in {"1", "true", "yes", "on"}


def _components(kg: Any) -> int:
    """Weakly-connected component count of the underlying graph. Never raises."""
    try:
        import networkx as nx
        g = getattr(kg, "graph", None)
        if g is None or g.number_of_nodes() == 0:
            return 0
        return nx.number_weakly_connected_components(g)
    except Exception:
        return -1


def _node_type(attrs: dict) -> str:
    return str(attrs.get("type") or attrs.get("entity_type") or "").strip()


def drop_generic_concepts(kg: Any, extra_suffixes: tuple[str, ...] = (),
                          max_degree: int = 2, use_stoplist: bool = False,
                          extra_stoplist: tuple[str, ...] = ()) -> dict:
    """删除泛化/噪声的 CONCEPT 节点。永不抛错；出错原样返回。

    两类规则：
      1) 后缀+低度数（默认）：类型 CONCEPT + 度数≤max_degree + 命中后缀（…委员会成员/…方向）。
      2) 停用词表（use_stoplist=True，默认关）：类型 CONCEPT + 名字精确命中停用表
         （首席执行官/外商独资企业/薪酬委员会 等角色·法律形态·治理机构）→ **不限度数**也删。
         用于清掉 `首席执行官(度113)` 这类被 7B 误抽成实体的高度数泛化枢纽。
         注意：删高度数枢纽会移除其边、可能再碎化 → 删完应**再跑一次共现补边**重连。
    返回 {before, after, dropped, dropped_by_suffix, dropped_by_stoplist}。
    """
    stats = {"before": 0, "after": 0, "dropped": 0,
             "dropped_by_suffix": 0, "dropped_by_stoplist": 0}
    try:
        g = getattr(kg, "graph", None)
        if g is None:
            return stats
        stats["before"] = g.number_of_nodes()
        suffixes = tuple(_GENERIC_CONCEPT_SUFFIXES) + tuple(extra_suffixes or ())
        stop = set(_GENERIC_CONCEPT_STOPLIST) | set(extra_stoplist or ()) if use_stoplist else set()
        drop_suffix, drop_stop = [], []
        for node, attrs in list(g.nodes(data=True)):
            if _node_type(attrs) not in _CONCEPT_TYPES:
                continue
            name = str(attrs.get("name") or node)
            if use_stoplist and name in stop:
                drop_stop.append(node)
                continue
            deg = g.degree(node)
            if deg <= max_degree and any(name.endswith(s) for s in suffixes):
                drop_suffix.append(node)
        for node in drop_suffix + drop_stop:
            try:
                g.remove_node(node)
                idx = getattr(kg, "_entity_index", None)
                if isinstance(idx, dict):
                    idx.pop(node, None)
            except Exception:
                pass
        stats["dropped_by_suffix"] = len(drop_suffix)
        stats["dropped_by_stoplist"] = len(drop_stop)
        stats["dropped"] = len(drop_suffix) + len(drop_stop)
        stats["after"] = g.number_of_nodes()
    except Exception as e:
        logger.info(f"drop_generic_concepts skipped: {e!r}")
    return stats


def add_cooccurrence_edges(kg: Any, level: str = "doc", chunk_to_doc: dict | None = None,
                           weight: float = 0.3, relation: str = "co_occurs_with",
                           max_edges: int = 50000) -> dict:
    """把【同一来源】里共现的实体用轻量边连起来，降低连通块数。

    level="doc"（默认，力度强）：按【同一篇文档】共现——一篇文档含几十个 chunk、十几个实体，
        把它们连起来能真正收拢碎块。需传 chunk_to_doc（chunk_id→doc_id 映射；CLI 从 BM25 语料建）。
    level="chunk"（力度弱）：仅按同一个 chunk 共现（回退用）。

    并查集策略：遍历每个来源组的实体，只有当一条边能把【两个不同连通块】合并时才加，
    用最少的边拿到最大的连通收益。`co_occurs_with` 是独立关系名，检索侧可单独降权/过滤。永不抛错。

    返回 {edges_added, components_before, components_after, nodes, level, groups}。
    """
    stats = {"edges_added": 0, "components_before": -1, "components_after": -1,
             "nodes": 0, "level": level, "groups": 0}
    try:
        g = getattr(kg, "graph", None)
        if g is None or g.number_of_nodes() == 0:
            return stats
        stats["nodes"] = g.number_of_nodes()
        stats["components_before"] = _components(kg)

        use_doc = (level == "doc") and bool(chunk_to_doc)
        if level == "doc" and not chunk_to_doc:
            logger.info("level=doc 但未提供 chunk_to_doc，回退到 chunk 级（连通收益会弱）")
            stats["level"] = "chunk(fallback)"

        # group_key → [node keys]
        group_to_nodes: dict[str, list] = {}
        for node, attrs in g.nodes(data=True):
            for sid in (attrs.get("source_ids") or []):
                key = chunk_to_doc.get(str(sid), str(sid)) if use_doc else str(sid)
                if not key:
                    key = str(sid)
                group_to_nodes.setdefault(key, []).append(node)
        stats["groups"] = len(group_to_nodes)

        # union-find over node keys, seeded with existing edges
        parent: dict[Any, Any] = {n: n for n in g.nodes()}

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for u, v in g.edges():
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv

        added = 0
        for key, nodes in group_to_nodes.items():
            uniq = list(dict.fromkeys(nodes))
            if len(uniq) < 2:
                continue
            anchor = uniq[0]
            for other in uniq[1:]:
                if added >= max_edges:
                    break
                ra, ro = find(anchor), find(other)
                if ra == ro:
                    continue  # already connected — no redundant edge
                if g.has_edge(anchor, other) or g.has_edge(other, anchor):
                    parent[ra] = ro
                    continue
                g.add_edge(anchor, other, relation=relation, weight=weight,
                           description=f"co-occurrence ({stats['level']}, post-build connectivity)",
                           source_ids=[key], cooccur=True)
                parent[ra] = ro
                added += 1
            if added >= max_edges:
                logger.info(f"add_cooccurrence_edges: hit max_edges={max_edges}, stopping early")
                break

        stats["edges_added"] = added
        stats["components_after"] = _components(kg)
    except Exception as e:
        logger.info(f"add_cooccurrence_edges skipped: {e!r}")
    return stats


def build_chunk_to_doc() -> dict:
    """从 BM25 语料建 chunk_id→doc_id 映射（不加载编码器）。失败返回空 dict。"""
    mapping: dict[str, str] = {}
    try:
        from hashmm.retrieval_pipeline import BM25Index
        bm = BM25Index()
        bm.load()
        for m in (getattr(bm, "_corpus", []) or []):
            cid = str(m.get("chunk_id", "") or "")
            doc = str(m.get("doc_id", "") or m.get("filename", "") or "")
            if cid and doc:
                mapping[cid] = doc
    except Exception as e:
        logger.info(f"build_chunk_to_doc skipped: {e!r}")
    return mapping


def main() -> int:
    print("=" * 64)
    print("KG 连通性修复 + 清理（不调 LLM、不重建，原地改善已存图）")
    print("=" * 64)
    try:
        from hashmm.kg.storage import KGStorage
    except Exception as e:
        print(f"✗ 无法导入 KGStorage：{e}")
        return 1
    try:
        storage = KGStorage()
        kg, comm = storage.load()
    except Exception as e:
        print(f"✗ 载入已存图失败（需在项目根、data/kg 存在）：{e}")
        return 1

    ents0, rels0, comps0 = kg.num_entities, kg.num_relations, _components(kg)
    print(f"载入：实体 {ents0} / 关系 {rels0} / 连通块 {comps0}")

    # 1) 概念去噪（CLI 默认执行保守规则；env 开启激进停用词表）
    aggressive = os.environ.get("HASHMM_KG_DROP_GENERIC_AGGRESSIVE", "").strip().lower() in {"1", "true", "yes", "on"}
    d = drop_generic_concepts(kg, use_stoplist=aggressive)
    if d.get("dropped"):
        extra = f"（后缀 {d['dropped_by_suffix']} + 停用表 {d['dropped_by_stoplist']}）" if aggressive else ""
        print(f"✓ 概念去噪：删除泛化 CONCEPT 节点 {d['dropped']} 个 {extra}（{d['before']}→{d['after']}）")
        if aggressive and d.get("dropped_by_stoplist"):
            print("  （已删高度数泛化枢纽如『首席执行官』；下面会再补边重连）")
    else:
        print("· 概念去噪：未命中（保守规则，正常）" + ("" if aggressive else "  [可设 HASHMM_KG_DROP_GENERIC_AGGRESSIVE=1 删角色/法律形态/委员会类枢纽]"))

    # 2) 共现补边（CLI 默认文档级：力度强）
    chunk_to_doc = build_chunk_to_doc()
    if chunk_to_doc:
        print(f"· 已建 chunk→doc 映射：{len(chunk_to_doc)} 块 → 文档级共现")
    else:
        print("· 未取到 chunk→doc 映射（BM25 语料缺失？）→ 回退 chunk 级")
    c = add_cooccurrence_edges(kg, level="doc", chunk_to_doc=chunk_to_doc)
    print(f"✓ 共现补边（{c['level']}）：+{c['edges_added']} 条 `co_occurs_with`；"
          f"连通块 {c['components_before']} → {c['components_after']}")

    try:
        storage.save(kg, comm)
        print(f"✓ 已保存：实体 {kg.num_entities} / 关系 {kg.num_relations} / 连通块 {_components(kg)}")
    except Exception as e:
        print(f"✗ 保存失败：{e}")
        return 1

    print("\n下一步：python -m hashmm.agent.status   # 复核连通块应大降、平均度上升")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
