"""Text Chunker v5.0 — structure-aware chunking for enterprise documents.

v5.0 improvements over v4:
  - Table-aware: tables stay intact (with header) instead of being split mid-row
  - Context window: each chunk carries section_path + surrounding context summary
  - Block-level chunking: operates on ContentBlocks, not raw text
  - Smarter text splitting: respects sentence boundaries in CJK text

Strategies:
  - recursive: Split by separators recursively (default, good balance)
  - fixed: Fixed-size chunks with overlap (fast, less accurate boundaries)
  - paragraph: Split by paragraphs/sections (best for academic papers)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from hashmm.utils import get_logger

logger = get_logger("hashmm.pipeline.chunker")


@dataclass
class Chunk:
    """A text chunk with metadata and context window."""
    text: str               # Core chunk text
    chunk_id: str
    doc_id: str
    start_char: int = 0
    end_char: int = 0
    page: int = -1
    section: str = ""       # Direct section heading
    section_path: str = ""  # Full path: "第三章 > 财务数据 > 营业收入"
    modality: str = "text"  # "text" | "table" | "code" | "image"
    context_before: str = ""  # Summary of preceding content
    context_after: str = ""   # Summary of following content
    doc_title: str = ""       # Document title (for search enrichment)
    metadata: dict | None = None

    @property
    def search_text(self) -> str:
        """Enriched text for embedding — includes context for better retrieval."""
        parts = []
        if self.doc_title:
            parts.append(f"文档：{self.doc_title}")
        if self.section_path:
            parts.append(f"章节：{self.section_path}")
        parts.append(self.text)
        return "\n".join(parts)

    @property
    def display_text(self) -> str:
        """Full text injected into LLM context — includes surrounding context."""
        parts = []
        if self.section_path:
            parts.append(f"[{self.section_path}]")
        if self.context_before:
            parts.append(f"上文摘要：{self.context_before[:150]}")
        parts.append(self.text)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        d = {
            "text": self.text, "chunk_id": self.chunk_id, "doc_id": self.doc_id,
            "start_char": self.start_char, "end_char": self.end_char,
            "page": self.page, "section": self.section, "modality": self.modality,
            "section_path": self.section_path,
            "doc_title": self.doc_title,
        }
        if self.metadata:
            d.update(self.metadata)
        return d


class TextChunker:
    """Structure-aware text chunker for enterprise documents.

    Can operate in two modes:
    1. chunk(text, ...) — legacy mode, works on raw text (backward compatible)
    2. chunk_blocks(blocks, ...) — v5.0 mode, operates on ContentBlocks
    """

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100,
                 strategy: str = "recursive"):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.strategy = strategy

    # ── v5.0: Block-level chunking (structure-aware) ──

    def chunk_blocks(self, blocks: list, doc_id: str = "doc",
                     doc_title: str = "", sections: list | None = None) -> list[Chunk]:
        """Chunk ContentBlocks with structure awareness.

        Args:
            blocks: List of ContentBlock objects (from parser)
            doc_id: Document identifier
            doc_title: Document title for context enrichment
            sections: List of Section objects (from parser)

        Returns:
            List of Chunks with context windows
        """
        if not blocks:
            return []

        # Build section path map: block_index → "Chapter > Section > Subsection"
        section_paths = self._build_section_paths(blocks, sections)

        chunks: list[Chunk] = []
        idx = 0

        for i, block in enumerate(blocks):
            if block.is_noise or not block.content.strip():
                continue

            block_chunks = []
            section_path = section_paths.get(i, block.section or "")

            if block.type == "table":
                block_chunks = self._chunk_table(block, doc_id, idx, section_path)
            elif block.type == "code":
                block_chunks = self._chunk_code(block, doc_id, idx, section_path)
            elif block.type == "image":
                # V104 P1：图像块按文本切（内容含上下文/OCR/VLM 描述），但标 modality=image，
                # 让模态感知检索（P0）能把"问图"的查询导向图像内容。
                block_chunks = self._chunk_text_block(block, doc_id, idx, section_path)
                for _c in block_chunks:
                    _c.modality = "image"
            else:
                block_chunks = self._chunk_text_block(block, doc_id, idx, section_path)

            # Add context window from neighboring blocks
            for chunk in block_chunks:
                chunk.doc_title = doc_title
                chunk.section_path = section_path

                # Context before: last non-noise block's first 100 chars
                if i > 0:
                    for j in range(i - 1, max(i - 3, -1), -1):
                        if not blocks[j].is_noise and blocks[j].content.strip():
                            chunk.context_before = blocks[j].content.strip()[:100]
                            break

                # Context after: next non-noise block's first 100 chars
                if i < len(blocks) - 1:
                    for j in range(i + 1, min(i + 3, len(blocks))):
                        if not blocks[j].is_noise and blocks[j].content.strip():
                            chunk.context_after = blocks[j].content.strip()[:100]
                            break

                chunks.append(chunk)
                idx += 1

        logger.info(f"Chunked {len(blocks)} blocks → {len(chunks)} chunks "
                    f"(tables: {sum(1 for c in chunks if c.modality == 'table')})")
        return chunks

    def _chunk_table(self, block, doc_id: str, start_idx: int,
                     section_path: str) -> list[Chunk]:
        """Chunk a table block — keep tables intact with headers.

        Small tables (< 1500 chars): one chunk.
        Large tables: split by rows, each group carries the header.
        """
        content = block.content.strip()

        # Small table → keep as one chunk
        if len(content) < 1500:
            return [Chunk(
                text=content,
                chunk_id=f"{doc_id}_c{start_idx}",
                doc_id=doc_id,
                page=block.page,
                section=block.section or "",
                section_path=section_path,
                modality="table",
            )]

        # Large table → split by rows, each group carries header
        lines = content.split("\n")
        if len(lines) < 3:
            return [Chunk(
                text=content, chunk_id=f"{doc_id}_c{start_idx}",
                doc_id=doc_id, page=block.page,
                section=block.section or "", modality="table",
            )]

        # Detect header and separator
        header_lines = [lines[0]]
        data_start = 1
        if len(lines) > 1 and re.match(r'^[\s|:-]+$', lines[1]):
            header_lines.append(lines[1])
            data_start = 2

        data_lines = lines[data_start:]
        header_text = "\n".join(header_lines)

        # Split data lines into groups of ~15 rows
        rows_per_group = 15
        chunks = []
        for g in range(0, len(data_lines), rows_per_group):
            group = data_lines[g:g + rows_per_group]
            chunk_text = header_text + "\n" + "\n".join(group)
            chunks.append(Chunk(
                text=chunk_text,
                chunk_id=f"{doc_id}_c{start_idx + len(chunks)}",
                doc_id=doc_id,
                page=block.page,
                section=block.section or "",
                section_path=section_path,
                modality="table",
            ))

        return chunks

    def _chunk_code(self, block, doc_id: str, start_idx: int,
                    section_path: str) -> list[Chunk]:
        """Chunk code blocks — keep function/class definitions together."""
        content = block.content.strip()
        if len(content) <= self.chunk_size:
            return [Chunk(
                text=content, chunk_id=f"{doc_id}_c{start_idx}",
                doc_id=doc_id, page=block.page,
                section=block.section or "", modality="code",
            )]

        # Split by function/class boundaries
        parts = re.split(r'(?=\n(?:def |class |function |public |private ))', content)
        chunks = []
        current = ""
        for part in parts:
            if len(current) + len(part) <= self.chunk_size:
                current += part
            else:
                if current.strip() and len(current.strip()) >= 20:
                    chunks.append(Chunk(
                        text=current.strip(),
                        chunk_id=f"{doc_id}_c{start_idx + len(chunks)}",
                        doc_id=doc_id, page=block.page,
                        section=block.section or "", modality="code",
                    ))
                current = part
        if current.strip() and len(current.strip()) >= 20:
            chunks.append(Chunk(
                text=current.strip(),
                chunk_id=f"{doc_id}_c{start_idx + len(chunks)}",
                doc_id=doc_id, page=block.page,
                section=block.section or "", modality="code",
            ))
        return chunks if chunks else [Chunk(
            text=content[:self.chunk_size],
            chunk_id=f"{doc_id}_c{start_idx}",
            doc_id=doc_id, page=block.page, modality="code",
        )]

    def _chunk_text_block(self, block, doc_id: str, start_idx: int,
                          section_path: str) -> list[Chunk]:
        """Chunk a text block using the configured strategy."""
        content = block.content.strip()
        if not content or len(content) < 20:
            return []

        if len(content) <= self.chunk_size:
            return [Chunk(
                text=content, chunk_id=f"{doc_id}_c{start_idx}",
                doc_id=doc_id, page=block.page,
                section=block.section or "", modality="text",
            )]

        # Use recursive splitting for text
        segments = self._split_recursive(content,
                                         ["\n\n\n", "\n\n", "\n", "。", "；", ". ", "! ", "? "])
        chunks = []
        for seg in segments:
            chunks.append(Chunk(
                text=seg, chunk_id=f"{doc_id}_c{start_idx + len(chunks)}",
                doc_id=doc_id, page=block.page,
                section=block.section or "", modality="text",
            ))
        return chunks

    def _build_section_paths(self, blocks: list, sections: list | None) -> dict[int, str]:
        """Build a map of block_index → full section path string.

        E.g., block at index 15 → "第三章 经营分析 > 3.1 营业收入"
        """
        if not sections:
            # Fallback: use block.section directly
            paths = {}
            for i, b in enumerate(blocks):
                if b.section:
                    paths[i] = b.section
            return paths

        # Build hierarchical paths from sections
        paths = {}
        active_path: list[str] = []  # [level1_title, level2_title, ...]

        section_starts = {s.start_block: s for s in sections}

        for i in range(len(blocks)):
            if i in section_starts:
                sec = section_starts[i]
                # Trim path to this level
                while len(active_path) >= sec.level:
                    active_path.pop()
                active_path.append(sec.title)

            if active_path:
                paths[i] = " > ".join(active_path)

        return paths

    # ── Legacy mode: chunk raw text (backward compatible) ──

    def chunk(self, text: str, doc_id: str = "doc",
              pages: list[tuple[int, int, int]] | None = None) -> list[Chunk]:
        """Legacy chunking on raw text. Use chunk_blocks() for better results."""
        if not text or not text.strip():
            return []
        if self.strategy == "fixed":
            return self._chunk_fixed(text, doc_id, pages)
        elif self.strategy == "paragraph":
            return self._chunk_paragraph(text, doc_id, pages)
        else:
            return self._chunk_recursive(text, doc_id, pages)

    def _chunk_fixed(self, text: str, doc_id: str,
                     pages: list[tuple[int, int, int]] | None) -> list[Chunk]:
        """Fixed-size chunks with overlap and sentence boundary alignment."""
        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text):
                for sep in ["。", ".\n", "\n\n", "；", ". ", "\n"]:
                    last = text[start:end].rfind(sep)
                    if last > self.chunk_size * 0.5:
                        end = start + last + len(sep)
                        break
            chunk_text = text[start:end].strip()
            if chunk_text and len(chunk_text) >= 20:
                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_id=f"{doc_id}_c{idx}",
                    doc_id=doc_id,
                    start_char=start,
                    end_char=end,
                    page=self._find_page(start, pages),
                ))
                idx += 1
            start = end - self.chunk_overlap
            if start >= len(text) - 20:
                break
        return chunks

    def _chunk_recursive(self, text: str, doc_id: str,
                         pages: list[tuple[int, int, int]] | None) -> list[Chunk]:
        """Recursive splitting by separators."""
        separators = ["\n\n\n", "\n\n", "\n", "。", "；", ". ", "! ", "? "]
        raw = self._split_recursive(text, separators)
        chunks = []
        pos = 0
        for i, segment in enumerate(raw):
            start = text.find(segment, pos)
            if start == -1:
                start = pos
            chunks.append(Chunk(
                text=segment,
                chunk_id=f"{doc_id}_c{i}",
                doc_id=doc_id,
                start_char=start,
                end_char=start + len(segment),
                page=self._find_page(start, pages),
            ))
            pos = start + len(segment)
        return chunks

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Core recursive split — returns list of text segments."""
        if len(text) <= self.chunk_size:
            t = text.strip()
            return [t] if t and len(t) >= 20 else []

        if not separators:
            result = []
            for i in range(0, len(text), self.chunk_size - self.chunk_overlap):
                seg = text[i:i + self.chunk_size].strip()
                if seg and len(seg) >= 20:
                    result.append(seg)
            return result

        sep = separators[0]
        parts = text.split(sep)

        if len(parts) <= 1:
            return self._split_recursive(text, separators[1:])

        result: list[str] = []
        current = ""

        for part in parts:
            candidate = (current + sep + part) if current else part
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current.strip() and len(current.strip()) >= 20:
                    if len(current) > self.chunk_size:
                        result.extend(self._split_recursive(current, separators[1:]))
                    else:
                        result.append(current.strip())
                current = part

        if current.strip() and len(current.strip()) >= 20:
            if len(current) > self.chunk_size:
                result.extend(self._split_recursive(current, separators[1:]))
            else:
                result.append(current.strip())

        return result

    def _chunk_paragraph(self, text: str, doc_id: str,
                         pages: list[tuple[int, int, int]] | None) -> list[Chunk]:
        """Split by paragraphs, merging small ones."""
        paragraphs = re.split(r'\n{2,}', text)
        chunks = []
        current = ""
        idx = 0
        pos = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= self.chunk_size:
                current += ("\n\n" + para) if current else para
            else:
                if current and len(current) >= 20:
                    chunks.append(Chunk(
                        text=current, chunk_id=f"{doc_id}_c{idx}",
                        doc_id=doc_id, start_char=pos,
                        page=self._find_page(pos, pages),
                    ))
                    idx += 1
                pos += len(current) + 2
                current = para

        if current and len(current) >= 20:
            chunks.append(Chunk(
                text=current, chunk_id=f"{doc_id}_c{idx}",
                doc_id=doc_id, start_char=pos,
                page=self._find_page(pos, pages),
            ))
        return chunks

    def _find_page(self, char_pos: int,
                   pages: list[tuple[int, int, int]] | None) -> int:
        if not pages:
            return -1
        for start, end, page_num in pages:
            if start <= char_pos < end:
                return page_num
        return -1
