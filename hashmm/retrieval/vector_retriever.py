"""Vector retriever — wraps RAG-Anything / LightRAG dense retrieval.

This is the strong baseline our hash retriever competes against in
benchmarks. Implementation just calls `RAGAnything.aquery(..., mode='naive',
only_need_context=True)` under the hood, which returns the raw retrieved
chunks as text.

We parse the returned context to recover individual chunks so we can fuse
with hash results in the hybrid router. Future improvement: hit
LightRAG's `chunks_vdb.query()` directly for typed results instead of
parsing a string blob.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hashmm.retrieval.base import BaseRetriever, RetrievedChunk
from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval.vector")


class VectorRetriever(BaseRetriever):
    """Dense-vector retrieval via RAG-Anything / LightRAG."""

    name = "vector"

    def __init__(self, rag_anything):
        """`rag_anything` is a configured RAGAnything instance from
        hashmm.ingestion.adapter.RAGAnythingAdapter (after parse_and_index).
        """
        self._rag = rag_anything

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        modality_hint: str | None = None,
        query_image_path: str | None = None,
    ) -> list[RetrievedChunk]:
        try:
            from lightrag import QueryParam
        except ImportError as e:
            raise RuntimeError("LightRAG not installed; install hashmm-rag[ingest]") from e

        if query_image_path:
            # Multimodal path: include image as a content item
            return asyncio.run(
                self._aretrieve_multimodal(query, query_image_path, top_k)
            )

        return asyncio.run(self._aretrieve_text(query, top_k))

    async def _aretrieve_text(self, query: str, top_k: int) -> list[RetrievedChunk]:
        from lightrag import QueryParam

        param = QueryParam(mode="mix", only_need_context=True, top_k=top_k)
        context = await self._rag.aquery(query, mode="mix", only_need_context=True)
        chunks = _parse_lightrag_context(context, top_k=top_k)
        return chunks

    async def _aretrieve_multimodal(
        self, query: str, image_path: str, top_k: int
    ) -> list[RetrievedChunk]:
        context = await self._rag.aquery_with_multimodal(
            query,
            multimodal_content=[{"type": "image", "img_path": image_path}],
            mode="mix",
            only_need_context=True,
        )
        return _parse_lightrag_context(context, top_k=top_k)


def _parse_lightrag_context(context: Any, top_k: int) -> list[RetrievedChunk]:
    """Best-effort parser for whatever LightRAG returns as context.

    LightRAG returns either a string blob, a dict with sections, or (in
    newer versions) a structured list. We handle all three loosely.
    """
    if context is None:
        return []

    # Structured case: list of dicts
    if isinstance(context, list):
        out = []
        for i, item in enumerate(context[:top_k]):
            if isinstance(item, dict):
                out.append(
                    RetrievedChunk(
                        chunk_id=item.get("chunk_id") or item.get("id") or f"vec-{i}",
                        modality=item.get("modality", "text"),
                        text=item.get("content") or item.get("text", ""),
                        image_path=item.get("image_path"),
                        score=float(item.get("score", -i)),
                        rank=i,
                        source="vector",
                        meta={k: v for k, v in item.items() if k not in {"score"}},
                    )
                )
        return out

    # Dict case: look for a "chunks" key
    if isinstance(context, dict):
        chunks = context.get("chunks") or context.get("results") or []
        return _parse_lightrag_context(chunks, top_k=top_k)

    # Fallback: string blob — split on common delimiters
    if isinstance(context, str):
        # LightRAG often emits "## Sources" + numbered chunks.
        import re

        # Split on numbered headers like "1.", "2.", or "Chunk 1:"
        parts = re.split(r"\n(?:\d+\.|Chunk \d+:|---)\s+", context)
        parts = [p.strip() for p in parts if p.strip()]
        return [
            RetrievedChunk(
                chunk_id=f"vec-blob-{i}",
                modality="text",
                text=p,
                image_path=None,
                score=-float(i),
                rank=i,
                source="vector",
                meta={"blob_parse": True},
            )
            for i, p in enumerate(parts[:top_k])
        ]

    logger.warning("unknown context format from LightRAG: %s", type(context).__name__)
    return []
