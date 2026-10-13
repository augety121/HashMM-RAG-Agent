"""Extract typed chunks and cross-modal pairs from RAG-Anything's content_list.

RAG-Anything's `parse_document()` returns a `List[Dict]` in MinerU format:

    [
      {"type": "text",     "text": "...", "page_idx": 0, ...},
      {"type": "image",    "img_path": "/abs/path/img.jpg",
                           "image_caption": ["..."], "image_footnote": ["..."],
                           "page_idx": 1, ...},
      {"type": "table",    "img_path": "/abs/.../table.jpg",
                           "table_caption": ["..."], "table_body": "<html>...</html>",
                           "page_idx": 2, ...},
      {"type": "equation", "text": "E = mc^2", "text_format": "latex", ...},
    ]

We do two things with this:

1. Convert each item into a uniform `Chunk` object with a stable ID and the
   text/image content the hash encoders need.
2. Emit `CrossModalPair`s — (text, image) tuples that the hash net trains on
   as semantic positives. The trick is *neighbourhood-based pairing*: an image
   on page N is paired with its caption *and* with surrounding text chunks on
   page N (configurable window). This is how we get supervision without
   labelled data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator

from hashmm.utils import get_logger

logger = get_logger("hashmm.ingestion")


# ───────────────────────────────────────────────────────────────────────
# Data classes
# ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Chunk:
    """A unit of content that can be indexed and retrieved.

    `chunk_id` is a deterministic hash of the content so the same content
    always produces the same ID across runs (matches LightRAG's convention).
    """

    chunk_id: str
    modality: str              # 'text' | 'image' | 'table' | 'equation'
    text: str                  # for text/equation: the content; for image/table: caption-derived description
    image_path: str | None     # for image/table: absolute path on disk; else None
    doc_id: str                # source document
    page_idx: int              # page number in the source doc (best-effort, may be 0)
    meta: dict = field(default_factory=dict)  # type-specific extras

    @staticmethod
    def make_id(content: str, prefix: str = "chunk") -> str:
        h = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"{prefix}-{h}"


@dataclass(frozen=True)
class CrossModalPair:
    """A (text, image) positive pair for cross-modal hash training."""

    text: str                  # the text side (caption or surrounding paragraph)
    image_path: str            # absolute path
    doc_id: str
    page_idx: int
    source: str                # 'caption' | 'neighbour_text' | 'table_caption'


# ───────────────────────────────────────────────────────────────────────
# Extractor
# ───────────────────────────────────────────────────────────────────────


class ChunkExtractor:
    """Turn RAG-Anything content_list into chunks and cross-modal pairs.

    Stateless: every call is independent. Configurable neighbourhood window
    for synthesising cross-modal pairs from un-captioned figures.
    """

    def __init__(
        self,
        neighbour_window: int = 1,
        min_caption_len: int = 4,
        min_neighbour_text_len: int = 20,
    ):
        """
        Args:
            neighbour_window: how many text items before/after to pair with each
                image when no caption exists.
            min_caption_len: discard captions shorter than this (chars).
            min_neighbour_text_len: skip very short neighbour paragraphs.
        """
        self.neighbour_window = neighbour_window
        self.min_caption_len = min_caption_len
        self.min_neighbour_text_len = min_neighbour_text_len

    # ── Public API ────────────────────────────────────────────────────

    def extract(
        self,
        content_list: list[dict],
        doc_id: str,
    ) -> tuple[list[Chunk], list[CrossModalPair]]:
        """Main entry point. Returns (chunks, cross_modal_pairs)."""
        chunks: list[Chunk] = []
        pairs: list[CrossModalPair] = []

        # First pass: produce chunks, remember each item's text content for
        # neighbourhood lookup.
        item_text_at: dict[int, str] = {}
        for i, item in enumerate(content_list):
            chunk = self._item_to_chunk(item, doc_id)
            if chunk is None:
                continue
            chunks.append(chunk)
            if chunk.modality == "text" and chunk.text:
                item_text_at[i] = chunk.text

        # Second pass: produce cross-modal pairs from image/table/chart items.
        for i, item in enumerate(content_list):
            t = item.get("type")
            if t == "image":
                pairs.extend(self._pairs_from_image(item, i, content_list, doc_id))
            elif t == "table":
                pairs.extend(self._pairs_from_table(item, i, content_list, doc_id))
            elif t == "chart":
                # MinerU 3.x emits a chart type with img_path + chart_caption.
                # Treat structurally the same as an image for our purposes.
                pairs.extend(self._pairs_from_chart(item, i, content_list, doc_id))

        logger.info(
            f"doc={doc_id[:12]}… → {len(chunks)} chunks "
            f"({_count_by(chunks, 'modality')}), {len(pairs)} cross-modal pairs"
        )
        return chunks, pairs

    # ── Per-item conversion ───────────────────────────────────────────

    def _item_to_chunk(self, item: dict, doc_id: str) -> Chunk | None:
        t = item.get("type")
        page_idx = int(item.get("page_idx", 0) or 0)

        if t == "text":
            text = (item.get("text") or "").strip()
            if not text:
                return None
            return Chunk(
                chunk_id=Chunk.make_id(text, "text"),
                modality="text",
                text=text,
                image_path=None,
                doc_id=doc_id,
                page_idx=page_idx,
                meta={"text_level": item.get("text_level")},
            )

        if t == "image":
            img_path = item.get("img_path") or ""
            if not img_path:
                return None
            captions = _flatten_list(item.get("image_caption") or item.get("img_caption"))
            description = " | ".join(captions) if captions else ""
            return Chunk(
                chunk_id=Chunk.make_id(f"image::{img_path}", "img"),
                modality="image",
                text=description,
                image_path=img_path,
                doc_id=doc_id,
                page_idx=page_idx,
                meta={
                    "captions": captions,
                    "footnotes": _flatten_list(
                        item.get("image_footnote") or item.get("img_footnote")
                    ),
                },
            )

        if t == "table":
            img_path = item.get("img_path") or ""
            captions = _flatten_list(item.get("table_caption"))
            body = item.get("table_body") or item.get("table_body_html") or ""
            description = " | ".join(captions) if captions else _strip_html(body)[:300]
            return Chunk(
                chunk_id=Chunk.make_id(
                    f"table::{img_path or body[:64]}", "tbl"
                ),
                modality="table",
                text=description,
                image_path=img_path or None,
                doc_id=doc_id,
                page_idx=page_idx,
                meta={
                    "captions": captions,
                    "table_body": body,
                },
            )

        if t == "equation":
            text = (item.get("text") or "").strip()
            if not text:
                return None
            fmt = item.get("text_format") or "latex"
            return Chunk(
                chunk_id=Chunk.make_id(f"eq::{text}", "eq"),
                modality="equation",
                text=text,
                image_path=None,
                doc_id=doc_id,
                page_idx=page_idx,
                meta={"text_format": fmt},
            )

        if t == "chart":
            # MinerU 3.x produces 'chart' for bar/pie/line graphics with
            # caption + rendered image + (sometimes) extracted content text.
            img_path = item.get("img_path") or ""
            if not img_path:
                return None
            captions = _flatten_list(item.get("chart_caption"))
            content_md = (item.get("content") or "").strip()  # chart-to-text by MinerU
            # Prefer caption for description; fall back to MinerU's content
            description = " | ".join(captions) if captions else content_md[:300]
            return Chunk(
                chunk_id=Chunk.make_id(f"chart::{img_path}", "chart"),
                modality="chart",
                text=description,
                image_path=img_path,
                doc_id=doc_id,
                page_idx=page_idx,
                meta={
                    "captions": captions,
                    "footnotes": _flatten_list(item.get("chart_footnote")),
                    "sub_type": item.get("sub_type"),  # bar/pie/line/etc.
                    "content_md": content_md,
                },
            )

        return None

    # ── Pair extraction ───────────────────────────────────────────────

    def _pairs_from_image(
        self,
        item: dict,
        idx: int,
        content_list: list[dict],
        doc_id: str,
    ) -> list[CrossModalPair]:
        img_path = item.get("img_path") or ""
        if not img_path:
            return []
        page_idx = int(item.get("page_idx", 0) or 0)

        pairs: list[CrossModalPair] = []

        # 1) Captions are gold positives.
        for cap in _flatten_list(item.get("image_caption") or item.get("img_caption")):
            if len(cap) >= self.min_caption_len:
                pairs.append(
                    CrossModalPair(
                        text=cap,
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="caption",
                    )
                )

        # 2) Neighbourhood text — only when we have no caption (avoid noise
        #    flooding the gold pairs).
        if not pairs:
            for nbr in self._neighbour_texts(content_list, idx):
                pairs.append(
                    CrossModalPair(
                        text=nbr,
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="neighbour_text",
                    )
                )

        return pairs

    def _pairs_from_table(
        self,
        item: dict,
        idx: int,
        content_list: list[dict],
        doc_id: str,
    ) -> list[CrossModalPair]:
        img_path = item.get("img_path") or ""
        if not img_path:
            return []  # we need an image rendering of the table to use it cross-modally
        page_idx = int(item.get("page_idx", 0) or 0)

        pairs: list[CrossModalPair] = []
        for cap in _flatten_list(item.get("table_caption")):
            if len(cap) >= self.min_caption_len:
                pairs.append(
                    CrossModalPair(
                        text=cap,
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="table_caption",
                    )
                )
        return pairs

    def _pairs_from_chart(
        self,
        item: dict,
        idx: int,
        content_list: list[dict],
        doc_id: str,
    ) -> list[CrossModalPair]:
        """Same shape as _pairs_from_image. MinerU 3.x charts have chart_caption."""
        img_path = item.get("img_path") or ""
        if not img_path:
            return []
        page_idx = int(item.get("page_idx", 0) or 0)

        pairs: list[CrossModalPair] = []
        # 1) Caption is the gold positive.
        for cap in _flatten_list(item.get("chart_caption")):
            if len(cap) >= self.min_caption_len:
                pairs.append(
                    CrossModalPair(
                        text=cap,
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="chart_caption",
                    )
                )

        # 2) Charts often have a "content" field with MinerU's chart-to-text
        #    extraction. If we have no caption, that's a usable positive.
        if not pairs:
            content_md = (item.get("content") or "").strip()
            if len(content_md) >= self.min_caption_len:
                # truncate long content
                pairs.append(
                    CrossModalPair(
                        text=content_md[:500],
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="chart_content",
                    )
                )

        # 3) Fall back to neighbour text if both above are empty.
        if not pairs:
            for nbr in self._neighbour_texts(content_list, idx):
                pairs.append(
                    CrossModalPair(
                        text=nbr,
                        image_path=img_path,
                        doc_id=doc_id,
                        page_idx=page_idx,
                        source="neighbour_text",
                    )
                )
        return pairs

    def _neighbour_texts(self, content_list: list[dict], idx: int) -> Iterator[str]:
        """Yield text items within `neighbour_window` of position `idx`."""
        lo = max(0, idx - self.neighbour_window)
        hi = min(len(content_list), idx + self.neighbour_window + 1)
        for j in range(lo, hi):
            if j == idx:
                continue
            it = content_list[j]
            if it.get("type") == "text":
                t = (it.get("text") or "").strip()
                if len(t) >= self.min_neighbour_text_len:
                    yield t


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────


def _flatten_list(x) -> list[str]:
    """RAG-Anything sometimes returns captions as list[str], sometimes nested."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x.strip()] if x.strip() else []
    out: list[str] = []
    for item in x:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, list):
            out.extend(_flatten_list(item))
    return out


def _strip_html(s: str) -> str:
    """Very crude HTML tag stripper — adequate for table previews."""
    import re

    return re.sub(r"<[^>]+>", " ", s).replace("\xa0", " ").strip()


def _count_by(items: Iterable[Chunk], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in items:
        v = getattr(c, attr)
        counts[v] = counts.get(v, 0) + 1
    return counts


# ───────────────────────────────────────────────────────────────────────
# Persistence helpers
# ───────────────────────────────────────────────────────────────────────


def write_chunks_jsonl(chunks: list[Chunk], path: str | Path) -> None:
    """Persist chunks as one-JSON-object-per-line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


def read_chunks_jsonl(path: str | Path) -> list[Chunk]:
    out: list[Chunk] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append(Chunk(**d))
    return out


def write_pairs_jsonl(pairs: list[CrossModalPair], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for pr in pairs:
            f.write(json.dumps(asdict(pr), ensure_ascii=False) + "\n")


def read_pairs_jsonl(path: str | Path) -> list[CrossModalPair]:
    out: list[CrossModalPair] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append(CrossModalPair(**d))
    return out
