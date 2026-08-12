"""Document Parser — enterprise-grade multi-format parser with 3-level fallback.

Each format has 3 parsing levels. If Level 1 fails or produces low quality,
automatically falls through to Level 2, then Level 3.

Supported: PDF, Word, Excel, PPT, Markdown, code (20+ languages), CSV/TSV
Output: ParsedDocument with ContentBlocks (text/table/code/image)
"""
from __future__ import annotations
import os
import re
import csv
import time
import warnings
import subprocess
from pathlib import Path
from hashmm.pipeline.content_block import (
    ParsedDocument, ContentBlock, Section, QualityReport,
)
from hashmm.pipeline.noise_filter import NoiseFilter
from hashmm.pipeline.quality import QualityAssessor
from hashmm.pipeline.text_preprocessor import TextPreprocessor
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.pipeline.parser")

# Suppress pdfplumber color warnings (common in enterprise PDFs)
warnings.filterwarnings("ignore", message=".*Cannot set.*color.*")
warnings.filterwarnings("ignore", message=".*Cannot set.*stroke.*")
warnings.filterwarnings("ignore", category=UserWarning, module="pdfplumber")
warnings.filterwarnings("ignore", category=UserWarning, module="pdfminer")


# ── Format detection by magic bytes (#8) ──

_MAGIC = {
    b'%PDF':          'pdf',
    b'PK\x03\x04':   '_zip',   # docx/xlsx/pptx are ZIP
    b'\xd0\xcf\x11':  '_ole',  # old .doc/.xls/.ppt
    b'\x89PNG':       'image',
    b'\xff\xd8\xff':  'image',  # JPEG
    b'GIF8':          'image',
}

def detect_format(filepath: Path) -> str:
    """Detect actual file format from magic bytes, not extension."""
    try:
        with open(filepath, 'rb') as f:
            head = f.read(8)
        for magic, fmt in _MAGIC.items():
            if head.startswith(magic):
                if fmt == '_zip':
                    # Distinguish docx/xlsx/pptx by internal files
                    import zipfile
                    try:
                        with zipfile.ZipFile(filepath) as zf:
                            names = zf.namelist()
                            if any('word/' in n for n in names):
                                return 'docx'
                            if any('xl/' in n for n in names):
                                return 'xlsx'
                            if any('ppt/' in n for n in names):
                                return 'pptx'
                    except Exception as _e:
                        log_suppressed(logger, _e)
                    return 'zip'
                return fmt
    except Exception as _e:
        log_suppressed(logger, _e)
    # Fall back to extension
    return filepath.suffix.lower().lstrip('.')


class DocumentParser:
    """Multi-format document parser with fallback chains.

    Args:
        output_dir: Directory for extracted images/tables
        noise_filter: NoiseFilter instance
    """

    def __init__(self, output_dir: str = "data/docs",
                 noise_filter: NoiseFilter | None = None,
                 *, allow_inline_ocr: bool = True):
        self.output_dir = Path(output_dir)
        self.noise_filter = noise_filter or NoiseFilter()
        self.quality_assessor = QualityAssessor()
        self.preprocessor = TextPreprocessor()
        self.allow_inline_ocr = bool(allow_inline_ocr)

    def parse(self, filepath: str | Path) -> ParsedDocument:
        """Parse any supported document format."""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        t0 = time.time()
        doc_id = f"doc-{filepath.stem}"
        file_size = filepath.stat().st_size

        # Detect actual format
        fmt = detect_format(filepath)
        ext = filepath.suffix.lower().lstrip('.')

        # Route to parser
        if fmt == 'pdf' or ext == 'pdf':
            doc = self._parse_pdf(filepath, doc_id)
        elif fmt == 'docx' or ext in ('docx', 'doc'):
            doc = self._parse_docx(filepath, doc_id)
        elif fmt == 'xlsx' or ext in ('xlsx', 'xls'):
            doc = self._parse_xlsx(filepath, doc_id)
        elif fmt == 'pptx' or ext in ('pptx', 'ppt'):
            doc = self._parse_pptx(filepath, doc_id)
        elif ext in ('csv', 'tsv'):
            doc = self._parse_csv(filepath, doc_id)
        elif ext in ('md', 'txt', 'rst', 'log'):
            doc = self._parse_text(filepath, doc_id)
        elif ext in ('py', 'js', 'ts', 'jsx', 'tsx', 'java', 'cpp', 'c', 'h',
                      'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'r',
                      'html', 'css', 'scss', 'json', 'yaml', 'yml', 'toml',
                      'xml', 'sql', 'sh', 'bash', 'ps1', 'bat', 'tex', 'bib',
                      'makefile', 'dockerfile', 'ini', 'cfg', 'conf'):
            doc = self._parse_code(filepath, doc_id)
        else:
            doc = self._parse_text(filepath, doc_id)

        doc.file_size = file_size
        doc.parse_time_ms = round((time.time() - t0) * 1000)

        # Apply noise filter
        pages_text = {b.page: b.content for b in doc.blocks if b.page > 0}
        self.noise_filter.clean(doc.blocks, pages_text)

        # Apply text preprocessor (Traditional→Simplified, encoding fixes)
        for b in doc.blocks:
            if not b.is_noise and b.content:
                b.content = self.preprocessor.process(b.content)

        # Detect sections
        doc.sections = self._detect_sections(doc.blocks)

        # Quality assessment
        doc.quality = self.quality_assessor.assess(doc)

        clean_blocks = [b for b in doc.blocks if not b.is_noise]
        clean_chars = sum(b.char_count for b in clean_blocks)
        logger.info(
            f"Parsed {filepath.name}: {len(clean_blocks)} blocks, "
            f"{clean_chars} chars, {doc.table_count} tables, "
            f"quality={doc.quality.overall_score:.2f} ({doc.parse_time_ms}ms)"
        )

        return doc

    # ── PDF: 3-level fallback ──

    def _is_text_garbled(self, blocks: list[ContentBlock], threshold: float = 0.08) -> bool:
        """Detect garbled/corrupted text from PDF extraction.
        
        Three independent checks — ANY one failing = garbled:
        1. Suspicious Unicode ranges (CJK Extension, Private Use, etc.)
        2. CID code patterns
        3. CJK characters present but almost no common Chinese chars
           (the killer check: garbled PDFs use real CJK codepoints but random ones)
        """
        text_blocks = [b for b in blocks if b.type == "text" and len(b.content.strip()) > 20]
        if not text_blocks:
            return True
        
        # Sample broadly: first 500 chars from 50 blocks spread across the document
        step = max(1, len(text_blocks) // 50)
        sampled = text_blocks[::step][:50]
        sample = " ".join(b.content[:500] for b in sampled)
        if len(sample) < 100:
            return True
        
        # Check 1: CID codes (definitive)
        if sample.count("(cid:") > 3:
            return True

        # Check 2: Suspicious Unicode ranges
        suspicious = 0
        normal_cjk = 0
        total_chars = 0
        for ch in sample:
            cp = ord(ch)
            if cp <= 32:
                continue
            total_chars += 1
            if (0x3400 <= cp <= 0x4DBF or     # CJK Extension A
                cp >= 0x20000 or               # CJK Extension B+
                0x2E00 <= cp <= 0x2E7F or      # Supplemental Punctuation
                0xE000 <= cp <= 0xF8FF or      # Private Use Area
                0x2000 <= cp <= 0x200F or      # Zero-width chars
                0x0370 <= cp <= 0x03FF or      # Greek
                0x1D00 <= cp <= 0x1DBF or      # Phonetic Extensions
                0x2100 <= cp <= 0x214F or      # Letterlike Symbols
                0x2300 <= cp <= 0x23FF or      # Miscellaneous Technical
                0x2460 <= cp <= 0x24FF or      # Enclosed Alphanumerics
                0x2580 <= cp <= 0x259F or      # Block Elements
                0x2C00 <= cp <= 0x2C5F or      # Glagolitic
                0x0300 <= cp <= 0x036F or      # Combining Diacritical
                0x02B0 <= cp <= 0x02FF):       # Spacing Modifier Letters
                suspicious += 1
            elif 0x4E00 <= cp <= 0x9FFF:
                normal_cjk += 1
        
        if total_chars < 50:
            return True
        if suspicious / total_chars > threshold:
            logger.warning(f"Garbled: {suspicious}/{total_chars} suspicious chars "
                           f"({suspicious/total_chars:.1%})")
            return True
        
        # Check 3 (CRITICAL): CJK chars present but no common Chinese words
        # Real Chinese text ALWAYS contains 的/是/了/在/有 etc.
        # Garbled text maps to random CJK codepoints → these common chars won't appear
        if normal_cjk > 50:
            common_chars = set(
                "的一是不了人我在有他这中大来上个国到说们时要就出会也你对生"
                "能自学下和年地为那都之后作同多经可以过以于所公司员工本报告"
                "期间业务收入利润资产负债股东董事会管理层审计发展市场产品"
                "服务技术研发投资增长下降比较主要包括其中以及通过进行"
            )
            common_count = sum(1 for ch in sample if ch in common_chars)
            ratio = common_count / normal_cjk if normal_cjk > 0 else 0
            if ratio < 0.03:  # < 3% common chars = definitely garbled
                logger.warning(f"Garbled: {common_count} common chars in {normal_cjk} "
                               f"CJK chars ({ratio:.1%}) — real Chinese text has >10%")
                return True
        
        return False

    def _parse_pdf(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="pdf")
        doc_dir = self._ensure_doc_dir(doc_id)
        
        parsers_to_try = []
        
        # Build parser list in priority order
        try:
            import pdfplumber
            parsers_to_try.append(("pdfplumber", lambda: self._pdf_pdfplumber(filepath, doc_id, doc_dir)))
        except ImportError:
            pass
        try:
            import fitz
            parsers_to_try.append(("pymupdf", lambda: self._pdf_pymupdf(filepath, doc_id, doc_dir)))
        except ImportError:
            pass
        try:
            from pypdf import PdfReader
            parsers_to_try.append(("pypdf", lambda: self._pdf_pypdf(filepath, doc_id)))
        except ImportError:
            pass
        parsers_to_try.append(("pdftotext", lambda: self._pdf_cli(filepath, doc_id)))
        
        # Try each parser, check for garbled text
        best_garbled_blocks = None  # Save best garbled result for table extraction
        best_garbled_parser = None
        best_garbled_tables = 0

        for parser_name, parser_fn in parsers_to_try:
            try:
                blocks, _ = parser_fn()
                text_chars = sum(b.char_count for b in blocks if b.type == "text")
                
                if not blocks or text_chars < 100:
                    logger.debug(f"{parser_name}: too little text ({text_chars} chars)")
                    continue
                
                if self._is_text_garbled(blocks):
                    logger.warning(f"{parser_name}: text is garbled, trying next parser")
                    # v5.1: Save garbled result if it has tables (for hybrid merge later)
                    table_count = sum(1 for b in blocks if b.type == "table")
                    if table_count > best_garbled_tables:
                        best_garbled_blocks = blocks
                        best_garbled_parser = parser_name
                        best_garbled_tables = table_count
                    continue
                
                # Good result
                doc.blocks = blocks
                doc.parser_used = parser_name
                logger.info(f"PDF parsed successfully with {parser_name}")
                return doc
                
            except Exception as e:
                logger.debug(f"{parser_name} failed: {e}")
                continue
        
        # All parsers failed or produced garbled text
        # Last resort: OCR (render pages to images → PaddleOCR/Tesseract)
        if not self.allow_inline_ocr:
            doc.quality.issues.append("document requires durable OCR")
            return doc
        try:
            blocks, parser = self._pdf_ocr(filepath, doc_id, doc_dir)
            if blocks and not self._is_text_garbled(blocks):
                # v5.1: Hybrid merge — inject tables from garbled parser into OCR result
                if best_garbled_blocks and best_garbled_tables > 0:
                    merged_tables = self._merge_tables_from_garbled(
                        blocks, best_garbled_blocks
                    )
                    if merged_tables > 0:
                        logger.info(f"Hybrid parse: OCR text + {merged_tables} tables "
                                    f"from {best_garbled_parser}")
                        doc.parser_used = f"hybrid-ocr+{best_garbled_parser}"
                    else:
                        doc.parser_used = parser
                else:
                    doc.parser_used = parser

                doc.blocks = blocks
                logger.info(f"PDF parsed successfully with {doc.parser_used}")
                return doc
        except Exception as e:
            logger.debug(f"OCR fallback failed: {e}")

        logger.error(f"All parsers produced garbled text for {filepath.name}.")
        doc.quality.issues.append("所有解析器输出乱码，建议安装 PaddleOCR: pip install paddleocr")
        
        # Return best effort from any parser
        for parser_name, parser_fn in parsers_to_try[:1]:
            try:
                blocks, _ = parser_fn()
                doc.blocks = blocks
                doc.parser_used = f"{parser_name}-fallback"
                break
            except Exception as _e:
                log_suppressed(logger, _e)
        
        return doc

    def _pdf_pdfplumber(self, fp, doc_id, doc_dir):
        import pdfplumber
        import io, os, sys

        # Redirect stderr to suppress pdfminer C-level "Cannot set color" warnings
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            return self._pdf_pdfplumber_inner(fp, doc_id, doc_dir)
        finally:
            sys.stderr = old_stderr

    def _pdf_pdfplumber_inner(self, fp, doc_id, doc_dir):
        import pdfplumber
        blocks = []
        pos = 0
        with pdfplumber.open(fp) as pdf:
            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                text = page.extract_text() or ""
                text = self._clean_pdf_encoding(text)
                if text.strip():
                    # Split into paragraphs with section detection
                    page_blocks = self._split_page_into_blocks(text, page_num, pos)
                    blocks.extend(page_blocks)
                    pos += len(page_blocks)
                # Tables
                for table in page.extract_tables():
                    if table and any(any(c for c in row if c) for row in table):
                        clean_rows = [[str(c).strip() if c else "" for c in r] for r in table]
                        md = self._table_to_markdown(clean_rows)
                        blocks.append(ContentBlock(
                            type="table", content=md,
                            page=page_num, position=pos,
                            table_data=clean_rows,
                        ))
                        pos += 1
        logger.info(f"PDF parsed (pdfplumber): {sum(b.char_count for b in blocks)} chars")
        return blocks, "pdfplumber"

    def _pdf_pymupdf(self, fp, doc_id, doc_dir):
        import fitz
        blocks = []
        pos = 0
        pdf = fitz.open(fp)

        for i in range(len(pdf)):
            page = pdf[i]
            page_num = i + 1
            raw_text = page.get_text("text") or ""
            raw_text = self._clean_pdf_encoding(raw_text)

            # Split page text into paragraphs + detect headings
            if raw_text.strip():
                paragraphs = self._split_page_into_blocks(raw_text, page_num, pos)
                blocks.extend(paragraphs)
                pos += len(paragraphs)

            # Extract tables (PyMuPDF 1.23+)
            try:
                tabs = page.find_tables()
                for t_idx, tab in enumerate(tabs.tables if hasattr(tabs, 'tables') else tabs):
                    try:
                        rows = tab.extract()
                        if rows and len(rows) >= 2:
                            # Clean None values
                            clean_rows = [[str(c).strip() if c else "" for c in r] for r in rows]
                            if any(any(c for c in r) for r in clean_rows):
                                md = self._table_to_markdown(clean_rows)
                                blocks.append(ContentBlock(
                                    type="table", content=md,
                                    page=page_num, position=pos,
                                    table_data=clean_rows,
                                ))
                                pos += 1
                    except Exception as _e:
                        log_suppressed(logger, _e)
            except (AttributeError, Exception):
                pass  # find_tables not available in older PyMuPDF

            # Extract images with context
            for img_idx, img in enumerate(page.get_images(full=True)):
                try:
                    xref = img[0]
                    pix = fitz.Pixmap(pdf, xref)
                    # Skip tiny images (icons, bullets)
                    if pix.width < 50 or pix.height < 50:
                        continue
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    img_path = doc_dir / "images" / f"p{page_num}_img{img_idx}.png"
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    pix.save(str(img_path))

                    # Find image context from surrounding text
                    context = self._find_image_context(raw_text, page_num, img_idx)

                    # Try OCR if available
                    ocr_text = self._ocr_image(str(img_path))

                    img_content_parts = [f"[图片: 第{page_num}页, 图{img_idx+1}]"]
                    if context:
                        img_content_parts.append(f"上下文: {context}")
                    if ocr_text:
                        img_content_parts.append(f"OCR: {ocr_text}")
                        # Save OCR text
                        ocr_path = doc_dir / "images" / f"p{page_num}_img{img_idx}_ocr.txt"
                        ocr_path.write_text(ocr_text, encoding="utf-8")

                    vlm_desc = self._vlm_describe_image(str(img_path))
                    if vlm_desc:
                        img_content_parts.append(f"图像描述: {vlm_desc}")

                    blocks.append(ContentBlock(
                        type="image", content="\n".join(img_content_parts),
                        page=page_num, position=pos,
                        image_path=str(img_path),
                    ))
                    pos += 1
                except Exception as _e:
                    log_suppressed(logger, _e)

        pdf.close()
        text_chars = sum(b.char_count for b in blocks if b.type == "text")
        table_count = sum(1 for b in blocks if b.type == "table")
        img_count = sum(1 for b in blocks if b.type == "image")
        logger.info(f"PDF parsed (PyMuPDF): {text_chars} chars, "
                    f"{table_count} tables, {img_count} images")
        return blocks, "pymupdf"

    def _split_page_into_blocks(self, text: str, page: int, start_pos: int) -> list[ContentBlock]:
        """Split a page's text into paragraph blocks, detecting section headings."""
        blocks = []
        # Split by double newline or detect heading patterns
        heading_re = re.compile(
            r'^(\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{3,80})$'   # "1. Introduction", "3.2 Method"
            r'|^(#{1,4}\s+.{3,80})$'                       # Markdown headings
            r'|^((?:Abstract|Introduction|Related\s+Work|Method(?:ology)?|'
            r'Experiment(?:s)?|Results?|Discussion|Conclusion|References|'
            r'Acknowledgment|Appendix)\s*)$'                # Common section names
            r'|^(第[一二三四五六七八九十\d]+[章节部分]\s*.{2,40})$',  # Chinese
            re.MULTILINE | re.IGNORECASE,
        )

        lines = text.split('\n')
        current_section = ""
        current_text = ""
        pos = start_pos

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                # Empty line → potential paragraph break
                if current_text.strip() and len(current_text.strip()) >= 15:
                    blocks.append(ContentBlock(
                        type="text", content=current_text.strip(),
                        page=page, position=pos, section=current_section,
                    ))
                    pos += 1
                    current_text = ""
                continue

            # Check if this line is a heading
            m = heading_re.match(line_stripped)
            if m and len(line_stripped) < 100:
                # Flush current text
                if current_text.strip() and len(current_text.strip()) >= 15:
                    blocks.append(ContentBlock(
                        type="text", content=current_text.strip(),
                        page=page, position=pos, section=current_section,
                    ))
                    pos += 1
                    current_text = ""
                current_section = line_stripped
                # Heading as its own block
                blocks.append(ContentBlock(
                    type="text", content=line_stripped,
                    page=page, position=pos, section=current_section,
                    quality_score=1.0,
                ))
                pos += 1
            else:
                current_text += line + "\n"

        # Flush remaining
        if current_text.strip() and len(current_text.strip()) >= 15:
            blocks.append(ContentBlock(
                type="text", content=current_text.strip(),
                page=page, position=pos, section=current_section,
            ))

        return blocks

    def _clean_pdf_encoding(self, text: str) -> str:
        """#4: Fix common PDF encoding issues."""
        if not text:
            return text
        # Remove NULL bytes and control chars
        text = text.replace('\x00', '')
        text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f]', '', text)
        # BOM and replacement chars
        text = text.replace('\ufeff', '').replace('\ufffd', '')
        # PDF ligatures
        text = text.replace('ﬁ', 'fi').replace('ﬂ', 'fl')
        text = text.replace('ﬀ', 'ff').replace('ﬃ', 'ffi').replace('ﬄ', 'ffl')
        # Dash normalization
        text = text.replace('−', '-').replace('–', '-').replace('—', '-')
        # Quote normalization
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace(''', "'").replace(''', "'")
        # Non-breaking space
        text = text.replace('\xa0', ' ')
        return text

    def _vlm_describe_image(self, img_path: str) -> str:
        """摄取期可选的图像 VLM 语义描述（默认关：HASHMM_VLM_INGEST=1 且 vision 已配置才生效）。
        让无文字的图（图表/示意图/照片）也有可检索语义。复用 chat 同款视觉通路，零新依赖、永不抛错。
        关闭时返回 ""，对解析结果零影响。"""
        import os as _os
        if _os.environ.get("HASHMM_VLM_INGEST", "0") != "1":
            return ""
        try:
            from hashmm.agent.vision import describe_image_file
            return describe_image_file(img_path)
        except Exception:
            return ""

    def _find_image_context(self, page_text: str, page_num: int, img_idx: int) -> str:
        """#3: Find contextual description of an image from surrounding text."""
        # Look for "Figure X", "Fig. X", "图X" references
        patterns = [
            re.compile(rf'(?:Figure|Fig\.?)\s*{img_idx + 1}[.:]\s*([^\n]{{10,200}})', re.I),
            re.compile(rf'(?:图\s*{img_idx + 1})[.:\s]\s*([^\n]{{5,200}})'),
            re.compile(rf'(?:Figure|Fig\.?)\s*{img_idx + 1}\s+([^\n]{{10,200}})', re.I),
        ]
        for pat in patterns:
            m = pat.search(page_text)
            if m:
                return m.group(1).strip()[:200]
        return ""

    def _ocr_image(self, image_path: str) -> str:
        """#3: OCR an image using PaddleOCR or Tesseract (if available)."""
        if not self.allow_inline_ocr:
            return ""
        # Try PaddleOCR
        try:
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
            result = ocr.ocr(image_path, cls=True)
            if result and result[0]:
                texts = [line[1][0] for line in result[0] if line[1][0].strip()]
                return " ".join(texts)[:500]
        except ImportError:
            pass
        except Exception as _e:
            log_suppressed(logger, _e)

        # Try Tesseract
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            if text.strip():
                return text.strip()[:500]
        except ImportError:
            pass
        except Exception as _e:
            log_suppressed(logger, _e)

        return ""

    def _pdf_pypdf(self, fp, doc_id):
        from pypdf import PdfReader
        blocks = []
        reader = PdfReader(fp)
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(ContentBlock(
                    type="text", content=text.strip(),
                    page=i + 1, position=i,
                ))
        logger.info(f"PDF parsed (pypdf): {sum(b.char_count for b in blocks)} chars")
        return blocks, "pypdf"

    def _pdf_cli(self, fp, doc_id):
        result = subprocess.run(
            ["pdftotext", "-layout", str(fp), "-"],
            capture_output=True, text=True, timeout=120,
        )
        blocks = []
        if result.returncode == 0 and result.stdout.strip():
            pages = result.stdout.split('\f')
            for i, page_text in enumerate(pages):
                if page_text.strip():
                    page_blocks = self._split_page_into_blocks(page_text, i + 1, len(blocks))
                    blocks.extend(page_blocks)
        logger.info(f"PDF parsed (pdftotext CLI): {sum(b.char_count for b in blocks)} chars")
        return blocks, "pdftotext"

    def _pdf_ocr(self, fp, doc_id, doc_dir):
        """Last resort: render PDF pages to images and OCR them.
        
        Requires either PaddleOCR or Tesseract + PyMuPDF for rendering.
        """
        import fitz
        
        # Detect available OCR engine
        ocr_engine = None
        try:
            from paddleocr import PaddleOCR
            ocr_engine = "paddle"
        except ImportError:
            try:
                import pytesseract
                ocr_engine = "tesseract"
            except ImportError:
                raise ImportError("No OCR engine available. Install: pip install paddleocr")

        pdf = fitz.open(fp)
        blocks = []
        pos = 0
        
        # Initialize OCR
        paddle_ocr = None
        if ocr_engine == "paddle":
            from paddleocr import PaddleOCR
            paddle_ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)

        total_pages = len(pdf)
        logger.info(f"OCR processing {total_pages} pages...")

        for i in range(total_pages):
            page = pdf[i]
            # Render page to image (300 DPI for good OCR quality)
            mat = fitz.Matrix(2, 2)  # 2x zoom ≈ 144 DPI (balance speed/quality)
            pix = page.get_pixmap(matrix=mat)
            img_path = doc_dir / "ocr_pages" / f"page_{i+1}.png"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(img_path))

            # OCR the rendered image
            page_text = ""
            if ocr_engine == "paddle":
                result = paddle_ocr.ocr(str(img_path), cls=True)
                if result and result[0]:
                    lines = sorted(result[0], key=lambda x: x[0][0][1])  # Sort by Y position
                    page_text = "\n".join(line[1][0] for line in lines if line[1][0].strip())
            elif ocr_engine == "tesseract":
                import pytesseract
                from PIL import Image
                img = Image.open(str(img_path))
                page_text = pytesseract.image_to_string(img, lang='chi_sim+eng')

            if page_text.strip():
                page_blocks = self._split_page_into_blocks(page_text.strip(), i + 1, pos)
                blocks.extend(page_blocks)
                pos += len(page_blocks)

            if (i + 1) % 20 == 0:
                logger.info(f"OCR progress: {i+1}/{total_pages} pages")

        pdf.close()
        text_chars = sum(b.char_count for b in blocks)
        logger.info(f"PDF parsed (OCR-{ocr_engine}): {text_chars} chars from {total_pages} pages")
        return blocks, f"ocr-{ocr_engine}"

    # ── Word: 3-level fallback ──

    def _parse_docx(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="docx")

        # Level 1: python-docx
        try:
            blocks = self._docx_python_docx(filepath, doc_id)
            if blocks:
                doc.blocks = blocks
                doc.parser_used = "python-docx"
                return doc
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"python-docx failed: {e}")

        # Level 2: mammoth (converts to HTML)
        try:
            import mammoth
            with open(filepath, "rb") as f:
                result = mammoth.convert_to_markdown(f)
            text = result.value
            if text.strip():
                doc.blocks = self._text_to_blocks(text, doc_id)
                doc.parser_used = "mammoth"
                return doc
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"mammoth failed: {e}")

        # Level 3: XML direct read
        try:
            blocks = self._docx_xml_read(filepath, doc_id)
            doc.blocks = blocks
            doc.parser_used = "xml-direct"
        except Exception as e:
            logger.error(f"All Word parsers failed: {e}")

        return doc

    def _docx_python_docx(self, fp, doc_id):
        from docx import Document as DocxDoc
        d = DocxDoc(fp)
        blocks = []
        pos = 0

        # Extract headers/footers for noise detection
        for section in d.sections:
            for hdr in [section.header, section.footer]:
                if hdr and hdr.text.strip():
                    blocks.append(ContentBlock(
                        type="text", content=hdr.text.strip(),
                        position=pos, is_noise=True, quality_score=0.0,
                    ))
                    pos += 1

        # Paragraphs
        current_section = ""
        for para in d.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            # Detect headings
            if para.style and para.style.name and 'Heading' in para.style.name:
                current_section = text
            blocks.append(ContentBlock(
                type="text", content=text,
                section=current_section, position=pos,
            ))
            pos += 1

        # Tables
        for table in d.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(cells)
            if rows and any(any(c for c in r) for r in rows):
                md = self._table_to_markdown(rows)
                blocks.append(ContentBlock(
                    type="table", content=md, position=pos,
                    table_data=rows, section=current_section,
                ))
                pos += 1

        logger.info(f"Word parsed (python-docx): {len(blocks)} blocks")
        return blocks

    def _docx_xml_read(self, fp, doc_id):
        """Last resort: unzip and read word/document.xml."""
        import zipfile
        from xml.etree import ElementTree as ET
        blocks = []
        try:
            with zipfile.ZipFile(fp) as zf:
                with zf.open('word/document.xml') as xf:
                    tree = ET.parse(xf)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            for i, para in enumerate(tree.findall('.//w:p', ns)):
                texts = [t.text for t in para.findall('.//w:t', ns) if t.text]
                text = ''.join(texts).strip()
                if text:
                    blocks.append(ContentBlock(type="text", content=text, position=i))
        except Exception as e:
            logger.error(f"XML direct read failed: {e}")
        return blocks

    # ── Excel: 3-level fallback ──

    def _parse_xlsx(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="xlsx")

        # Level 1: openpyxl
        try:
            blocks = self._xlsx_openpyxl(filepath, doc_id)
            if blocks:
                doc.blocks = blocks
                doc.parser_used = "openpyxl"
                return doc
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"openpyxl failed: {e}")

        # Level 2: pandas
        try:
            import pandas as pd
            blocks = self._xlsx_pandas(filepath, doc_id)
            if blocks:
                doc.blocks = blocks
                doc.parser_used = "pandas"
                return doc
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"pandas failed: {e}")

        # Level 3: CSV fallback (rename to csv and try)
        doc.quality.issues.append("Excel 解析器不可用")
        doc.quality.suggestions.append("安装 openpyxl: pip install openpyxl")
        return doc

    def _xlsx_openpyxl(self, fp, doc_id):
        import openpyxl
        wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
        blocks = []
        pos = 0
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            empty_rows = 0
            for row in ws.iter_rows(max_row=5000, values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    rows.append(cells)
                    empty_rows = 0
                else:
                    empty_rows += 1
                    if empty_rows > 5:
                        break  # Stop after 5 consecutive empty rows

            if not rows:
                continue

            # Clean: remove columns that are >80% empty
            if len(rows) > 2:
                col_count = max(len(r) for r in rows)
                keep_cols = []
                for c in range(col_count):
                    filled = sum(1 for r in rows if c < len(r) and r[c].strip())
                    if filled / len(rows) > 0.2:
                        keep_cols.append(c)
                if keep_cols:
                    rows = [[r[c] if c < len(r) else "" for c in keep_cols] for r in rows]

            # Split into blocks of 30 rows (with header repeated)
            header = rows[0] if rows else []
            data_rows = rows[1:]
            chunk_size = 30
            for i in range(0, max(len(data_rows), 1), chunk_size):
                chunk_rows = [header] + data_rows[i:i + chunk_size]
                md = self._table_to_markdown(chunk_rows)
                blocks.append(ContentBlock(
                    type="table", content=f"## Sheet: {sheet_name}\n\n{md}",
                    position=pos, section=sheet_name,
                    table_data=chunk_rows,
                ))
                pos += 1

        wb.close()
        logger.info(f"Excel parsed (openpyxl): {len(blocks)} blocks")
        return blocks

    def _xlsx_pandas(self, fp, doc_id):
        import pandas as pd
        blocks = []
        pos = 0
        xls = pd.ExcelFile(fp)
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name, nrows=5000)
            df = df.dropna(how='all').dropna(axis=1, how='all')
            if df.empty:
                continue
            md = df.to_markdown(index=False)
            blocks.append(ContentBlock(
                type="table", content=f"## Sheet: {sheet_name}\n\n{md}",
                position=pos, section=sheet_name,
            ))
            pos += 1
        return blocks

    # ── PPT: 3-level fallback ──

    def _parse_pptx(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="pptx")

        # Level 1: python-pptx
        try:
            blocks = self._pptx_python_pptx(filepath, doc_id)
            if blocks:
                doc.blocks = blocks
                doc.parser_used = "python-pptx"
                return doc
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"python-pptx failed: {e}")

        # Level 2: XML direct read
        try:
            blocks = self._pptx_xml_read(filepath, doc_id)
            if blocks:
                doc.blocks = blocks
                doc.parser_used = "xml-direct"
                return doc
        except Exception as e:
            logger.debug(f"PPTX XML read failed: {e}")

        doc.quality.issues.append("PPT 解析器不可用")
        return doc

    def _pptx_python_pptx(self, fp, doc_id):
        from pptx import Presentation
        prs = Presentation(fp)
        blocks = []
        for i, slide in enumerate(prs.slides, 1):
            parts = [f"## 幻灯片 {i}"]
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t:
                            parts.append(t)
                if shape.has_table:
                    rows = []
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        rows.append(cells)
                    if rows:
                        parts.append(self._table_to_markdown(rows))
            # Speaker notes
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    parts.append(f"[备注] {notes}")

            content = "\n".join(parts)
            if len(content.strip()) > 10:
                blocks.append(ContentBlock(
                    type="text", content=content,
                    page=i, position=i - 1,
                    section=f"幻灯片 {i}",
                ))
        logger.info(f"PPT parsed (python-pptx): {len(blocks)} slides")
        return blocks

    def _pptx_xml_read(self, fp, doc_id):
        import zipfile
        from xml.etree import ElementTree as ET
        blocks = []
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
        try:
            with zipfile.ZipFile(fp) as zf:
                slide_files = sorted(n for n in zf.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
                for i, sf in enumerate(slide_files, 1):
                    with zf.open(sf) as xf:
                        tree = ET.parse(xf)
                    texts = [t.text for t in tree.findall('.//a:t', ns) if t.text]
                    content = " ".join(texts).strip()
                    if content:
                        blocks.append(ContentBlock(type="text", content=content, page=i, position=i - 1))
        except Exception as e:
            logger.error(f"PPTX XML read failed: {e}")
        return blocks

    # ── Text/Markdown ──

    def _parse_text(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="text")
        try:
            text = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = filepath.read_text(encoding="gbk")
            except Exception:
                text = filepath.read_text(encoding="latin-1", errors="replace")
                doc.quality.encoding_issues += 1

        doc.blocks = self._text_to_blocks(text, doc_id)
        doc.parser_used = "text"
        return doc

    # ── CSV/TSV ──

    def _parse_csv(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="csv")
        delimiter = "\t" if filepath.suffix == ".tsv" else ","
        try:
            with open(filepath, encoding="utf-8", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                rows = [r for r in reader]
        except UnicodeDecodeError:
            with open(filepath, encoding="gbk", newline="", errors="replace") as f:
                reader = csv.reader(f, delimiter=delimiter)
                rows = [r for r in reader]

        if rows:
            header = rows[0]
            for i in range(0, max(len(rows) - 1, 1), 30):
                chunk = [header] + rows[1 + i:1 + i + 30]
                md = self._table_to_markdown(chunk)
                doc.blocks.append(ContentBlock(
                    type="table", content=md, position=i // 30,
                    table_data=chunk,
                ))
        doc.parser_used = "csv"
        return doc

    # ── Code ──

    def _parse_code(self, filepath: Path, doc_id: str) -> ParsedDocument:
        doc = ParsedDocument(doc_id=doc_id, filename=filepath.name, file_type="code")
        try:
            text = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = filepath.read_text(encoding="latin-1", errors="replace")

        lang = filepath.suffix.lstrip(".")
        ext = filepath.suffix.lower()

        # Python: AST-based splitting
        if ext == ".py":
            blocks = self._code_python_ast(text, doc_id, lang)
        else:
            blocks = self._code_generic(text, doc_id, lang)

        doc.blocks = blocks
        doc.parser_used = f"code-{lang}"
        return doc

    def _code_python_ast(self, text, doc_id, lang):
        """Split Python code by functions/classes using AST."""
        import ast as _ast
        blocks = []
        try:
            tree = _ast.parse(text)
            lines = text.split("\n")

            # Extract imports as metadata block
            imports = []
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                    imports.append(_ast.get_source_segment(text, node) or "")
            if imports:
                blocks.append(ContentBlock(
                    type="code", content="\n".join(imports),
                    position=0, code_language=lang,
                    section="imports",
                ))

            # Extract top-level functions and classes
            pos = 1
            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    start = node.lineno - 1
                    end = node.end_lineno or start + 1
                    code = "\n".join(lines[start:end])
                    # Include docstring as searchable content
                    docstring = _ast.get_docstring(node) or ""
                    section_name = f"{'class' if isinstance(node, _ast.ClassDef) else 'def'} {node.name}"
                    blocks.append(ContentBlock(
                        type="code", content=code,
                        position=pos, code_language=lang,
                        section=section_name,
                    ))
                    pos += 1
        except SyntaxError:
            # Fall back to generic splitting
            return self._code_generic(text, doc_id, lang)

        if not blocks:
            return self._code_generic(text, doc_id, lang)
        return blocks

    def _code_generic(self, text, doc_id, lang):
        """Split code by blank lines + indentation."""
        blocks = []
        segments = re.split(r'\n{2,}', text)
        for i, seg in enumerate(segments):
            seg = seg.strip()
            if seg and len(seg) >= 20:
                blocks.append(ContentBlock(
                    type="code", content=seg,
                    position=i, code_language=lang,
                ))
        return blocks

    # ── Helpers ──

    def _text_to_blocks(self, text: str, doc_id: str) -> list[ContentBlock]:
        """Convert plain text to blocks, split by double newlines."""
        blocks = []
        paragraphs = re.split(r'\n{2,}', text)
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if para and len(para) >= 5:
                blocks.append(ContentBlock(
                    type="text", content=para, position=i,
                ))
        return blocks

    def _detect_sections(self, blocks: list[ContentBlock]) -> list[Section]:
        """Detect section headings from blocks."""
        sections = []
        heading_re = re.compile(
            r'^(?:#{1,4}\s+.+|'                                # Markdown headings
            r'\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{3,80}|'           # "1. Introduction", "3.2 Method"
            r'(?:第[一二三四五六七八九十\d]+[章节部分篇]\s*.+)|'  # Chinese sections
            r'(?:Abstract|Introduction|Related\s+Work|Methodology|'
            r'Method(?:s)?|Experiment(?:s)?|Results?|Discussion|'
            r'Conclusion(?:s)?|References|Acknowledgment(?:s)?|'
            r'Appendix|Background|Preliminary|Overview|'
            r'摘要|引言|相关工作|方法|实验|结果|讨论|结论|参考文献)\s*$)',
            re.IGNORECASE | re.MULTILINE,
        )
        for i, b in enumerate(blocks):
            if b.is_noise or b.type != "text":
                continue
            first_line = b.content.split("\n")[0].strip()
            if len(first_line) < 100 and heading_re.match(first_line):
                level = 1
                if re.match(r'^\d+\.\d+', first_line):
                    level = 2
                elif re.match(r'^\d+\.\d+\.\d+', first_line):
                    level = 3
                sections.append(Section(title=first_line, level=level, start_block=i))
                if not b.section:
                    b.section = first_line
        # Propagate section to subsequent blocks
        current_section = ""
        for b in blocks:
            if b.section:
                current_section = b.section
            elif current_section and not b.is_noise:
                b.section = current_section
        return sections

    def _ensure_doc_dir(self, doc_id: str) -> Path:
        d = self.output_dir / doc_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _merge_tables_from_garbled(self, ocr_blocks: list[ContentBlock],
                                    garbled_blocks: list[ContentBlock]) -> int:
        """v5.1: Merge table blocks from a garbled parser into OCR results.

        PyMuPDF can detect table STRUCTURE even when text is garbled.
        We take the table structure and apply OCR correction to the cell text.

        Args:
            ocr_blocks: Clean text blocks from OCR (will be modified in-place)
            garbled_blocks: Blocks from the garbled parser (PyMuPDF etc.)

        Returns:
            Number of tables merged.
        """
        # Extract table blocks from garbled result
        table_blocks = [b for b in garbled_blocks if b.type == "table" and b.content.strip()]
        if not table_blocks:
            return 0

        # Apply OCR correction to table text
        try:
            from hashmm.pipeline.ocr_corrector import OCRCorrector
            from hashmm.pipeline.text_preprocessor import TextPreprocessor
            corrector = OCRCorrector()
            preprocessor = TextPreprocessor()
            for tb in table_blocks:
                tb.content = preprocessor.process(tb.content)
                tb.content = corrector.correct(tb.content)
        except Exception as _e:
            log_suppressed(logger, _e)

        # Group table blocks by page
        tables_by_page: dict[int, list[ContentBlock]] = {}
        for tb in table_blocks:
            p = tb.page
            if p not in tables_by_page:
                tables_by_page[p] = []
            tables_by_page[p].append(tb)

        # Insert table blocks into OCR blocks at the right page positions
        merged = 0
        insert_positions: list[tuple[int, ContentBlock]] = []

        for i, ocr_block in enumerate(ocr_blocks):
            page = ocr_block.page
            if page in tables_by_page:
                for tb in tables_by_page[page]:
                    tb.position = ocr_block.position + 1
                    insert_positions.append((i + 1, tb))
                    merged += 1
                del tables_by_page[page]

        # Insert in reverse order to preserve indices
        for idx, tb in reversed(insert_positions):
            ocr_blocks.insert(idx, tb)

        # Add remaining tables (pages that had no OCR text blocks)
        for page, tbs in tables_by_page.items():
            for tb in tbs:
                ocr_blocks.append(tb)
                merged += 1

        return merged

    @staticmethod
    def _table_to_markdown(rows: list[list]) -> str:
        if not rows:
            return ""
        max_cols = max(len(r) for r in rows)
        # Clean: replace newlines in cells, normalize to string
        norm = []
        for r in rows:
            row = []
            for c in (list(r) + [""] * (max_cols - len(r))):
                s = str(c) if c else ""
                s = s.replace("\n", " ").replace("\r", "").replace("|", "\\|").strip()
                row.append(s)
            norm.append(row)
        header = "| " + " | ".join(norm[0]) + " |"
        sep = "| " + " | ".join(["---"] * max_cols) + " |"
        body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
        return "\n".join([header, sep] + body)
