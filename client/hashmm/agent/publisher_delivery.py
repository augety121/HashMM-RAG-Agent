"""Deterministic delivery checks for the AI briefing workflow.

The model may propose facts and files, but it is not allowed to decide that its
own work is complete.  This module validates the two durable artifacts and the
minimum evidence/time boundaries required by the user-facing publisher flow.
It intentionally uses only deterministic parsing; it does not treat model prose
such as ``已核验`` as execution evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape, unescape
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*"
    r"(\d{1,2})\s*日?(?:\s*[T ]?\s*(\d{1,2})\s*[:：]\s*(\d{2}))?"
)
_URL_RE = re.compile(r"https?://[^\s<>)\]\"']+", re.I)
_RISK_MARKERS = (
    "经搜索摘要",
    "仅基于搜索摘要",
    "原始页面获取失败",
    "单一来源",
    "待核验",
    "未经核验",
    "转载",
)
_SECONDARY_ATTRIBUTION_RE = re.compile(
    r"(?:经|据)\s*[^，。；|\n]{1,80}?(?:报道|引用|转述|汇总|转载)",
    re.I,
)


@dataclass(frozen=True)
class PublisherDeliveryReport:
    """Machine-checkable result used by AgentLoop's completion gate."""

    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def _latest_content(files: Iterable[dict[str, Any]], suffix: str) -> str:
    for item in reversed(list(files)):
        name = str(item.get("filename") or "").lower()
        if name.endswith(suffix):
            return str(item.get("_content") or item.get("content") or "")
    return ""


def _normalized_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("\r\n", "\n").split("\n")
        if line.strip()
    ]


def _has_repeated_document(text: str) -> bool:
    """Catch the common failure where the complete briefing is emitted twice."""
    lines = _normalized_lines(text)
    if len(lines) >= 12 and len(lines) % 2 == 0:
        half = len(lines) // 2
        if lines[:half] == lines[half:]:
            return True
    headings = sum(
        1 for line in lines if re.fullmatch(r"#{1,2}\s*AI\s*行业简报", line, re.I)
    )
    return headings > 1


def _parse_dates(text: str) -> list[tuple[datetime, bool]]:
    """Parse Shanghai timestamps and retain whether minute precision existed."""
    parsed: list[tuple[datetime, bool]] = []
    for year, month, day, hour, minute in _DATE_RE.findall(text or ""):
        try:
            precise = bool(hour and minute)
            parsed.append(
                (
                    datetime(
                        int(year),
                        int(month),
                        int(day),
                        int(hour or 0),
                        int(minute or 0),
                        tzinfo=_SHANGHAI,
                    ),
                    precise,
                )
            )
        except ValueError:
            continue
    return parsed


def _source_dates(markdown: str) -> list[tuple[datetime, bool]]:
    lines: list[str] = []
    continuation = 0
    for line in str(markdown or "").splitlines():
        normalized = line.lower()
        if any(marker in normalized for marker in ("来源", "发布", "报道", "source")):
            lines.append(line)
            # Markdown commonly wraps the publication timestamp onto the next
            # physical line.  Keep a narrow continuation window so a model
            # cannot evade the recency gate merely through line wrapping.
            continuation = 2
        elif continuation and line.strip():
            lines.append(line)
            continuation -= 1
        elif not line.strip():
            continuation = 0
    return _parse_dates("\n".join(lines))


def _substantive_headings(markdown: str) -> list[str]:
    """Return article section headings that the HTML draft must preserve."""
    headings: list[str] = []
    for line in str(markdown or "").splitlines():
        match = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
        if not match:
            continue
        title = re.sub(r"[*_`]", "", match.group(1)).strip()
        if len(title) >= 2 and title.lower() not in {"ai 行业简报", "ai行业简报"}:
            headings.append(title)
    return headings


def _html_plain_text(value: str) -> str:
    without_blocks = re.sub(
        r"<(?:style|script)\b[^>]*>[\s\S]*?</(?:style|script)>",
        " ",
        str(value or ""),
        flags=re.I,
    )
    return unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_blocks)).strip()
    )


def _render_inline_markdown(value: str) -> str:
    """Render a deliberately small, safe subset of inline Markdown."""
    rendered = escape(str(value or ""), quote=True)
    rendered = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda match: (
            f'<a href="{match.group(2)}" '
            'style="color:#2563eb;text-decoration:none">'
            f"{match.group(1)}</a>"
        ),
        rendered,
    )
    rendered = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(
        r"`([^`\n]+)`",
        r'<code style="font-family:ui-monospace,monospace;'
        r'background:#f3f4f6;padding:2px 5px;border-radius:5px">\1</code>',
        rendered,
    )
    return rendered


def render_publisher_html(markdown: str, *, title: str = "AI 行业简报") -> str:
    """Create a portable WeChat-compatible HTML draft from existing Markdown.

    This is a presentation transform only: it never adds, removes or upgrades
    facts and therefore can safely close the common "Markdown exists, HTML was
    merely promised" failure without another model call.
    """
    source = str(markdown or "").replace("\r\n", "\n").strip()
    if not source:
        return ""

    lines = source.split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_kind = "ul"

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(part.strip() for part in paragraph if part.strip())
        if text:
            blocks.append(
                '<p style="margin:0 0 16px;color:#1f2937;'
                f'font-size:16px;line-height:1.85">{_render_inline_markdown(text)}</p>'
            )
        paragraph.clear()

    def flush_list() -> None:
        if not list_items:
            return
        tag = "ol" if list_kind == "ol" else "ul"
        body = "".join(
            '<li style="margin:0 0 8px;line-height:1.75">'
            f"{_render_inline_markdown(item)}</li>"
            for item in list_items
        )
        blocks.append(
            f'<{tag} style="margin:0 0 18px;padding-left:24px;'
            f'color:#1f2937;font-size:16px">{body}</{tag}>'
        )
        list_items.clear()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            size = {1: 30, 2: 22, 3: 18}[level]
            margin_top = 8 if level == 1 else 28
            blocks.append(
                f'<h{level} style="margin:{margin_top}px 0 14px;'
                f'color:#111827;font-size:{size}px;line-height:1.35">'
                f"{_render_inline_markdown(heading.group(2))}</h{level}>"
            )
            continue
        if re.fullmatch(r"---+", line):
            flush_paragraph()
            flush_list()
            blocks.append(
                '<hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0">'
            )
            continue
        unordered = re.match(r"^[-*]\s+(.+)$", line)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if unordered or ordered:
            flush_paragraph()
            wanted_kind = "ol" if ordered else "ul"
            if list_items and list_kind != wanted_kind:
                flush_list()
            list_kind = wanted_kind
            list_items.append((ordered or unordered).group(1))
            continue
        if line.startswith(">"):
            flush_paragraph()
            flush_list()
            blocks.append(
                '<blockquote style="margin:0 0 18px;padding:12px 16px;'
                'border-left:3px solid #2563eb;background:#f8fafc;'
                f'color:#475569;line-height:1.75">{_render_inline_markdown(line[1:].strip())}'
                "</blockquote>"
            )
            continue
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    safe_title = escape(title or "AI 行业简报", quote=True)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title></head>"
        '<body style="margin:0;background:#f3f4f6;color:#111827;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,'
        'PingFang SC,Microsoft YaHei,sans-serif">'
        '<main style="box-sizing:border-box;max-width:760px;margin:0 auto;'
        'padding:36px 28px 52px;background:#ffffff">'
        + "".join(blocks)
        + "</main></body></html>"
    )


def missing_publisher_html_artifact(
    files: Iterable[dict[str, Any]],
) -> tuple[str, str] | None:
    """Return a deterministic HTML artifact only when Markdown is durable."""
    items = list(files)
    markdown = _latest_content(items, ".md")
    if len(markdown.strip()) < 240 or _latest_content(items, ".html").strip():
        return None
    markdown_name = next(
        (
            str(item.get("filename") or "")
            for item in reversed(items)
            if str(item.get("filename") or "").lower().endswith(".md")
        ),
        "ai-brief.md",
    )
    filename = re.sub(r"\.md$", ".html", markdown_name, flags=re.I)
    return filename, render_publisher_html(markdown)


def _verification_issues(markdown: str) -> list[str]:
    issues: list[str] = []
    verified_blocks = [
        block
        for block in re.split(r"\n\s*---+\s*\n", markdown or "")
        if "已核验" in block
    ]
    for index, block in enumerate(verified_blocks, start=1):
        if not _URL_RE.search(block):
            issues.append(f"第 {index} 个“已核验”条目缺少可打开的原始来源 URL")
        risk_scope = "\n".join(
            line
            for line in block.splitlines()
            if any(label in line.lower() for label in ("来源", "状态", "source"))
        )
        if any(marker in risk_scope for marker in _RISK_MARKERS):
            issues.append(f"第 {index} 个“已核验”条目仍包含二手/待核验风险")
        if _SECONDARY_ATTRIBUTION_RE.search(risk_scope):
            issues.append(f"第 {index} 个“已核验”条目使用二手转述，不能视为原始来源核验")
    return issues


def validate_publisher_delivery(
    files: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    recent_hours: int = 24,
) -> PublisherDeliveryReport:
    """Validate Markdown + HTML output for a recent-news publisher run.

    A calendar date is coarser than a publication timestamp, so the accepted
    source-date window includes one extra day on either side.  This still
    deterministically rejects the observed 2025-as-2026 hallucination while
    allowing sources that expose only a date rather than an exact time.
    """
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)

    markdown = _latest_content(files, ".md")
    html = _latest_content(files, ".html")
    issues: list[str] = []

    if not markdown.strip():
        issues.append("缺少真实 Markdown 文件")
    elif len(markdown.strip()) < 240:
        issues.append("Markdown 正文过短，不能作为完整简报")

    if not html.strip():
        issues.append("缺少真实 HTML 文件")
    else:
        lowered = html.lower()
        if len(html.strip()) < 240 or "<html" not in lowered or "<body" not in lowered:
            issues.append("HTML 草稿不完整（必须包含 html/body 与完整正文）")
        if "<script" in lowered or "stylesheet" in lowered:
            issues.append("HTML 草稿包含脚本或外部样式，不符合公众号安全边界")
        html_text = _html_plain_text(html)
        missing_headings = [
            heading
            for heading in _substantive_headings(markdown)
            if heading not in html_text
        ]
        if missing_headings:
            preview = "、".join(missing_headings[:3])
            issues.append(f"HTML 未包含 Markdown 的完整章节：{preview}")

    if markdown:
        if _has_repeated_document(markdown):
            issues.append("Markdown 正文发生整篇重复")

        dates = _source_dates(markdown)
        precise_dates = [item for item, precise in dates if precise]
        lower_bound = current - timedelta(hours=recent_hours)
        recent_dates = [
            item for item in precise_dates if lower_bound <= item <= current
        ]
        if not dates:
            issues.append("来源条目缺少可解析的发布时间")
        elif not precise_dates:
            issues.append("来源发布时间缺少时分，无法证明位于最近 24 小时内")
        elif not recent_dates:
            issues.append(
                f"来源发布时间不在服务端当前时间之前的最近 {recent_hours} 小时内"
            )

        cutoff_lines = "\n".join(
            line for line in markdown.splitlines() if "数据截至" in line
        )
        for cutoff, precise in _parse_dates(cutoff_lines):
            # A report must never claim knowledge from the future.  When the
            # author supplies a time, compare the complete Shanghai timestamp,
            # not merely the calendar date.
            if cutoff > current if precise else cutoff.date() > current.date():
                issues.append("数据截止时间晚于服务端当前时间")
                break

        issues.extend(_verification_issues(markdown))

    # Preserve order while avoiding a noisy repeated repair prompt.
    unique = tuple(dict.fromkeys(issue for issue in issues if issue))
    return PublisherDeliveryReport(issues=unique)
