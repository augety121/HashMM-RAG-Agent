"""PPT Builder v10.0 — Seven slide layout types.

Builds from structured JSON plan (from DocumentHandler)
or fallback from raw Markdown.

Layout types:
  cover, section, bullets, table, two_column, highlight,
  timeline, comparison, quote, image, end

Themes: blue, green, purple, red, dark, business
"""
from __future__ import annotations
from hashmm.utils import log_suppressed
import logging
logger = logging.getLogger(__name__)
import re, json
from pathlib import Path
from typing import Any


def build_pptx_from_plan(plan: dict, output_path: Path) -> str:
    """Build a PPTX from a structured JSON plan.

    Args:
        plan: {"title": str, "slides": [{"role": ..., ...}, ...]}
        output_path: Where to save the .pptx file
    Returns:
        Result message string
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
    except ImportError:
        return "Error: python-pptx not installed"

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # ── 6 Theme palettes ──
    THEMES = {
        # ── Light-first professional themes (cover uses deep tone, content is light) ──
        "business":  {"dark": (0x1B,0x2A,0x4A), "dark2": (0x2A,0x3F,0x66), "accent": (0x2D,0x6C,0xDF), "accent2": (0x5B,0x8F,0xF0)},
        "slate":     {"dark": (0x1E,0x29,0x3B), "dark2": (0x33,0x41,0x55), "accent": (0x0E,0x7A,0x6B), "accent2": (0x2D,0xB3,0x9A)},
        "navy":      {"dark": (0x0F,0x2C,0x52), "dark2": (0x1B,0x3F,0x6E), "accent": (0xE8,0x8A,0x1A), "accent2": (0xF5,0xB0,0x4A)},
        "burgundy":  {"dark": (0x3A,0x16,0x22), "dark2": (0x5A,0x24,0x35), "accent": (0xB0,0x3A,0x52), "accent2": (0xD9,0x6A,0x80)},
        # ── v17: 现代高级配色（克制、有温度的单 accent，对齐当代设计审美）──
        "ink":       {"dark": (0x1A,0x1A,0x1A), "dark2": (0x2D,0x2D,0x2D), "accent": (0xC0,0x4A,0x1A), "accent2": (0xE0,0x7A,0x4A)},
        "forest":    {"dark": (0x14,0x2A,0x22), "dark2": (0x22,0x3D,0x33), "accent": (0x2F,0x6B,0x4F), "accent2": (0x5A,0x9B,0x7A)},
        "midnight":  {"dark": (0x12,0x1A,0x2E), "dark2": (0x1E,0x2A,0x45), "accent": (0x4F,0x6B,0xED), "accent2": (0x82,0x9B,0xF5)},
        "warmgray":  {"dark": (0x2B,0x28,0x24), "dark2": (0x44,0x3F,0x39), "accent": (0xB5,0x82,0x3A), "accent2": (0xD4,0xA8,0x5F)},
        # ── Legacy themes kept for back-compat (some are dark-cover) ──
        "blue":   {"dark": (0x0F,0x17,0x2A), "dark2": (0x1E,0x29,0x3B), "accent": (0x38,0x6F,0xEE), "accent2": (0x60,0xA5,0xFA)},
        "green":  {"dark": (0x06,0x2E,0x16), "dark2": (0x14,0x53,0x2D), "accent": (0x22,0xC5,0x5E), "accent2": (0x4A,0xDE,0x80)},
        "purple": {"dark": (0x1E,0x1B,0x3A), "dark2": (0x2E,0x27,0x5F), "accent": (0x7C,0x3A,0xED), "accent2": (0xA7,0x8B,0xFA)},
        "red":    {"dark": (0x2A,0x0F,0x0F), "dark2": (0x45,0x1A,0x1A), "accent": (0xEF,0x44,0x44), "accent2": (0xF8,0x71,0x71)},
        "dark":   {"dark": (0x11,0x11,0x11), "dark2": (0x1F,0x1F,0x1F), "accent": (0x60,0xA5,0xFA), "accent2": (0x93,0xC5,0xFD)},
    }
    theme_name = plan.get("theme", "business")
    if theme_name not in THEMES:
        theme_name = "business"
    t = THEMES[theme_name]
    C = {
        "dark":    RGBColor(*t["dark"]),
        "dark2":   RGBColor(*t["dark2"]),
        "accent":  RGBColor(*t["accent"]),
        "accent2": RGBColor(*t["accent2"]),
        "title":   RGBColor(*t["dark"]),
        "body":    RGBColor(0x33, 0x3D, 0x4F),
        "subtle":  RGBColor(0x94, 0xA3, 0xB8),
        "white":   RGBColor(0xFF, 0xFF, 0xFF),
        "bg":      RGBColor(0xF8, 0xFA, 0xFC),
        "stripe":  RGBColor(0xF1, 0xF5, 0xF9),
        "highlight_bg": RGBColor(0xEF, 0xF6, 0xFF),
    }

    page_num = [0]

    def _bg(slide, color=None):
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = color or C["bg"]

    def _page_num(slide):
        page_num[0] += 1
        pn = slide.shapes.add_textbox(Inches(12.3), Inches(7.0), Inches(0.9), Inches(0.3))
        p = pn.text_frame.paragraphs[0]
        p.text = str(page_num[0])
        p.font.size = Pt(10); p.font.color.rgb = C["subtle"]
        p.alignment = PP_ALIGN.RIGHT

    def _bottom_bar(slide):
        bar = slide.shapes.add_shape(1, Inches(0), Inches(7.35), Inches(13.333), Inches(0.03))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()

    def _add_text(tf, text, size=15, color=None, bold=False, space_after=6):
        """Add a paragraph with inline **bold** support."""
        color = color or C["body"]
        parts = re.split(r'(\*\*[^*]+\*\*)', text)
        p = tf.add_paragraph() if tf.paragraphs[0].text else tf.paragraphs[0]
        for part in parts:
            if part.startswith('**') and part.endswith('**'):
                r = p.add_run()
                r.text = part[2:-2]
                r.font.bold = True; r.font.size = Pt(size); r.font.color.rgb = C["title"]
            elif part.strip():
                r = p.add_run()
                r.text = part
                r.font.size = Pt(size); r.font.color.rgb = color
                if bold: r.font.bold = True
        p.space_after = Pt(space_after)
        return p

    # ═══ Slide builders ═══

    def build_cover(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s, C["dark"])
        # Top accent
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(13.333), Inches(0.06))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        # Decorative circle
        circ = s.shapes.add_shape(9, Inches(10), Inches(0.8), Inches(4.5), Inches(4.5))
        circ.fill.solid(); circ.fill.fore_color.rgb = C["dark2"]; circ.line.fill.background()
        # Title
        tx = s.shapes.add_textbox(Inches(1.2), Inches(2.0), Inches(9), Inches(2.5))
        p = tx.text_frame.paragraphs[0]
        p.text = sd.get("title", "Presentation")
        p.font.size = Pt(42); p.font.bold = True; p.font.color.rgb = C["white"]
        # Subtitle
        sub = sd.get("subtitle", "")
        if sub:
            p2 = tx.text_frame.add_paragraph()
            p2.text = sub; p2.font.size = Pt(18); p2.font.color.rgb = C["accent2"]
            p2.space_before = Pt(20)
        # Accent line
        bar2 = s.shapes.add_shape(1, Inches(1.2), Inches(5.0), Inches(4), Inches(0.04))
        bar2.fill.solid(); bar2.fill.fore_color.rgb = C["accent"]; bar2.line.fill.background()

    def build_section(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s, C["dark"])
        page_num[0] += 1
        tx = s.shapes.add_textbox(Inches(1.2), Inches(2.8), Inches(10), Inches(1.5))
        p = tx.text_frame.paragraphs[0]
        p.text = sd.get("title", "")
        p.font.size = Pt(36); p.font.bold = True; p.font.color.rgb = C["white"]
        bar = s.shapes.add_shape(1, Inches(1.2), Inches(4.6), Inches(3), Inches(0.04))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()

    def build_bullets(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s)
        # Left accent bar
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.08), Inches(7.5))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        # Title
        tx = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.5), Inches(0.9))
        p = tx.text_frame.paragraphs[0]
        p.text = sd.get("title", ""); p.font.size = Pt(28); p.font.bold = True
        p.font.color.rgb = C["title"]
        # Underline
        ul = s.shapes.add_shape(1, Inches(0.8), Inches(1.4), Inches(2.5), Inches(0.045))
        ul.fill.solid(); ul.fill.fore_color.rgb = C["accent"]; ul.line.fill.background()

        bullets = [b for b in sd.get("bullets", []) if str(b).strip()][:8]
        n = len(bullets)
        # Distribute bullets vertically to fill the slide instead of cramming
        # them at the top. Each bullet gets a card-like row with a marker dot.
        top = 1.95
        avail = 5.0
        row_h = max(0.62, min(1.05, avail / max(n, 1)))
        gap = (avail - row_h * n) / max(n, 1) if n else 0
        font_sz = 20 if n <= 5 else 17

        y = top
        for b in bullets:
            # Marker dot
            dot = s.shapes.add_shape(9, Inches(0.85), Inches(y + row_h/2 - 0.07),
                                     Inches(0.14), Inches(0.14))
            dot.fill.solid(); dot.fill.fore_color.rgb = C["accent"]; dot.line.fill.background()
            # Text box
            tb = s.shapes.add_textbox(Inches(1.2), Inches(y), Inches(11.0), Inches(row_h))
            tf = tb.text_frame; tf.word_wrap = True
            try:
                from pptx.enum.text import MSO_ANCHOR
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception as _e:
                log_suppressed(logger, _e)
            _add_text(tf, str(b), size=font_sz, space_after=0)
            y += row_h + gap

        _page_num(s); _bottom_bar(s)

    def build_table(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s)
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.08), Inches(7.5))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        # Title
        tx = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.5), Inches(0.9))
        p = tx.text_frame.paragraphs[0]
        p.text = sd.get("title", ""); p.font.size = Pt(26); p.font.bold = True
        p.font.color.rgb = C["title"]
        # Table
        headers = sd.get("headers", [])
        rows = sd.get("rows", [])
        if headers and rows:
            ncols = len(headers)
            nrows = len(rows) + 1  # +1 for header
            tbl_h = min(Inches(0.38 * nrows), Inches(5.5))
            tbl = s.shapes.add_table(nrows, ncols,
                Inches(0.8), Inches(1.7), Inches(11.5), tbl_h).table
            # Header
            for ci, h in enumerate(headers):
                if ci < ncols:
                    tbl.cell(0, ci).text = str(h)
                    for para in tbl.cell(0, ci).text_frame.paragraphs:
                        para.font.size = Pt(12); para.font.bold = True
                        para.font.color.rgb = C["white"]
                    tbl.cell(0, ci).fill.solid()
                    tbl.cell(0, ci).fill.fore_color.rgb = C["accent"]
            # Rows
            for ri, row in enumerate(rows):
                for ci, cell in enumerate(row):
                    if ci < ncols:
                        tbl.cell(ri+1, ci).text = str(cell)
                        for para in tbl.cell(ri+1, ci).text_frame.paragraphs:
                            para.font.size = Pt(12)
                            para.font.color.rgb = C["body"]
                        if ri % 2 == 1:
                            tbl.cell(ri+1, ci).fill.solid()
                            tbl.cell(ri+1, ci).fill.fore_color.rgb = C["stripe"]
        _page_num(s); _bottom_bar(s)

    def build_two_column(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s)
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.08), Inches(7.5))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        # Title
        tx = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.5), Inches(0.9))
        p = tx.text_frame.paragraphs[0]
        p.text = sd.get("title", ""); p.font.size = Pt(26); p.font.bold = True
        p.font.color.rgb = C["title"]
        ul = s.shapes.add_shape(1, Inches(0.8), Inches(1.35), Inches(2.5), Inches(0.03))
        ul.fill.solid(); ul.fill.fore_color.rgb = C["accent"]; ul.line.fill.background()
        # Left column
        left = sd.get("left", {})
        lbox = s.shapes.add_textbox(Inches(0.8), Inches(1.7), Inches(5.5), Inches(5.0))
        ltf = lbox.text_frame; ltf.word_wrap = True
        if left.get("heading"):
            _add_text(ltf, left["heading"], size=18, bold=True, color=C["accent"])
        for b in left.get("bullets", [])[:6]:
            _add_text(ltf, b, size=14)
        # Divider
        div = s.shapes.add_shape(1, Inches(6.5), Inches(1.7), Inches(0.02), Inches(5.0))
        div.fill.solid(); div.fill.fore_color.rgb = C["subtle"]; div.line.fill.background()
        # Right column
        right = sd.get("right", {})
        rbox = s.shapes.add_textbox(Inches(6.8), Inches(1.7), Inches(5.5), Inches(5.0))
        rtf = rbox.text_frame; rtf.word_wrap = True
        if right.get("heading"):
            _add_text(rtf, right["heading"], size=18, bold=True, color=C["accent"])
        for b in right.get("bullets", [])[:6]:
            _add_text(rtf, b, size=14)
        _page_num(s); _bottom_bar(s)

    def build_highlight(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s, C["highlight_bg"])
        # Centered highlight text
        tx = s.shapes.add_textbox(Inches(1.5), Inches(2.0), Inches(10.333), Inches(2.5))
        tf = tx.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = sd.get("title", "")
        p.font.size = Pt(32); p.font.bold = True; p.font.color.rgb = C["title"]
        p.alignment = PP_ALIGN.CENTER
        if sd.get("text"):
            p2 = tf.add_paragraph()
            p2.text = sd["text"]
            p2.font.size = Pt(18); p2.font.color.rgb = C["body"]
            p2.alignment = PP_ALIGN.CENTER; p2.space_before = Pt(20)
        # Bottom accent
        bar = s.shapes.add_shape(1, Inches(5), Inches(5.2), Inches(3.333), Inches(0.04))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        _page_num(s)

    def build_timeline(sd):
        """Timeline slide — sequence of events/steps."""
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s); _page_num(s); _bottom_bar(s)
        # Title
        ttl = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(0.7))
        _add_text(ttl.text_frame, sd.get("title", ""), size=26, color=C["title"], bold=True)
        # Timeline items
        items = sd.get("items", sd.get("bullets", []))[:6]
        spacing = min(11.0 / max(len(items), 1), 2.0)
        for i, item in enumerate(items):
            x = Inches(0.8 + i * spacing)
            # Circle marker
            shp = s.shapes.add_shape(9, x, Inches(2.5), Inches(0.4), Inches(0.4))  # Oval
            shp.fill.solid(); shp.fill.fore_color.rgb = C["accent"]; shp.line.fill.background()
            num = shp.text_frame.paragraphs[0]
            num.text = str(i + 1); num.font.size = Pt(14); num.font.color.rgb = C["white"]
            num.font.bold = True; num.alignment = PP_ALIGN.CENTER
            # Line
            if i < len(items) - 1:
                ln = s.shapes.add_shape(1, Inches(1.2 + i * spacing), Inches(2.65), Inches(spacing - 0.5), Inches(0.03))
                ln.fill.solid(); ln.fill.fore_color.rgb = C["accent2"]; ln.line.fill.background()
            # Label
            lbl = s.shapes.add_textbox(x - Inches(0.3), Inches(3.2), Inches(spacing), Inches(2.5))
            lbl.text_frame.word_wrap = True
            txt = item if isinstance(item, str) else item.get("text", str(item))
            _add_text(lbl.text_frame, txt, size=13, color=C["body"])

    def build_comparison(sd):
        """Side-by-side comparison card."""
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s); _page_num(s); _bottom_bar(s)
        ttl = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(0.7))
        _add_text(ttl.text_frame, sd.get("title", ""), size=26, color=C["title"], bold=True)
        # Left card
        left = sd.get("left", {})
        lbox = s.shapes.add_shape(1, Inches(0.8), Inches(1.6), Inches(5.5), Inches(5.0))
        lbox.fill.solid(); lbox.fill.fore_color.rgb = C["highlight_bg"]; lbox.line.fill.background()
        lt = s.shapes.add_textbox(Inches(1.2), Inches(1.8), Inches(4.7), Inches(0.5))
        _add_text(lt.text_frame, left.get("heading", "A"), size=20, color=C["accent"], bold=True)
        for j, b in enumerate((left.get("bullets", []))[:5]):
            bt = s.shapes.add_textbox(Inches(1.2), Inches(2.6 + j * 0.7), Inches(4.7), Inches(0.6))
            bt.text_frame.word_wrap = True
            _add_text(bt.text_frame, f"• {b}", size=14, color=C["body"])
        # Right card
        right = sd.get("right", {})
        rbox = s.shapes.add_shape(1, Inches(7.0), Inches(1.6), Inches(5.5), Inches(5.0))
        rbox.fill.solid(); rbox.fill.fore_color.rgb = RGBColor(0xFF, 0xF7, 0xED); rbox.line.fill.background()
        rt = s.shapes.add_textbox(Inches(7.4), Inches(1.8), Inches(4.7), Inches(0.5))
        _add_text(rt.text_frame, right.get("heading", "B"), size=20, color=C["accent2"], bold=True)
        for j, b in enumerate((right.get("bullets", []))[:5]):
            bt = s.shapes.add_textbox(Inches(7.4), Inches(2.6 + j * 0.7), Inches(4.7), Inches(0.6))
            bt.text_frame.word_wrap = True
            _add_text(bt.text_frame, f"• {b}", size=14, color=C["body"])

    def build_quote(sd):
        """Quote/highlight slide — large centered text."""
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s, C["dark"]); _page_num(s)
        # Large quote mark
        q = s.shapes.add_textbox(Inches(1.5), Inches(1.0), Inches(2), Inches(2))
        qp = q.text_frame.paragraphs[0]
        qp.text = "❝"; qp.font.size = Pt(120); qp.font.color.rgb = C["accent"]
        # Quote text
        qt = s.shapes.add_textbox(Inches(2.0), Inches(2.5), Inches(9.5), Inches(3.0))
        qt.text_frame.word_wrap = True
        _add_text(qt.text_frame, sd.get("text", sd.get("title", "")), size=28, color=C["white"], bold=True, space_after=12)
        # Source
        if sd.get("source"):
            src = s.shapes.add_textbox(Inches(2.0), Inches(5.5), Inches(9.5), Inches(0.5))
            _add_text(src.text_frame, f"— {sd['source']}", size=16, color=C["accent2"])

    def build_chart(sd):
        """Real chart slide — bar/line/pie/area with actual data."""
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE

        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s); _page_num(s); _bottom_bar(s)

        # Left accent bar
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(0.08), Inches(7.5))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()

        # Title
        ttl = s.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.7))
        _add_text(ttl.text_frame, sd.get("title", "图表"), size=26, color=C["title"], bold=True)

        # Chart type mapping
        chart_type_map = {
            "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS,
            "pie": XL_CHART_TYPE.PIE,
            "area": XL_CHART_TYPE.AREA,
            "stacked_bar": XL_CHART_TYPE.COLUMN_STACKED,
        }
        ct = chart_type_map.get(sd.get("chart_type", "bar"), XL_CHART_TYPE.COLUMN_CLUSTERED)

        # Build chart data
        chart_data = CategoryChartData()
        labels = sd.get("labels", sd.get("categories", ["A", "B", "C"]))
        chart_data.categories = labels

        # Support both single series (values) and multi-series (series array)
        series_list = sd.get("series", None)
        if series_list and isinstance(series_list, list):
            for s_data in series_list:
                if isinstance(s_data, dict):
                    chart_data.add_series(s_data.get("name", "数据"), s_data.get("values", [0] * len(labels)))
                elif isinstance(s_data, (list, tuple)):
                    chart_data.add_series(f"系列{series_list.index(s_data)+1}", list(s_data))
        else:
            values = sd.get("values", sd.get("data", [0] * len(labels)))
            if isinstance(values, dict):
                # {"labels": [...], "values": [...]} shorthand
                chart_data = CategoryChartData()
                chart_data.categories = values.get("labels", labels)
                chart_data.add_series("数据", values.get("values", [0]))
            else:
                chart_data.add_series(sd.get("series_name", "数据"), values)

        # Position: full width below title
        chart_shape = s.shapes.add_chart(
            ct, Inches(1.0), Inches(1.5), Inches(11.3), Inches(5.3), chart_data
        )
        chart = chart_shape.chart

        is_pie = sd.get("chart_type", "bar") == "pie"

        # Hide the auto chart title (we already have a slide title above)
        try:
            chart.has_title = False
        except Exception as _e:
            log_suppressed(logger, _e)

        # Legend: always show for pie (slices need labels) and multi-series
        chart.has_legend = is_pie or (len(chart_data._series) > 1)
        if chart.has_legend:
            from pptx.enum.chart import XL_LEGEND_POSITION
            chart.legend.include_in_layout = False
            chart.legend.position = XL_LEGEND_POSITION.RIGHT if is_pie else XL_LEGEND_POSITION.BOTTOM
            chart.legend.font.size = Pt(12)
            chart.legend.font.color.rgb = C["body"]

        # Data labels — show values (and percentages for pie)
        try:
            plot0 = chart.plots[0]
            plot0.has_data_labels = True
            dl = plot0.data_labels
            dl.font.size = Pt(11)
            dl.font.color.rgb = C["title"]
            if is_pie:
                dl.show_percentage = True
                dl.show_value = False
                dl.number_format = '0.0%'
                dl.number_format_is_linked = False
            else:
                dl.show_value = True
        except Exception as _e:
            log_suppressed(logger, _e)

        # Color the series
        try:
            plot = chart.plots[0]
            if hasattr(plot, 'gap_width'):
                plot.gap_width = 60
            accent_colors = [C["accent"], C["accent2"], RGBColor(0x22, 0xC5, 0x5E),
                             RGBColor(0xF5, 0x9E, 0x0B), RGBColor(0xB0, 0x3A, 0x52)]
            if is_pie:
                # Pie: color each point (slice) differently
                pts = plot.series[0].points
                for i, pt in enumerate(pts):
                    pt.format.fill.solid()
                    pt.format.fill.fore_color.rgb = accent_colors[i % len(accent_colors)]
            else:
                for i, series in enumerate(plot.series):
                    fill = series.format.fill
                    fill.solid()
                    fill.fore_color.rgb = accent_colors[i % len(accent_colors)]
        except Exception as _e:
            log_suppressed(logger, _e)

        # Subtitle/note below chart
        if sd.get("note"):
            note = s.shapes.add_textbox(Inches(1.0), Inches(6.9), Inches(11.3), Inches(0.4))
            _add_text(note.text_frame, sd["note"], size=10, color=C["subtle"])

    def build_image(sd):
        """Image placeholder slide (for diagrams/photos not yet generated)."""
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s); _page_num(s); _bottom_bar(s)
        ttl = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(0.7))
        _add_text(ttl.text_frame, sd.get("title", ""), size=26, color=C["title"], bold=True)
        box = s.shapes.add_shape(1, Inches(1.5), Inches(1.8), Inches(10.3), Inches(5.0))
        box.fill.solid(); box.fill.fore_color.rgb = C["stripe"]; box.line.fill.background()
        ctr = s.shapes.add_textbox(Inches(4.0), Inches(3.5), Inches(5.0), Inches(1.0))
        _add_text(ctr.text_frame, sd.get("caption", "图表/图片区域"), size=16, color=C["subtle"])

    def build_end(sd):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _bg(s, C["dark"])
        bar = s.shapes.add_shape(1, Inches(0), Inches(0), Inches(13.333), Inches(0.06))
        bar.fill.solid(); bar.fill.fore_color.rgb = C["accent"]; bar.line.fill.background()
        tx = s.shapes.add_textbox(Inches(2), Inches(2.3), Inches(9), Inches(2))
        p = tx.text_frame.paragraphs[0]
        p.text = "Thank You"; p.font.size = Pt(48); p.font.bold = True
        p.font.color.rgb = C["white"]; p.alignment = PP_ALIGN.CENTER
        p2 = tx.text_frame.add_paragraph()
        p2.text = "Questions & Discussion"; p2.font.size = Pt(20)
        p2.font.color.rgb = C["accent2"]; p2.alignment = PP_ALIGN.CENTER
        p2.space_before = Pt(16)
        bar2 = s.shapes.add_shape(1, Inches(5), Inches(5.0), Inches(3.333), Inches(0.04))
        bar2.fill.solid(); bar2.fill.fore_color.rgb = C["accent"]; bar2.line.fill.background()

    # ═══ Build slides from plan ═══
    builders = {
        "cover": build_cover,
        "section": build_section,
        "bullets": build_bullets,
        "table": build_table,
        "two_column": build_two_column,
        "highlight": build_highlight,
        "timeline": build_timeline,
        "comparison": build_comparison,
        "quote": build_quote,
        "image": build_image,
        "chart": build_chart,       # v11: real charts with data
        "end": build_end,
    }

    slides = plan.get("slides", [])
    if not slides:
        return "Error: plan has no slides"

    for sd in slides:
        role = sd.get("role", "bullets")
        builder = builders.get(role, build_bullets)
        try:
            builder(sd)
        except Exception as e:
            # Skip broken slides, don't crash
            logger.warning(f"Slide build error ({role}): {e}")
            continue

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return f"OK: PPT {output_path.name} 已创建（{len(prs.slides)} 页）。下载链接: /api/files/{output_path.name}"


def build_pptx_from_markdown(content: str, title: str, output_path: Path) -> str:
    """Fallback: build PPTX from raw Markdown (when JSON plan fails)."""
    # Convert Markdown to a simple plan
    plan = {"title": title, "slides": []}
    plan["slides"].append({"role": "cover", "title": title})

    sections = re.split(r'^#\s+', content, flags=re.MULTILINE)
    for sec in sections:
        if not sec.strip():
            continue
        lines = sec.strip().split("\n")
        sec_title = lines[0].strip()
        rest = "\n".join(lines[1:])

        subsections = re.split(r'^##\s+', rest, flags=re.MULTILINE)
        has_content = any(s.strip() for s in subsections)
        if has_content:
            plan["slides"].append({"role": "section", "title": sec_title})

        for sub in subsections:
            if not sub.strip():
                continue
            sub_lines = sub.strip().split("\n")
            heading = sub_lines[0].strip()
            body = [l for l in sub_lines[1:] if l.strip() and not l.strip().startswith("```")]

            # Detect table
            table_lines = [l for l in body if "|" in l and l.strip().startswith("|")]
            if table_lines:
                _pr = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]
                rows_data = [_pr(l) for l in table_lines
                            if not all(c.replace("-","").replace(":","") == "" for c in _pr(l))]
                if len(rows_data) >= 2:
                    plan["slides"].append({
                        "role": "table",
                        "title": heading,
                        "headers": rows_data[0],
                        "rows": rows_data[1:]
                    })
                    # Also add non-table bullets if any
                    non_table = [l for l in body if not ("|" in l and l.strip().startswith("|"))]
                    non_table = [re.sub(r'^[\-\*•]\s*', '', l).strip() for l in non_table if l.strip()]
                    if non_table:
                        plan["slides"].append({"role": "bullets", "title": heading, "bullets": non_table})
                    continue

            # Normal bullets
            bullets = [re.sub(r'^[\-\*•]\s*', '', l).strip() for l in body if l.strip()]
            if bullets:
                plan["slides"].append({"role": "bullets", "title": heading, "bullets": bullets})

    plan["slides"].append({"role": "end"})
    return build_pptx_from_plan(plan, output_path)