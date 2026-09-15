"""Citation validator — post-processes LLM output to fix hallucinated citations.

Problem: LLM sometimes generates [3] when only 2 sources exist, or
references [0] which is not valid. This module cleans up such issues.

Usage:
    from hashmm.api.core.citation_validator import validate_citations

    cleaned = validate_citations(llm_output, num_sources=3)
    # [1] → kept, [2] → kept, [3] → kept, [4] → removed, [0] → removed
"""
from __future__ import annotations

import re

# Matches [N] or [N][M] style citations
_CITE_RE = re.compile(r'\[(\d+)\]')


def validate_citations(text: str, num_sources: int) -> str:
    """Remove citation markers that reference non-existent sources.

    Args:
        text: LLM output containing [1], [2], etc.
        num_sources: Number of actual sources provided to the LLM.

    Returns:
        Text with invalid citations removed.
    """
    if num_sources <= 0:
        # No sources — remove ALL citation markers
        return _CITE_RE.sub('', text)

    def _replace(m: re.Match) -> str:
        n = int(m.group(1))
        if 1 <= n <= num_sources:
            return m.group(0)  # valid, keep
        return ''  # invalid, remove

    cleaned = _CITE_RE.sub(_replace, text)

    # Clean up double spaces left by removal
    cleaned = re.sub(r'  +', ' ', cleaned)
    # Clean up orphaned punctuation patterns like " ，" or " 。"
    cleaned = re.sub(r' ([，。；：、])', r'\1', cleaned)

    return cleaned


def extract_cited_indices(text: str) -> set[int]:
    """Extract all citation indices from text.

    Returns set of integers, e.g. {1, 2, 4}.
    """
    return {int(m.group(1)) for m in _CITE_RE.finditer(text)}
