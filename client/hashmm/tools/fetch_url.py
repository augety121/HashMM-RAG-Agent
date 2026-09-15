"""fetch_url tool — reads webpages, arxiv papers, and PDFs from URLs.

Registered as a tool in the Agent's tool registry. Enables:
  - Reading arxiv papers (converts /abs/ → /pdf/ → parse)
  - Reading any PDF URL (download → parse with existing pipeline)
  - Reading webpages (html2text extraction)

Usage by Agent:
  {"tool": "fetch_url", "args": {"url": "https://arxiv.org/abs/2410.21276"}}
"""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.tools.fetch_url")

# Max content length (chars) to return to Agent context
MAX_CONTENT_CHARS = 12000


def execute(args: dict, ctx: dict) -> str:
    """Fetch and extract text from a URL."""
    url = args.get("url", "").strip()
    if not url:
        return "Error: 缺少 url 参数"

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # SSRF 防护（大厂标准）：解析 URL + DNS 解析后逐个 IP 校验，挡回环/内网/链路本地/云元数据/IP变体。
    # 旧版仅字符串黑名单，可被域名解析到内网、172.16/12、IPv6、十进制IP 等绕过——现改为 net_guard 统一判定。
    from hashmm.tools.net_guard import check_url_safe
    _ok, _reason = check_url_safe(url)
    if not _ok:
        return f"Error: 目标地址被安全策略拦截（{_reason}）"

    t0 = time.time()

    try:
        # Route by URL type
        if "arxiv.org" in url:
            content = _fetch_arxiv(url)
        elif url.lower().endswith(".pdf"):
            content = _fetch_pdf(url)
        else:
            content = _fetch_webpage(url)

        elapsed = round((time.time() - t0) * 1000)

        if not content or len(content.strip()) < 50:
            return f"无法提取有效内容 (URL: {url})"

        # Truncate if too long
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + f"\n\n... (内容截断，共 {len(content)} 字符)"

        return f"[来源: {url}] ({elapsed}ms)\n\n{content}"

    except Exception as e:
        logger.warning(f"fetch_url failed: {url} → {e}")
        return f"Error: 无法访问 {url} ({type(e).__name__}: {str(e)[:100]})"


def _fetch_arxiv(url: str) -> str:
    """Fetch arxiv paper — tries abstract page first, then PDF."""
    from hashmm.tools.net_guard import safe_get

    # Extract paper ID
    match = re.search(r'(\d{4}\.\d{4,5})', url)
    if not match:
        return _fetch_webpage(url)

    paper_id = match.group(1)

    # 1. Try arxiv API for metadata + abstract
    try:
        api_url = f"http://export.arxiv.org/api/query?id_list={paper_id}"
        resp = safe_get(api_url, timeout=15)
        if resp.status_code == 200:
            text = resp.text
            # Parse basic XML fields
            title = _xml_text(text, "title") or ""
            abstract = _xml_text(text, "summary") or ""
            authors = [_xml_text(a, "name") or "" for a in re.findall(r'<author>(.*?)</author>', text, re.DOTALL)]
            published = _xml_text(text, "published") or ""

            parts = [f"# {title.strip()}"]
            if authors:
                parts.append(f"**Authors:** {', '.join(a.strip() for a in authors[:10])}")
            if published:
                parts.append(f"**Published:** {published[:10]}")
            parts.append(f"**arXiv:** {paper_id}")
            parts.append(f"\n## Abstract\n{abstract.strip()}")

            # 2. Try to get full PDF text too
            try:
                pdf_text = _fetch_pdf(f"https://arxiv.org/pdf/{paper_id}.pdf")
                if pdf_text and len(pdf_text) > len(abstract) * 2:
                    parts.append(f"\n## Full Text\n{pdf_text}")
            except Exception:
                parts.append("\n(PDF 全文提取失败，以上为摘要)")

            return "\n".join(parts)
    except Exception as e:
        logger.debug(f"arxiv API failed: {e}")

    # Fallback: just fetch the HTML page
    return _fetch_webpage(url)


def _fetch_pdf(url: str) -> str:
    """Download PDF and extract text using existing parser."""
    from hashmm.tools.net_guard import safe_get

    resp = safe_get(url, timeout=30, stream=True,
                    headers={"User-Agent": "Mozilla/5.0 HashMM-RAG/1.0"})
    resp.raise_for_status()

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)
        tmp_path = f.name

    try:
        # Use existing PDF parser
        try:
            from hashmm.pipeline.parser import DocumentParser
            parser = DocumentParser(output_dir=tempfile.gettempdir())
            doc = parser.parse(Path(tmp_path))
            if doc.full_text and len(doc.full_text) > 100:
                return doc.full_text
        except Exception as _e:
            log_suppressed(logger, _e)

        # Fallback: PyMuPDF
        try:
            import fitz
            pdf = fitz.open(tmp_path)
            text = ""
            for page in pdf:
                text += page.get_text() + "\n"
            pdf.close()
            if text.strip():
                return text.strip()
        except ImportError:
            pass

        # Fallback: pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(tmp_path) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            if text.strip():
                return text.strip()
        except ImportError:
            pass

        return "(PDF 解析失败 — 请安装 PyMuPDF: pip install pymupdf)"
    finally:
        try:
            Path(tmp_path).unlink()
        except Exception as _e:
            log_suppressed(logger, _e)


def _fetch_webpage(url: str) -> str:
    """Fetch webpage and extract main text content."""
    from hashmm.tools.net_guard import safe_get

    # safe_get 内部逐跳校验重定向（禁用自动跳转，手动跟随并重校验每一跳）→ 挡"公网URL 302到内网"绕过
    resp = safe_get(url, timeout=15,
                    headers={"User-Agent": "Mozilla/5.0 HashMM-RAG/1.0"})
    resp.raise_for_status()

    html = resp.text

    # Try readability
    try:
        from readability import Document
        doc = Document(html)
        title = doc.title()
        # Convert HTML summary to plain text
        summary = doc.summary()
        text = _html_to_text(summary)
        return f"# {title}\n\n{text}"
    except ImportError:
        pass

    # Fallback: BeautifulSoup
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        title = soup.title.string if soup.title else ""
        text = soup.get_text(separator="\n", strip=True)
        # Clean up excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return f"# {title}\n\n{text[:MAX_CONTENT_CHARS]}"
    except ImportError:
        pass

    # Last resort: regex strip tags
    text = re.sub(r'<[^>]+>', '', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:MAX_CONTENT_CHARS]


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    except ImportError:
        return re.sub(r'<[^>]+>', '', html).strip()


def _xml_text(xml: str, tag: str) -> str | None:
    """Extract text from an XML tag."""
    match = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', xml, re.DOTALL)
    return match.group(1).strip() if match else None


# ── Tool definition for Agent registry ──

TOOL_DEFINITION = {
    "name": "fetch_url",
    "description": "读取网页或PDF内容。支持 arxiv 论文、PDF 文件、普通网页。输入 URL，返回提取的文本内容。",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要读取的 URL 地址（支持 arxiv.org、.pdf、普通网页）",
            }
        },
        "required": ["url"],
    },
}
