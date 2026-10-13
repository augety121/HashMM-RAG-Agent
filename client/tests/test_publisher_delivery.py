"""Deterministic acceptance tests for the AI briefing delivery gate."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hashmm.agent.publisher_delivery import (
    missing_publisher_html_artifact,
    render_publisher_html,
    validate_publisher_delivery,
)


pytestmark = pytest.mark.unit


NOW = datetime(2026, 7, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _valid_markdown() -> str:
    return """# AI 行业简报

数据截至：2026-07-30 18:00（北京时间）

## 官方发布：模型服务可靠性更新

模型厂商在官方页面公布了一项面向生产工作负载的可靠性更新。原始页面已经实际打开，
标题、发布时间与关键事实均完成逐项核对。这里仅保留原始页面能够直接支持的事实，
不把搜索摘要、转载文字或编辑推断写成已核验事实。

来源：https://example.com/official-release ，发布于 2026-07-30 09:30（北京时间）
｜状态：已核验

## 待确认项

发布前仍需用户确认标题、编辑判断和最终版式；系统不得代替用户自动发布。
"""


def _valid_html() -> str:
    return """<!doctype html><html><head><meta charset="utf-8">
<style>body{font-family:sans-serif;line-height:1.7}h1{font-size:28px}</style>
</head><body><h1>AI 行业简报</h1><p>数据截至：2026-07-30 18:00（北京时间）</p>
<h2>官方发布：模型服务可靠性更新</h2>
<p>模型厂商在官方页面公布了一项面向生产工作负载的可靠性更新。原始页面已经实际打开，
标题、发布时间与关键事实均完成逐项核对。这里仅保留原始页面能够直接支持的事实，
不把搜索摘要、转载文字或编辑推断写成已核验事实。</p>
<p>来源：https://example.com/official-release，发布于 2026-07-30 09:30，状态：已核验。</p>
<h2>待确认项</h2><p>发布前仍需用户确认标题、编辑判断和最终版式。</p></body></html>"""


def _files(markdown: str | None = None, html: str | None = None):
    result = []
    if markdown is not None:
        result.append({"filename": "brief.md", "_content": markdown})
    if html is not None:
        result.append({"filename": "brief.html", "_content": html})
    return result


def test_valid_recent_dual_file_delivery_passes():
    report = validate_publisher_delivery(
        _files(_valid_markdown(), _valid_html()),
        now=NOW,
    )
    assert report.ok, report.issues


def test_old_news_cannot_be_presented_as_current_24_hour_brief():
    markdown = _valid_markdown().replace("2026-07-30", "2025-07-30")
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert not report.ok
    assert any("最近 24 小时" in issue for issue in report.issues)


def test_duplicate_markdown_and_missing_html_fail_closed():
    markdown = _valid_markdown() + "\n" + _valid_markdown()
    report = validate_publisher_delivery(_files(markdown), now=NOW)
    assert "Markdown 正文发生整篇重复" in report.issues
    assert "缺少真实 HTML 文件" in report.issues


def test_secondary_or_untraceable_source_cannot_be_marked_verified():
    markdown = _valid_markdown().replace(
        "来源：https://example.com/official-release ，发布于",
        "来源：某聚合站转载（仅基于搜索摘要），发布于",
    )
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert any("缺少可打开的原始来源 URL" in issue for issue in report.issues)
    assert any("二手/待核验风险" in issue for issue in report.issues)


def test_secondary_attribution_with_url_still_cannot_be_marked_verified():
    markdown = _valid_markdown().replace(
        "来源：https://example.com/official-release ，发布于",
        "来源：https://example.com/aggregator ，经 Bloomberg 报道，发布于",
    )
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert any("二手转述" in issue for issue in report.issues)


def test_future_cutoff_is_rejected_without_calendar_tolerance():
    markdown = _valid_markdown().replace(
        "数据截至：2026-07-30",
        "数据截至：2026-07-31",
    )
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert "数据截止时间晚于服务端当前时间" in report.issues


def test_same_day_future_cutoff_time_is_rejected():
    markdown = _valid_markdown().replace(
        "数据截至：2026-07-30 18:00",
        "数据截至：2026-07-30 18:01",
    )
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert "数据截止时间晚于服务端当前时间" in report.issues


def test_date_only_source_cannot_prove_an_exact_24_hour_window():
    markdown = _valid_markdown().replace(
        "发布于 2026-07-30 09:30",
        "发布于 2026-07-30",
    )
    report = validate_publisher_delivery(_files(markdown, _valid_html()), now=NOW)
    assert any("缺少时分" in issue for issue in report.issues)


def test_html_shell_without_markdown_sections_is_not_a_complete_delivery():
    html = _valid_html().replace(
        "<h2>官方发布：模型服务可靠性更新</h2>",
        "<h2>简报正文稍后生成</h2>",
    )
    report = validate_publisher_delivery(_files(_valid_markdown(), html), now=NOW)
    assert any("HTML 未包含 Markdown 的完整章节" in issue for issue in report.issues)


def test_markdown_can_be_deterministically_materialized_as_safe_html():
    artifact = missing_publisher_html_artifact(_files(_valid_markdown()))
    assert artifact is not None
    filename, html = artifact
    assert filename == "brief.html"
    assert "<script" not in html.lower()
    assert "官方发布：模型服务可靠性更新" in html
    report = validate_publisher_delivery(
        _files(_valid_markdown(), html),
        now=NOW,
    )
    assert report.ok, report.issues


def test_html_renderer_escapes_untrusted_markup():
    rendered = render_publisher_html(
        _valid_markdown() + "\n\n<script>alert('x')</script>"
    )
    assert "<script" not in rendered.lower()
    assert "&lt;script&gt;" in rendered


def test_observed_briefing_failure_is_rejected_as_one_delivery():
    """Regress the exact class of failure reported from the desktop client.

    A visually complete article is still invalid when it claims tomorrow's
    cutoff, uses year-old news for a 24-hour brief, upgrades an aggregator to
    "verified", and emits the whole document twice.
    """
    article = """# AI 行业简报

**2026年7月30日–31日**
**数据截至 2026-07-31 18:00（北京时间）**

## Anthropic 估值逼近 1700 亿美元

Anthropic 即将完成一轮融资。这段正文故意保持足够长度，用来证明完成门检查的是
事实边界、来源链和真实交付，而不是仅按字数把模型回答判定为已经完成。发布前仍然
需要打开原始页面核验标题、时间、金额及关键事实，不能把搜索摘要当成浏览证据。

来源：https://example.com/aggregated-news ，经 Crunchbase 转述 Bloomberg 报道，
2025年7月30日 | 状态：**已核验**

## 待确认项

发布前请用户确认；不得自动发布。
"""
    duplicated = article + "\n\n" + article
    html = render_publisher_html(duplicated)

    report = validate_publisher_delivery(_files(duplicated, html), now=NOW)

    assert not report.ok
    assert "Markdown 正文发生整篇重复" in report.issues
    assert "数据截止时间晚于服务端当前时间" in report.issues
    assert any("最近 24 小时" in issue for issue in report.issues)
    assert any("二手转述" in issue for issue in report.issues)
