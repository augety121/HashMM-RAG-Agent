"""DOCX Builder v10.0 — Professional Word document generation.

Supports inline formatting (**bold**, `code`, *italic*), tables,
code blocks with shaded backgrounds, blockquotes, and CJK fonts.
"""
from __future__ import annotations
import re
from pathlib import Path


def build_docx(content: str, title: str, output_path: Path) -> str:
    """Build a professional Word document from Markdown content."""
    try:
        from docx import Document
        from docx.shared import Pt, Inches, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        # Fallback to plain .md
        output_path = output_path.with_suffix('.md')
        output_path.write_text(f"# {title}\n\n{content}", encoding="utf-8")
        return f"OK: python-docx 未安装，已创建 Markdown: {output_path.name}"

    doc = Document()

    # ── Page setup ──
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # ── v15: Cover page ──
    cover_p = doc.add_paragraph()
    cover_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for _ in range(6):  # Vertical spacing
        cover_p.add_run("\n")
    title_run = cover_p.add_run(title)
    title_run.font.size = Pt(36)
    title_run.bold = True
    title_run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)
    cover_p.add_run("\n\n")
    sub_run = cover_p.add_run("HashMM-RAG Agent 生成")
    sub_run.font.size = Pt(14)
    sub_run.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
    import datetime
    cover_p.add_run(f"\n{datetime.datetime.now().strftime('%Y年%m月%d日')}")
    doc.add_page_break()

    # ── v15: Table of Contents placeholder ──
    toc_heading = doc.add_heading("目录", level=1)
    toc_para = doc.add_paragraph()
    toc_para.add_run("（打开文档后右键此处 → 更新域 → 更新整个目录）")
    toc_para.runs[0].font.size = Pt(10)
    toc_para.runs[0].font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
    # Insert TOC field
    try:
        from docx.oxml import OxmlElement
        fld_char_begin = OxmlElement('w:fldChar')
        fld_char_begin.set(qn('w:fldCharType'), 'begin')
        instr = OxmlElement('w:instrText')
        instr.set(qn('xml:space'), 'preserve')
        instr.text = 'TOC \\o "1-3" \\h \\z \\u'
        fld_char_end = OxmlElement('w:fldChar')
        fld_char_end.set(qn('w:fldCharType'), 'end')
        r = toc_para.add_run()._r
        r.append(fld_char_begin)
        r2 = toc_para.add_run()._r
        r2.append(instr)
        r3 = toc_para.add_run()._r
        r3.append(fld_char_end)
    except Exception as _e:

        pass  # Silenced: see logs if needed
    doc.add_page_break()

    # ── v15: Header/Footer ──
    try:
        section = doc.sections[0]
        section.different_first_page_header_footer = True
        header = section.header
        hp = header.paragraphs[0]
        hp.text = title
        hp.style.font.size = Pt(9)
        hp.style.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    except Exception as _e:

        pass  # Silenced: see logs if needed

    # ── Default style ──
    style = doc.styles['Normal']
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.15
    try:
        style.font.name = 'Calibri'
        rPr = style.element.rPr
        if rPr is None:
            rPr = style.element.makeelement(qn('w:rPr'), {})
            style.element.append(rPr)
        rFonts = rPr.find(qn('w:rFonts'))
        if rFonts is None:
            rFonts = rPr.makeelement(qn('w:rFonts'), {})
            rPr.append(rFonts)
        rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    except Exception as _e:

        pass  # Silenced: see logs if needed
    if title:
        h = doc.add_heading(title, 0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in h.runs:
            run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    in_code = False
    code_buf = []
    code_lang = ""
    table_rows = []

    def _flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        ncols = max(len(r) for r in table_rows)
        t = doc.add_table(rows=len(table_rows), cols=ncols)
        t.style = 'Light Grid Accent 1'
        for ri, row in enumerate(table_rows):
            for ci, cell in enumerate(row):
                if ci < ncols:
                    t.cell(ri, ci).text = cell
                    for para in t.cell(ri, ci).paragraphs:
                        para.style.font.size = Pt(10)
                        if ri == 0:
                            for run in para.runs:
                                run.font.bold = True
        table_rows = []

    def _add_rich_para(text):
        """Add paragraph with inline **bold**, `code`, and *italic* support."""
        p = doc.add_paragraph()
        parts = re.split(r'(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)', text)
        for part in parts:
            if part.startswith('**') and part.endswith('**'):
                run = p.add_run(part[2:-2])
                run.font.bold = True
            elif part.startswith('`') and part.endswith('`'):
                run = p.add_run(part[1:-1])
                run.font.name = 'Consolas'
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x38, 0x6F, 0xEE)
            elif part.startswith('*') and part.endswith('*') and not part.startswith('**'):
                run = p.add_run(part[1:-1])
                run.font.italic = True
            elif part:
                p.add_run(part)

    for line in content.split("\n"):
        # Code block fence
        if line.startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(4)
                try:
                    shading = p._element.makeelement(qn('w:shd'), {
                        qn('w:fill'): 'F1F5F9', qn('w:val'): 'clear',
                    })
                    pPr = p._element.get_or_add_pPr()
                    pPr.append(shading)
                except Exception as _e:

                    pass  # Silenced: see logs if needed
                if code_lang:
                    lang_run = p.add_run(f"[{code_lang}]\n")
                    lang_run.font.size = Pt(8)
                    lang_run.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
                run = p.add_run("\n".join(code_buf))
                run.font.name = "Consolas"
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
                code_buf = []
                code_lang = ""
            else:
                code_lang = line[3:].strip()
            in_code = not in_code
            continue

        if in_code:
            code_buf.append(line)
            continue

        # Table
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(c.replace("-", "").replace(":", "") == "" for c in cells):
                table_rows.append(cells)
            continue
        elif table_rows:
            _flush_table()

        # Headings
        if line.startswith("#### "):
            doc.add_heading(line[5:], level=4)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        # Blockquote
        elif line.startswith("> "):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(1)
            run = p.add_run(line[2:])
            run.font.italic = True
            run.font.color.rgb = RGBColor(0x52, 0x52, 0x5B)
        # Horizontal rule
        elif re.match(r'^-{3,}$', line.strip()):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(8)
            run = p.add_run("─" * 50)
            run.font.size = Pt(6)
            run.font.color.rgb = RGBColor(0xD4, 0xD4, 0xD8)
        # Lists
        elif line.startswith("- ") or line.startswith("* "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif re.match(r"^\d+\.\s", line):
            doc.add_paragraph(re.sub(r"^\d+\.\s*", "", line), style="List Number")
        elif line.strip():
            _add_rich_para(line)

    _flush_table()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return f"OK: Word 文档 {output_path.name} 已创建。下载链接: /api/files/{output_path.name}"
