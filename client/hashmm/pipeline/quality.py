"""Document Quality Assessor — score and diagnose parsed documents.

Detects:
  - Incomplete extraction (empty pages)
  - Flow-of-consciousness writing (low structure variance)
  - High noise ratio
  - Encoding problems
  - Duplicate content
"""
from __future__ import annotations
import re
from collections import Counter
from hashmm.pipeline.content_block import ParsedDocument, QualityReport, ContentBlock
from hashmm.utils import get_logger

logger = get_logger("hashmm.pipeline.quality")


class QualityAssessor:
    """Assess document quality after parsing."""

    def assess(self, doc: ParsedDocument) -> QualityReport:
        """Generate a quality report for a parsed document."""
        report = QualityReport()
        blocks = [b for b in doc.blocks if not b.is_noise]

        if not blocks:
            report.overall_score = 0.0
            report.issues.append("文档解析结果为空")
            report.suggestions.append("检查文件是否损坏，或尝试其他解析器")
            return report

        total_chars = sum(b.char_count for b in blocks)
        noise_chars = sum(b.char_count for b in doc.blocks if b.is_noise)

        # 1. Extractable ratio
        if doc.num_pages > 1:
            pages_with_content = len(set(b.page for b in blocks if b.page > 0))
            report.extractable_ratio = pages_with_content / max(doc.num_pages, 1)
            empty_pages = []
            all_pages = set(range(1, doc.num_pages + 1))
            content_pages = set(b.page for b in blocks if b.page > 0)
            empty_pages = sorted(all_pages - content_pages)
            if empty_pages:
                report.empty_pages = empty_pages[:20]  # Cap at 20
                report.issues.append(f"{len(empty_pages)} 页提取为空（可能是扫描图片页）")
                report.suggestions.append("可使用 OCR 对空白页重新提取")

        # 2. Noise ratio
        report.noise_ratio = noise_chars / max(total_chars + noise_chars, 1)
        if report.noise_ratio > 0.2:
            report.issues.append(f"噪声比例较高 ({report.noise_ratio:.0%})")

        # 3. Structure score (heading variance)
        text_blocks = [b for b in blocks if b.type == "text"]
        if text_blocks:
            lengths = [b.char_count for b in text_blocks]
            avg_len = sum(lengths) / len(lengths)
            report.avg_paragraph_length = avg_len

            if len(lengths) > 2:
                variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
                std = variance ** 0.5
                cv = std / max(avg_len, 1)  # Coefficient of variation
                report.structure_score = min(cv / 0.5, 1.0)  # CV > 0.5 = good structure

                if cv < 0.15:
                    report.issues.append("文档结构性差（段落长度均匀，可能是流水账写法）")
                    report.suggestions.append("检索精度可能降低，建议人工检查文档逻辑")

        # 4. Unique sentence ratio
        all_sentences = []
        for b in text_blocks:
            sents = re.split(r'[。！？.!?\n]+', b.content)
            all_sentences.extend(s.strip() for s in sents if len(s.strip()) > 10)
        if all_sentences:
            unique = len(set(all_sentences))
            report.unique_sentence_ratio = unique / len(all_sentences)
            if report.unique_sentence_ratio < 0.7:
                report.issues.append(f"文档有 {1 - report.unique_sentence_ratio:.0%} 重复内容")

        # 5. Encoding issues
        for b in blocks:
            bad = sum(1 for c in b.content if ord(c) > 0xFFFD)
            if bad > 0:
                report.encoding_issues += 1
        if report.encoding_issues > 0:
            report.issues.append(f"{report.encoding_issues} 个内容块有编码问题")
            report.suggestions.append("尝试用不同编码重新解析")

        # 6. Section detection
        has_sections = len(doc.sections) >= 2
        if not has_sections and total_chars > 5000:
            report.issues.append("未检测到章节标题结构")
            report.structure_score *= 0.7

        # Overall score
        report.overall_score = (
            report.extractable_ratio * 0.3 +
            (1 - report.noise_ratio) * 0.2 +
            report.structure_score * 0.2 +
            report.unique_sentence_ratio * 0.15 +
            (1.0 if report.encoding_issues == 0 else 0.5) * 0.15
        )
        report.overall_score = round(min(max(report.overall_score, 0), 1), 3)

        return report
