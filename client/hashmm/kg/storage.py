"""KG Storage — persistence for knowledge graphs.

Supports JSON file-based storage (default) and optional Neo4j.
Handles graph serialization, incremental saves, and snapshots.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
import networkx as nx
from hashmm.kg.graph import KnowledgeGraph
from hashmm.kg.community import CommunityManager
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.storage")

DEFAULT_KG_DIR = Path("data/kg")


class KGStorage:
    """Persist and load knowledge graphs from disk.

    File layout:
        data/kg/
        ├── graph.json          ← NetworkX node_link_data
        ├── communities.json    ← Community summaries
        ├── stats.json          ← Graph statistics cache
        └── snapshots/          ← Versioned backups
            ├── graph_20250524.json
            └── ...
    """

    def __init__(self, kg_dir: Path | str = DEFAULT_KG_DIR):
        self.kg_dir = Path(kg_dir)
        self.kg_dir.mkdir(parents=True, exist_ok=True)
        (self.kg_dir / "snapshots").mkdir(exist_ok=True)

    def save(self, kg: KnowledgeGraph, community_mgr: CommunityManager | None = None):
        """Save the full knowledge graph to disk."""
        t0 = time.time()

        # Save graph
        graph_data = nx.node_link_data(kg.graph, edges="links")
        graph_path = self.kg_dir / "graph.json"
        with open(graph_path, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=None)

        # Save communities
        if community_mgr and community_mgr.communities:
            comm_path = self.kg_dir / "communities.json"
            with open(comm_path, "w", encoding="utf-8") as f:
                json.dump(community_mgr.to_dict(), f, ensure_ascii=False, indent=None)

        # Save stats
        stats = kg.stats()
        stats["saved_at"] = time.time()
        stats_path = self.kg_dir / "stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        elapsed = round((time.time() - t0) * 1000)
        logger.info(f"KG saved: {kg.num_entities} entities, "
                    f"{kg.num_relations} relations ({elapsed}ms)")

    def load(self) -> tuple[KnowledgeGraph, CommunityManager]:
        """Load knowledge graph and communities from disk.

        Returns:
            (KnowledgeGraph, CommunityManager)
        """
        kg = KnowledgeGraph()
        comm_mgr = CommunityManager()

        # Load graph
        graph_path = self.kg_dir / "graph.json"
        if graph_path.exists():
            try:
                with open(graph_path, "r", encoding="utf-8") as f:
                    graph_data = json.load(f)
                kg.graph = nx.node_link_graph(graph_data, directed=True, edges="links")
                # Rebuild entity index
                for key, attrs in kg.graph.nodes(data=True):
                    kg._entity_index[key] = attrs
                logger.info(f"KG loaded: {kg.num_entities} entities, {kg.num_relations} relations")
            except Exception as e:
                logger.error(f"Failed to load KG graph: {e}")

        # Load communities
        comm_path = self.kg_dir / "communities.json"
        if comm_path.exists():
            try:
                with open(comm_path, "r", encoding="utf-8") as f:
                    comm_data = json.load(f)
                comm_mgr.from_dict(comm_data)
                logger.info(f"Communities loaded: {len(comm_mgr.communities)} communities")
            except Exception as e:
                logger.error(f"Failed to load communities: {e}")

        return kg, comm_mgr

    def snapshot(self, kg: KnowledgeGraph):
        """Create a timestamped snapshot of the graph."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        snap_path = self.kg_dir / "snapshots" / f"graph_{ts}.json"
        graph_data = nx.node_link_data(kg.graph, edges="links")
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, ensure_ascii=False)
        logger.info(f"KG snapshot created: {snap_path}")

        # Keep only last 5 snapshots
        snaps = sorted((self.kg_dir / "snapshots").glob("graph_*.json"))
        for old in snaps[:-5]:
            old.unlink()

    def exists(self) -> bool:
        """Check if a saved KG exists."""
        return (self.kg_dir / "graph.json").exists()

    def get_stats(self) -> dict:
        """Load cached stats without loading the full graph."""
        stats_path = self.kg_dir / "stats.json"
        if stats_path.exists():
            try:
                with open(stats_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as _e:
                log_suppressed(logger, _e)
        return {"entities": 0, "relations": 0, "communities": 0}
