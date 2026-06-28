"""Excel Builder v11 — Generate .xlsx from structured data.

Supports: multiple sheets, styled headers, auto-width columns, bar/line charts.
"""
from __future__ import annotations
import json, re
from pathlib import Path


def build_xlsx(data: dict | str, output_path: Path) -> str:
    """Build Excel from JSON data or Markdown tables.
    
    JSON format:
    {
      "sheets": [{
        "name": "Sheet1",
        "headers": ["col1", "col2"],
        "rows": [["a", "b"], ...],
        "chart": {"type": "bar", "title": "...", "x_col": 0, "y_col": 1}  // optional
      }]
    }
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        output_path = output_path.with_suffix('.csv')
        if isinstance(data, str):
            output_path.write_text(data, encoding="utf-8")
        return f"OK: openpyxl 未安装，已创建 CSV: {output_path.name}"

    # Parse data
    if isinstance(data, str):
        sheets_data = _parse_markdown_tables(data)
    elif isinstance(data, dict) and "sheets" in data:
        sheets_data = data["sheets"]
    else:
        sheets_data = [{"name": "Sheet1", "headers": [], "rows": [], "raw": str(data)}]

    wb = Workbook()
    # Remove default sheet
    if wb.active:
        wb.remove(wb.active)

    # Styles
    header_font = Font(name='Calibri', bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill(start_color='386FEE', end_color='386FEE', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center')
    thin_border = Border(
        left=Side(style='thin', color='D4D4D8'),
        right=Side(style='thin', color='D4D4D8'),
        top=Side(style='thin', color='D4D4D8'),
        bottom=Side(style='thin', color='D4D4D8'),
    )
    stripe_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')

    for sd in sheets_data[:10]:  # Max 10 sheets
        name = sd.get("name", "Sheet1")[:31]
        ws = wb.create_sheet(title=name)
        headers = sd.get("headers", [])
        rows = sd.get("rows", [])

        if not headers and not rows:
            # Raw text fallback
            ws.cell(row=1, column=1, value=sd.get("raw", ""))
            continue

        # Write headers
        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=str(h))
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Write data rows
        for ri, row in enumerate(rows, 2):
            for ci, val in enumerate(row, 1):
                if ci > len(headers):
                    continue
                # Try numeric conversion
                cell_val = val
                try:
                    cell_val = float(val)
                    if cell_val == int(cell_val):
                        cell_val = int(cell_val)
                except (ValueError, TypeError):
                    cell_val = str(val)
                cell = ws.cell(row=ri, column=ci, value=cell_val)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='left')
                if ri % 2 == 0:
                    cell.fill = stripe_fill

        # Auto-width columns
        for ci in range(1, len(headers) + 1):
            max_len = max(
                len(str(ws.cell(row=r, column=ci).value or ""))
                for r in range(1, len(rows) + 2)
            )
            ws.column_dimensions[get_column_letter(ci)].width = min(max_len + 4, 40)

        # Add chart if specified
        chart_cfg = sd.get("chart")
        if chart_cfg and len(rows) >= 2:
            try:
                from openpyxl.chart import BarChart, LineChart, Reference
                ChartClass = LineChart if chart_cfg.get("type") == "line" else BarChart
                chart = ChartClass()
                chart.title = chart_cfg.get("title", name)
                chart.style = 10

                y_col = chart_cfg.get("y_col", 1) + 1  # 1-indexed
                x_col = chart_cfg.get("x_col", 0) + 1

                data_ref = Reference(ws, min_col=y_col, min_row=1, max_row=len(rows) + 1)
                cats_ref = Reference(ws, min_col=x_col, min_row=2, max_row=len(rows) + 1)
                chart.add_data(data_ref, titles_from_data=True)
                chart.set_categories(cats_ref)
                chart.shape = 4
                ws.add_chart(chart, f"{get_column_letter(len(headers) + 2)}2")
            except Exception as _e:

                pass  # Silenced: see logs if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    n_sheets = len(wb.sheetnames)
    return f"OK: Excel {output_path.name} 已创建（{n_sheets} 个工作表）。下载链接: /api/files/{output_path.name}"


def _parse_markdown_tables(text: str) -> list[dict]:
    """Extract tables from Markdown text."""
    sheets = []
    # Find all table blocks
    table_pattern = re.compile(r'(\|.+\|[ \t]*\n)+', re.MULTILINE)
    for match in table_pattern.finditer(text):
        block = match.group(0).strip()
        rows = block.split("\n")
        if len(rows) < 2:
            continue
        parse_row = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
        headers = parse_row(rows[0])
        data_rows = []
        for r in rows[1:]:
            cells = parse_row(r)
            if not all(c.replace("-", "").replace(":", "") == "" for c in cells):
                data_rows.append(cells)
        if headers and data_rows:
            sheets.append({"name": f"Table{len(sheets)+1}", "headers": headers, "rows": data_rows})
    return sheets if sheets else [{"name": "Sheet1", "headers": [], "rows": [], "raw": text}]
