"""Knowledge Graph — NetworkX-based graph management.

Provides CRUD operations, neighbor queries, path finding,
and statistics for the knowledge graph.
"""
from __future__ import annotations
import networkx as nx
from collections import Counter
from typing import Any
from hashmm.kg.extractor import Entity, Relation
from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.graph")


class KnowledgeGraph:
    """In-memory knowledge graph backed by NetworkX."""

    def __init__(self):
        self.graph = nx.DiGraph()
        self._entity_index: dict[str, dict] = {}  # name_lower → node attrs
        self._alias_map: dict[str, str] = {}       # variant_key → canonical_key

    @property
    def num_entities(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def num_relations(self) -> int:
        return self.graph.number_of_edges()

    # ── Normalization (v16: fixes entity fragmentation / low density) ──

    # Common org suffixes that cause "小米" vs "小米集团" vs "小米集团股份有限公司"
    # to be treated as different entities.
    _ORG_SUFFIXES = [
        "集团股份有限公司", "股份有限公司", "有限责任公司", "有限公司",
        "集团公司", "科技有限公司", "集团", "公司",
        "corporation", "incorporated", "limited", "company",
        "corp.", "corp", "inc.", "inc", "ltd.", "ltd", "co.", "llc",
    ]

    def _canonical_key(self, name: str) -> str:
        """Map an entity name to its canonical key, merging known variants.

        The normalisation algorithm lives in :mod:`hashmm.kg.entity_canonicalize`
        (single source of truth, with full/half-width folding, ASCII case
        folding, and stem-length guards that prevent over-merging). Here we layer
        the graph's alias map on top so variants seen earlier reuse the same node.
        """
        from hashmm.kg.entity_canonicalize import canonical_key as _ck
        stripped = _ck(name)
        if not stripped:
            stripped = " ".join(name.lower().strip().split())
        # If we've already seen this stripped form, reuse its canonical node.
        if stripped in self._alias_map:
            return self._alias_map[stripped]
        self._alias_map[stripped] = stripped
        return stripped

    # ── Add ──

    def add_entity(self, entity: Entity):
        """Add or update an entity node (with variant merging)."""
        key = self._canonical_key(entity.name)
        if self.graph.has_node(key):
            # Merge into existing canonical node
            existing = self.graph.nodes[key]
            existing["source_ids"] = list(
                set(existing.get("source_ids", []) + list(entity.source_ids))
            )
            if len(entity.description) > len(existing.get("description", "")):
                existing["description"] = entity.description
            # Track the surface form as an alias (keep the most "complete" name)
            aliases = existing.setdefault("aliases", [])
            if entity.name not in aliases and entity.name != existing.get("name"):
                aliases.append(entity.name)
            # Prefer the longer, more specific display name
            if len(entity.name) > len(existing.get("name", "")):
                existing["name"] = entity.name
        else:
            self.graph.add_node(key, **{
                "name": entity.name,
                "type": entity.entity_type,
                "description": entity.description,
                "source_ids": list(entity.source_ids),
                "aliases": [],
                "community": -1,
                "modality": getattr(entity, "modality", "text"),
            })
        self._entity_index[key] = self.graph.nodes[key]

    def add_relation(self, relation: Relation):
        """Add or update a relation edge (endpoints normalized to canonical keys)."""
        # Ensure both entity nodes exist first (this registers their canonical keys)
        head = self._canonical_key(relation.head)
        tail = self._canonical_key(relation.tail)
        if not self.graph.has_node(head):
            self.add_entity(Entity(
                name=relation.head, entity_type=relation.head_type,
                source_ids=relation.source_ids,
            ))
            head = self._canonical_key(relation.head)
        if not self.graph.has_node(tail):
            self.add_entity(Entity(
                name=relation.tail, entity_type=relation.tail_type,
                source_ids=relation.source_ids,
            ))
            tail = self._canonical_key(relation.tail)

        if head == tail:
            return  # skip self-loops created by merging

        if self.graph.has_edge(head, tail):
            existing = self.graph.edges[head, tail]
            existing["weight"] = max(existing.get("weight", 0), relation.weight)
            existing["source_ids"] = list(
                set(existing.get("source_ids", []) + list(relation.source_ids))
            )
        else:
            self.graph.add_edge(head, tail, **{
                "relation": relation.relation,
                "weight": relation.weight,
                "description": relation.description,
                "source_ids": list(relation.source_ids),
                "modality": getattr(relation, "modality", "text"),
            })

    def add_entities(self, entities: list[Entity]):
        for e in entities:
            self.add_entity(e)

    def add_relations(self, relations: list[Relation]):
        for r in relations:
            self.add_relation(r)

    # ── Query ──

    def get_entity(self, name: str) -> dict | None:
        """Get entity by name (case-insensitive, variant-aware)."""
        # Try canonical key first, then raw, so 小米集团 resolves to 小米.
        for key in (self._canonical_key(name), name.lower().strip()):
            if self.graph.has_node(key):
                return {"key": key, **self.graph.nodes[key]}
        return None

    def search_entities(self, query: str, limit: int = 10) -> list[dict]:
        """Search entities by name substring match."""
        q = query.lower().strip()
        results = []
        for key, attrs in self._entity_index.items():
            if q in key or q in attrs.get("description", "").lower():
                results.append({"key": key, **attrs})
                if len(results) >= limit:
                    break
        return results

    def renormalize(self) -> dict:
        """v16: rebuild this graph merging fragmented entity variants.

        Fixes graphs built before normalization existed (the 'density 0.0001,
        小米/小米集团/小米集团股份有限公司 are 3 nodes' problem). Returns stats.
        """
        from hashmm.kg.extractor import Entity as _E, Relation as _R
        old_nodes = self.graph.number_of_nodes()
        old_edges = self.graph.number_of_edges()

        # Snapshot current nodes/edges
        nodes = [(k, dict(a)) for k, a in self.graph.nodes(data=True)]
        edges = [(u, v, dict(a)) for u, v, a in self.graph.edges(data=True)]

        # Fresh graph + alias map
        self.graph = nx.DiGraph()
        self._entity_index = {}
        self._alias_map = {}

        # Re-add entities (merging happens via _canonical_key)
        for _, attrs in nodes:
            self.add_entity(_E(
                name=attrs.get("name", ""),
                entity_type=attrs.get("type", ""),
                description=attrs.get("description", ""),
                source_ids=attrs.get("source_ids", []),
            ))
        # Re-add edges using original surface names (re-routed to canonical keys)
        # We rebuild head/tail display names from the snapshot node keys.
        key_to_name = {k: a.get("name", k) for k, a in nodes}
        for u, v, attrs in edges:
            self.add_relation(_R(
                head=key_to_name.get(u, u), tail=key_to_name.get(v, v),
                head_type="", tail_type="",
                relation=attrs.get("relation", "related"),
                weight=attrs.get("weight", 1.0),
                description=attrs.get("description", ""),
                source_ids=attrs.get("source_ids", []),
            ))

        return {
            "entities_before": old_nodes, "entities_after": self.graph.number_of_nodes(),
            "relations_before": old_edges, "relations_after": self.graph.number_of_edges(),
            "merged": old_nodes - self.graph.number_of_nodes(),
        }

    def get_neighbors(self, name: str, depth: int = 1, max_per_hop: int = 30) -> dict:
        """Get entity neighborhood (BFS to given depth).

        ``max_per_hop`` caps how many new nodes each hop adds, so depth=2 on a
        dense graph can't explode into thousands of weakly-related nodes. Highest-
        weight neighbours are kept first.

        Returns:
            {"center": {...}, "neighbors": [...], "edges": [...]}
        """
        key = name.lower().strip()
        if not self.graph.has_node(key):
            ck = self._canonical_key(name)
            if self.graph.has_node(ck):
                key = ck
            else:
                return {"center": None, "neighbors": [], "edges": []}

        center = {"key": key, **self.graph.nodes[key]}
        neighbors = []
        edges = []
        visited = {key}

        # BFS
        frontier = [key]
        for d in range(depth):
            next_frontier = []
            hop_candidates = []  # (node, edge_dict) collected this hop, then capped
            for node in frontier:
                for pred in self.graph.predecessors(node):
                    if pred not in visited:
                        hop_candidates.append((pred, {"from": pred, "to": node, **self.graph.edges[pred, node]}))
                for succ in self.graph.successors(node):
                    if succ not in visited:
                        hop_candidates.append((succ, {"from": node, "to": succ, **self.graph.edges[node, succ]}))
            # Keep the highest-weight candidates first, capped per hop.
            hop_candidates.sort(key=lambda c: self.graph.nodes[c[0]].get("weight", 0), reverse=True)
            for cand, edge_data in hop_candidates[:max_per_hop]:
                if cand in visited:
                    continue
                visited.add(cand)
                next_frontier.append(cand)
                neighbors.append({"key": cand, **self.graph.nodes[cand], "depth": d + 1})
                edges.append(edge_data)
            frontier = next_frontier

        return {"center": center, "neighbors": neighbors, "edges": edges}

    def get_relations_for_entity(self, name: str) -> list[dict]:
        """Get all relations involving an entity."""
        key = name.lower().strip()
        if not self.graph.has_node(key):
            return []

        rels = []
        for pred in self.graph.predecessors(key):
            edge = self.graph.edges[pred, key]
            rels.append({
                "head": self.graph.nodes[pred].get("name", pred),
                "relation": edge.get("relation", ""),
                "tail": self.graph.nodes[key].get("name", key),
                "weight": edge.get("weight", 1.0),
            })
        for succ in self.graph.successors(key):
            edge = self.graph.edges[key, succ]
            rels.append({
                "head": self.graph.nodes[key].get("name", key),
                "relation": edge.get("relation", ""),
                "tail": self.graph.nodes[succ].get("name", succ),
                "weight": edge.get("weight", 1.0),
            })
        return rels

    def get_path(self, source: str, target: str, max_hops: int = 3) -> list[dict] | None:
        """Find shortest path between two entities."""
        src = source.lower().strip()
        tgt = target.lower().strip()
        if not self.graph.has_node(src) or not self.graph.has_node(tgt):
            return None
        try:
            path = nx.shortest_path(self.graph, src, tgt)
            if len(path) - 1 > max_hops:
                return None
            result = []
            for i in range(len(path) - 1):
                edge = self.graph.edges[path[i], path[i + 1]]
                result.append({
                    "from": self.graph.nodes[path[i]].get("name", path[i]),
                    "relation": edge.get("relation", ""),
                    "to": self.graph.nodes[path[i + 1]].get("name", path[i + 1]),
                })
            return result
        except nx.NetworkXNoPath:
            return None

    # ── Remove ──

    def remove_entity(self, name: str) -> bool:
        """Remove an entity and all its relations."""
        key = name.lower().strip()
        if self.graph.has_node(key):
            self.graph.remove_node(key)
            self._entity_index.pop(key, None)
            return True
        return False

    def remove_by_source(self, source_id: str):
        """Remove all entities and relations from a specific source document."""
        nodes_to_remove = []
        for node, attrs in self.graph.nodes(data=True):
            sids = attrs.get("source_ids", [])
            if source_id in sids:
                sids.remove(source_id)
                if not sids:
                    nodes_to_remove.append(node)

        edges_to_remove = []
        for u, v, attrs in self.graph.edges(data=True):
            sids = attrs.get("source_ids", [])
            if source_id in sids:
                sids.remove(source_id)
                if not sids:
                    edges_to_remove.append((u, v))

        for u, v in edges_to_remove:
            self.graph.remove_edge(u, v)
        for node in nodes_to_remove:
            self.graph.remove_node(node)
            self._entity_index.pop(node, None)

        logger.info(f"Removed source {source_id}: {len(nodes_to_remove)} nodes, "
                    f"{len(edges_to_remove)} edges")

    # ── Stats ──

    def detect_communities(self) -> int:
        """Run Louvain community detection on the graph.

        Sets 'community' attribute on each node. Returns number of communities.
        Uses networkx.algorithms.community if available, falls back to
        connected components.
        """
        if self.num_entities < 3:
            return 0

        try:
            import networkx.algorithms.community as nx_comm
            # Louvain (best quality)
            try:
                communities = nx_comm.louvain_communities(
                    self.graph.to_undirected(), resolution=1.0, seed=42
                )
            except Exception:
                # Fallback: greedy modularity
                communities = list(nx_comm.greedy_modularity_communities(
                    self.graph.to_undirected()
                ))

            for i, comm in enumerate(communities):
                for node in comm:
                    self.graph.nodes[node]["community"] = i

            logger.info(f"Detected {len(communities)} communities in {self.num_entities} nodes")
            return len(communities)

        except (ImportError, Exception) as e:
            # Fallback: connected components
            import networkx as nx
            undirected = self.graph.to_undirected()
            for i, comp in enumerate(nx.connected_components(undirected)):
                for node in comp:
                    self.graph.nodes[node]["community"] = i
            n = i + 1 if self.num_entities > 0 else 0
            logger.info(f"Fallback: {n} connected components")
            return n

    def get_communities(self) -> list[dict]:
        """Get community summaries for global mode retrieval.

        Returns: [{"id": 0, "size": N, "entities": [...], "summary": "..."}]
        """
        comm_map: dict[int, list[str]] = {}
        for node, attrs in self.graph.nodes(data=True):
            cid = attrs.get("community", -1)
            if cid >= 0:
                comm_map.setdefault(cid, []).append(node)

        if not comm_map:
            # No communities detected yet
            self.detect_communities()
            for node, attrs in self.graph.nodes(data=True):
                cid = attrs.get("community", -1)
                if cid >= 0:
                    comm_map.setdefault(cid, []).append(node)

        communities = []
        for cid, members in sorted(comm_map.items()):
            # Get entity names and types
            entities = []
            source_ids = set()
            for node in members:
                attrs = self.graph.nodes.get(node, {})
                entities.append({
                    "name": attrs.get("name", node),
                    "type": attrs.get("type", ""),
                })
                for sid in attrs.get("source_ids", []):
                    source_ids.add(sid)

            # Build summary from entity names + types
            type_groups: dict[str, list[str]] = {}
            for e in entities:
                t = e["type"] or "概念"
                type_groups.setdefault(t, []).append(e["name"])

            summary_parts = []
            for t, names in type_groups.items():
                summary_parts.append(f"{t}: {', '.join(names[:5])}")
            summary = "; ".join(summary_parts)

            communities.append({
                "id": cid,
                "size": len(members),
                "entities": entities[:10],
                "source_ids": list(source_ids),
                "summary": summary[:300],
            })

        return communities

    def stats(self) -> dict:
        """Return graph statistics."""
        if self.num_entities == 0:
            return {"entities": 0, "relations": 0, "density": 0,
                    "communities": 0, "type_distribution": {}, "top_entities": []}

        type_counts = Counter(
            attrs.get("type", "unknown")
            for _, attrs in self.graph.nodes(data=True)
        )
        degree_sorted = sorted(
            self.graph.degree(), key=lambda x: x[1], reverse=True
        )
        top_entities = [
            {"name": self.graph.nodes[n].get("name", n), "degree": d,
             "type": self.graph.nodes[n].get("type", "")}
            for n, d in degree_sorted[:15]
        ]
        communities = set(
            attrs.get("community", -1)
            for _, attrs in self.graph.nodes(data=True)
        )
        communities.discard(-1)

        # v16 Phase 15: meaningful connectivity diagnostics.
        # Density is near-zero for ANY real sparse graph, so it's misleading.
        # Average degree + largest connected component tell the real story.
        n = self.num_entities
        e = self.num_relations
        avg_degree = round(2 * e / n, 2) if n else 0
        try:
            import networkx as _nx
            ug = self.graph.to_undirected()
            ug.remove_nodes_from(list(_nx.isolates(ug)))
            n_components = _nx.number_connected_components(ug) if ug.number_of_nodes() else 0
            largest_cc = (max((len(c) for c in _nx.connected_components(ug)), default=0)
                          if ug.number_of_nodes() else 0)
            isolated = n - ug.number_of_nodes()
        except Exception:
            n_components, largest_cc, isolated = 0, 0, 0

        return {
            "entities": n,
            "relations": e,
            "density": round(nx.density(self.graph), 6),
            "avg_degree": avg_degree,
            "connected_components": n_components,
            "largest_component": largest_cc,
            "isolated_entities": isolated,
            "communities": len(communities),
            "type_distribution": dict(type_counts.most_common()),
            "top_entities": top_entities,
        }

    def to_vis_data(
        self,
        max_nodes: int = 200,
        *,
        allowed_source_ids: set[str] | None = None,
    ) -> dict:
        """Export graph data for visualization.

        ``allowed_source_ids=None`` is the administrator/global view.  An
        explicit set is a tenant boundary: nodes and edges without evidence in
        that set are excluded, including legacy graph facts with no provenance.
        Source identifiers themselves are never returned to the client.
        """
        source_graph = self.graph
        if allowed_source_ids is not None:
            allowed = {str(item) for item in allowed_source_ids if str(item)}
            visible_nodes = {
                key
                for key, attrs in source_graph.nodes(data=True)
                if allowed.intersection(
                    str(item) for item in (attrs.get("source_ids") or [])
                )
            }
            source_graph = source_graph.subgraph(visible_nodes)

        # Limit to top nodes by degree
        if source_graph.number_of_nodes() > max_nodes:
            top_nodes = sorted(source_graph.degree(), key=lambda x: -x[1])[:max_nodes]
            subgraph = source_graph.subgraph([n for n, _ in top_nodes])
        else:
            subgraph = source_graph

        type_colors = {
            # Chinese types (from full KG extractor)
            "方法": "#2563eb", "算法": "#7c3aed", "模型": "#059669",
            "数据集": "#d97706", "概念": "#0891b2", "人物": "#dc2626",
            "组织": "#4f46e5", "工具": "#0d9488", "论文": "#6366f1",
            "指标": "#ea580c", "任务": "#8b5cf6", "框架": "#06b6d4",
            # English types (from LightweightKG)
            "ORG": "#4f46e5", "PERSON": "#dc2626", "MONEY": "#059669",
            "DATE": "#d97706", "PRODUCT": "#0891b2", "METRIC": "#ea580c",
            "PERCENT": "#8b5cf6", "CONCEPT": "#71717a",
        }

        nodes = []
        for key, attrs in subgraph.nodes(data=True):
            degree = subgraph.degree(key)
            etype = attrs.get("type", "概念")
            nodes.append({
                "id": key,
                "label": attrs.get("name", key),
                "type": etype,
                "color": type_colors.get(etype, "#71717a"),
                "size": min(8 + degree * 2, 40),
                "description": attrs.get("description", ""),
                "community": attrs.get("community", -1),
            })

        edges = []
        for u, v, attrs in subgraph.edges(data=True):
            if allowed_source_ids is not None and not set(
                str(item) for item in (attrs.get("source_ids") or [])
            ).intersection(allowed_source_ids):
                continue
            edges.append({
                "from": u, "to": v,
                "label": attrs.get("relation", ""),
                "weight": attrs.get("weight", 1.0),
            })

        return {"nodes": nodes, "edges": edges}
