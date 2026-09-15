"""Entity Vector Database — semantic search over KG entities.

Builds a FAISS index over entity descriptions so that queries like
"小米的AI投入" can find the entity "小米集团" even without exact name match.

Lifecycle:
    1. build_from_kg(kg) — reads all entities from KnowledgeGraph, encodes
       descriptions with BGE-M3, stores in FAISS IndexFlatIP
    2. search(query, top_k) — encodes query, searches FAISS, returns
       matched entities with scores
    3. save() / load() — persistence to data/kg/entity_vdb/

Thread-safety: read-only after build. Concurrent search is safe.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.entity_vdb")

_DEFAULT_DIR = Path("data/kg/entity_vdb")


@dataclass
class EntityMatch:
    """A matched entity from vector search."""
    name: str
    entity_type: str
    description: str
    source_ids: list[str]
    score: float
    key: str  # lowercased graph key


class EntityVDB:
    """FAISS-backed semantic search over KG entity descriptions."""

    def __init__(self, persist_dir: Path | str = _DEFAULT_DIR):
        self._dir = Path(persist_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = None       # FAISS index
        self._entries: list[dict] = []  # parallel metadata
        self._dim: int = 1024

    @property
    def size(self) -> int:
        return len(self._entries)

    def build_from_kg(self, kg) -> int:
        """Build entity VDB from a KnowledgeGraph instance.

        Args:
            kg: KnowledgeGraph with populated graph

        Returns:
            Number of entities indexed
        """
        t0 = time.time()
        entities = []
        for key in kg.graph.nodes:
            attrs = kg.graph.nodes[key]
            name = attrs.get("name", key)
            etype = attrs.get("type", "OTHER")
            desc = attrs.get("description", "")
            source_ids = attrs.get("source_ids", [])
            # Build text for embedding: name + type + description
            embed_text = f"{name} ({etype})"
            if desc:
                embed_text += f": {desc}"
            entities.append({
                "key": key,
                "name": name,
                "type": etype,
                "description": desc,
                "source_ids": source_ids,
                "embed_text": embed_text,
            })

        if not entities:
            logger.warning("No entities found in KG")
            return 0

        # Encode all entity descriptions
        from hashmm.encoder_pool import EncoderPool
        texts = [e["embed_text"] for e in entities]
        embeddings = EncoderPool.encode_texts(texts, show_progress=len(texts) > 200)

        # Normalize for cosine similarity (IndexFlatIP)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = (embeddings / norms).astype(np.float32)

        # Build FAISS index
        import faiss
        self._dim = embeddings.shape[1]
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(embeddings)
        self._entries = [{k: v for k, v in e.items() if k != "embed_text"} for e in entities]

        elapsed = round((time.time() - t0) * 1000)
        logger.info(f"EntityVDB built: {len(entities)} entities, {elapsed}ms")
        return len(entities)

    def search(self, query: str, top_k: int = 10) -> list[EntityMatch]:
        """Search entities by semantic similarity to query.

        Args:
            query: Search query text
            top_k: Maximum results to return

        Returns:
            List of EntityMatch sorted by score descending
        """
        if not self._index or self._index.ntotal == 0:
            return []

        from hashmm.encoder_pool import EncoderPool
        q_emb = EncoderPool.encode_query(query).astype(np.float32)
        # Normalize
        norm = np.linalg.norm(q_emb)
        if norm > 0:
            q_emb = q_emb / norm

        scores, indices = self._index.search(q_emb, min(top_k, self._index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._entries):
                continue
            if score < 0.3:  # minimum relevance threshold
                continue
            e = self._entries[idx]
            results.append(EntityMatch(
                name=e["name"],
                entity_type=e["type"],
                description=e["description"],
                source_ids=e.get("source_ids", []),
                score=float(score),
                key=e["key"],
            ))
        return results

    def save(self) -> None:
        """Persist index and metadata to disk."""
        if not self._index:
            return
        import faiss
        faiss.write_index(self._index, str(self._dir / "entity.faiss"))
        with open(self._dir / "entity_meta.jsonl", "w", encoding="utf-8") as f:
            for e in self._entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        logger.info(f"EntityVDB saved: {len(self._entries)} entries")

    def load(self) -> bool:
        """Load index and metadata from disk."""
        faiss_path = self._dir / "entity.faiss"
        meta_path = self._dir / "entity_meta.jsonl"
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
            logger.info(f"EntityVDB loaded: {len(self._entries)} entries")
            return True
        except Exception as e:
            logger.warning(f"EntityVDB load failed: {e}")
            return False
