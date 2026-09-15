"""hashmm/retrieval/section_graph.py — 单文档结构图（Section 树 + Chunk 连接 + Navigate 扩展）。

借鉴 Knowhere 的「不要把文档拍平成序列，要重建层级」思路（其 heading_tree / heading_hierarchy /
section_tree / chunk_connections），把 HashMM 切块已带的 section_path（如 "第三章 > 财务数据 > 营业收入"）
还原成**显式的章节树**，并为 chunk 之间建立**结构连接**（上一块/下一块、父章节、同章节兄弟）。

检索时的用法是「先召回 → 再沿结构图补上下文」（navigate）：向量/混合检索命中若干 chunk 后，
顺着结构图把**相邻块 + 父章节首块 + 同节兄弟**补进来，解决「命中了一句、但答案要靠上下文/整节」的碎片化问题。

这是**单文档内的结构**，与 hashmm/kg/（跨文档 GraphRAG）互补：一个补「同一篇里的上下文」，
一个补「跨文档的实体关系」，叠起来正是比通用 top-k 向量 RAG 更强的地方。

设计：
- 纯函数、零外部依赖（只用标准库），可被 tests 直接冒烟，不依赖检索栈/模型；
- 输入是 chunk dict（hashmm Chunk.to_dict() 的形状：含 chunk_id / doc_id / start_char / section_path / text …）；
- 一切 best-effort：字段缺失/异常都安全降级，绝不抛错。
"""
from __future__ import annotations

import os
from typing import Callable, Iterable

SEP = " > "   # 与 chunker 的 section_path 分隔符一致


# ───────────────────────── 解析与小工具 ─────────────────────────

def _path_parts(section_path: str) -> list[str]:
    if not section_path:
        return []
    return [p.strip() for p in str(section_path).split(SEP) if p and p.strip()]


def _parent_path(section_path: str) -> str:
    parts = _path_parts(section_path)
    return SEP.join(parts[:-1]) if len(parts) > 1 else ""


def _cid(ch: dict) -> str:
    return str(ch.get("chunk_id") or "")


def _doc(ch: dict) -> str:
    return str(ch.get("doc_id") or "")


def _order_key(ch: dict):
    # 文档内顺序：优先 start_char，其次 page，再 chunk_id（稳定）
    try:
        sc = int(ch.get("start_char") or 0)
    except (TypeError, ValueError):
        sc = 0
    try:
        pg = int(ch.get("page") if ch.get("page") is not None else -1)
    except (TypeError, ValueError):
        pg = -1
    return (pg, sc, _cid(ch))


# ───────────────────────── Section 树 ─────────────────────────

class SectionNode:
    """章节树节点（按 section_path 层级）。"""
    __slots__ = ("label", "level", "doc_id", "path", "chunk_ids", "children")

    def __init__(self, label: str, level: int, doc_id: str, path: str):
        self.label = label
        self.level = level
        self.doc_id = doc_id
        self.path = path
        self.chunk_ids: list[str] = []
        self.children: dict[str, "SectionNode"] = {}

    def to_dict(self) -> dict:
        return {
            "label": self.label, "level": self.level, "doc_id": self.doc_id,
            "path": self.path, "chunk_ids": list(self.chunk_ids),
            "children": [c.to_dict() for c in self.children.values()],
        }


def build_section_tree(chunks: Iterable[dict]) -> dict[str, SectionNode]:
    """按 doc_id 重建章节树。返回 {doc_id: root_node}。root 的 chunk_ids 收无章节的块。"""
    roots: dict[str, SectionNode] = {}
    for ch in chunks or []:
        doc = _doc(ch)
        cid = _cid(ch)
        if not cid:
            continue
        root = roots.get(doc)
        if root is None:
            root = roots[doc] = SectionNode("", 0, doc, "")
        parts = _path_parts(ch.get("section_path") or "")
        node = root
        acc: list[str] = []
        for i, label in enumerate(parts):
            acc.append(label)
            child = node.children.get(label)
            if child is None:
                child = node.children[label] = SectionNode(label, i + 1, doc, SEP.join(acc))
            node = child
        node.chunk_ids.append(cid)
    return roots


# ───────────────────────── Chunk 连接图 ─────────────────────────

def build_chunk_graph(chunks: Iterable[dict]) -> dict[str, dict]:
    """为每个 chunk 建结构连接。返回 {chunk_id: {prev, next, parent_path, section_path, sibling_ids,
    section_first}}。section_first=该 chunk 所在章节按文档序的首块（用于补"本节开头/定义"）。
    """
    chunks = [c for c in (chunks or []) if _cid(c)]
    # 文档内排序
    by_doc: dict[str, list[dict]] = {}
    for ch in chunks:
        by_doc.setdefault(_doc(ch), []).append(ch)
    graph: dict[str, dict] = {}
    # 章节 → 该章节按序的 chunk_id 列表
    for doc, items in by_doc.items():
        items.sort(key=_order_key)
        sec_to_ids: dict[str, list[str]] = {}
        for ch in items:
            sec_to_ids.setdefault(str(ch.get("section_path") or ""), []).append(_cid(ch))
        for idx, ch in enumerate(items):
            cid = _cid(ch)
            sp = str(ch.get("section_path") or "")
            sib = [x for x in sec_to_ids.get(sp, []) if x != cid]
            graph[cid] = {
                "prev": _cid(items[idx - 1]) if idx > 0 else None,
                "next": _cid(items[idx + 1]) if idx + 1 < len(items) else None,
                "parent_path": _parent_path(sp),
                "section_path": sp,
                "sibling_ids": sib,
                "section_first": (sec_to_ids.get(sp) or [None])[0],
            }
    return graph


# ───────────────────────── Navigate 扩展 ─────────────────────────

def navigate_expand(seed_ids: list[str], graph: dict[str, dict], *,
                    budget: int = 6,
                    include: tuple = ("next", "prev", "section_first", "sibling")) -> list[str]:
    """从命中的 seed chunk 出发，沿结构图补上下文，返回**新增**的 chunk_id（不含 seed，去重，有界）。

    顺序优先级（每个 seed 轮转，避免只补到第一个 seed 周围）：
      next（下一块，常承接答案）→ prev（上一块）→ section_first（本节首块/定义）→ sibling（同节兄弟）。
    """
    seeds = [s for s in (seed_ids or []) if s in graph]
    if not seeds or budget <= 0:
        return []
    have = set(seeds)
    added: list[str] = []

    def _try_add(cid):
        if cid and cid not in have:
            have.add(cid)
            added.append(cid)
            return True
        return False

    # 轮转各关系，保证广度（先给每个 seed 补 next，再补 prev …）
    for rel in include:
        if len(added) >= budget:
            break
        for s in seeds:
            if len(added) >= budget:
                break
            g = graph.get(s) or {}
            if rel == "sibling":
                for sib in (g.get("sibling_ids") or [])[:2]:
                    if len(added) >= budget:
                        break
                    _try_add(sib)
            else:
                _try_add(g.get(rel))
    return added


# ───────────────────────── 检索侧便捷封装 ─────────────────────────

def navigate_enabled() -> bool:
    return os.environ.get("HASHMM_NAVIGATE_EXPAND", "0").strip().lower() in ("1", "true", "yes", "on")


def _nav_budget() -> int:
    try:
        return max(0, int(os.environ.get("HASHMM_NAVIGATE_BUDGET", "6")))
    except (TypeError, ValueError):
        return 6


def enrich_sources(sources: list[dict], corpus_chunks: Iterable[dict] | None = None, *,
                   graph: dict[str, dict] | None = None,
                   chunk_lookup: Callable[[str], dict] | None = None,
                   budget: int | None = None) -> list[dict]:
    """把检索结果 sources 沿结构图补上下文后返回（原 sources 在前，补的上下文在后并标 via=navigate）。

    依赖（任一可用即可）：
      - graph：预建的 chunk 连接图（build_chunk_graph 的产物）；或
      - corpus_chunks：全量 chunk dict（内部建图）。
    取补充块的正文：
      - chunk_lookup(chunk_id) -> chunk dict；缺省时尝试从 corpus_chunks 建查找表。
    任何一步缺失/异常 → 原样返回 sources（绝不抛错、绝不吞掉已有结果）。
    """
    try:
        if not sources:
            return sources or []
        if budget is None:
            budget = _nav_budget()
        if budget <= 0:
            return sources

        lut: dict[str, dict] = {}
        if corpus_chunks is not None:
            for c in corpus_chunks:
                cid = _cid(c)
                if cid:
                    lut[cid] = c
        if graph is None:
            src = corpus_chunks if corpus_chunks is not None else list(lut.values())
            if not src:
                return sources
            graph = build_chunk_graph(src)

        def _lookup(cid: str) -> dict:
            if chunk_lookup:
                try:
                    r = chunk_lookup(cid)
                    if r:
                        return r
                except Exception:
                    pass
            return lut.get(cid) or {}

        seed_ids = [str(s.get("chunk_id")) for s in sources if s.get("chunk_id")]
        if not seed_ids:
            return sources
        seen = set(seed_ids)
        extra_ids = navigate_expand(seed_ids, graph, budget=budget)
        out = list(sources)
        for cid in extra_ids:
            if cid in seen:
                continue
            ch = _lookup(cid)
            if not ch or not str(ch.get("text") or "").strip():
                continue
            seen.add(cid)
            out.append({
                "chunk_id": cid,
                "doc_id": ch.get("doc_id", ""),
                "filename": ch.get("doc_title") or ch.get("filename") or "",
                "page": ch.get("page", -1),
                "section_path": ch.get("section_path", ""),
                "text": ch.get("text", ""),
                "score": 0.0,
                "via": "navigate",   # 标记：结构图补的上下文，非直接命中
            })
        return out
    except Exception:
        return sources or []
