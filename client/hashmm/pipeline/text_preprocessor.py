"""Text Preprocessor v5.0 — enterprise document text normalization.

v5.0 improvements:
  - Full-width → half-width conversion (全角→半角)
  - Number format normalization (全角数字→半角, 千分位统一)
  - CJK spacing fix (PDF提取的 "中 文 变 这 样" → "中文变这样")
  - opencc as primary T2S engine (pure Python reimplementation, no C deps)
  - Expanded character-level fallback map (~300 enterprise-frequency chars)

Handles real-world enterprise document issues:
  - Traditional → Simplified Chinese (港股年报)
  - PDF encoding artifacts (ligatures, control chars)
  - Word boundary fixing (concatenated text from PDF)
  - Whitespace normalization
  - Number format preservation (1,234,567 stays intact)
"""
from __future__ import annotations
import re
from hashmm.utils import get_logger

logger = get_logger("hashmm.text_preprocessor")

# ── Full-width → Half-width ranges ──
# Full-width ASCII: U+FF01 (！) to U+FF5E (～) → U+0021 (!) to U+007E (~)
# Full-width space: U+3000 → U+0020

# ── PDF encoding fixes ──
_PDF_LIGATURES = {
    'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
}
_DASH_CHARS = {'−', '–', '—', '‐', '‑', '‒'}
# V308 修 F601 真 bug：原写法是
#     _QUOTE_MAP = {'"': '"', '"': '"', ''': "'", ''': "'", '「': '"', ...}
# 意图是把【中文弯引号】统一成直引号，但源文件里的弯引号在某次编辑中被"智能引号"
# 功能转成了普通 ASCII 引号 → key 变成同一个 '"' 重复 4 次、'\'' 重复 2 次，
# 后者覆盖前者，弯引号 “ ” ‘ ’ 压根没进字典 → 引号规范化【完全失效】
# （直接拉低 RAG 文本预处理与检索匹配质量）。
# 改用 Unicode 转义显式书写，编辑器再也无法改写它们。
_QUOTE_MAP = {
    '\u201c': '"',   # “ 左双引号
    '\u201d': '"',   # ” 右双引号
    '\u2018': "'",   # ‘ 左单引号
    '\u2019': "'",   # ’ 右单引号
    '\u300c': '"',   # 「
    '\u300d': '"',   # 」
    '\u300e': '"',   # 『
    '\u300f': '"',   # 』
}

# ── Word boundary patterns ──
_UPPER_LOWER_RE = re.compile(r'([A-Z]{2,})([a-z]{2,})')
_LOWER_UPPER_RE = re.compile(r'([a-z]{3,})([A-Z])')
_PUNCT_NOSPACE_RE = re.compile(r'([.!?,;:])([A-Za-z\u4e00-\u9fff])')

# ── CJK spacing fix: detect "每 个 字 之 间 都 有 空 格" ──
_CJK_SPACE_RE = re.compile(r'([\u4e00-\u9fff]) (?=[\u4e00-\u9fff])')

# ── Traditional → Simplified fallback map (top ~300 enterprise-frequency chars) ──
_T2S_MAP = {
    '與': '与', '為': '为', '從': '从', '這': '这', '國': '国',
    '學': '学', '將': '将', '對': '对', '經': '经', '過': '过',
    '開': '开', '關': '关', '體': '体', '點': '点', '當': '当',
    '機': '机', '發': '发', '現': '现', '種': '种', '後': '后',
    '進': '进', '還': '还', '應': '应', '該': '该', '認': '认',
    '識': '识', '問': '问', '題': '题', '號': '号', '區': '区',
    '產': '产', '業': '业', '務': '务', '報': '报', '營': '营',
    '運': '运', '資': '资', '負': '负', '債': '债', '權': '权',
    '損': '损', '額': '额', '準': '准', '備': '备', '佔': '占',
    '設': '设', '計': '计', '處': '处', '費': '费', '線': '线',
    '結': '结', '構': '构', '則': '则', '條': '条', '幣': '币',
    '盤': '盘', '櫃': '柜', '繳': '缴', '僱': '雇', '員': '员',
    '億': '亿', '萬': '万', '於': '于', '據': '据', '訂': '订',
    '購': '购', '銷': '销', '總': '总', '項': '项', '實': '实',
    '際': '际', '環': '环', '監': '监', '變': '变', '動': '动',
    '確': '确', '許': '许', '續': '续', '間': '间', '減': '减',
    '復': '复', '類': '类', '擁': '拥', '層': '层', '範': '范',
    '圍': '围', '維': '维', '護': '护', '執': '执', '達': '达',
    '遠': '远', '選': '选', '擇': '择', '轉': '转', '換': '换',
    '獲': '获', '獎': '奖', '優': '优', '勢': '势', '競': '竞',
    '爭': '争', '戰': '战', '術': '术', '價': '价', '漲': '涨',
    '長': '长', '張': '张', '場': '场', '廠': '厂', '廣': '广',
    '慶': '庆', '療': '疗', '醫': '医', '藥': '药', '農': '农',
    '鄉': '乡', '縣': '县', '鎮': '镇', '網': '网', '電': '电',
    '視': '视', '話': '话', '訊': '讯', '車': '车', '輛': '辆',
    '輸': '输', '鐵': '铁', '鋼': '钢', '銀': '银',
    '錢': '钱', '賬': '账', '貿': '贸', '貨': '货', '質': '质',
    '領': '领', '導': '导', '團': '团', '組': '组',
    '織': '织', '聯': '联', '繫': '系', '統': '统', '規': '规',
    '劃': '划', '畫': '画', '書': '书', '記': '记', '議': '议',
    '論': '论', '證': '证', '評': '评', '審': '审', '檢': '检',
    '調': '调', '節': '节', '約': '约', '終': '终',
    '齊': '齐', '補': '补', '辦': '办', '歷': '历', '歸': '归',
    '屬': '属', '職': '职', '責': '责',
    '陳': '陈', '碼': '码', '頁': '页',
    '見': '见', '觀': '观', '覽': '览', '訪': '访', '試': '试',
    '驗': '验', '積': '积', '極': '极', '標': '标',
    '複': '复', '雜': '杂', '傳': '传', '輕': '轻',
    '雙': '双', '隻': '只', '裡': '里', '麼': '么', '適': '适',
    '顯': '显', '離': '离', '難': '难', '頭': '头',
    '歲': '岁', '陽': '阳', '陰': '阴', '衛': '卫',
    '隊': '队', '黨': '党', '寶': '宝', '藝': '艺', '豐': '丰',
    '灣': '湾', '華': '华', '東': '东', '鑰': '钥', '鍵': '键',
    '錄': '录', '盡': '尽', '盜': '盗', '願': '愿',
}
_TRADITIONAL_CHARS = frozenset(_T2S_MAP.keys())

# ── Full-width number map ──
_FW_DIGIT_MAP = str.maketrans('０１２３４５６７８９', '0123456789')
_FW_ALPHA_MAP = str.maketrans(
    'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ',
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
)


class TextPreprocessor:
    """Enterprise document text preprocessor.

    Call .process(text) on all text before chunking or indexing.
    Handles: encoding artifacts, T2S conversion, full-width normalization,
    CJK spacing, number formats, word boundaries.
    """

    def __init__(self, convert_traditional: bool = True):
        self.convert_traditional = convert_traditional
        self._opencc = None  # Lazy loaded

    def process(self, text: str) -> str:
        """Full preprocessing pipeline."""
        if not text:
            return text

        # Phase 1: Encoding cleanup
        text = self._remove_control_chars(text)
        text = self._clean_cid_codes(text)
        text = self._fix_pdf_encoding(text)

        # Phase 2: Character normalization
        text = self._fullwidth_to_halfwidth(text)
        text = self._normalize_numbers(text)

        # Phase 3: CJK-specific fixes
        if self.convert_traditional and self._has_traditional(text):
            text = self._t2s(text)
        text = self._fix_cjk_spacing(text)

        # Phase 4: Word/whitespace cleanup
        text = self._fix_word_boundaries(text)
        text = self._normalize_whitespace(text)

        return text

    # ── Phase 1: Encoding cleanup ──

    def _clean_cid_codes(self, text: str) -> str:
        """Remove PDF CID codes like (cid:12345) that indicate font decoding failure."""
        if "(cid:" not in text:
            return text
        text = re.sub(r'\(cid:\d+\)', '', text)
        text = re.sub(r'  +', ' ', text)
        return text

    def _remove_control_chars(self, text: str) -> str:
        """Remove NULL bytes, BOM, and non-printable control characters."""
        text = text.replace('\x00', '')
        text = text.replace('\ufeff', '')  # BOM
        text = text.replace('\ufffd', '')  # Replacement char
        text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f]', '', text)
        return text

    def _fix_pdf_encoding(self, text: str) -> str:
        """Fix PDF-specific encoding issues: ligatures, dashes, quotes."""
        for old, new in _PDF_LIGATURES.items():
            text = text.replace(old, new)
        for dash in _DASH_CHARS:
            text = text.replace(dash, '-')
        for old, new in _QUOTE_MAP.items():
            text = text.replace(old, new)
        text = text.replace('\xa0', ' ')  # Non-breaking space
        return text

    # ── Phase 2: Character normalization (NEW in v5.0) ──

    def _fullwidth_to_halfwidth(self, text: str) -> str:
        """Convert full-width ASCII characters to half-width.

        Full-width chars are common in Chinese documents — e.g., Ａ instead of A,
        ３ instead of 3, （ instead of (.
        """
        result = []
        for c in text:
            code = ord(c)
            # Full-width ASCII: FF01-FF5E → 0021-007E
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            # Full-width space
            elif code == 0x3000:
                result.append(' ')
            else:
                result.append(c)
        return ''.join(result)

    def _normalize_numbers(self, text: str) -> str:
        """Normalize number formats for consistent retrieval.

        - Full-width digits → half-width (handled by fullwidth_to_halfwidth)
        - Chinese comma in numbers: 3，659 → 3,659
        - Preserve meaningful number formats: 1,234,567.89
        """
        # Chinese comma between digits → half-width comma (thousand separator)
        text = re.sub(r'(\d)，(\d{3})', r'\1,\2', text)
        # Multiple consecutive commas in numbers (malformed)
        text = re.sub(r'(\d),{2,}(\d)', r'\1,\2', text)
        return text

    # ── Phase 3: CJK-specific fixes (NEW in v5.0) ──

    def _fix_cjk_spacing(self, text: str) -> str:
        """Fix PDF extraction artifact: spaces between every CJK character.

        PDF extractors sometimes insert a space between each character:
        "中 文 变 成 这 样" → "中文变成这样"

        Only fixes when it's a consistent pattern (>5 consecutive spaced chars).
        """
        # Detect pattern: CJK SPACE CJK SPACE CJK (at least 3 pairs)
        if not re.search(r'[\u4e00-\u9fff] [\u4e00-\u9fff] [\u4e00-\u9fff]', text):
            return text

        # Count how many CJK-space-CJK pairs vs normal CJK-CJK pairs
        spaced_pairs = len(re.findall(r'[\u4e00-\u9fff] [\u4e00-\u9fff]', text))
        direct_pairs = len(re.findall(r'[\u4e00-\u9fff][\u4e00-\u9fff]', text))

        # Only fix if spaced pairs dominate (>60% of all CJK adjacencies)
        total = spaced_pairs + direct_pairs
        if total > 0 and spaced_pairs / total > 0.6:
            text = _CJK_SPACE_RE.sub(r'\1', text)
            logger.info(f"Fixed CJK spacing: removed {spaced_pairs} inter-character spaces")

        return text

    def _has_traditional(self, text: str) -> bool:
        """Detect if text contains Traditional Chinese characters."""
        sample = text[:2000]
        count = sum(1 for c in sample if c in _TRADITIONAL_CHARS)
        return count > len(sample) * 0.003  # > 0.3% traditional chars

    def _t2s(self, text: str) -> str:
        """Convert Traditional Chinese to Simplified.

        Uses opencc (most accurate) with fallback to character-level mapping.
        """
        # Try opencc first (most accurate, handles context-dependent conversions)
        try:
            if self._opencc is None:
                try:
                    import opencc
                    self._opencc = opencc.OpenCC('t2s')
                except ImportError:
                    pass

            if self._opencc is not None:
                return self._opencc.convert(text)
        except Exception as e:
            logger.debug(f"opencc conversion failed, using fallback: {e}")

        # Fallback: character-level mapping (less accurate but always works)
        return ''.join(_T2S_MAP.get(c, c) for c in text)

    # ── Phase 4: Word/whitespace cleanup ──

    def _fix_word_boundaries(self, text: str) -> str:
        """Fix concatenated words from PDF extraction."""
        text = _UPPER_LOWER_RE.sub(r'\1 \2', text)
        text = _LOWER_UPPER_RE.sub(r'\1 \2', text)
        text = _PUNCT_NOSPACE_RE.sub(r'\1 \2', text)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        """Collapse multiple spaces, preserve meaningful newlines."""
        text = re.sub(r'[^\S\n]+', ' ', text)  # Multiple spaces → single (keep \n)
        text = re.sub(r'\n{3,}', '\n\n', text)  # 3+ newlines → 2
        return text.strip()


# Module-level singleton for convenience
_preprocessor = TextPreprocessor()


def preprocess(text: str) -> str:
    """Quick access to the default preprocessor."""
    return _preprocessor.process(text)
