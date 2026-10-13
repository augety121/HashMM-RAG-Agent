"""Content Block — structured output of document parsing.

Each document is parsed into an ordered list of ContentBlocks.
A block can be text, table, image description, code, or formula.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ContentBlock:
    """One semantic unit from a parsed document."""
    type: str              # "text" | "table" | "code" | "image" | "formula"
    content: str           # Main text content (tables as Markdown, images as OCR text)
    page: int = -1         # Page number (-1 if unknown)
    section: str = ""      # Section heading this block belongs to
    position: int = 0      # Order within the document

    # Type-specific
    table_data: list[list[str]] | None = None   # Raw table rows×cols
    image_path: str | None = None               # Path to extracted image file
    code_language: str | None = None             # Programming language

    # Quality
    quality_score: float = 1.0   # 0-1
    is_noise: bool = False       # Flagged as noise (header/footer/watermark)

    def to_dict(self) -> dict:
        d = {"type": self.type, "content": self.content, "page": self.page,
             "section": self.section, "position": self.position,
             "quality_score": self.quality_score, "is_noise": self.is_noise}
        if self.table_data:
            d["table_data"] = self.table_data
        if self.image_path:
            d["image_path"] = self.image_path
        if self.code_language:
            d["code_language"] = self.code_language
        return d

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass
class Section:
    """A document section (heading + level)."""
    title: str
    level: int        # 1=h1, 2=h2, ...
    start_block: int  # Index of first block in this section
    end_block: int = -1


@dataclass
class QualityReport:
    """Document quality assessment."""
    overall_score: float = 1.0
    extractable_ratio: float = 1.0      # Pages successfully extracted / total
    noise_ratio: float = 0.0            # Noise chars / total chars
    structure_score: float = 1.0        # How well-structured (headings, etc.)
    encoding_issues: int = 0
    empty_pages: list[int] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    avg_paragraph_length: float = 0.0
    unique_sentence_ratio: float = 1.0

    def to_dict(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 3),
            "extractable_ratio": round(self.extractable_ratio, 3),
            "noise_ratio": round(self.noise_ratio, 3),
            "structure_score": round(self.structure_score, 3),
            "encoding_issues": self.encoding_issues,
            "empty_pages": self.empty_pages,
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


@dataclass
class ParsedDocument:
    """Complete result of parsing a document."""
    doc_id: str
    filename: str
    file_type: str              # pdf/docx/xlsx/pptx/code/text
    file_size: int = 0

    blocks: list[ContentBlock] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    quality: QualityReport = field(default_factory=QualityReport)

    parser_used: str = ""
    parse_time_ms: int = 0

    @property
    def full_text(self) -> str:
        """All non-noise text blocks concatenated."""
        return "\n\n".join(
            b.content for b in self.blocks
            if not b.is_noise and b.content.strip()
        )

    @property
    def num_pages(self) -> int:
        pages = [b.page for b in self.blocks if b.page > 0]
        return max(pages) if pages else 1

    @property
    def table_count(self) -> int:
        return sum(1 for b in self.blocks if b.type == "table")

    @property
    def image_count(self) -> int:
        return sum(1 for b in self.blocks if b.type == "image")

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "filename": self.filename,
            "file_type": self.file_type, "file_size": self.file_size,
            "num_blocks": len(self.blocks), "num_pages": self.num_pages,
            "tables": self.table_count, "images": self.image_count,
            "full_text_chars": len(self.full_text),
            "parser_used": self.parser_used,
            "parse_time_ms": self.parse_time_ms,
            "quality": self.quality.to_dict(),
            "sections": [{"title": s.title, "level": s.level} for s in self.sections],
        }
