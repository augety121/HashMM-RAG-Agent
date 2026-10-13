"""PPTX Thumbnail Renderer — converts slides to PNG images for Artifact preview.

Approach: PPTX → PDF (via LibreOffice) → PNG (via pdf2image/Pillow).
Falls back to basic metadata extraction if LibreOffice not available.

Usage:
    from hashmm.api.doc_preview import render_pptx_thumbnails, get_pptx_metadata

    # Full thumbnails (requires LibreOffice)
    images = render_pptx_thumbnails("path/to/file.pptx", max_width=800)

    # Metadata only (always works)
    meta = get_pptx_metadata("path/to/file.pptx")
    # → {"pages": 8, "title": "...", "outline": ["Slide 1", ...]}
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.doc_preview")


def get_pptx_metadata(pptx_path: str) -> dict:
    """Extract metadata from PPTX without rendering.

    Always works — no external dependencies required.
    Returns: {"pages": int, "title": str, "outline": [str, ...], "width": int, "height": int}
    """
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
        outline = []
        for slide in prs.slides:
            title = ""
            for shape in slide.shapes:
                if shape.has_text_frame:
                    txt = shape.text_frame.text.strip()
                    if txt and len(txt) < 100:
                        title = txt
                        break
            outline.append(title or f"Slide {len(outline) + 1}")

        return {
            "pages": len(prs.slides),
            "title": outline[0] if outline else "",
            "outline": outline,
            "width": int(prs.slide_width),
            "height": int(prs.slide_height),
        }
    except Exception as e:
        logger.warning(f"PPTX metadata extraction failed: {e}")
        return {"pages": 0, "title": "", "outline": [], "width": 0, "height": 0}


def get_docx_metadata(docx_path: str) -> dict:
    """Extract metadata from DOCX."""
    try:
        from docx import Document
        doc = Document(docx_path)
        headings = []
        word_count = 0
        for para in doc.paragraphs:
            word_count += len(para.text.split())
            if para.style.name.startswith("Heading"):
                headings.append(para.text.strip())
        return {
            "headings": headings[:20],
            "word_count": word_count,
            "paragraphs": len(doc.paragraphs),
            "tables": len(doc.tables),
        }
    except Exception as e:
        logger.warning(f"DOCX metadata extraction failed: {e}")
        return {"headings": [], "word_count": 0, "paragraphs": 0, "tables": 0}


def render_pptx_thumbnails(
    pptx_path: str,
    output_dir: str | None = None,
    max_width: int = 800,
    dpi: int = 150,
) -> list[str]:
    """Render PPTX slides to PNG thumbnails.

    Requires: LibreOffice (soffice) + pdf2image (poppler-utils).
    Returns list of PNG file paths, or empty list if rendering fails.
    """
    pptx_path = str(Path(pptx_path).resolve())
    if not os.path.exists(pptx_path):
        return []

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="pptx_thumb_")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Step 1: Convert PPTX → PDF via LibreOffice
    pdf_path = _pptx_to_pdf(pptx_path, output_dir)
    if not pdf_path:
        return []

    # Step 2: Convert PDF → PNG images
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=dpi)
        png_paths = []
        for i, img in enumerate(images):
            # Resize to max_width
            w, h = img.size
            if w > max_width:
                ratio = max_width / w
                img = img.resize((max_width, int(h * ratio)))
            png_path = os.path.join(output_dir, f"slide_{i + 1:03d}.png")
            img.save(png_path, "PNG")
            png_paths.append(png_path)
        return png_paths
    except ImportError:
        logger.warning("pdf2image not installed — cannot render PPTX thumbnails")
        return []
    except Exception as e:
        logger.warning(f"PDF→PNG conversion failed: {e}")
        return []


def _pptx_to_pdf(pptx_path: str, output_dir: str) -> str | None:
    """Convert PPTX to PDF using LibreOffice."""
    try:
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", output_dir, pptx_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning(f"LibreOffice conversion failed: {result.stderr[:200]}")
            return None

        # Find the generated PDF
        base = Path(pptx_path).stem
        pdf_path = os.path.join(output_dir, f"{base}.pdf")
        if os.path.exists(pdf_path):
            return pdf_path
        return None
    except FileNotFoundError:
        logger.info("LibreOffice (soffice) not found — PPTX thumbnail rendering unavailable")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice conversion timed out")
        return None
    except Exception as e:
        logger.warning(f"PPTX→PDF conversion error: {e}")
        return None


def docx_to_html(docx_path: str) -> str:
    """Convert DOCX to styled HTML for in-browser preview.

    Uses python-docx to extract content + basic styling.
    Falls back to raw text if parsing fails.
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor

        doc = Document(docx_path)
        html_parts = [
            '<div style="font-family: system-ui, sans-serif; max-width: 700px; '
            'margin: 0 auto; padding: 24px; line-height: 1.7; color: #1a1a1a;">'
        ]

        for para in doc.paragraphs:
            style_name = para.style.name or ""
            text = para.text.strip()
            if not text:
                html_parts.append("<br/>")
                continue

            # Heading levels
            if style_name.startswith("Heading 1"):
                html_parts.append(f'<h1 style="font-size: 1.6em; font-weight: 700; '
                                  f'margin: 1em 0 0.3em; border-bottom: 2px solid #e5e7eb; '
                                  f'padding-bottom: 0.3em;">{_escape(text)}</h1>')
            elif style_name.startswith("Heading 2"):
                html_parts.append(f'<h2 style="font-size: 1.3em; font-weight: 600; '
                                  f'margin: 0.8em 0 0.2em;">{_escape(text)}</h2>')
            elif style_name.startswith("Heading 3"):
                html_parts.append(f'<h3 style="font-size: 1.1em; font-weight: 600; '
                                  f'margin: 0.6em 0 0.2em;">{_escape(text)}</h3>')
            elif "List" in style_name:
                html_parts.append(f'<li style="margin: 0.2em 0; margin-left: 1.5em;">{_escape(text)}</li>')
            else:
                # Regular paragraph — handle inline formatting
                inline_html = _render_runs(para)
                html_parts.append(f'<p style="margin: 0.4em 0;">{inline_html}</p>')

        # Tables
        for table in doc.tables:
            html_parts.append(
                '<table style="width: 100%; border-collapse: collapse; margin: 1em 0; '
                'font-size: 0.9em;">'
            )
            for i, row in enumerate(table.rows):
                html_parts.append("<tr>")
                tag = "th" if i == 0 else "td"
                style = ('style="padding: 8px 12px; border: 1px solid #e5e7eb; '
                        f'{"background: #f9fafb; font-weight: 600;" if i == 0 else ""}"')
                for cell in row.cells:
                    html_parts.append(f"<{tag} {style}>{_escape(cell.text)}</{tag}>")
                html_parts.append("</tr>")
            html_parts.append("</table>")

        html_parts.append("</div>")
        return "\n".join(html_parts)

    except ImportError:
        logger.warning("python-docx not available for HTML preview")
        return "<p>python-docx 未安装，无法预览 DOCX</p>"
    except Exception as e:
        logger.warning(f"DOCX→HTML conversion failed: {e}")
        return f"<p>预览失败: {str(e)[:100]}</p>"


def _escape(text: str) -> str:
    """HTML escape."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_runs(para) -> str:
    """Render paragraph runs with inline formatting (bold, italic, etc.)."""
    parts = []
    for run in para.runs:
        text = _escape(run.text)
        if not text:
            continue
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"
        if run.font and run.font.color and run.font.color.rgb:
            text = f'<span style="color: #{run.font.color.rgb};">{text}</span>'
        parts.append(text)
    return "".join(parts) or _escape(para.text)


def xlsx_to_json(xlsx_path: str, max_rows: int = 100) -> dict:
    """Convert XLSX to JSON for frontend table rendering.

    Returns: {"sheets": [{"name": "Sheet1", "headers": [...], "rows": [[...], ...], "total_rows": N}]}
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets[:5]:  # Max 5 sheets
            rows_data = []
            headers = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                row_vals = [str(c) if c is not None else "" for c in row]
                if i == 0:
                    headers = row_vals
                else:
                    rows_data.append(row_vals)
                if i >= max_rows:
                    break
            sheets.append({
                "name": ws.title,
                "headers": headers,
                "rows": rows_data[:max_rows],
                "total_rows": ws.max_row or 0,
                "total_cols": ws.max_column or 0,
            })
        wb.close()
        return {"sheets": sheets}
    except ImportError:
        return {"error": "openpyxl 未安装"}
    except Exception as e:
        return {"error": str(e)[:200]}
