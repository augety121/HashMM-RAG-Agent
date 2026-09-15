"""Cross-document entity canonicalisation.

The KG builders count entities by raw surface form, so "小米" / "小米集团" /
"Xiaomi" become three separate nodes even though they're one entity. That
fragments the graph and weakens multi-hop reasoning. This module maps surface
forms to a canonical key so mentions across documents merge into one node.

Design principle: **conservative**. A wrong merge (two different entities
collapsed) is worse than a missed merge (one entity left split), because the
former silently corrupts answers. So we only normalise transformations that are
safe by construction:

  - full-width → half-width, whitespace/案 punctuation trimming, case folding
    (for ASCII);
  - a small, explicit set of corporate/organisation suffixes ("集团", "公司",
    "有限公司", "股份有限公司", "Inc", "Corp", "Ltd", "Co.") — stripped only when
    the remaining stem is still substantial (≥2 CJK chars or ≥3 ASCII), so we
    don't collapse "A公司" and "B公司" by over-stripping.

We deliberately do NOT do fuzzy/embedding similarity merges here — those need a
human-tunable threshold and can mis-merge; they can be layered later as an
opt-in pass. The canonical key is for grouping; the display name keeps the most
complete surface form seen.
"""
from __future__ import annotations

import re
import unicodedata

# Corporate / organisation suffixes that denote the same entity with/without them.
# Ordered longest-first so "股份有限公司" strips before "公司".
_ORG_SUFFIXES = [
    "股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "科技有限公司",
    "集团", "公司", "厂", "研究院", "研究所", "大学", "学院",
    "Corporation", "Incorporated", "Limited", "Company",
    "Inc.", "Inc", "Corp.", "Corp", "Ltd.", "Ltd", "Co.", "LLC",
]

# Minimum stem length after suffix removal (avoid over-stripping short names).
_MIN_CJK_STEM = 2
_MIN_ASCII_STEM = 3

_PUNCT = re.compile(r"[·•・,，.。!！?？\"'“”‘’()（）\[\]【】]+")
_MULTISPACE = re.compile(r"\s+")


def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def canonical_key(name: str) -> str:
    """Return a canonical grouping key for an entity surface form.

    Two surface forms that should merge return the same key. Returns the cleaned
    name itself when no safe normalisation applies (never raises).
    """
    if not name:
        return ""
    # 1) Unicode normalise (full-width → half-width etc.) + trim.
    s = unicodedata.normalize("NFKC", str(name)).strip()
    # 2) Drop punctuation; collapse runs of whitespace to a single space.
    s = _PUNCT.sub("", s)
    s = _MULTISPACE.sub(" ", s).strip()
    if not s:
        return ""
    # 3) Case fold ASCII (Chinese unaffected). For pure-CJK, also drop the now-
    #    single spaces (Chinese entity names don't use internal spaces); keep
    #    spaces for ASCII phrases ("pairwise loss") where they're meaningful.
    if _is_ascii(s):
        folded = s.lower()
    else:
        folded = s.replace(" ", "")

    # 4) Iteratively strip org suffixes while a substantial stem remains, so
    #    "小米集团股份有限公司" → "小米集团" → "小米" all collapse to one key.
    changed = True
    while changed:
        changed = False
        for suf in _ORG_SUFFIXES:
            suf_cmp = suf.lower() if _is_ascii(suf) else suf
            if folded.endswith(suf_cmp) and len(folded) > len(suf_cmp):
                stem = folded[: -len(suf_cmp)].strip()  # 剥离后缀后去尾空格("apple inc"→"apple"不带尾空格)
                min_stem = _MIN_ASCII_STEM if _is_ascii(stem) else _MIN_CJK_STEM
                if len(stem) >= min_stem:
                    folded = stem
                    changed = True
                    break  # restart scan from longest suffix
                # stem too short → stop stripping (don't over-merge)
                return folded.strip()
    return folded


class EntityCanonicalizer:
    """Groups surface forms by canonical key, tracking the best display name.

    Usage during KG build:
        canon = EntityCanonicalizer()
        key = canon.add("小米集团")     # records mapping, returns canonical key
        key2 = canon.add("小米")        # same key → merged
        display = canon.display(key)    # most complete surface form seen
    """

    def __init__(self):
        self._key_to_display: dict[str, str] = {}
        self._surface_to_key: dict[str, str] = {}

    def add(self, name: str) -> str:
        key = canonical_key(name)
        if not key:
            return ""
        self._surface_to_key[name] = key
        # Display name = the longest surface form seen for this key (most complete).
        cur = self._key_to_display.get(key)
        if cur is None or len(name.strip()) > len(cur):
            self._key_to_display[key] = name.strip()
        return key

    def key_of(self, name: str) -> str:
        return self._surface_to_key.get(name) or canonical_key(name)

    def display(self, key: str) -> str:
        return self._key_to_display.get(key, key)

    def groups(self) -> dict[str, list[str]]:
        """canonical key → list of surface forms that mapped to it."""
        out: dict[str, list[str]] = {}
        for surface, key in self._surface_to_key.items():
            out.setdefault(key, []).append(surface)
        return out
