"""Hybrid Retriever — 5-mode retrieval engine with KG integration.

Modes (inspired by LightRAG):
  - naive:  Pure hash/vector search (existing HashMM)
  - local:  Entity neighborhood + related chunks (KG-enhanced)
  - global: Community summaries + global topics
  - hybrid: local + global merged
  - mix:    KG + vector + BM25 fused via RRF (default, best quality)

Also includes:
  - Reciprocal Rank Fusion (RRF) for multi-signal merging
  - BGE Reranker integration (optional, +0.7GB VRAM)
  - Citation tracking (chunk → doc_id → filename + page)
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any, Callable, Literal
from hashmm.kg.graph import KnowledgeGraph
from hashmm.kg.community import CommunityManager
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retriever")

RetrievalMode = Literal["naive", "local", "global", "hybrid", "mix"]


@dataclass
class RetrievalResult:
    """A single retrieval result with provenance."""
    chunk_id: str
    text: str
    score: float
    doc_id: str = ""
    source: str = ""        # filename
    page: int = -1
    modality: str = "text"
    method: str = ""        # which retrieval method found this
    entity_context: str = ""  # KG context if from local/global mode

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "text": self.text, "score": round(self.score, 4),
            "doc_id": self.doc_id, "source": self.source, "page": self.page,
            "modality": self.modality, "method": self.method,
        }


@dataclass
class RetrievalResponse:
    """Full retrieval response with citations."""
    results: list[RetrievalResult]
    mode: str
    kg_context: str = ""       # aggregated KG context
    community_context: str = ""  # community summaries
    citations: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    debug: dict = field(default_factory=dict)


class HybridRetriever:
    """5-mode hybrid retrieval engine.

    Combines existing HashMM hash search with KG-enhanced retrieval.

    Args:
        kg: KnowledgeGraph instance
        community_mgr: CommunityManager instance
        hash_search_fn: Existing hash search function (query → results)
        keyword_search_fn: Existing BM25/keyword search function
        metadata: List of chunk metadata dicts
        reranker: Optional BGE reranker instance
    """

    def __init__(
        self,
        kg: KnowledgeGraph | None = None,
        community_mgr: CommunityManager | None = None,
        hash_search_fn: Callable | None = None,
        keyword_search_fn: Callable | None = None,
        metadata: list[dict] | None = None,
        reranker: "Reranker | None" = None,
    ):
        self.kg = kg or KnowledgeGraph()
        self.community_mgr = community_mgr or CommunityManager()
        self.hash_search_fn = hash_search_fn
        self.keyword_search_fn = keyword_search_fn
        self.metadata = metadata or []
        self.reranker = reranker

        # Build chunk index for fast lookup
        self._chunk_index: dict[str, dict] = {}
        self._rebuild_chunk_index()

    def _rebuild_chunk_index(self):
        """Index metadata by doc_id and chunk_id for fast lookup."""
        self._chunk_index.clear()
        for m in self.metadata:
            cid = m.get("chunk_id", m.get("doc_id", ""))
            if cid:
                self._chunk_index[cid] = m

    def retrieve(self, query: str, mode: RetrievalMode = "mix",
                 top_k: int = 10,
                 filters: dict | None = None) -> RetrievalResponse:
        """Main retrieval entry point.

        Args:
            query: User query
            mode: Retrieval mode
            top_k: Number of results to return
            filters: Optional metadata filters {"file_type": "pdf", "source": "report.docx"}

        Returns:
            RetrievalResponse with results, citations, and debug info
        """
        t0 = time.time()
        debug: dict[str, Any] = {"mode": mode, "top_k": top_k}

        if mode == "naive":
            results = self._naive_search(query, top_k)
        elif mode == "local":
            results = self._local_search(query, top_k)
        elif mode == "global":
            results = self._global_search(query, top_k)
        elif mode == "hybrid":
            local = self._local_search(query, top_k)
            global_ = self._global_search(query, top_k // 2)
            results = self._merge_deduplicate(local + global_, top_k)
            debug["local_count"] = len(local)
            debug["global_count"] = len(global_)
        elif mode == "mix":
            results = self._mix_search(query, top_k)
        else:
            results = self._naive_search(query, top_k)

        # #18: Apply metadata filters
        if filters:
            results = self._apply_filters(results, filters)
            debug["filters"] = filters

        # Rerank if available
        if self.reranker and len(results) > 1:
            results = self.reranker.rerank(query, results, top_k=top_k)
            debug["reranked"] = True

        # Build citations
        citations = self._build_citations(results)

        # Build KG context
        kg_context = ""
        community_context = ""
        if mode in ("local", "hybrid", "mix"):
            kg_context = self._build_kg_context(query)
        if mode in ("global", "hybrid", "mix"):
            community_context = self._build_community_context(query)

        # #20: Coverage stats
        total_chunks = len(self.metadata) if self.metadata else 0
        debug["index_coverage"] = {
            "total_chunks": total_chunks,
            "total_docs": len(set(m.get("doc_id", "") for m in self.metadata)) if self.metadata else 0,
        }

        elapsed = round((time.time() - t0) * 1000)
        debug["result_count"] = len(results)
        debug["elapsed_ms"] = elapsed

        return RetrievalResponse(
            results=results, mode=mode,
            kg_context=kg_context, community_context=community_context,
            citations=citations, elapsed_ms=elapsed, debug=debug,
        )

    def _apply_filters(self, results: list[RetrievalResult],
                       filters: dict) -> list[RetrievalResult]:
        """#18: Filter results by metadata (file_type, source, date, etc.)."""
        filtered = []
        for r in results:
            match = True
            meta = self._chunk_index.get(r.chunk_id, {})
            for key, value in filters.items():
                meta_value = meta.get(key, r.__dict__.get(key, ""))
                if isinstance(value, str) and str(meta_value).lower() != value.lower():
                    match = False
                    break
            if match:
                filtered.append(r)
        return filtered

    # ── Search modes ──

    def _naive_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """Pure vector/hash search (existing HashMM pipeline)."""
        if not self.hash_search_fn:
            return []
        try:
            raw_results, _, _ = self.hash_search_fn(query, top_k)
            return [
                RetrievalResult(
                    chunk_id=r.get("chunk_id", f"c{i}"),
                    text=r.get("text", ""),
                    score=float(r.get("score", 0)),
                    doc_id=r.get("doc_id", ""),
                    source=r.get("source", ""),
                    page=r.get("page", -1),
                    modality=r.get("modality", "text"),
                    method="hash",
                )
                for i, r in enumerate(raw_results) if isinstance(r, dict)
            ]
        except Exception as e:
            logger.warning(f"Naive search failed: {e}")
            return []

    def _local_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """KG local search: find entities in query → expand neighborhoods → get related chunks."""
        if self.kg.num_entities == 0:
            return self._naive_search(query, top_k)

        # Step 1: Find entities mentioned in the query
        query_entities = self._extract_query_entities(query)

        if not query_entities:
            # Fallback to naive if no entities found
            return self._naive_search(query, top_k)

        # Step 2: Expand entity neighborhoods
        all_source_ids: list[str] = []
        entity_context_parts: list[str] = []

        for entity_name in query_entities[:5]:  # Limit to top 5 entities
            neighborhood = self.kg.get_neighbors(entity_name, depth=1)
            if neighborhood["center"]:
                center = neighborhood["center"]
                entity_context_parts.append(
                    f"{center.get('name', entity_name)} ({center.get('type', '')}): "
                    f"{center.get('description', '')}"
                )
                all_source_ids.extend(center.get("source_ids", []))

            for neighbor in neighborhood["neighbors"]:
                all_source_ids.extend(neighbor.get("source_ids", []))

            # Get relations for context
            relations = self.kg.get_relations_for_entity(entity_name)
            for rel in relations[:5]:
                entity_context_parts.append(
                    f"  {rel['head']} → {rel['relation']} → {rel['tail']}"
                )

        # Step 3: Retrieve chunks by source_ids
        results = []
        seen_chunks: set[str] = set()
        for sid in all_source_ids:
            if sid in self._chunk_index and sid not in seen_chunks:
                seen_chunks.add(sid)
                m = self._chunk_index[sid]
                results.append(RetrievalResult(
                    chunk_id=sid, text=m.get("text", ""),
                    score=0.8, doc_id=m.get("doc_id", ""),
                    source=m.get("source", ""), page=m.get("page", -1),
                    method="kg_local",
                    entity_context="\n".join(entity_context_parts[:10]),
                ))
                if len(results) >= top_k:
                    break

        # Supplement with naive search if not enough
        if len(results) < top_k:
            naive = self._naive_search(query, top_k - len(results))
            for r in naive:
                if r.chunk_id not in seen_chunks:
                    results.append(r)

        return results[:top_k]

    def _global_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """KG global search: use community summaries for broad context."""
        if not self.community_mgr.communities:
            return self._naive_search(query, top_k)

        # Find relevant communities by keyword overlap
        q_lower = query.lower()
        scored_communities: list[tuple[float, int]] = []

        for cid, info in self.community_mgr.communities.items():
            # Score by keyword overlap between query and community summary + members
            summary_lower = info.summary.lower()
            members_text = " ".join(info.members).lower()
            combined = summary_lower + " " + members_text

            # Simple word overlap scoring
            q_words = set(re.findall(r'\w+', q_lower))
            c_words = set(re.findall(r'\w+', combined))
            overlap = len(q_words & c_words)
            if overlap > 0:
                score = overlap / max(len(q_words), 1)
                scored_communities.append((score, cid))

        scored_communities.sort(reverse=True)

        # Get chunks from top communities
        results = []
        for score, cid in scored_communities[:3]:
            info = self.community_mgr.communities[cid]
            # Get source_ids from community members
            for member in info.members[:10]:
                if self.kg.graph.has_node(member):
                    for sid in self.kg.graph.nodes[member].get("source_ids", []):
                        if sid in self._chunk_index and len(results) < top_k:
                            m = self._chunk_index[sid]
                            results.append(RetrievalResult(
                                chunk_id=sid, text=m.get("text", ""),
                                score=score, doc_id=m.get("doc_id", ""),
                                source=m.get("source", ""), page=m.get("page", -1),
                                method="kg_global",
                                entity_context=f"[社区{cid}] {info.summary[:200]}",
                            ))

        if len(results) < top_k:
            naive = self._naive_search(query, top_k - len(results))
            results.extend(naive)

        return results[:top_k]

    def _mix_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """Mix mode: fuse hash + KG-local + KG-global + BM25 via RRF."""
        # Collect results from all sources
        candidates: dict[str, list[tuple[str, int, RetrievalResult]]] = defaultdict(list)

        # Source 1: Hash/vector search
        hash_results = self._naive_search(query, top_k * 2)
        for rank, r in enumerate(hash_results):
            candidates[r.chunk_id].append(("hash", rank, r))

        # Source 2: KG local
        if self.kg.num_entities > 0:
            local_results = self._local_search(query, top_k)
            for rank, r in enumerate(local_results):
                candidates[r.chunk_id].append(("kg_local", rank, r))

        # Source 3: KG global
        if self.community_mgr.communities:
            global_results = self._global_search(query, top_k // 2)
            for rank, r in enumerate(global_results):
                candidates[r.chunk_id].append(("kg_global", rank, r))

        # Source 4: BM25 keyword
        if self.keyword_search_fn:
            try:
                kw_results = self.keyword_search_fn(query, top_k)
                for rank, r in enumerate(kw_results):
                    cid = r.get("chunk_id", f"kw_{rank}")
                    candidates[cid].append(("bm25", rank, RetrievalResult(
                        chunk_id=cid, text=r.get("text", ""),
                        score=float(r.get("score", 0)),
                        doc_id=r.get("doc_id", ""), source=r.get("source", ""),
                        method="bm25",
                    )))
            except Exception as _e:
                log_suppressed(logger, _e)

        # RRF fusion
        weights = {"hash": 0.40, "kg_local": 0.30, "kg_global": 0.15, "bm25": 0.15}
        rrf_scores: dict[str, float] = {}
        best_result: dict[str, RetrievalResult] = {}

        K = 60  # RRF constant
        for chunk_id, entries in candidates.items():
            total_score = 0.0
            for source, rank, result in entries:
                w = weights.get(source, 0.1)
                total_score += w / (K + rank + 1)
                if chunk_id not in best_result:
                    best_result[chunk_id] = result
            rrf_scores[chunk_id] = total_score

        # Sort by RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: -rrf_scores[x])

        results = []
        for cid in sorted_ids[:top_k]:
            r = best_result[cid]
            r.score = rrf_scores[cid]
            r.method = "mix"
            results.append(r)

        return results

    # ── Helpers ──

    def _extract_query_entities(self, query: str) -> list[str]:
        """Find KG entities mentioned in the query."""
        q_lower = query.lower()
        found = []
        for key in self.kg._entity_index:
            name = self.kg._entity_index[key].get("name", key)
            if name.lower() in q_lower or key in q_lower:
                found.append(key)
        # Sort by name length (longer = more specific = higher priority)
        found.sort(key=lambda x: -len(x))
        return found

    def _build_kg_context(self, query: str) -> str:
        """Build KG context string for the query."""
        entities = self._extract_query_entities(query)
        if not entities:
            return ""
        parts = []
        for e in entities[:5]:
            attrs = self.kg.graph.nodes.get(e, {})
            name = attrs.get("name", e)
            desc = attrs.get("description", "")
            parts.append(f"• {name}: {desc}")
            for rel in self.kg.get_relations_for_entity(e)[:3]:
                parts.append(f"  → {rel['relation']} → {rel['tail']}")
        return "\n".join(parts)

    def _build_community_context(self, query: str) -> str:
        """Build community context for global retrieval."""
        entities = self._extract_query_entities(query)
        relevant = self.community_mgr.get_relevant_communities(entities)
        if not relevant:
            return ""
        parts = []
        for info in relevant[:3]:
            parts.append(f"[主题社区] {info.summary}")
        return "\n".join(parts)

    def multi_hop_reasoning(self, query: str, max_hops: int = 2) -> str:
        """C2: Multi-hop KG reasoning — follow relation chains for deeper context.

        Example: DCMH → uses → pairwise loss → outperforms → triplet loss
        Returns reasoning paths as context string.
        """
        entities = self._extract_query_entities(query)
        if not entities:
            return ""

        paths_found: list[str] = []
        visited_pairs: set[str] = set()

        for entity in entities[:3]:
            # Get direct relations
            relations = self.kg.get_relations_for_entity(entity)
            for rel in relations[:5]:
                tail_key = rel["tail"].lower().strip()
                pair_key = f"{entity}→{tail_key}"
                if pair_key in visited_pairs:
                    continue
                visited_pairs.add(pair_key)

                path_str = f"{rel['head']} → {rel['relation']} → {rel['tail']}"

                # Hop 2: follow from tail
                if max_hops >= 2:
                    tail_rels = self.kg.get_relations_for_entity(tail_key)
                    for tr in tail_rels[:3]:
                        hop2_key = f"{tail_key}→{tr['tail'].lower()}"
                        if hop2_key not in visited_pairs:
                            visited_pairs.add(hop2_key)
                            paths_found.append(
                                f"{path_str} → {tr['relation']} → {tr['tail']}"
                            )

                paths_found.append(path_str)

        if not paths_found:
            return ""

        return "## 知识推理路径\n" + "\n".join(f"• {p}" for p in paths_found[:10])

    def analyze_query(self, query: str) -> dict:
        """C1: Analyze query to determine optimal retrieval strategy.

        Returns:
            {
                "mode": "mix",
                "entities": ["DCMH"],
                "query_type": "comparison",
                "top_k": 10,
                "needs_multi_hop": True,
                "explanation": "..."
            }
        """
        entities = self._extract_query_entities(query)
        mode = self.auto_select_mode(query)
        q = query.lower()

        # Determine query type
        if any(w in q for w in ["区别", "对比", "比较", "vs", "优劣"]):
            query_type = "comparison"
            top_k = 15
        elif any(w in q for w in ["综述", "概述", "总结", "有哪些", "overview"]):
            query_type = "survey"
            top_k = 15
        elif any(w in q for w in ["怎么", "如何", "步骤", "how"]):
            query_type = "howto"
            top_k = 8
        elif any(w in q for w in ["是什么", "定义", "what is", "define"]):
            query_type = "definition"
            top_k = 5
        elif len(query) < 20:
            query_type = "factual"
            top_k = 5
        else:
            query_type = "complex"
            top_k = 10

        needs_multi_hop = (
            len(entities) >= 2 or
            query_type in ("comparison", "survey") or
            any(w in q for w in ["关系", "影响", "导致", "因为"])
        )

        return {
            "mode": mode,
            "entities": [self.kg._entity_index.get(e, {}).get("name", e) for e in entities],
            "query_type": query_type,
            "top_k": top_k,
            "needs_multi_hop": needs_multi_hop,
            "explanation": f"检测到 {len(entities)} 个实体，查询类型: {query_type}，推荐模式: {mode}",
        }

    def _build_citations(self, results: list[RetrievalResult]) -> list[dict]:
        """Build citation list from retrieval results."""
        citations = []
        seen_docs: set[str] = set()
        for i, r in enumerate(results):
            if r.doc_id and r.doc_id not in seen_docs:
                seen_docs.add(r.doc_id)
                citations.append({
                    "index": i + 1,
                    "doc_id": r.doc_id,
                    "source": r.source or r.doc_id,
                    "page": r.page if r.page > 0 else None,
                    "text_preview": r.text[:150] if r.text else "",
                })
        return citations

    def _merge_deduplicate(self, results: list[RetrievalResult],
                           top_k: int) -> list[RetrievalResult]:
        """Merge and deduplicate results by chunk_id."""
        seen: dict[str, RetrievalResult] = {}
        for r in results:
            if r.chunk_id not in seen or r.score > seen[r.chunk_id].score:
                seen[r.chunk_id] = r
        sorted_results = sorted(seen.values(), key=lambda x: -x.score)
        return sorted_results[:top_k]

    def auto_select_mode(self, query: str) -> RetrievalMode:
        """Auto-select best retrieval mode based on query type."""
        q = query.lower()
        entities = self._extract_query_entities(query)

        # Entity-specific query → local
        if len(entities) == 1 and len(query) < 50:
            return "local"

        # Comparison query → hybrid
        compare_words = ["区别", "对比", "比较", "vs", "versus", "优劣", "差异"]
        if any(w in q for w in compare_words):
            return "hybrid"

        # Overview/survey query → global
        survey_words = ["综述", "概述", "总结", "有哪些", "主要方法", "survey", "overview"]
        if any(w in q for w in survey_words):
            return "global"

        # Simple fact → naive
        if len(query) < 20 and not entities:
            return "naive"

        # Default → mix
        return "mix"


class Reranker:
    """BGE Reranker wrapper for result reranking.

    Uses BAAI/bge-reranker-v2-m3 (~680MB, fits on 4090 with BGE-M3).
    Falls back to no-op if model not available.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3",
                 device: str = "cuda:0"):
        self.model = None
        self.device = device
        try:
            from FlagEmbedding import FlagReranker
            self.model = FlagReranker(model_name, device=device, use_fp16=True)
            logger.info(f"Reranker loaded: {model_name} on {device}")
        except ImportError:
            logger.info("FlagEmbedding not installed, reranker disabled. "
                        "Install: pip install FlagEmbedding")
        except Exception as e:
            logger.warning(f"Reranker loading failed: {e}")

    @property
    def available(self) -> bool:
        return self.model is not None

    def rerank(self, query: str, results: list[RetrievalResult],
               top_k: int = 10) -> list[RetrievalResult]:
        """Rerank results using cross-encoder score."""
        if not self.model or not results:
            return results

        try:
            pairs = [[query, r.text] for r in results if r.text]
            scores = self.model.compute_score(pairs, normalize=True)
            if isinstance(scores, (int, float)):
                scores = [scores]

            for r, s in zip(results, scores):
                r.score = float(s)

            results.sort(key=lambda x: -x.score)
            return results[:top_k]
        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return results
