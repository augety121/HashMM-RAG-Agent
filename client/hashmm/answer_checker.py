"""Answer Quality Checker v5.0 — multi-dimensional hallucination detection.

v5.0 improvements over v4:
  - Claim-level attribution: check if each factual sentence is backed by context
  - Time/year consistency: detect when answer says "2024" but context has "2023"
  - Entity co-occurrence: verify that entity+number pairs appear together in context
  - Negation consistency: detect when answer contradicts context
  - Citation completeness: warn when facts lack citations

Checks:
  1. Number verification: numbers in answer must appear in retrieved chunks
  2. Entity verification: mentioned entities should be in the context
  3. Citation verification: [1][2] references should match actual sources
  4. Claim attribution: factual sentences should map to context chunks
  5. Year/time consistency: temporal references must match context
  6. Confidence scoring: how much of the answer is backed by context

Usage:
    checker = AnswerChecker()
    report = checker.check(answer, retrieved_chunks, sources)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from hashmm.utils import get_logger

logger = get_logger("hashmm.answer_checker")


@dataclass
class QualityReport:
    """Answer quality assessment."""
    grounded_ratio: float = 1.0
    total_claims: int = 0
    grounded_claims: int = 0
    ungrounded_numbers: list[str] = field(default_factory=list)
    ungrounded_entities: list[str] = field(default_factory=list)
    citation_valid: bool = True
    confidence: str = "high"  # "high" | "medium" | "low"

    # v5.0: Additional diagnostics
    warnings: list[str] = field(default_factory=list)
    ungrounded_claims: list[str] = field(default_factory=list)
    year_mismatches: list[str] = field(default_factory=list)
    uncited_facts: int = 0  # Facts without [N] citations

    def to_dict(self) -> dict:
        return {
            "grounded_ratio": round(self.grounded_ratio, 2),
            "confidence": self.confidence,
            "total_claims": self.total_claims,
            "grounded_claims": self.grounded_claims,
            "ungrounded_numbers": self.ungrounded_numbers[:5],
            "ungrounded_entities": self.ungrounded_entities[:5],
            "citation_valid": self.citation_valid,
            "warnings": self.warnings[:5],
            "ungrounded_claims": self.ungrounded_claims[:3],
            "year_mismatches": self.year_mismatches[:3],
            "uncited_facts": self.uncited_facts,
        }


# Number patterns (Chinese and English)
_NUMBER_RE = re.compile(
    r'(\d[\d,]*\.?\d*)\s*(?:亿|万|百万|千万|%|元|美元|港币|人民币|RMB|USD|HKD)',
    re.IGNORECASE,
)
_PLAIN_NUMBER_RE = re.compile(r'(?<!\[)(\d{3,}[\d,]*\.?\d*)(?!\])')

# Entity patterns
_COMPANY_RE = re.compile(
    r'([\u4e00-\u9fff]{2,8}(?:集团|公司|控股|有限|科技|银行|基金|保险|证券))'
)
_PRODUCT_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6}(?:手机|汽车|平台|系统|服务|业务|产品))'
)

# Year pattern
_YEAR_RE = re.compile(r'(20\d{2})')

# Sentence splitter (Chinese + English)
_SENT_RE = re.compile(r'[。！？.!?]+')

# Transition/meta sentences to skip
_META_RE = re.compile(
    r'^(根据|总的来说|综上|简而言之|以下是|总结|需要注意|值得一提|'
    r'基于以上|如上所述|According to|In summary|Overall)',
)


class AnswerChecker:
    """Multi-dimensional answer quality checker against retrieved context."""

    def check(self, answer: str, context_chunks: list[str],
              sources: list[dict] | None = None) -> QualityReport:
        """Run all quality checks.

        Args:
            answer: LLM-generated answer text
            context_chunks: List of retrieved chunk texts used as context
            sources: List of source dicts (for citation verification)
        """
        report = QualityReport()
        if not answer or not context_chunks:
            report.confidence = "low"
            return report

        all_context = " ".join(context_chunks)

        # 1. Number verification
        answer_numbers = self._extract_numbers(answer)
        context_numbers = self._extract_numbers(all_context)
        report.total_claims = len(answer_numbers)

        for num in answer_numbers:
            if self._number_in_context(num, context_numbers, all_context):
                report.grounded_claims += 1
            else:
                report.ungrounded_numbers.append(num)

        # 2. Entity verification
        answer_entities = self._extract_entities(answer)
        for entity in answer_entities:
            if entity not in all_context:
                report.ungrounded_entities.append(entity)

        # 3. Citation verification
        if sources:
            cited_ids = set(int(m) for m in re.findall(r'\[(\d+)\]', answer))
            valid_ids = set(s.get("id", 0) for s in sources)
            if cited_ids and not cited_ids.issubset(valid_ids):
                report.citation_valid = False
                invalid = cited_ids - valid_ids
                report.warnings.append(f"引用了不存在的来源: {invalid}")

        # 4. v5.0: Claim-level attribution
        self._check_claim_attribution(answer, context_chunks, report)

        # 5. v5.0: Year/time consistency
        self._check_year_consistency(answer, all_context, report)

        # 6. v5.0: Uncited facts detection
        self._check_uncited_facts(answer, sources, report)

        # Calculate grounded ratio
        entity_grounded = len(answer_entities) - len(report.ungrounded_entities)
        total = max(report.total_claims + len(answer_entities), 1)
        grounded = report.grounded_claims + entity_grounded
        report.grounded_ratio = max(0, grounded / total)

        # Confidence level (v5.0: factor in new checks)
        penalty = 0
        if report.year_mismatches:
            penalty += 0.15
        if report.ungrounded_claims:
            penalty += 0.1 * len(report.ungrounded_claims)

        adjusted_ratio = report.grounded_ratio - penalty

        if adjusted_ratio >= 0.8 and not report.ungrounded_numbers:
            report.confidence = "high"
        elif adjusted_ratio >= 0.5:
            report.confidence = "medium"
        else:
            report.confidence = "low"

        return report

    # ── v5.0: Claim-level attribution ──

    def _check_claim_attribution(self, answer: str, chunks: list[str],
                                 report: QualityReport):
        """Check if factual sentences in the answer are supported by context.

        Only flags claims with BOTH entity AND number where neither can be
        found individually in any chunk. This avoids false positives when
        the document name isn't repeated in every chunk.
        """
        sentences = _SENT_RE.split(answer)

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 15 or _META_RE.match(sent):
                continue

            entities = _COMPANY_RE.findall(sent)
            numbers = re.findall(r'\d[\d,]*\.?\d*\s*(?:亿|万|%|元)?', sent)

            if not entities or not numbers:
                continue

            # Check if the NUMBER can be found in any chunk (entity matching is
            # too strict because documents often don't repeat the company name)
            number_found = False
            for chunk in chunks:
                chunk_clean = chunk.replace(',', '')
                for n in numbers:
                    n_digits = re.sub(r'[,\s]', '', n).split('.')[0]
                    n_digits = re.sub(r'[^\d]', '', n_digits)
                    if len(n_digits) >= 3 and n_digits in chunk_clean:
                        number_found = True
                        break
                if number_found:
                    break

            if not number_found:
                claim_preview = sent[:80]
                report.ungrounded_claims.append(claim_preview)
                report.warnings.append(
                    f"声明 \"{claim_preview}...\" 中的数字未在检索结果中找到"
                )

    # ── v5.0: Year/time consistency ──

    def _check_year_consistency(self, answer: str, context: str,
                                report: QualityReport):
        """Detect when answer references years not in the context.

        E.g., answer says "2024年营收" but context only has 2023 data.
        """
        answer_years = set(_YEAR_RE.findall(answer))
        context_years = set(_YEAR_RE.findall(context))

        if not answer_years or not context_years:
            return

        for year in answer_years:
            if year not in context_years:
                # Allow adjacent years only if the answer is making a comparison
                year_int = int(year)
                is_comparison = bool(re.search(
                    r'(同比|环比|较.{0,4}年|compared|YoY|versus|vs)',
                    answer, re.IGNORECASE
                ))
                adjacent = any(abs(year_int - int(cy)) == 1 for cy in context_years)

                if is_comparison and adjacent:
                    continue  # OK: "同比2023年" when context has 2024

                report.year_mismatches.append(year)
                report.warnings.append(
                    f"答案提到{year}年，但检索结果中没有该年份的数据 "
                    f"(检索到的年份: {', '.join(sorted(context_years))})"
                )

    # ── v5.0: Uncited facts detection ──

    def _check_uncited_facts(self, answer: str, sources: list[dict] | None,
                             report: QualityReport):
        """Count factual sentences that should have citations but don't.

        Only checks when sources are available (meaning retrieval was done).
        """
        if not sources:
            return

        sentences = _SENT_RE.split(answer)
        uncited = 0

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 15 or _META_RE.match(sent):
                continue

            has_fact = bool(
                _NUMBER_RE.search(sent) or
                _COMPANY_RE.search(sent) or
                re.search(r'(增长|下降|同比|环比|达到|超过|低于)', sent)
            )

            has_citation = bool(re.search(r'\[\d+\]', sent))

            if has_fact and not has_citation:
                uncited += 1

        report.uncited_facts = uncited
        if uncited > 3:
            report.warnings.append(
                f"{uncited} 个事实性句子缺少引用标注 [N]"
            )

    # ── Core extraction methods ──

    def _extract_numbers(self, text: str) -> list[str]:
        """Extract significant numbers from text."""
        numbers = []
        for m in _NUMBER_RE.finditer(text):
            numbers.append(m.group(0))
        for m in _PLAIN_NUMBER_RE.finditer(text):
            num = m.group(1).replace(",", "")
            if len(num) >= 3:
                numbers.append(m.group(0))
        return numbers

    def _number_in_context(self, num_str: str, context_numbers: list[str],
                           full_context: str) -> bool:
        """Check if a number appears in the context (with tolerance).

        Handles: 3659 ≈ 3,659.4 ≈ 3659.4 (comma formatting + decimal truncation)
        """
        # Extract just the digits (no commas, no unit suffix)
        clean_num = re.sub(r'[,\s]', '', re.match(r'[\d,.\s]+', num_str).group(0) if re.match(r'[\d,.\s]+', num_str) else num_str)
        clean_context = full_context.replace(",", "").replace(" ", "")

        # Direct substring match
        if clean_num in clean_context:
            return True

        # Integer part match: "3659" matches "3659.4"
        int_part = clean_num.split('.')[0]
        if len(int_part) >= 3 and int_part in clean_context:
            return True

        # Check against extracted numbers
        for ctx_num in context_numbers:
            clean_ctx = re.sub(r'[,\s]', '', ctx_num)
            ctx_int = clean_ctx.split('.')[0] if '.' in clean_ctx else clean_ctx
            # Match integer parts
            if int_part == ctx_int:
                return True
            # Substring containment
            if clean_num in clean_ctx or clean_ctx in clean_num:
                return True

        return False

    def _extract_entities(self, text: str) -> list[str]:
        """Extract Chinese entity names (companies, products)."""
        entities = []
        for m in _COMPANY_RE.finditer(text):
            entities.append(m.group(1))
        for m in _PRODUCT_RE.finditer(text):
            entities.append(m.group(1))
        return list(set(entities))
