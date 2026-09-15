"""Vector Index — FAISS-based semantic search with document management.

Supports:
  - Add documents (batch encode + index)
  - Search by query embedding
  - Remove documents by doc_id
  - Persist to disk / load from disk
  - Hot update (add without restart)
"""
from __future__ import annotations
import json
import os
import time
import numpy as np
from pathlib import Path
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.vector_index")

INDEX_DIR = Path("data/vector_index")


class VectorIndex:
    """FAISS-based vector index with metadata tracking.

    Each vector has associated metadata (doc_id, filename, page, section, text preview).
    Supports incremental add/remove without full rebuild.
    """

    def __init__(self, dim: int = 1024, index_dir: str | Path = INDEX_DIR):
        self.dim = dim
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self._index = None
        self._metadata: list[dict] = []
        self._loaded = False
        
        # Auto-load if index exists on disk
        self._ensure_index()

    @property
    def total_vectors(self) -> int:
        return self._index.ntotal if self._index else 0

    @property
    def total_docs(self) -> int:
        return len(set(m.get("doc_id", "") for m in self._metadata))

    def _ensure_index(self):
        """Create or load the FAISS index."""
        if self._index is not None:
            return
        
        meta_path = self.index_dir / "metadata.jsonl"
        
        # Try loading FAISS index
        try:
            import faiss
            index_path = self.index_dir / "faiss.index"
            if index_path.exists():
                self._index = faiss.read_index(str(index_path))
                self._metadata = []
                if meta_path.exists():
                    with open(meta_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    self._metadata.append(json.loads(line))
                                except json.JSONDecodeError as _e:
                                    log_suppressed(logger, _e)
                logger.info(f"Loaded FAISS index: {self._index.ntotal} vectors, "
                            f"{self.total_docs} docs")
                # v17 Phase 31 (idx version pinning): warn loudly if the index was
                # built with a different embedding than we're serving (silent
                # retrieval corruption otherwise). Advisory only — never blocks.
                try:
                    import os as _os
                    from hashmm import index_version as _iv
                    _meta = _iv.read_index_meta(self.index_dir)
                    _model = _os.environ.get("HASHMM_EMBEDDING_MODEL", "bge-m3")
                    _ok, _reason = _iv.check_index_compatibility(_meta, _model, self.dim)
                    if not _ok:
                        logger.warning(f"[IndexVersion] {_reason}")
                except Exception as _e:
                    log_suppressed(logger, _e)
                self._loaded = True
                return
            # No existing index → create new
            self._index = faiss.IndexFlatIP(self.dim)
            self._metadata = []
            return
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"FAISS load failed: {e}")
        
        # Numpy fallback
        vectors_path = self.index_dir / "vectors.npy"
        self._index = NumpyFallbackIndex(self.dim)
        self._metadata = []
        
        if vectors_path.exists():
            try:
                self._index.vectors = np.load(str(vectors_path))
                if meta_path.exists():
                    with open(meta_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    self._metadata.append(json.loads(line))
                                except json.JSONDecodeError as _e:
                                    log_suppressed(logger, _e)
                logger.info(f"Loaded numpy index: {self._index.ntotal} vectors")
                self._loaded = True
            except Exception as e:
                logger.warning(f"Numpy index load failed: {e}")
                self._index = NumpyFallbackIndex(self.dim)
                self._metadata = []

    def add(self, embeddings: np.ndarray, metadata_list: list[dict]):
        """Add vectors with metadata to the index.

        Args:
            embeddings: np.ndarray of shape (N, dim), float32, L2-normalized
            metadata_list: List of dicts, one per vector. Must include 'doc_id'.
        """
        self._ensure_index()
        assert len(embeddings) == len(metadata_list), \
            f"Embeddings ({len(embeddings)}) and metadata ({len(metadata_list)}) count mismatch"
        assert embeddings.shape[1] == self.dim, \
            f"Embedding dim {embeddings.shape[1]} != index dim {self.dim}"

        embeddings = embeddings.astype(np.float32)
        self._index.add(embeddings)
        self._metadata.extend(metadata_list)

        logger.info(f"Added {len(embeddings)} vectors (total: {self.total_vectors})")

    def search(self, query_embedding: np.ndarray, top_k: int = 10,
               filters: dict | None = None) -> list[dict]:
        """Search for similar vectors.

        Args:
            query_embedding: np.ndarray shape (1, dim) or (dim,)
            top_k: Number of results
            filters: Optional metadata filters {"doc_id": "...", "filename": "..."}

        Returns:
            List of dicts with 'score', 'rank', and all metadata fields.
        """
        self._ensure_index()
        if self.total_vectors == 0:
            return []

        qe = query_embedding.astype(np.float32)
        if qe.ndim == 1:
            qe = qe.reshape(1, -1)

        # Over-fetch if we need to filter
        fetch_k = top_k * 3 if filters else top_k
        fetch_k = min(fetch_k, self.total_vectors)

        scores, indices = self._index.search(qe, fetch_k)

        results = []
        for rank, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx < 0 or idx >= len(self._metadata):
                continue
            meta = dict(self._metadata[idx])
            meta["score"] = float(score)
            meta["rank"] = rank

            # Apply filters
            if filters:
                match = True
                for key, value in filters.items():
                    meta_val = str(meta.get(key, ""))
                    if isinstance(value, str):
                        if "*" in value:
                            # Wildcard match
                            import fnmatch
                            if not fnmatch.fnmatch(meta_val, value):
                                match = False
                        elif meta_val.lower() != value.lower():
                            match = False
                    if not match:
                        break
                if not match:
                    continue

            results.append(meta)
            if len(results) >= top_k:
                break

        return results

    def remove_by_doc(self, doc_id: str) -> int:
        """Remove all vectors for a document. Returns count removed.

        Note: FAISS IndexFlatIP doesn't support removal, so we rebuild.
        """
        self._ensure_index()
        if not self._metadata:
            return 0

        keep_indices = [i for i, m in enumerate(self._metadata) if m.get("doc_id") != doc_id]
        removed = len(self._metadata) - len(keep_indices)

        if removed == 0:
            return 0

        # Rebuild index with remaining vectors
        try:
            import faiss
            old_index = self._index
            new_index = faiss.IndexFlatIP(self.dim)
            if keep_indices:
                # Extract remaining vectors
                remaining = np.array([old_index.reconstruct(i) for i in keep_indices])
                new_index.add(remaining)
            self._index = new_index
            self._metadata = [self._metadata[i] for i in keep_indices]
        except Exception:
            # Numpy fallback
            if hasattr(self._index, 'vectors') and keep_indices:
                self._index.vectors = self._index.vectors[keep_indices]
            self._metadata = [self._metadata[i] for i in keep_indices]

        logger.info(f"Removed {removed} vectors for {doc_id} (remaining: {self.total_vectors})")
        return removed

    def save(self):
        """Persist index and metadata to disk."""
        self._ensure_index()
        # v17 Phase 31: pin the embedding fingerprint so a future model/dim swap
        # without a rebuild is caught at load time.
        try:
            import os as _os
            from hashmm import index_version as _iv
            _iv.write_index_meta(
                self.index_dir, _os.environ.get("HASHMM_EMBEDDING_MODEL", "bge-m3"),
                self.dim, n_vectors=getattr(self._index, "ntotal", 0))
        except Exception as _e:
            log_suppressed(logger, _e)
        try:
            import faiss
            if hasattr(self._index, 'ntotal'):
                faiss.write_index(self._index, str(self.index_dir / "faiss.index"))
        except (ImportError, Exception):
            # Numpy fallback save
            if hasattr(self._index, 'vectors'):
                np.save(self.index_dir / "vectors.npy", self._index.vectors)

        with open(self.index_dir / "metadata.jsonl", "w", encoding="utf-8") as f:
            for m in self._metadata:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

        with open(self.index_dir / "config.json", "w") as f:
            json.dump({"dim": self.dim, "total": self.total_vectors,
                       "docs": self.total_docs, "backend": type(self._index).__name__}, f)

        logger.info(f"Saved vector index: {self.total_vectors} vectors, {self.total_docs} docs")

    def stats(self) -> dict:
        self._ensure_index()
        return {
            "total_vectors": self.total_vectors,
            "total_docs": self.total_docs,
            "dim": self.dim,
            "index_type": type(self._index).__name__,
        }


class NumpyFallbackIndex:
    """Pure numpy fallback when FAISS is not installed."""

    def __init__(self, dim: int):
        self.dim = dim
        self.vectors = np.empty((0, dim), dtype=np.float32)

    @property
    def ntotal(self):
        return len(self.vectors)

    def add(self, vectors: np.ndarray):
        self.vectors = np.vstack([self.vectors, vectors]) if self.ntotal > 0 else vectors.copy()

    def search(self, query: np.ndarray, k: int):
        if self.ntotal == 0:
            return np.array([[]]), np.array([[]])
        # Cosine similarity (vectors are L2-normalized)
        scores = query @ self.vectors.T  # (1, N)
        k = min(k, self.ntotal)
        top_k_indices = np.argpartition(scores[0], -k)[-k:]
        top_k_indices = top_k_indices[np.argsort(-scores[0][top_k_indices])]
        return scores[:, top_k_indices], top_k_indices.reshape(1, -1)

    def reconstruct(self, idx: int) -> np.ndarray:
        return self.vectors[idx]
