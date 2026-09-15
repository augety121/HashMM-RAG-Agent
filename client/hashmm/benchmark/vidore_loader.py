"""ViDoRe v2 dataset loader (HuggingFace → BEIR-style tuples).

A ViDoRe v2 dataset has three configs (loaded as separate splits):
  - corpus   : (corpus-id, image, text?) per page
  - queries  : (query-id, query) per query
  - qrels    : (query-id, corpus-id, score) judgements

We normalise everything to:
    corpus  : { doc_id: {"text": str, "image_path": Path | None} }
    queries : { query_id: str }
    qrels   : { query_id: { doc_id: int_relevance } }

Schema variations are tolerated — we look for common field names.
Image data is saved to disk under a cache dir so we can pass paths around.
OCR fallback uses pytesseract iff `corpus_text_field` is absent.

Caches everything under ./benchmark_cache/{dataset_short_name}/ so re-runs
are sub-second.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.vidore_loader")


# Field-name candidates per HF dataset versions
_DOC_ID_CANDIDATES = ("corpus-id", "doc-id", "id", "image_filename")
_DOC_TEXT_CANDIDATES = ("text", "ocr_text", "page_text", "content")
_DOC_IMG_CANDIDATES = ("image", "page_image", "img")

_QUERY_ID_CANDIDATES = ("query-id", "qid", "id")
_QUERY_TEXT_CANDIDATES = ("query", "question", "text")

_QREL_QID_CANDIDATES = ("query-id", "qid")
_QREL_DOCID_CANDIDATES = ("corpus-id", "doc-id", "docid", "image_filename")
_QREL_SCORE_CANDIDATES = ("score", "relevance", "rel")


def _first_key(row: dict, candidates: tuple[str, ...]) -> str | None:
    for k in candidates:
        if k in row:
            return k
    return None


def _detect_keys(rows: Iterable[dict], candidates: tuple[str, ...]) -> str:
    """Pick the first candidate key that exists in the first row.
    Raises if none found."""
    for row in rows:
        for k in candidates:
            if k in row:
                return k
        break
    raise KeyError(f"none of {candidates} found in dataset schema")


class ViDoReDataset:
    """Loaded + cached ViDoRe v2 dataset.

    Attributes:
        name           : short dataset name (e.g. 'biomedical_lectures_eng_v2')
        cache_dir      : Path to where images are dumped + JSON metadata
        corpus         : {doc_id: {"text": str, "image_path": Path | None}}
        queries        : {query_id: query_text}
        qrels          : {query_id: {doc_id: int_score}}
    """

    def __init__(self, name: str, cache_dir: str | Path = "./benchmark_cache",
                 hf_namespace: str = "vidore"):
        self.name = name
        self.hf_id = f"{hf_namespace}/{name}"
        self.cache_dir = Path(cache_dir) / name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir = self.cache_dir / "images"
        self.meta_path = self.cache_dir / "loaded.json"

        # Lazy fields
        self.corpus: dict[str, dict] = {}
        self.queries: dict[str, str] = {}
        self.qrels: dict[str, dict[str, int]] = {}

    # ── public ────────────────────────────────────────────────────────

    def load(self, force_reload: bool = False,
             ocr: str | None = None,
             ocr_workers: int = 4,
             ocr_lang: str = "eng",
             force_reocr: bool = False) -> "ViDoReDataset":
        """Populate corpus/queries/qrels. Cached after first run.

        Args:
            force_reload : redownload from HF, ignoring cache
            ocr          : if "tesseract" and corpus has no text but has
                           images, run OCR to populate text. None = no OCR.
            ocr_workers  : parallel OCR threads (4 is sweet spot for cpu)
            ocr_lang     : tesseract language code (e.g. 'eng', 'fra')

        Returns self for chaining.
        """
        if self.meta_path.exists() and not force_reload:
            logger.info("loading cached %s from %s", self.name, self.cache_dir)
            self._load_from_cache()
        else:
            logger.info("downloading %s from HuggingFace", self.hf_id)
            from datasets import load_dataset

            # Each ViDoRe v2 dataset publishes 3 configurations.
            # We try the canonical names; if a dataset uses different splits,
            # the schema detection still adapts via key candidates.
            try:
                corpus_ds = load_dataset(self.hf_id, "corpus", split="test")
            except Exception:
                corpus_ds = load_dataset(self.hf_id, split="test")

            try:
                queries_ds = load_dataset(self.hf_id, "queries", split="test")
            except Exception:
                queries_ds = load_dataset(self.hf_id, "test", split="test")

            try:
                qrels_ds = load_dataset(self.hf_id, "qrels", split="test")
            except Exception:
                qrels_ds = load_dataset(self.hf_id, "qrels", split="train")

            self._materialise_corpus(corpus_ds)
            self._materialise_queries(queries_ds)
            self._materialise_qrels(qrels_ds)
            self._dump_cache()

        # OCR top-up if we have images but no text
        if ocr:
            self._maybe_ocr(engine=ocr, workers=ocr_workers, lang=ocr_lang,
                            force=force_reocr)

        return self

    def _maybe_ocr(self, engine: str, workers: int, lang: str,
                   force: bool = False) -> None:
        """If corpus has images but no text, run OCR to populate text."""
        n_text = sum(1 for d in self.corpus.values() if d.get("text"))
        n_image = sum(1 for d in self.corpus.values() if d.get("image_path"))
        if n_text >= len(self.corpus) // 2 and not force:
            logger.info("OCR: %d/%d docs already have text — skipping",
                        n_text, len(self.corpus))
            return
        if n_image == 0:
            logger.warning("OCR requested but corpus has no images — skipping")
            return
        if engine not in ("tesseract", "paddleocr"):
            raise ValueError(f"OCR engine {engine!r} not supported "
                             f"(use 'tesseract' or 'paddleocr')")

        from hashmm.benchmark.ocr import OCRCache

        ocr_cache_dir = self.cache_dir / f"ocr_{engine}_{lang}"
        cache = OCRCache(ocr_cache_dir, lang=lang, engine=engine)
        logger.info("running OCR (%s/%s) on %d images (workers=%d)",
                    engine, lang, n_image, workers)

        paths = [d["image_path"] for d in self.corpus.values()
                 if d.get("image_path")]
        results = cache.ocr_batch(paths, workers=workers)

        n_updated = 0
        n_empty = 0
        for doc_id, entry in self.corpus.items():
            ip = entry.get("image_path")
            if not ip:
                continue
            text = results.get(str(ip), "")
            entry["text"] = text
            if text:
                n_updated += 1
            else:
                n_empty += 1

        # Persist updated cache
        self._dump_cache()
        logger.info("OCR done: %d docs got text, %d still empty (cache: %s)",
                    n_updated, n_empty, cache.stats())

    def stats(self) -> dict:
        return {
            "name": self.name,
            "n_docs": len(self.corpus),
            "n_queries": len(self.queries),
            "n_qrels": sum(len(v) for v in self.qrels.values()),
            "n_docs_with_text": sum(1 for d in self.corpus.values()
                                    if d.get("text")),
            "n_docs_with_image": sum(1 for d in self.corpus.values()
                                     if d.get("image_path")),
            "cache_dir": str(self.cache_dir),
        }

    # ── internal: materialisation from HF datasets ────────────────────

    def _materialise_corpus(self, ds) -> None:
        """Dump per-row image to disk, collect text."""
        self.images_dir.mkdir(parents=True, exist_ok=True)

        sample = next(iter(ds))
        id_key = _detect_keys([sample], _DOC_ID_CANDIDATES)
        text_key = _first_key(sample, _DOC_TEXT_CANDIDATES)
        img_key = _first_key(sample, _DOC_IMG_CANDIDATES)

        logger.info("corpus schema → id=%s text=%s image=%s",
                    id_key, text_key, img_key)
        n_with_text = 0
        n_with_image = 0
        for row in ds:
            doc_id = str(row[id_key])
            text = row.get(text_key) if text_key else None
            entry = {"text": text or "", "image_path": None}
            if text:
                n_with_text += 1

            if img_key and row.get(img_key) is not None:
                pil = row[img_key]
                # HF returns a PIL.Image. Convert to RGB and write to disk.
                img_path = self.images_dir / f"{doc_id}.png"
                if not img_path.exists():
                    try:
                        pil.convert("RGB").save(img_path, format="PNG",
                                                 optimize=True)
                    except Exception as e:
                        logger.warning("failed to save image for %s: %s",
                                       doc_id, e)
                        img_path = None
                if img_path:
                    entry["image_path"] = str(img_path)
                    n_with_image += 1

            self.corpus[doc_id] = entry

        logger.info("corpus: %d docs (text=%d, image=%d)",
                    len(self.corpus), n_with_text, n_with_image)

    def _materialise_queries(self, ds) -> None:
        sample = next(iter(ds))
        id_key = _detect_keys([sample], _QUERY_ID_CANDIDATES)
        text_key = _detect_keys([sample], _QUERY_TEXT_CANDIDATES)
        for row in ds:
            qid = str(row[id_key])
            self.queries[qid] = row[text_key]
        logger.info("queries: %d", len(self.queries))

    def _materialise_qrels(self, ds) -> None:
        sample = next(iter(ds))
        qid_key = _detect_keys([sample], _QREL_QID_CANDIDATES)
        did_key = _detect_keys([sample], _QREL_DOCID_CANDIDATES)
        score_key = _detect_keys([sample], _QREL_SCORE_CANDIDATES)
        n = 0
        for row in ds:
            qid = str(row[qid_key])
            did = str(row[did_key])
            score = int(row[score_key])
            if score <= 0:
                continue
            self.qrels.setdefault(qid, {})[did] = score
            n += 1
        logger.info("qrels: %d positive judgements over %d queries",
                    n, len(self.qrels))

    # ── caching ───────────────────────────────────────────────────────

    def _dump_cache(self) -> None:
        payload = {
            "name": self.name, "hf_id": self.hf_id,
            "corpus": self.corpus, "queries": self.queries,
            "qrels": self.qrels,
        }
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.info("cached %d docs / %d queries to %s",
                    len(self.corpus), len(self.queries), self.meta_path)

    def _load_from_cache(self) -> None:
        with open(self.meta_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.corpus = payload["corpus"]
        # qrels values are dicts keyed by doc_id with int scores; json loads
        # everything as strings so we convert.
        self.qrels = {
            qid: {did: int(s) for did, s in docs.items()}
            for qid, docs in payload["qrels"].items()
        }
        self.queries = payload["queries"]
