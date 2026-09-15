"""Wrap RAG-Anything's parser so we can drive ingestion from one place.

We deliberately do NOT modify RAG-Anything source. We import it lazily,
call its public APIs, and capture what we need.

Two scenarios are supported:

A) `parse_only`: just run RAG-Anything's parser and grab the `content_list`
   without inserting into LightRAG. This is what we need for *training* the
   hash net (we want raw chunks, not indexed chunks). Faster — no LLM calls.

B) `parse_and_index`: full RAG-Anything `process_document_complete`, so
   LightRAG also indexes the chunks. This is what the production pipeline
   does — it gives us the *dense vector* baseline for benchmarking against
   our hash retriever.

The adapter is async (matches RAG-Anything's API) but provides a sync
convenience wrapper too.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hashmm.config import HashMMConfig
from hashmm.utils import get_logger

logger = get_logger("hashmm.ingestion.adapter")


@dataclass
class ParseResult:
    """What we get back from a parse call."""

    doc_id: str
    content_list: list[dict]
    file_path: str


class RAGAnythingAdapter:
    """Thin async wrapper around RAG-Anything.

    Construction is cheap (no models loaded). The first parse call lazily
    imports RAG-Anything and constructs a `RAGAnything` instance with
    sensible defaults. Pass `llm_model_func` / `vision_model_func` / `embedding_func`
    if you want full insertion; leave them None to do parse-only.
    """

    def __init__(
        self,
        cfg: HashMMConfig,
        llm_model_func: Callable | None = None,
        vision_model_func: Callable | None = None,
        embedding_func: Any = None,
    ):
        self.cfg = cfg
        self._llm = llm_model_func
        self._vlm = vision_model_func
        self._emb = embedding_func
        self._rag = None  # lazy

    # ── Lazy init ─────────────────────────────────────────────────────

    def _ensure_rag(self):
        if self._rag is not None:
            return self._rag
        try:
            from raganything import RAGAnything, RAGAnythingConfig
        except ImportError as e:
            raise RuntimeError(
                "RAG-Anything not installed. Run: pip install 'hashmm-rag[ingest]'"
            ) from e

        rag_cfg = RAGAnythingConfig(
            working_dir=self.cfg.working_dir,
            parser_output_dir=self.cfg.parser_output_dir,
        )
        kwargs = {"config": rag_cfg}
        if self._llm is not None:
            kwargs["llm_model_func"] = self._llm
        if self._vlm is not None:
            kwargs["vision_model_func"] = self._vlm
        if self._emb is not None:
            kwargs["embedding_func"] = self._emb
        self._rag = RAGAnything(**kwargs)
        logger.info("RAGAnything initialised (working_dir=%s)", self.cfg.working_dir)
        return self._rag

    # ── Parse-only (no LightRAG insertion) ────────────────────────────

    async def aparse_only(self, file_path: str | Path) -> ParseResult:
        """Run RAG-Anything's parser; do not call insertion. Returns content_list."""
        rag = self._ensure_rag()
        # Only pass kwargs that RAG-Anything's MineruParser actually accepts.
        # In particular, do NOT pass `parser=` — MineruParser is the parser
        # and rejects that kwarg. backend/device/lang depend on
        # RAGAnythingConfig/parser version; pass via config.
        content_list, doc_id = await rag.parse_document(
            file_path=str(file_path),
            output_dir=self.cfg.parser_output_dir,
            parse_method="auto",
        )
        logger.info(
            "parsed %s → doc_id=%s, %d items",
            file_path, doc_id[:16], len(content_list),
        )
        return ParseResult(
            doc_id=doc_id, content_list=content_list, file_path=str(file_path)
        )

    def parse_only(self, file_path: str | Path) -> ParseResult:
        """Sync convenience."""
        return asyncio.run(self.aparse_only(file_path))

    # ── Full parse + LightRAG insertion ───────────────────────────────

    async def aparse_and_index(self, file_path: str | Path) -> ParseResult:
        """Full pipeline: parse + multimodal processing + LightRAG insertion."""
        if self._llm is None or self._emb is None:
            raise RuntimeError(
                "parse_and_index needs llm_model_func and embedding_func "
                "(pass them to the constructor)"
            )
        rag = self._ensure_rag()
        # process_document_complete returns None; the parsed list is cached internally.
        # We re-call parse_document afterwards — its cache means this is free.
        await rag.process_document_complete(
            file_path=str(file_path),
            output_dir=self.cfg.parser_output_dir,
            parse_method="auto",
        )
        # Now fetch content_list from RAG-Anything's parse cache.
        content_list, doc_id = await rag.parse_document(
            file_path=str(file_path),
            output_dir=self.cfg.parser_output_dir,
            parse_method="auto",
        )
        return ParseResult(
            doc_id=doc_id, content_list=content_list, file_path=str(file_path)
        )

    def parse_and_index(self, file_path: str | Path) -> ParseResult:
        return asyncio.run(self.aparse_and_index(file_path))

    # ── Persistence of parse results ──────────────────────────────────

    @staticmethod
    def save_parse_result(result: ParseResult, out_dir: str | Path) -> Path:
        """Dump a single document's parse output to a JSON file."""
        out = Path(out_dir) / f"{result.doc_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_id": result.doc_id,
                    "file_path": result.file_path,
                    "content_list": result.content_list,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("saved parse result → %s", out)
        return out

    @staticmethod
    def load_parse_result(path: str | Path) -> ParseResult:
        with Path(path).open("r", encoding="utf-8") as f:
            d = json.load(f)
        return ParseResult(
            doc_id=d["doc_id"],
            file_path=d["file_path"],
            content_list=d["content_list"],
        )
