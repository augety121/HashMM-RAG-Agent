"""KG Retriever — Graph-RAG retrieval via entity/relation VDB + graph traversal.

This is the core Graph-RAG module. It implements three retrieval modes:

  naive: Traditional vector + BM25 search (no KG involvement)
  kg:    Entity VDB search → graph traversal → collect associated chunks
  mix:   Combine naive + kg results, deduplicate, unified rerank

The "mix" mode is recommended as it gets the best of both worlds:
vector similarity catches surface-level matches while KG traversal
catches semantically-connected but textually-dissimilar information.

Usage:
    from hashmm.kg.kg_retriever import KGRetriever

    retriever = KGRetriever()
    retriever.init()  # loads KG + VDBs

    result = retriever.search("小米和腾讯的AI投入对比", mode="mix")
    # result.entities: matched KG entities
    # result.relations: relevant relationships
    # result.chunk_ids: source chunk IDs to fetch from main index
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.kg_retriever")


@dataclass
class KGSearchResult:
    """Structured result from KG-enhanced retrieval."""
    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)  # source_ids to fetch
    # Query-local, evidence-preserving graph support.  Each item identifies an
    # original indexed chunk plus the real KG nodes/edges that selected it.
    # This is deliberately not generated prose: Chat can audit and fetch the
    # underlying chunk before it reaches the model context.
    evidence: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    mode: str = "mix"


class KGRetriever:
    """Graph-RAG retriever — semantic KG search + graph traversal.

    This class coordinates EntityVDB, RelationVDB, and KnowledgeGraph
    to provide KG-enhanced chunk retrieval.
    """

    def __init__(self):
        self._kg = None
        self._entity_vdb = None
        self._relation_vdb = None
        self._initialized = False

    def init(self) -> bool:
        """Initialize from persisted KG and VDBs.

        Returns True if KG is available (even if VDBs need rebuilding).
        """
        if self._initialized:
            return self._kg is not None

        self._initialized = True

        # Load KG
        try:
            from hashmm.kg.storage import KGStorage
            storage = KGStorage()
            result = storage.load()
            # load() returns (KnowledgeGraph, CommunityManager)
            self._kg = result[0] if isinstance(result, tuple) else result
            if self._kg and self._kg.num_entities > 0:
                logger.info(f"KGRetriever: loaded KG ({self._kg.num_entities} entities, "
                            f"{self._kg.num_relations} relations)")
            else:
                logger.info("KGRetriever: KG is empty or not built yet")
                return False
        except Exception as e:
            logger.warning(f"KGRetriever: KG load failed: {e}")
            return False

        # Load or build VDBs
        from hashmm.kg.entity_vdb import EntityVDB
        from hashmm.kg.relation_vdb import RelationVDB

        self._entity_vdb = EntityVDB()
        self._relation_vdb = RelationVDB()

        if not self._entity_vdb.load():
            logger.info("KGRetriever: building EntityVDB from KG...")
            self._entity_vdb.build_from_kg(self._kg)
            self._entity_vdb.save()

        if not self._relation_vdb.load():
            logger.info("KGRetriever: building RelationVDB from KG...")
            self._relation_vdb.build_from_kg(self._kg)
            self._relation_vdb.save()

        logger.info(f"KGRetriever ready: {self._entity_vdb.size} entity vectors, "
                    f"{self._relation_vdb.size} relation vectors")
        return True

    def rebuild_vdbs(self) -> dict:
        """Rebuild entity and relation VDBs from current KG. Called after KG update."""
        if not self._kg:
            return {"ok": False, "error": "KG not loaded"}

        from hashmm.kg.entity_vdb import EntityVDB
        from hashmm.kg.relation_vdb import RelationVDB

        self._entity_vdb = EntityVDB()
        self._relation_vdb = RelationVDB()

        n_ent = self._entity_vdb.build_from_kg(self._kg)
        n_rel = self._relation_vdb.build_from_kg(self._kg)

        self._entity_vdb.save()
        self._relation_vdb.save()

        return {"ok": True, "entities": n_ent, "relations": n_rel}

    def search(self, query: str, mode: str = "mix",
               top_k_entities: int = 8, top_k_relations: int = 8,
               graph_hops: int = 1,
               hl_keywords: list[str] | None = None,
               ll_keywords: list[str] | None = None) -> KGSearchResult:
        """Perform KG-enhanced retrieval.

        Args:
            query: User query text
            mode: "kg" | "mix" | "global" (community-level summary retrieval)
            top_k_entities: Max entities from EntityVDB
            top_k_relations: Max relations from RelationVDB
            graph_hops: Graph traversal depth
            hl_keywords: High-level keywords → RelationVDB
            ll_keywords: Low-level keywords → EntityVDB

        Returns:
            KGSearchResult with entities, relations, and chunk_ids
        """
        if not self._kg or not self._entity_vdb or not self._relation_vdb:
            return KGSearchResult(mode=mode)

        # v11: Global mode — community-level retrieval
        if mode == "global":
            return self._search_global(query)

        t0 = time.time()
        all_chunk_ids: set[str] = set()
        chunk_support: dict[str, dict] = {}
        result_entities: list[dict] = []
        result_relations: list[dict] = []
        seen_entity_keys: set[str] = set()

        # Use keywords if provided, otherwise fall back to raw query
        entity_query = " ".join(ll_keywords) if ll_keywords else query
        relation_query = " ".join(hl_keywords) if hl_keywords else query

        def add_support(source_id, *, score: float, kind: str, label: str) -> None:
            sid = str(source_id or "").strip()
            if not sid:
                return
            all_chunk_ids.add(sid)
            item = chunk_support.setdefault(sid, {
                "chunk_id": sid, "score": 0.0, "entities": [], "relations": [],
            })
            item["score"] = max(float(item["score"]), max(0.0, min(1.0, float(score or 0.0))))
            bucket = "entities" if kind == "entity" else "relations"
            clean_label = str(label or "").strip()[:240]
            if clean_label and clean_label not in item[bucket]:
                item[bucket].append(clean_label)

        # ── Step 1: Search EntityVDB (local retrieval) ──
        entity_matches = self._entity_vdb.search(entity_query, top_k=top_k_entities)

        for em in entity_matches:
            if em.key in seen_entity_keys:
                continue
            seen_entity_keys.add(em.key)
            result_entities.append({
                "name": em.name,
                "type": em.entity_type,
                "description": em.description,
                "score": round(em.score, 4),
            })
            # Collect source chunks from this entity
            for sid in em.source_ids:
                add_support(sid, score=em.score, kind="entity", label=em.name)

        # ── Step 2: Graph traversal from matched entities ──
        for em in entity_matches[:5]:  # traverse from top-5 entities
            neighborhood = self._kg.get_neighbors(em.key, depth=graph_hops)
            for edge in neighborhood.get("edges", []):
                head_name = self._kg.graph.nodes.get(edge["from"], {}).get("name", edge["from"])
                tail_name = self._kg.graph.nodes.get(edge["to"], {}).get("name", edge["to"])
                result_relations.append({
                    "head": head_name,
                    "tail": tail_name,
                    "relation": edge.get("relation", "related"),
                    "description": edge.get("description", ""),
                    "weight": edge.get("weight", 1.0),
                })
                # Collect source chunks from edges
                edge_sources = edge.get("source_ids", [])
                if isinstance(edge_sources, list):
                    for sid in edge_sources:
                        add_support(
                            sid, score=float(edge.get("weight", 0.5) or 0.5),
                            kind="relation",
                            label=f"{head_name} → {edge.get('relation', 'related')} → {tail_name}",
                        )
                elif edge_sources:
                    add_support(
                        edge_sources, score=float(edge.get("weight", 0.5) or 0.5),
                        kind="relation",
                        label=f"{head_name} → {edge.get('relation', 'related')} → {tail_name}",
                    )

            # Also collect chunks from neighbor nodes
            for neighbor in neighborhood.get("neighbors", []):
                n_key = neighbor.get("key", "")
                if n_key and n_key not in seen_entity_keys:
                    seen_entity_keys.add(n_key)
                    n_sources = neighbor.get("source_ids", [])
                    if isinstance(n_sources, list):
                        for sid in n_sources:
                            add_support(
                                sid, score=0.35, kind="entity",
                                label=neighbor.get("name") or n_key,
                            )

        # ── Step 3: Search RelationVDB (global retrieval) ──
        relation_matches = self._relation_vdb.search(relation_query, top_k=top_k_relations)

        for rm in relation_matches:
            # Deduplicate with graph-traversal relations
            rel_key = f"{rm.head}→{rm.tail}"
            already = any(
                r["head"] == rm.head and r["tail"] == rm.tail
                for r in result_relations
            )
            if not already:
                result_relations.append({
                    "head": rm.head,
                    "tail": rm.tail,
                    "relation": rm.relation,
                    "description": rm.description,
                    "weight": rm.weight,
                    "score": round(rm.score, 4),
                })
            for sid in rm.source_ids:
                add_support(
                    sid, score=rm.score, kind="relation",
                    label=f"{rm.head} → {rm.relation} → {rm.tail}",
                )

        # ── Deduplicate relations ──
        seen_rels: set[str] = set()
        unique_relations = []
        for r in result_relations:
            key = f"{r['head']}::{r.get('relation', '')}::{r['tail']}"
            rev_key = f"{r['tail']}::{r.get('relation', '')}::{r['head']}"
            if key not in seen_rels and rev_key not in seen_rels:
                seen_rels.add(key)
                unique_relations.append(r)
        result_relations = unique_relations[:top_k_relations]

        elapsed = round((time.time() - t0) * 1000)

        logger.info(f"KGRetriever [{mode}]: {len(result_entities)} entities, "
                    f"{len(result_relations)} relations, "
                    f"{len(all_chunk_ids)} source chunks, {elapsed}ms")

        evidence = sorted(
            chunk_support.values(),
            key=lambda item: (
                -float(item.get("score", 0.0)),
                -(len(item.get("entities", [])) + len(item.get("relations", []))),
                item.get("chunk_id", ""),
            ),
        )

        return KGSearchResult(
            entities=result_entities,
            relations=result_relations,
            chunk_ids=[item["chunk_id"] for item in evidence],
            evidence=evidence,
            elapsed_ms=elapsed,
            mode=mode,
        )

    def _search_global(self, query: str) -> KGSearchResult:
        """Global mode: retrieve via community summaries (LightRAG-style).

        Instead of searching for specific entities, this mode:
        1. Gets all communities from the KG
        2. Matches query keywords against community summaries
        3. Returns chunk_ids from matching communities
        """
        import re
        t0 = time.time()

        communities = self._kg.get_communities()
        if not communities:
            return KGSearchResult(mode="global", elapsed_ms=round((time.time() - t0) * 1000))

        # Extract keywords from query
        cn_kw = set(re.findall(r'[\u4e00-\u9fff]{2,4}', query))
        en_kw = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', query))
        all_kw = cn_kw | en_kw

        # Score communities by keyword overlap with summary + entity names
        scored = []
        for comm in communities:
            score = 0
            summary_lower = comm["summary"].lower()
            for kw in all_kw:
                if kw in summary_lower:
                    score += 2
            # Also check entity names
            for ent in comm.get("entities", []):
                for kw in all_kw:
                    if kw in ent.get("name", "").lower():
                        score += 3
            if score > 0:
                scored.append((score, comm))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_comms = scored[:3]

        # Collect entities and chunk_ids from matching communities
        all_chunk_ids: set[str] = set()
        result_entities: list[dict] = []

        for _, comm in top_comms:
            for sid in comm.get("source_ids", []):
                all_chunk_ids.add(sid)
            for ent in comm.get("entities", [])[:5]:
                result_entities.append({
                    "name": ent["name"],
                    "type": ent.get("type", ""),
                    "description": f"社区 {comm['id']} ({comm['size']} 实体)",
                    "score": 1.0,
                })

        elapsed = round((time.time() - t0) * 1000)
        logger.info(f"KGRetriever [global]: {len(top_comms)} communities matched, "
                    f"{len(result_entities)} entities, {len(all_chunk_ids)} chunks, {elapsed}ms")

        return KGSearchResult(
            entities=result_entities,
            relations=[],
            chunk_ids=list(all_chunk_ids),
            elapsed_ms=elapsed,
            mode="global",
        )

    @property
    def is_available(self) -> bool:
        """Whether KG retrieval is possible (KG loaded + VDBs built)."""
        return (self._kg is not None
                and self._entity_vdb is not None
                and self._entity_vdb.size > 0)

    def stats(self) -> dict:
        return {
            "kg_entities": self._kg.num_entities if self._kg else 0,
            "kg_relations": self._kg.num_relations if self._kg else 0,
            "entity_vdb_size": self._entity_vdb.size if self._entity_vdb else 0,
            "relation_vdb_size": self._relation_vdb.size if self._relation_vdb else 0,
            "available": self.is_available,
        }


# ── Module-level singleton ──

_kg_retriever: KGRetriever | None = None


def get_kg_retriever() -> KGRetriever:
    """Get or create the global KGRetriever singleton."""
    global _kg_retriever
    if _kg_retriever is None:
        _kg_retriever = KGRetriever()
        _kg_retriever.init()
    return _kg_retriever
