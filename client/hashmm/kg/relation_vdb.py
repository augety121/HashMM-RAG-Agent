"""Relation Vector Database — semantic search over KG relationships.

Enables "global" retrieval: given high-level keywords like "投资趋势",
find relationships like "小米集团 → 研发投入 → 增长15%" even if the
original chunk text doesn't contain the keyword "投资趋势".

Lifecycle same as EntityVDB: build_from_kg → search → save/load.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.relation_vdb")

_DEFAULT_DIR = Path("data/kg/relation_vdb")


@dataclass
class RelationMatch:
    """A matched relation from vector search."""
    head: str
    tail: str
    relation: str
    description: str
    source_ids: list[str]
    score: float
    weight: float


class RelationVDB:
    """FAISS-backed semantic search over KG relationship descriptions."""

    def __init__(self, persist_dir: Path | str = _DEFAULT_DIR):
        self._dir = Path(persist_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = None
        self._entries: list[dict] = []
        self._dim: int = 1024

    @property
    def size(self) -> int:
        return len(self._entries)

    def build_from_kg(self, kg) -> int:
        """Build relation VDB from a KnowledgeGraph instance.

        Args:
            kg: KnowledgeGraph with populated graph

        Returns:
            Number of relations indexed
        """
        t0 = time.time()
        relations = []
        for u, v, data in kg.graph.edges(data=True):
            head_name = kg.graph.nodes[u].get("name", u) if kg.graph.has_node(u) else u
            tail_name = kg.graph.nodes[v].get("name", v) if kg.graph.has_node(v) else v
            rel_type = data.get("relation", "related_to")
            desc = data.get("description", "")
            source_ids = data.get("source_ids", [])
            weight = data.get("weight", 1.0)

            # Build text for embedding
            embed_text = f"{head_name} {rel_type} {tail_name}"
            if desc:
                embed_text += f": {desc}"

            relations.append({
                "head": head_name,
                "tail": tail_name,
                "relation": rel_type,
                "description": desc,
                "source_ids": source_ids if isinstance(source_ids, list) else [str(source_ids)],
                "weight": float(weight) if weight else 1.0,
                "embed_text": embed_text,
            })

        if not relations:
            logger.warning("No relations found in KG")
            return 0

        from hashmm.encoder_pool import EncoderPool
        texts = [r["embed_text"] for r in relations]
        embeddings = EncoderPool.encode_texts(texts, show_progress=len(texts) > 200)

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = (embeddings / norms).astype(np.float32)

        import faiss
        self._dim = embeddings.shape[1]
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(embeddings)
        self._entries = [{k: v for k, v in r.items() if k != "embed_text"} for r in relations]

        elapsed = round((time.time() - t0) * 1000)
        logger.info(f"RelationVDB built: {len(relations)} relations, {elapsed}ms")
        return len(relations)

    def search(self, query: str, top_k: int = 10) -> list[RelationMatch]:
        """Search relations by semantic similarity to query."""
        if not self._index or self._index.ntotal == 0:
            return []

        from hashmm.encoder_pool import EncoderPool
        q_emb = EncoderPool.encode_query(query).astype(np.float32)
        norm = np.linalg.norm(q_emb)
        if norm > 0:
            q_emb = q_emb / norm

        scores, indices = self._index.search(q_emb, min(top_k, self._index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._entries):
                continue
            if score < 0.25:
                continue
            r = self._entries[idx]
            results.append(RelationMatch(
                head=r["head"],
                tail=r["tail"],
                relation=r["relation"],
                description=r.get("description", ""),
                source_ids=r.get("source_ids", []),
                score=float(score),
                weight=r.get("weight", 1.0),
            ))
        return results

    def save(self) -> None:
        if not self._index:
            return
        import faiss
        faiss.write_index(self._index, str(self._dir / "relation.faiss"))
        with open(self._dir / "relation_meta.jsonl", "w", encoding="utf-8") as f:
            for r in self._entries:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logger.info(f"RelationVDB saved: {len(self._entries)} entries")

    def load(self) -> bool:
        faiss_path = self._dir / "relation.faiss"
        meta_path = self._dir / "relation_meta.jsonl"
        if not faiss_path.exists() or not meta_path.exists():
            return False
        try:
            import faiss
            self._index = faiss.read_index(str(faiss_path))
            self._entries = []
            with open(meta_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._entries.append(json.loads(line))
            self._dim = self._index.d
            logger.info(f"RelationVDB loaded: {len(self._entries)} entries")
            return True
        except Exception as e:
            logger.warning(f"RelationVDB load failed: {e}")
            return False
