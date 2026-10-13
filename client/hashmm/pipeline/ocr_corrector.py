"""OCR Post-Corrector v5.1 — fix common OCR misrecognition errors.

Handles character-level errors that are NOT traditional→simplified conversion:
these are OCR engine mistakes where visually similar characters are confused.

Two correction strategies:
  1. Phrase-level: known multi-char patterns (highest accuracy)
  2. Char-level: single-char substitutions in known contexts

Built from real OCR error analysis on Chinese enterprise documents (annual reports,
financial statements, contracts, prospectuses).

Usage:
    from hashmm.pipeline.ocr_corrector import OCRCorrector
    corrector = OCRCorrector()
    fixed = corrector.correct("合供财务报表附")
    # → "合并财务报表附注"
"""
from __future__ import annotations
import re
from hashmm.utils import get_logger

logger = get_logger("hashmm.pipeline.ocr_corrector")


# ── Phrase-level corrections (highest confidence) ──
# Format: wrong_phrase → correct_phrase
# These are multi-character patterns that are unambiguous.

_PHRASE_CORRECTIONS: dict[str, str] = {
    # Financial statements
    "合供财务报表": "合并财务报表",
    "合餠财务报表": "合并财务报表",
    "合幷财务报表": "合并财务报表",
    "合倂财务报表": "合并财务报表",
    "财务报表附": "财务报表附注",  # Only when not already "附注"
    "另有明外": "另有说明外",
    "另有明": "另有说明",
    "除另有明": "除另有说明",
    "人民列示": "人民币列示",
    "以人民列示": "以人民币列示",
    "赈面值": "账面值",
    "帐面值": "账面值",
    "唇级": "层级",
    "三唇级": "三层级",
    "第三唇级": "第三层级",
    "公允价值唇级": "公允价值层级",
    "列后综合": "列后综合",

    # Audit / Governance
    "独立核数飞": "独立核数师",
    "核数飞报告": "核数师报告",
    "审计飞": "审计师",
    "董事舍": "董事会",
    "董事舍报告": "董事会报告",

    # Units / Accounting
    "人民千元": "人民币千元",
    "人民百万元": "人民币百万元",
    "人民亿元": "人民币亿元",
    "美元千元": "美元千元",
    "以千元计": "以千元计",

    # Common financial phrases
    "营业额入": "营业收入",
    "净利閏": "净利润",
    "毛利閏": "毛利润",
    "应收帐款": "应收账款",
    "应付帐款": "应付账款",
    "折旧摊销": "折旧及摊销",
    "无形资严": "无形资产",
    "固定资严": "固定资产",
    "流动资严": "流动资产",
    "非流动资严": "非流动资产",
    "商誉减惮": "商誉减值",
    "资产减惮": "资产减值",
    "减惮损失": "减值损失",
    "减惮准备": "减值准备",

    # Regulatory
    "上市规则": "上市规则",
    "证监舍": "证监会",
    "交易所上市规贝lj": "交易所上市规则",
    "公司条侈lj": "公司条例",

    # Other common OCR errors
    "股份有限公同": "股份有限公司",
    "有限公同": "有限公司",
    "子公同": "子公司",
    "母公同": "母公司",
    "联营公同": "联营公司",
    "合营公同": "合营公司",
    "截至12月": "截至12月",
}

# ── Context-sensitive char corrections ──
# Format: (wrong_char, correct_char, context_regex)
# Only apply when the character appears in a specific context.

_CONTEXT_CORRECTIONS: list[tuple[str, str, str]] = [
    # "供" → "并" only when preceded by "合" and followed by "财"
    ("供", "并", r"合.财"),
    # "餠" → "并" only near "财务"
    ("餠", "并", r"合.财"),
    # "明" → "说明" — tricky, only fix when standalone "明" follows "另有"
    # Handled by phrase corrections above
    # "赈" → "账" when followed by "面值"
    ("赈", "账", r".面值"),
    # "唇" → "层" when followed by "级"
    ("唇", "层", r".级"),
    # "飞" → "师" when preceded by "核数" or "审计"
    ("飞", "师", r"(?:核数|审计)."),
    # "舍" → "会" when preceded by "董事" or "证监"
    ("舍", "会", r"(?:董事|证监)."),
    # "严" → "产" when preceded by "资"
    ("严", "产", r"资."),
    # "惮" → "值" when preceded by "减"
    ("惮", "值", r"减."),
    # "閏" → "润" when preceded by "利"
    ("閏", "润", r"利."),
    # "同" → "司" when preceded by "公"
    ("同", "司", r"公."),
]

# ── Page header noise patterns ──
# Lines at the start of chunks that are just page numbers or repeated titles
_PAGE_NUM_LINE_RE = re.compile(r'^\d{1,4}\s*$')
_SHORT_TITLE_RE = re.compile(r'^[\u4e00-\u9fff]{2,8}$')


class OCRCorrector:
    """Fix common OCR errors in Chinese enterprise documents.

    Call .correct(text) on all text during or after parsing.
    Safe to apply multiple times (corrections are idempotent).
    """

    def __init__(self):
        # Pre-sort phrase corrections: longest first for greedy matching
        self._phrases = sorted(
            _PHRASE_CORRECTIONS.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        self._stats = {"phrases_fixed": 0, "chars_fixed": 0}

    def correct(self, text: str) -> str:
        """Apply all OCR corrections to text.

        Order:
        1. Phrase-level corrections (highest confidence)
        2. Context-sensitive char corrections
        3. Clean up page number lines at block start
        """
        if not text or len(text) < 3:
            return text

        # Phase 1: Phrase corrections (longest-first, avoid double replacement)
        for wrong, right in self._phrases:
            if wrong in text and right not in text:
                text = text.replace(wrong, right)
                self._stats["phrases_fixed"] += 1

        # Phase 2: Context-sensitive char corrections
        for wrong_char, right_char, context in _CONTEXT_CORRECTIONS:
            if wrong_char in text:
                # Only replace if context matches
                pattern = context.replace(".", wrong_char, 1)
                if re.search(pattern, text):
                    text = text.replace(wrong_char, right_char)
                    self._stats["chars_fixed"] += 1

        return text

    def correct_blocks(self, blocks: list) -> int:
        """Apply OCR correction to a list of ContentBlocks.

        Three passes:
        1. Strip leading page number lines (e.g., "336\\n")
        2. Detect and strip repeated document titles (e.g., "小米集团\\n")
        3. Apply OCR phrase/char corrections

        Returns:
            Number of blocks modified.
        """
        modified = 0

        # Pass 1: Strip leading page number lines
        for b in blocks:
            if b.is_noise or not b.content.strip():
                continue
            lines = b.content.split('\n')
            if lines and _PAGE_NUM_LINE_RE.match(lines[0].strip()):
                b.content = '\n'.join(lines[1:])
                modified += 1

        # Pass 2: Detect repeated first-line titles (after page numbers stripped)
        # v5.2: Log detected titles but DON'T strip them from content.
        # Stripping "小米集团" from every chunk breaks BM25 search for "小米".
        # Instead, mark them as metadata for potential future use.
        first_lines: dict[str, int] = {}
        non_noise_count = 0
        for b in blocks:
            if b.is_noise or not b.content.strip():
                continue
            non_noise_count += 1
            first_line = b.content.strip().split('\n')[0].strip()
            if first_line and len(first_line) < 20:
                first_lines[first_line] = first_lines.get(first_line, 0) + 1

        repeated_titles = {
            line for line, count in first_lines.items()
            if count > max(non_noise_count * 0.3, 2)
            and _SHORT_TITLE_RE.match(line)
        }

        if repeated_titles:
            logger.info(f"Detected repeated titles (kept for search): {repeated_titles}")
            # Store as metadata on blocks but DON'T remove from content
            for b in blocks:
                if b.is_noise or not b.content.strip():
                    continue
                first_line = b.content.strip().split('\n')[0].strip()
                if first_line in repeated_titles:
                    # Mark as doc_title metadata (accessible via block attributes)
                    if hasattr(b, 'section') and not b.section:
                        b.section = first_line

        # Pass 3: OCR phrase/char corrections
        for b in blocks:
            if b.is_noise or not b.content.strip():
                continue
            original = b.content
            b.content = self.correct(b.content)
            if b.content != original:
                modified += 1

        if modified > 0:
            logger.info(f"OCR corrected {modified} blocks "
                        f"(phrases: {self._stats['phrases_fixed']}, "
                        f"chars: {self._stats['chars_fixed']})")

        return modified


# Module-level singleton
_corrector = OCRCorrector()


def correct_ocr(text: str) -> str:
    """Quick access to OCR correction."""
    return _corrector.correct(text)
