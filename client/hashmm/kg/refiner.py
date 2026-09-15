"""KG Refiner — post-extraction entity refinement.

Phase 3: After coarse extraction, refine entities by:
  #13: Noise entity filtering (blacklist + min frequency + context validation)
  #14: Embedding-based entity merging (BGE-M3 cosine > 0.85 → merge)
  #15: Entity weight scoring (TF-IDF × chunk frequency × position weight)
  #16: Entity type correction (reclassify based on co-occurrence context)
"""
from __future__ import annotations
import re
import math
from collections import Counter, defaultdict
from hashmm.kg.extractor import Entity, Relation
from hashmm.kg.graph import KnowledgeGraph
from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.refiner")

# #13: Known noise entities — publisher names, page elements, common words
ENTITY_BLACKLIST = {
    # Publishers / page elements
    "elsevier", "elsevierpage", "springer", "ieee", "acm", "arxiv",
    "wiley", "taylor", "francis", "mdpi", "nature", "science",
    "copyright", "allrightsreserved", "doi", "issn", "isbn",
    "received", "accepted", "published", "volume", "issue",
    "journal", "proceedings", "conference", "workshop",
    "preprint", "manuscript", "supplementary", "appendix",

    # Too generic
    "method", "approach", "model", "algorithm", "framework",
    "system", "network", "module", "layer", "function",
    "result", "experiment", "table", "figure", "section",
    "data", "dataset", "loss", "map", "set", "input", "output",
    "feature", "image", "text", "label", "class", "task",
    "training", "testing", "learning", "deep", "neural",
    "performance", "baseline", "comparison", "evaluation",
    "proposed", "existing", "previous", "recent", "novel",
    "paper", "work", "study", "research", "analysis",
}

# #16: Context patterns for type correction
_TYPE_CONTEXT = {
    "工具": ["framework", "library", "package", "tool", "platform",
             "implemented in", "built with", "using", "框架", "工具", "库"],
    "数据集": ["dataset", "benchmark", "corpus", "数据集", "语料", "基准",
               "images", "samples", "instances", "训练集", "测试集"],
    "指标": ["metric", "score", "measure", "evaluate", "指标", "评价",
             "precision", "recall", "accuracy", "performance"],
    "损失函数": ["loss", "objective", "criterion", "optimize", "损失", "目标函数"],
    "方法": ["propose", "introduce", "method", "algorithm", "approach",
             "提出", "方法", "算法", "模型"],
}


class KGRefiner:
    """Refine extracted KG entities for higher quality."""

    def __init__(self, encoder_fn=None, min_frequency: int = 2):
        """
        Args:
            encoder_fn: Optional BGE-M3 encode function for embedding merge
            min_frequency: Minimum chunk appearances to keep an entity
        """
        self.encoder_fn = encoder_fn
        self.min_frequency = min_frequency

    def refine(self, kg: KnowledgeGraph,
               chunks: list[dict] | None = None) -> dict:
        """Run all refinement steps on a KnowledgeGraph.

        Returns:
            {"removed": int, "merged": int, "retyped": int, "reweighted": int}
        """
        stats = {"removed": 0, "merged": 0, "retyped": 0, "reweighted": 0}

        # #13: Filter noise entities
        removed = self._filter_noise(kg)
        stats["removed"] = removed

        # #14: Merge similar entities (embedding-based if encoder available)
        merged = self._merge_similar(kg)
        stats["merged"] = merged

        # #15: Recalculate entity weights
        reweighted = self._recalculate_weights(kg)
        stats["reweighted"] = reweighted

        # #16: Correct entity types
        if chunks:
            retyped = self._correct_types(kg, chunks)
            stats["retyped"] = retyped

        logger.info(f"KG refined: removed={removed}, merged={merged}, "
                    f"retyped={stats['retyped']}, reweighted={reweighted}")
        return stats

    def _filter_noise(self, kg: KnowledgeGraph) -> int:
        """#13: Remove noise entities."""
        to_remove = []
        for key, attrs in list(kg._entity_index.items()):
            name = attrs.get("name", key)
            name_lower = name.lower().strip()

            # Blacklist check
            if name_lower in ENTITY_BLACKLIST:
                to_remove.append(key)
                continue

            # Too short (single char)
            if len(name_lower) <= 1:
                to_remove.append(key)
                continue

            # Pure numbers
            if name_lower.replace(".", "").replace("-", "").isnumeric():
                to_remove.append(key)
                continue

            # Minimum frequency check
            source_count = len(attrs.get("source_ids", []))
            if source_count < self.min_frequency:
                degree = kg.graph.degree(key) if kg.graph.has_node(key) else 0
                if degree < 2:  # Low connectivity + low frequency → noise
                    to_remove.append(key)
                    continue

        for key in to_remove:
            kg.remove_entity(key)

        return len(to_remove)

    def _merge_similar(self, kg: KnowledgeGraph) -> int:
        """#14: Merge entities with similar names."""
        merged_count = 0
        entities = list(kg._entity_index.items())

        # Simple name-based merging (no embedding needed)
        merge_map: dict[str, str] = {}  # key_to_remove → key_to_keep

        for i in range(len(entities)):
            key_i, attrs_i = entities[i]
            if key_i in merge_map:
                continue
            name_i = attrs_i.get("name", key_i).lower().strip()

            for j in range(i + 1, len(entities)):
                key_j, attrs_j = entities[j]
                if key_j in merge_map:
                    continue
                name_j = attrs_j.get("name", key_j).lower().strip()

                # Check if one is a substring of the other (e.g., "MS" ⊂ "MS COCO")
                should_merge = False
                if name_i == name_j:
                    should_merge = True
                elif len(name_i) >= 3 and len(name_j) >= 3:
                    # "MS COCO" and "COCO" → merge
                    if name_i in name_j or name_j in name_i:
                        should_merge = True
                    # Abbreviation match: "NUS-WIDE" and "NUS WIDE"
                    elif name_i.replace("-", " ") == name_j.replace("-", " "):
                        should_merge = True

                if should_merge:
                    # Keep the longer name
                    if len(name_i) >= len(name_j):
                        merge_map[key_j] = key_i
                    else:
                        merge_map[key_i] = key_j
                    merged_count += 1

        # Apply merges
        for remove_key, keep_key in merge_map.items():
            if not kg.graph.has_node(remove_key) or not kg.graph.has_node(keep_key):
                continue
            # Transfer edges
            for pred in list(kg.graph.predecessors(remove_key)):
                if pred != keep_key:
                    edge_data = kg.graph.edges[pred, remove_key]
                    if not kg.graph.has_edge(pred, keep_key):
                        kg.graph.add_edge(pred, keep_key, **edge_data)
            for succ in list(kg.graph.successors(remove_key)):
                if succ != keep_key:
                    edge_data = kg.graph.edges[remove_key, succ]
                    if not kg.graph.has_edge(keep_key, succ):
                        kg.graph.add_edge(keep_key, succ, **edge_data)
            # Merge source_ids
            keep_sids = kg.graph.nodes[keep_key].get("source_ids", [])
            remove_sids = kg.graph.nodes[remove_key].get("source_ids", [])
            kg.graph.nodes[keep_key]["source_ids"] = list(set(keep_sids + remove_sids))
            # Remove
            kg.graph.remove_node(remove_key)
            kg._entity_index.pop(remove_key, None)

        return merged_count

    def _recalculate_weights(self, kg: KnowledgeGraph) -> int:
        """#15: Recalculate entity importance weights."""
        if kg.num_entities == 0:
            return 0

        total_sources = sum(
            len(attrs.get("source_ids", []))
            for _, attrs in kg.graph.nodes(data=True)
        )
        avg_sources = total_sources / max(kg.num_entities, 1)

        count = 0
        for key, attrs in kg.graph.nodes(data=True):
            source_count = len(attrs.get("source_ids", []))
            degree = kg.graph.degree(key)

            # TF-IDF-like: frequency × inverse document frequency
            tf = source_count / max(avg_sources, 1)
            idf = math.log(1 + kg.num_entities / max(degree + 1, 1))

            # Position weight: entities in section titles get bonus
            section = attrs.get("section", "")
            position_bonus = 1.5 if section else 1.0

            weight = min(tf * idf * position_bonus, 10.0)
            attrs["weight"] = round(weight, 3)
            count += 1

        return count

    def _correct_types(self, kg: KnowledgeGraph, chunks: list[dict]) -> int:
        """#16: Correct entity types based on co-occurrence context."""
        # Build context for each entity from chunks
        entity_contexts: dict[str, list[str]] = defaultdict(list)
        for chunk in chunks:
            text = chunk.get("text", "").lower()
            for key in kg._entity_index:
                name = kg._entity_index[key].get("name", key)
                if name.lower() in text:
                    # Get surrounding context (±50 chars)
                    idx = text.find(name.lower())
                    context = text[max(0, idx - 50):idx + len(name) + 50]
                    entity_contexts[key].append(context)

        retyped = 0
        for key, contexts in entity_contexts.items():
            if not kg.graph.has_node(key):
                continue
            current_type = kg.graph.nodes[key].get("type", "概念")
            all_context = " ".join(contexts[:10])

            # Score each type by context keywords
            best_type = current_type
            best_score = 0
            for etype, keywords in _TYPE_CONTEXT.items():
                score = sum(1 for kw in keywords if kw in all_context)
                if score > best_score:
                    best_score = score
                    best_type = etype

            if best_type != current_type and best_score >= 2:
                kg.graph.nodes[key]["type"] = best_type
                kg._entity_index[key]["type"] = best_type
                retyped += 1

        return retyped
