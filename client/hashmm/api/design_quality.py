"""Offline quality gate for generated HTML/SVG artifacts.

The gate is deliberately deterministic: it verifies structure, portability and
basic accessibility signals, but does not pretend to judge visual taste without
rendered evidence.  The compact contract is persisted with the run manifest.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any


SCHEMA = "hashmm.design-quality.v1"


class _HTMLAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: dict[str, int] = {}
        self.html_lang = False
        self.viewport = False
        self.images = 0
        self.images_with_alt = 0
        self.controls = 0
        self.labelled_controls = 0
        self.external_resources: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag == "html" and values.get("lang", "").strip():
            self.html_lang = True
        if tag == "meta" and values.get("name", "").lower() == "viewport":
            self.viewport = bool(values.get("content", "").strip())
        if tag == "img":
            self.images += 1
            self.images_with_alt += int("alt" in values)
        if tag in {"button", "input", "select", "textarea"}:
            self.controls += 1
            label = (values.get("aria-label") or values.get("title") or
                     values.get("placeholder") or values.get("value"))
            if tag == "button" or label.strip():
                self.labelled_controls += 1
        if tag in {"script", "img", "iframe", "video", "audio", "source"}:
            value = values.get("src", "")
            if re.match(r"https?://", value, re.I):
                self.external_resources.append(value[:160])
        if tag == "link":
            value = values.get("href", "")
            if re.match(r"https?://", value, re.I):
                self.external_resources.append(value[:160])


def audit_html(html: str) -> dict[str, Any]:
    text = str(html or "")
    parser = _HTMLAuditParser()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        errors.append(f"HTML 解析失败：{type(exc).__name__}")
    if not any(parser.tags.get(t, 0) for t in ("html", "body", "section", "div")):
        errors.append("缺少可渲染的页面结构")
    imports = re.findall(r"@import\s+(?:url\()?['\"]?https?://", text, re.I)
    if parser.external_resources or imports:
        errors.append("包含外部脚本、样式或媒体依赖，不能离线稳定交付")
    if not parser.viewport:
        warnings.append("缺少 viewport，窄屏适配不可验证")
    if not parser.html_lang and parser.tags.get("html", 0):
        warnings.append("html 未声明 lang，辅助技术难以确定语言")
    if not (parser.tags.get("title", 0) or parser.tags.get("h1", 0)):
        warnings.append("缺少页面标题或一级标题")
    if parser.images > parser.images_with_alt:
        warnings.append(f"{parser.images - parser.images_with_alt} 张图片缺少 alt")
    if parser.controls > parser.labelled_controls:
        warnings.append(f"{parser.controls - parser.labelled_controls} 个控件缺少可识别标签")
    if re.search(r"\bwidth\s*:\s*(?:1[4-9]\d{2}|[2-9]\d{3,})px", text, re.I):
        warnings.append("存在较大的固定像素宽度，窄屏可能横向溢出")
    checks = {
        "parseable": not any(e.startswith("HTML 解析失败") for e in errors),
        "renderable_structure": "缺少可渲染的页面结构" not in errors,
        "offline_self_contained": not bool(parser.external_resources or imports),
        "responsive_hint": parser.viewport,
        "heading_hint": bool(parser.tags.get("title", 0) or parser.tags.get("h1", 0)),
        "accessible_media": parser.images == parser.images_with_alt,
        "labelled_controls": parser.controls == parser.labelled_controls,
    }
    return {"schema": SCHEMA, "kind": "html", "passed": not errors,
            "checks": checks, "errors": errors, "warnings": warnings}


def audit_svg(svg: str) -> dict[str, Any]:
    text = str(svg or "").strip()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = ET.fromstring(text)
        if not str(root.tag).lower().endswith("svg"):
            errors.append("根元素不是 svg")
        drawing = any(str(node.tag).lower().endswith(t) for node in root.iter()
                      for t in ("path", "rect", "circle", "polygon", "polyline",
                                "line", "ellipse", "text", "g", "use"))
        if not drawing:
            errors.append("SVG 没有可绘制元素")
        if not (root.attrib.get("viewBox") or root.attrib.get("viewbox")):
            warnings.append("SVG 缺少 viewBox，缩放适配可能不稳定")
    except Exception as exc:
        errors.append(f"SVG 解析失败：{type(exc).__name__}")
    external = bool(re.search(r"(?:href|src)\s*=\s*['\"]https?://", text, re.I))
    if external:
        errors.append("SVG 包含外部资源依赖")
    return {"schema": SCHEMA, "kind": "svg", "passed": not errors,
            "checks": {"parseable": not any(e.startswith("SVG 解析失败") for e in errors),
                       "offline_self_contained": not external},
            "errors": errors, "warnings": warnings}


def audit_design_artifact(content: str, extension: str) -> dict[str, Any]:
    if str(extension or "").lower() == "svg":
        return audit_svg(content)
    return audit_html(content)


__all__ = ["SCHEMA", "audit_design_artifact", "audit_html", "audit_svg"]
