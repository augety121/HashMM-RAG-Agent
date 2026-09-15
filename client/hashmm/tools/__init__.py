"""DocumentGenerator — unified two-step document creation.

Step 1: LLM generates structured JSON outline
Step 2: Renderer converts JSON → real .pptx/.docx/.xlsx file

This replaces the old approach of outputting markdown text.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.tools.doc_generator")

FILES_DIR = Path("data/files")
FILES_DIR.mkdir(parents=True, exist_ok=True)


# ── JSON outline schemas for LLM prompts ──

PPTX_SCHEMA_PROMPT = """你是专业的PPT大纲生成器。根据用户需求生成结构化JSON。

输出格式（只返回JSON，不要其他文字）：
{
  "title": "PPT标题",
  "theme": "business",
  "slides": [
    {"role": "cover", "title": "封面标题", "subtitle": "副标题"},
    {"role": "section", "title": "章节标题"},
    {"role": "highlight", "title": "关键数字", "number": "4573亿", "label": "2025年总营收，同比+25%"},
    {"role": "chart", "title": "营收结构", "chart_type": "bar", "labels": ["手机","IoT","互联网","汽车"], "values": [1918,1041,341,328], "note": "单位：亿元"},
    {"role": "chart", "title": "营收趋势", "chart_type": "line", "labels": ["2021","2022","2023","2024","2025"], "series": [{"name":"总营收","values":[3283,2800,2710,3659,4573]}]},
    {"role": "chart", "title": "业务占比", "chart_type": "pie", "labels": ["手机×AIoT","汽车及AI"], "values": [76.8, 23.2]},
    {"role": "bullets", "title": "页面标题", "bullets": ["要点1", "要点2", "要点3"]},
    {"role": "table", "title": "数据表", "headers": ["指标","2024","2025"], "rows": [["营收","3659","4573"]]},
    {"role": "two_column", "title": "对比", "left_title": "左栏", "left_bullets": ["..."], "right_title": "右栏", "right_bullets": ["..."]},
    {"role": "comparison", "title": "对比分析", "left": {"heading": "A", "bullets": [...]}, "right": {"heading": "B", "bullets": [...]}},
    {"role": "timeline", "title": "时间线", "items": ["2020: 事件1", "2021: 事件2"]},
    {"role": "quote", "text": "引用内容", "source": "来源"},
    {"role": "end"}
  ]
}

可用角色: cover, section, highlight, chart, bullets, table, two_column, comparison, timeline, quote, image, end
chart_type 可选: bar(柱状), line(折线), pie(饼图), area(面积), stacked_bar(堆叠柱)
可用主题: business(推荐,商务浅色), slate, navy, burgundy

【重要排版要求】
1. 这是给人看的演示文稿，不是文字稿。优先用【图表(chart)】和【关键数字(highlight)】来呈现数据，而不是堆文字。
2. 只要有数值数据（营收、增长率、占比、对比），【必须】用 chart 可视化——至少包含 2-3 张图表。
3. bullets 每页最多 5 条，每条简短（≤25字），不要写成长段落。
4. 结构建议：封面 → 关键数字highlight → 趋势chart → 结构chart → 数据table → 分析bullets → 总结 → end。
5. 建议10-14页，每页只讲一个要点。"""

DOCX_SCHEMA_PROMPT = """你是专业的文档大纲生成器。根据用户需求生成结构化JSON。

输出格式（只返回JSON，不要其他文字）：
{
  "title": "文档标题",
  "style": "report",
  "sections": [
    {
      "heading": "一级标题",
      "level": 1,
      "content": "段落正文...",
      "subsections": [
        {"heading": "二级标题", "level": 2, "content": "段落正文..."}
      ]
    }
  ],
  "metadata": {"author": "", "date": ""}
}

可用style: report, memo, paper
内容要专业、有深度。"""


class DocumentGenerator:
    """Two-step document generator: LLM outline → file render."""

    def __init__(self, llm_fn: Any = None):
        self.llm_fn = llm_fn

    def generate_pptx(
        self,
        requirements: str,
        data_context: str = "",
        theme: str = "business",
    ) -> dict:
        """Generate a PPTX file from requirements.

        Returns:
            {"ok": bool, "path": str, "filename": str, "slides": int, "message": str}
        """
        # Step 1: Generate structured outline via LLM
        outline = self._generate_outline(
            schema_prompt=PPTX_SCHEMA_PROMPT,
            requirements=requirements,
            data_context=data_context,
            extra=f"主题: {theme}",
        )
        if not outline:
            return {"ok": False, "message": "无法生成PPT大纲"}

        if "theme" not in outline:
            outline["theme"] = theme

        # Step 2: Render PPTX
        filename = f"ppt_{uuid.uuid4().hex[:8]}.pptx"
        output_path = FILES_DIR / filename
        try:
            from hashmm.api.pptx_builder import build_pptx_from_plan
            result = build_pptx_from_plan(outline, output_path)
            if result.startswith("OK"):
                n_slides = len(outline.get("slides", []))
                return {
                    "ok": True,
                    "path": str(output_path),
                    "filename": filename,
                    "slides": n_slides,
                    "download_url": f"/api/files/{filename}",
                    "message": f"PPT 已创建（{n_slides} 页），主题: {theme}",
                }
            return {"ok": False, "message": result}
        except Exception as e:
            logger.error(f"PPTX render error: {e}", exc_info=True)
            return {"ok": False, "message": f"PPT 渲染失败: {str(e)[:100]}"}

    def generate_docx(
        self,
        requirements: str,
        data_context: str = "",
        style: str = "report",
    ) -> dict:
        """Generate a DOCX file from requirements."""
        outline = self._generate_outline(
            schema_prompt=DOCX_SCHEMA_PROMPT,
            requirements=requirements,
            data_context=data_context,
            extra=f"文档风格: {style}",
        )
        if not outline:
            return {"ok": False, "message": "无法生成文档大纲"}

        filename = f"doc_{uuid.uuid4().hex[:8]}.docx"
        output_path = FILES_DIR / filename
        try:
            from hashmm.api.docx_builder import build_docx_from_plan
            result = build_docx_from_plan(outline, output_path)
            if "OK" in result or output_path.exists():
                return {
                    "ok": True,
                    "path": str(output_path),
                    "filename": filename,
                    "download_url": f"/api/files/{filename}",
                    "message": f"文档 已创建",
                }
            return {"ok": False, "message": result}
        except Exception as e:
            logger.error(f"DOCX render error: {e}", exc_info=True)
            return {"ok": False, "message": f"文档渲染失败: {str(e)[:100]}"}

    def generate_xlsx(
        self,
        requirements: str,
        data_context: str = "",
    ) -> dict:
        """Generate an XLSX file from requirements."""
        # For XLSX, LLM generates the data structure directly
        outline = self._generate_outline(
            schema_prompt=(
                "生成Excel数据的JSON。格式：\n"
                '{"title": "表名", "headers": ["列1","列2"], '
                '"rows": [["值1","值2"]], "chart_type": "bar"}\n'
                "只返回JSON。"
            ),
            requirements=requirements,
            data_context=data_context,
        )
        if not outline:
            return {"ok": False, "message": "无法生成表格数据"}

        filename = f"xlsx_{uuid.uuid4().hex[:8]}.xlsx"
        output_path = FILES_DIR / filename
        try:
            from hashmm.api.xlsx_builder import build_xlsx_from_plan
            result = build_xlsx_from_plan(outline, output_path)
            if "OK" in result or output_path.exists():
                return {
                    "ok": True,
                    "path": str(output_path),
                    "filename": filename,
                    "download_url": f"/api/files/{filename}",
                    "message": "Excel 已创建",
                }
            return {"ok": False, "message": result}
        except Exception as e:
            logger.error(f"XLSX render error: {e}", exc_info=True)
            return {"ok": False, "message": f"表格渲染失败: {str(e)[:100]}"}

    def _generate_outline(
        self,
        schema_prompt: str,
        requirements: str,
        data_context: str = "",
        extra: str = "",
    ) -> dict | None:
        """Use LLM to generate a structured JSON outline."""
        if not self.llm_fn:
            return self._fallback_outline(requirements, data_context)

        user_msg = f"用户需求：{requirements}"
        if data_context:
            user_msg += f"\n\n可用数据：\n{data_context[:4000]}"
        if extra:
            user_msg += f"\n{extra}"

        # v13: Add explicit JSON instruction
        json_instruction = "\n\n重要：只返回 JSON，不要返回其他文字。不要用 markdown 代码块。"
        full_schema = schema_prompt + json_instruction

        try:
            if hasattr(self.llm_fn, 'quick_call'):
                raw = self.llm_fn.quick_call(full_schema, user_msg, max_tok=3000)
            elif hasattr(self.llm_fn, 'chat'):
                raw = self.llm_fn.chat([
                    {"role": "system", "content": full_schema},
                    {"role": "user", "content": user_msg},
                ], max_tok=3000)
            else:
                raw = self.llm_fn(f"{full_schema}\n\n{user_msg}")

            # Parse JSON (handle markdown fences and mixed text)
            raw = raw.strip()
            # Strip markdown code fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:])
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            try:
                return json.loads(raw)
            except json.JSONDecodeError as _e:
                log_suppressed(logger, _e)

            # Try to extract JSON object from mixed text
            try:
                start = raw.index("{")
                # Find matching closing brace
                depth = 0
                for i in range(start, len(raw)):
                    if raw[i] == "{": depth += 1
                    elif raw[i] == "}": depth -= 1
                    if depth == 0:
                        return json.loads(raw[start:i+1])
            except (ValueError, json.JSONDecodeError) as _e:
                log_suppressed(logger, _e)

            logger.warning(f"Failed to parse LLM outline as JSON, using fallback")
            return self._fallback_outline(requirements, data_context)

        except Exception as e:
            logger.warning(f"Outline generation failed: {e}")
            return self._fallback_outline(requirements, data_context)

    def _fallback_outline(self, requirements: str, data_context: str = "") -> dict:
        """v13: Create a simple outline directly from content (no LLM needed)."""
        # Split data_context into sections by markdown headers
        sections = []
        current_title = "概述"
        current_content = []

        for line in (data_context or requirements).split("\n"):
            line = line.strip()
            if line.startswith("##") or line.startswith("**第"):
                if current_content:
                    sections.append({"title": current_title, "content": "\n".join(current_content)})
                    current_content = []
                current_title = line.lstrip("#* ").rstrip("*")
            elif line:
                current_content.append(line)

        if current_content:
            sections.append({"title": current_title, "content": "\n".join(current_content)})

        # Build PPT plan
        slides = [{"role": "cover", "title": requirements[:50], "subtitle": "数据驱动分析报告"}]
        for sec in sections[:8]:
            bullets = [b.strip("- •·") for b in sec["content"].split("\n") if b.strip()][:6]
            slides.append({
                "role": "bullets",
                "title": sec["title"][:40],
                "bullets": bullets if bullets else [sec["content"][:200]],
            })
        slides.append({"role": "end", "title": "谢谢", "subtitle": "基于 HashMM-RAG 知识库"})

        return {"title": requirements[:50], "slides": slides, "theme": "blue"}


def get_doc_generator(llm_fn: Any = None) -> DocumentGenerator:
    """Factory function — gets LLM from ServiceRegistry if not provided."""
    if llm_fn is None:
        try:
            from hashmm.api.core.services import ServiceRegistry
            llm_fn = ServiceRegistry.llm_fn
        except Exception as _e:
            log_suppressed(logger, _e)
    return DocumentGenerator(llm_fn=llm_fn)
