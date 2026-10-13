"""PPT Editor v25 — modify existing PowerPoint files.

Can:
  - Change slide titles/content
  - Swap theme (recolor all slides)
  - Get slide-by-slide summary
  - Delete/reorder slides
"""
from __future__ import annotations
import re
from pathlib import Path


def get_pptx_summary(pptx_path: str) -> str:
    """Get a text summary of each slide."""
    try:
        from pptx import Presentation
    except ImportError:
        return "Error: python-pptx not installed"
    
    path = Path(pptx_path)
    if not path.exists():
        return f"Error: 文件不存在: {pptx_path}"
    
    prs = Presentation(str(path))
    lines = [f"PPT: {path.name} ({len(prs.slides)} 页)\n"]
    
    for i, slide in enumerate(prs.slides):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    if para.text.strip():
                        texts.append(para.text.strip()[:80])
        title = texts[0] if texts else "(无标题)"
        content_preview = "; ".join(texts[1:3]) if len(texts) > 1 else ""
        lines.append(f"  第 {i+1} 页: {title}")
        if content_preview:
            lines.append(f"          {content_preview[:100]}")
    
    return "\n".join(lines)


def edit_pptx_slide(pptx_path: str, slide_index: int, changes: dict) -> str:
    """Edit a specific slide's content."""
    try:
        from pptx import Presentation
        from pptx.util import Pt
    except ImportError:
        return "Error: python-pptx not installed"
    
    path = Path(pptx_path)
    if not path.exists():
        return f"Error: 文件不存在"
    
    prs = Presentation(str(path))
    if slide_index < 0 or slide_index >= len(prs.slides):
        return f"Error: 页码 {slide_index + 1} 超出范围 (共 {len(prs.slides)} 页)"
    
    slide = prs.slides[slide_index]
    modified = []
    
    # Change title
    if "title" in changes:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    if para.text.strip() and len(para.text) < 100:  # Likely a title
                        old_text = para.text
                        for run in para.runs:
                            run.text = ""
                        para.runs[0].text = changes["title"] if para.runs else changes["title"]
                        modified.append(f"标题: '{old_text}' → '{changes['title']}'")
                        break
                if modified:
                    break
    
    # Change bullets
    if "bullets" in changes:
        for shape in slide.shapes:
            if shape.has_text_frame and len(shape.text_frame.paragraphs) > 2:
                for j, new_text in enumerate(changes["bullets"]):
                    if j + 1 < len(shape.text_frame.paragraphs):
                        para = shape.text_frame.paragraphs[j + 1]  # Skip title
                        for run in para.runs:
                            run.text = ""
                        if para.runs:
                            para.runs[0].text = new_text
                        modified.append(f"要点 {j+1}: → '{new_text}'")
                break
    
    prs.save(str(path))
    return f"OK: 第 {slide_index + 1} 页已修改。" + ("; ".join(modified) if modified else "")


def delete_pptx_slide(pptx_path: str, slide_index: int) -> str:
    """Delete a slide by index."""
    try:
        from pptx import Presentation
    except ImportError:
        return "Error: python-pptx not installed"
    
    path = Path(pptx_path)
    prs = Presentation(str(path))
    
    if slide_index < 0 or slide_index >= len(prs.slides):
        return f"Error: 页码超出范围"
    
    rId = prs.slides._sldIdLst[slide_index].rId
    prs.part.drop_rel(rId)
    del prs.slides._sldIdLst[slide_index]
    
    prs.save(str(path))
    return f"OK: 已删除第 {slide_index + 1} 页（剩余 {len(prs.slides)} 页）"
