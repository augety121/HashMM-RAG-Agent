"""Noise Filter — clean non-content elements from parsed documents.

Detects and removes:
  - Page headers/footers (repeated text across pages)
  - Page numbers (various formats)
  - Watermarks (repeated text at same position)
  - Table of contents pages
  - Broken sentences across page boundaries
  - Empty/whitespace-only paragraphs
  - Duplicate paragraphs
  - Encoding artifacts
"""
from __future__ import annotations
import re
from collections import Counter
from hashmm.pipeline.content_block import ContentBlock
from hashmm.utils import get_logger

logger = get_logger("hashmm.pipeline.noise")

# Page number patterns
_PAGE_NUM_RE = re.compile(
    r'^\s*[-—]?\s*\d{1,4}\s*[-—]?\s*$'           # "- 15 -" or "15"
    r'|^\s*(?:第\s*\d+\s*页|Page\s*\d+)\s*$'       # "第 15 页" or "Page 15"
    r'|^\s*\d+\s*/\s*\d+\s*$',                     # "15/30"
    re.IGNORECASE | re.MULTILINE,
)

# TOC line pattern: "Title ........... 15" or "Title    15"
_TOC_LINE_RE = re.compile(
    r'^.{5,60}\s*[\.·…]{3,}\s*\d{1,4}\s*$'
    r'|^.{5,60}\s{3,}\d{1,4}\s*$',
    re.MULTILINE,
)


class NoiseFilter:
    """Remove non-content noise from parsed document blocks."""

    def __init__(self, min_block_chars: int = 10):
        self.min_block_chars = min_block_chars

    def clean(self, blocks: list[ContentBlock],
              pages_text: dict[int, str] | None = None) -> list[ContentBlock]:
        """Apply all noise filters to a list of content blocks.

        Args:
            blocks: Parsed content blocks
            pages_text: {page_num: raw_text} for header/footer detection

        Returns:
            Cleaned blocks (noise blocks have is_noise=True)
        """
        if not blocks:
            return blocks

        # 1. Detect and mark page headers/footers
        if pages_text:
            self._mark_headers_footers(blocks, pages_text)

        # 2. Remove page numbers
        self._mark_page_numbers(blocks)

        # 3. Detect TOC pages
        self._mark_toc(blocks)

        # 4. Fix broken sentences across blocks
        self._fix_broken_sentences(blocks)

        # 5. Remove empty/whitespace blocks
        self._mark_empty(blocks)

        # 6. Remove duplicate blocks
        self._mark_duplicates(blocks)

        # 7. Detect encoding issues
        self._fix_encoding(blocks)

        # v5.0: Enterprise-specific noise detection
        # 8. Legal disclaimers and boilerplate
        self._mark_disclaimers(blocks)

        # 9. Watermark text
        self._mark_watermarks(blocks)

        # 10. Garbled CJK text (font decoding failures)
        self._mark_garbled_cjk(blocks)

        # 11. Contact info blocks (URLs, emails, phone numbers)
        self._mark_contact_blocks(blocks)

        # Count noise
        noise_count = sum(1 for b in blocks if b.is_noise)
        if noise_count > 0:
            logger.info(f"Noise filter: {noise_count}/{len(blocks)} blocks marked as noise")

        return blocks

    def _mark_headers_footers(self, blocks: list[ContentBlock],
                               pages_text: dict[int, str]):
        """Detect repeated text at top/bottom of pages = header/footer."""
        if len(pages_text) < 3:
            return

        page_nums = sorted(pages_text.keys())
        first_lines: list[str] = []
        last_lines: list[str] = []

        for pn in page_nums:
            text = pages_text[pn].strip()
            lines = text.split("\n")
            if lines:
                first_lines.append(lines[0].strip())
                last_lines.append(lines[-1].strip())

        # Text appearing in ≥60% of pages at the same position = header/footer
        threshold = max(3, int(len(page_nums) * 0.6))

        header_texts = {t for t, c in Counter(first_lines).items() if c >= threshold and len(t) > 3}
        footer_texts = {t for t, c in Counter(last_lines).items() if c >= threshold and len(t) > 3}

        for b in blocks:
            content_stripped = b.content.strip()
            if content_stripped in header_texts or content_stripped in footer_texts:
                b.is_noise = True
                b.quality_score = 0.0

        if header_texts or footer_texts:
            logger.info(f"Detected {len(header_texts)} headers, {len(footer_texts)} footers")

    def _mark_page_numbers(self, blocks: list[ContentBlock]):
        """Mark standalone page number lines as noise."""
        for b in blocks:
            if b.is_noise:
                continue
            content = b.content.strip()
            if len(content) < 15 and _PAGE_NUM_RE.match(content):
                b.is_noise = True
                b.quality_score = 0.0

    def _mark_toc(self, blocks: list[ContentBlock]):
        """Detect Table of Contents pages and mark as noise."""
        # Look for clusters of TOC-like lines
        window = 5
        for i in range(len(blocks) - window + 1):
            toc_count = 0
            for j in range(i, i + window):
                if blocks[j].is_noise:
                    continue
                lines = blocks[j].content.strip().split("\n")
                toc_lines = sum(1 for l in lines if _TOC_LINE_RE.match(l))
                if toc_lines > len(lines) * 0.5 and len(lines) >= 2:
                    toc_count += 1
            if toc_count >= 3:
                for j in range(i, i + window):
                    blocks[j].is_noise = True
                    blocks[j].quality_score = 0.1
                logger.info(f"TOC detected at blocks {i}-{i + window - 1}")

    def _fix_broken_sentences(self, blocks: list[ContentBlock]):
        """Fix sentences broken across page/block boundaries.

        Only merges when both sides are the same script (both CJK or both Latin).
        """
        sentence_end_re = re.compile(r'[。！？.!?\n]\s*$')
        sentence_start_re = re.compile(r'^[a-z\u4e00-\u9fff]')  # Starts with lowercase or CJK

        for i in range(len(blocks) - 1):
            if blocks[i].is_noise or blocks[i + 1].is_noise:
                continue
            if blocks[i].type != "text" or blocks[i + 1].type != "text":
                continue

            curr = blocks[i].content.rstrip()
            next_block = blocks[i + 1].content.lstrip()

            if not curr or not next_block:
                continue

            # Current block doesn't end with sentence-end punctuation
            # AND next block starts with lowercase/CJK
            if (not sentence_end_re.search(curr)
                    and sentence_start_re.match(next_block)):
                # v5.0: Only merge within same script (both Latin or both CJK)
                curr_is_cjk = '\u4e00' <= curr[-1] <= '\u9fff'
                next_is_cjk = '\u4e00' <= next_block[0] <= '\u9fff'
                curr_is_alpha = curr[-1].isascii() and curr[-1].isalpha()
                next_is_alpha = next_block[0].isascii() and next_block[0].isalpha()

                # Only merge if same script: both CJK or both ASCII alpha
                if not ((curr_is_cjk and next_is_cjk) or (curr_is_alpha and next_is_alpha)):
                    continue

                # Check if it looks like a broken word (no space at boundary)
                if curr[-1].isalpha() and next_block[0].isalpha():
                    blocks[i].content = curr + next_block[:50].split(" ")[0]
                    rest = " ".join(next_block[:50].split(" ")[1:]) + next_block[50:]
                    if rest.strip():
                        blocks[i + 1].content = rest
                    else:
                        blocks[i + 1].is_noise = True

    def _mark_empty(self, blocks: list[ContentBlock]):
        """Mark empty or whitespace-only blocks."""
        for b in blocks:
            if b.is_noise:
                continue
            cleaned = b.content.strip()
            if len(cleaned) < self.min_block_chars:
                b.is_noise = True
                b.quality_score = 0.0

    def _mark_duplicates(self, blocks: list[ContentBlock]):
        """Mark duplicate blocks (keep first occurrence)."""
        seen: dict[str, int] = {}
        for i, b in enumerate(blocks):
            if b.is_noise or b.type != "text":
                continue
            # Normalize for comparison
            key = re.sub(r'\s+', ' ', b.content.strip().lower())[:200]
            if len(key) < 20:
                continue
            if key in seen:
                b.is_noise = True
                b.quality_score = 0.1
            else:
                seen[key] = i

    def _fix_encoding(self, blocks: list[ContentBlock]):
        """Score encoding quality — cleanup is done by TextPreprocessor.

        v5.0: Removed duplicate cleanup (ligatures, dashes, control chars).
        TextPreprocessor.process() handles all of that. This method only
        scores blocks that still have encoding issues after preprocessing.
        """
        for b in blocks:
            if b.is_noise:
                continue
            text = b.content
            # Count remaining non-printable chars (after preprocessing)
            bad_chars = sum(1 for c in text if ord(c) > 0xFFFD or
                           (ord(c) < 32 and c not in '\n\r\t'))
            if bad_chars > 0:
                ratio = bad_chars / max(len(text), 1)
                if ratio > 0.1:
                    b.quality_score = max(0, b.quality_score - 0.5)
                    b.is_noise = ratio > 0.3

    # ── v5.0: Enterprise-specific noise detection ──

    def _mark_disclaimers(self, blocks: list[ContentBlock]):
        """Detect legal disclaimers, boilerplate, and standard notices.

        Common in: annual reports, contracts, prospectuses, audit reports.
        """
        disclaimer_re = re.compile(
            r'(本报告[仅供由].{0,30}(参考|阅读|内部))|'
            r'(免责[声明条款]|法律[声明条款]|disclaimer)|'
            r'(未经.{0,15}(书面)?许可.{0,15}(不得|禁止))|'
            r'(本文件.*机密.*不得.*传播)|'
            r'(forward.looking.statement)|'
            r'((过往|历史).*业绩.*不代表.*未来)|'
            r'(本报告.*不构成.*(投资|买卖|交易).*建议)|'
            r'(safe.harbor|风险提示.*投资者)|'
            r'(版权所有.*侵权必究)|'
            r'(copyright.{0,5}\d{4})|'
            r'(all\s+rights\s+reserved)',
            re.IGNORECASE
        )
        count = 0
        for b in blocks:
            if b.is_noise:
                continue
            if disclaimer_re.search(b.content) and len(b.content) < 800:
                b.is_noise = True
                b.quality_score = 0.05
                count += 1
        if count:
            logger.info(f"Marked {count} disclaimer/boilerplate blocks as noise")

    def _mark_watermarks(self, blocks: list[ContentBlock]):
        """Detect watermark text (repeated short text at consistent positions).

        Common watermarks: "机密", "CONFIDENTIAL", "内部资料", "DRAFT", company names.
        """
        watermark_re = re.compile(
            r'^[\s]*(机密|保密|CONFIDENTIAL|DRAFT|内部资料|'
            r'仅供内部|内部使用|请勿外传|禁止复制)[\s]*$',
            re.IGNORECASE | re.MULTILINE
        )
        for b in blocks:
            if b.is_noise:
                continue
            content = b.content.strip()
            if len(content) < 30 and watermark_re.search(content):
                b.is_noise = True
                b.quality_score = 0.0

    def _mark_garbled_cjk(self, blocks: list[ContentBlock]):
        """Detect garbled Chinese text by character frequency analysis.

        Real Chinese text has high-frequency common characters (的一是不了人)
        making up >10% of all CJK chars. Garbled text has near-uniform distribution.
        """
        HIGH_FREQ_CJK = frozenset("的一是不了人我在有他这中大来上个国们到说时地为子会作")
        for b in blocks:
            if b.is_noise or len(b.content) < 50:
                continue
            cjk_chars = [c for c in b.content if '\u4e00' <= c <= '\u9fff']
            if len(cjk_chars) < 30:
                continue
            high_freq_count = sum(1 for c in cjk_chars if c in HIGH_FREQ_CJK)
            ratio = high_freq_count / len(cjk_chars)
            if ratio < 0.03:  # <3% high-freq chars → almost certainly garbled
                b.is_noise = True
                b.quality_score = 0.0
                logger.debug(f"Garbled CJK block (p.{b.page}): "
                             f"freq ratio {ratio:.1%}, {len(cjk_chars)} CJK chars")

    def _mark_contact_blocks(self, blocks: list[ContentBlock]):
        """Mark blocks dominated by URLs, emails, phone numbers, addresses."""
        contact_re = re.compile(
            r'(https?://\S+|www\.\S+|[\w.]+@[\w.]+\.\w+|'
            r'[电話话][:：]\s*[\d\-+()（）\s]+|'
            r'[传傳]真[:：]\s*[\d\-+]+|'
            r'[地址][:：].{10,60})',
            re.IGNORECASE
        )
        for b in blocks:
            if b.is_noise or len(b.content) < 20:
                continue
            matches = contact_re.findall(b.content)
            if matches:
                contact_chars = sum(len(m) for m in matches)
                ratio = contact_chars / len(b.content)
                if ratio > 0.5 and len(b.content) < 300:
                    b.is_noise = True
                    b.quality_score = 0.1
