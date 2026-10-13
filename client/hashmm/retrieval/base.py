"""Retriever protocol shared by hash, vector, and hybrid implementations.

Designed so we can wrap any retriever into LangChain's `BaseRetriever`
later — the method signatures match.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedChunk:
    """A retrieval result, source-agnostic."""

    chunk_id: str
    modality: str                # text/image/table/equation
    text: str                    # content (caption for image/table)
    image_path: str | None       # original image, if any
    score: float                 # higher = better; semantics differ by retriever
    rank: int                    # 0-indexed position in this retriever's result list
    source: str                  # 'vector' | 'hash' | 'hybrid'
    meta: dict = field(default_factory=dict)


class BaseRetriever(ABC):
    """Minimal retriever interface."""

    name: str = "base"

    @abstractmethod
    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        modality_hint: str | None = None,
        query_image_path: str | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve top-k chunks.

        Args:
            query: text query (always required).
            top_k: max results.
            modality_hint: optional preferred modality for retrieved chunks.
            query_image_path: if set, this is the image side of an
                image-to-X query.
        """
        ...
