"""Corpus vocabulary — data-driven subject coverage, not a hard-coded brand list.

Why this module exists
----------------------
The legacy refusal gate in ``chat_retrieval`` decides "does the corpus cover the
query's subject?" by matching against ``_BRAND_RE`` — a hard-coded list of ~28
companies (小米/网易/华为/特斯拉/…). That binds the whole product to one corpus:
the moment a customer ingests medical, legal, or manufacturing documents, their
real subjects ("某药企", "某条款") are not in the list, so the gate falls back to
brittle tokenisation and refuses (or answers) wrongly.

Large agents (Claude, Hermes) never gate on an entity whitelist. They judge
coverage from retrieval-score distribution + query↔source overlap. This module is
the data-driven half of that: at index time it learns **this customer's** proper
nouns from their own corpus and persists them, so coverage judgement adapts to
whatever was actually ingested — no code change per customer.

What it does
------------
- ``build_corpus_vocab(corpus)``  : scan ingested chunks, extract candidate proper
  nouns (jieba POS tags nr/nt/nz when available; a conservative regex fallback
  otherwise), keep those appearing in >= ``min_freq`` chunks, drop garbage via the
  shared entity-name validator. Returns a set of terms.
- ``save_corpus_vocab`` / ``load_corpus_vocab`` : persist to ``data/corpus_vocab.json``.
- ``CorpusVocab``                 : in-memory holder with ``query_subjects(q)`` —
  the terms from a query that the corpus knows about.

Design constraints honoured
---------------------------
- **Opt-in / safe default**: nothing here changes behaviour until the gate is
  wired to use it AND a vocab file exists. An empty/absent vocab → callers fall
  back to the existing brand-list logic, so the current corpus behaves identically.
- **No KG dependency**: vocab must work even when the knowledge graph is empty.
- **Cheap**: jieba is already a dependency; building over ~20k chunks is seconds.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from hashmm.kg.extractor import validate_entity_name
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.corpus_vocab")

DEFAULT_VOCAB_PATH = Path("data/corpus_vocab.json")

# A candidate proper noun: 2–12 chars, CJK and/or ASCII, no pure digits/punct.
_TERM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·]{2,12}")
_PURE_NUM_RE = re.compile(r"^[0-9.,%]+$")

# Generic words that look like nouns but carry no subject signal. These are
# domain-neutral (not tied to any one corpus).
_GENERIC = {
    "公司", "集团", "企业", "有限", "股份", "控股", "本公司", "该公司", "我们",
    "报告", "年报", "财报", "数据", "信息", "情况", "内容", "部分", "方面",
    "业务", "产品", "服务", "项目", "工作", "管理", "系统", "平台", "技术",
    "the", "and", "for", "with", "company", "group", "report", "data",
}

# Common academic / ML jargon and dataset-ish tokens that pollute a corpus mixed
# with research papers. Domain-neutral; these are never a customer's "subject".
_ASCII_JARGON = {
    "retrieval", "knowledge", "benchmark", "reasoning", "conformal", "scattering",
    "defocusing", "experiments", "engineering", "transactions", "relatedwork",
    "abstract", "introduction", "conclusion", "appendix", "dataset", "baseline",
    "embedding", "encoder", "decoder", "transformer", "attention", "finetuning",
    "pretrained", "evaluation", "ablation", "framework", "overview", "method",
}

# ASCII tokens that look like dataset/model/paper artefacts:
#   - mixed letters+digits (doc2query, EMNLP2023, DeBERTaV3, Flickr30K, AA1000ASV3)
#   - ALL-CAPS runs >= 5 (RETRIEVAL, RXOGQRWILQG, MIRFLICKR)
_ASCII_ALNUM_MIX = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+$")
_ASCII_ALLCAPS = re.compile(r"^[A-Z]{5,}$")
_VOWELS = set("aeiouAEIOU")

_MIN_TERM_LEN = 2
_MAX_VOCAB = 5000  # cap persisted vocab size (keep the most frequent)


# Org/institution suffixes used by the no-jieba fallback to carve proper nouns
# out of a CJK run (e.g. "协和医院" from "...协和医院心血管科...").
_SUFFIX_RE = re.compile(
    r"[\u4e00-\u9fff]{2,8}(?:公司|集团|医院|大学|学院|银行|证券|保险|基金|"
    r"研究院|研究所|实验室|事务所|工厂|酒店|药业|制药|科技|网络|传媒|学校|中心)"
)


def _jieba_proper_nouns(text: str) -> list[str]:
    """Extract proper-noun candidates with jieba POS tagging when available.

    Tags kept: nr (person), nt (organisation), nz (other proper noun), eng
    (ASCII tokens like 'Xiaomi'). Falls back to suffix-anchored extraction +
    ASCII tokens if jieba is unavailable, so this never hard-fails and the
    fallback still yields useful org names instead of whole sentences.
    """
    try:
        import jieba.posseg as pseg  # type: ignore
        out = []
        for w, flag in pseg.cut(text):
            w = w.strip()
            if not w:
                continue
            if flag in ("nr", "nt", "nz", "nrt", "nrfg") or (flag == "eng" and len(w) >= 3):
                out.append(w)
        return out
    except Exception:
        # No jieba: carve institution-suffixed names out of CJK runs, plus ASCII
        # tokens. Coarse but far better than emitting whole sentences.
        out = [m.group(0) for m in _SUFFIX_RE.finditer(text)]
        out += re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)
        return out


def _is_ascii(term: str) -> bool:
    return all(ord(c) < 128 for c in term)


def _is_noisy_ascii(term: str) -> bool:
    """True if an all-ASCII term looks like dataset/model/paper jargon or OCR
    garbage rather than a real subject (brand/product). Kept terms: normal
    capitalised brand-like words (Xiaomi) and clean model names (SU7 stays via
    the alnum rule below only if short & uppercase-led — see _is_valid_term)."""
    low = term.lower()
    if low in _ASCII_JARGON:
        return True
    if _ASCII_ALLCAPS.match(term):          # RETRIEVAL, MIRFLICKR, RXOGQRWILQG
        return True
    if _ASCII_ALNUM_MIX.match(term):        # EMNLP2023, DeBERTaV3, Flickr30K, doc2query
        return True
    # Consonant-soup OCR garbage: long, letters-only, almost no vowels.
    letters = [c for c in term if c.isalpha()]
    if len(letters) >= 6 and sum(1 for c in letters if c in _VOWELS) <= 1:
        return True
    return False


def _is_valid_term(term: str, ascii_policy: str = "drop") -> bool:
    term = term.strip()
    if len(term) < _MIN_TERM_LEN or len(term) > 12:
        return False
    if term.lower() in _GENERIC:
        return False
    if _PURE_NUM_RE.match(term):
        return False
    if _is_ascii(term):
        if ascii_policy == "drop":
            return False  # Chinese corpus: drop all pure-ASCII (jargon/garbage)
        if _is_noisy_ascii(term):  # "smart": drop only jargon/garbage
            return False
    # Reuse the KG's garbage filter (rejects sentence fragments, particles, etc.)
    try:
        if not validate_entity_name(term):
            return False
    except Exception as _e:
        log_suppressed(logger, _e)
    return True


def build_corpus_vocab(
    corpus: list[dict],
    min_freq: int = 3,
    max_chunks: int | None = None,
    ascii_policy: str = "drop",
) -> set[str]:
    """Build a proper-noun vocabulary from ingested chunks.

    Args:
        corpus: list of chunk dicts (each with a ``text`` field).
        min_freq: a term must appear in at least this many chunks to be kept
            (filters one-off noise; raise for large corpora).
        max_chunks: optionally cap how many chunks are scanned (speed).
        ascii_policy: how to treat all-ASCII candidate terms.
            - ``"drop"`` (default): drop ALL pure-ASCII terms. Best for Chinese
              enterprise corpora — real subjects are Chinese ("小米集团"), and
              pure-ASCII tokens are overwhelmingly paper jargon / dataset names /
              OCR garbage (EMNLP2023, MIRFLICKR, RXOGQRWILQG). The matching
              Chinese term still enters the vocab, so coverage is unaffected.
            - ``"smart"``: keep clean brand-like ASCII (Xiaomi) and drop only the
              jargon/garbage heuristically. Use for English-heavy corpora.

    Returns:
        A set of vocabulary terms (proper nouns characteristic of this corpus).
    """
    doc_freq: Counter = Counter()
    n = 0
    for chunk in corpus:
        text = str(chunk.get("text", "")) if isinstance(chunk, dict) else str(chunk)
        if not text.strip():
            continue
        n += 1
        if max_chunks and n > max_chunks:
            break
        seen = set()
        for term in _jieba_proper_nouns(text):
            if term in seen:
                continue
            if _is_valid_term(term, ascii_policy=ascii_policy):
                seen.add(term)
        for term in seen:
            doc_freq[term] += 1

    vocab = {t for t, f in doc_freq.items() if f >= min_freq}
    if len(vocab) > _MAX_VOCAB:
        vocab = {t for t, _ in doc_freq.most_common(_MAX_VOCAB)}
    logger.info(
        f"Built corpus vocab: {len(vocab)} terms from {n} chunks "
        f"(min_freq={min_freq}, ascii_policy={ascii_policy}, {len(doc_freq)} candidates)"
    )
    return vocab


def save_corpus_vocab(vocab: set[str], path: Path | str = DEFAULT_VOCAB_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "terms": sorted(vocab)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
    logger.info(f"Saved corpus vocab: {len(vocab)} terms → {path}")


def load_corpus_vocab(path: Path | str = DEFAULT_VOCAB_PATH) -> set[str]:
    """Load persisted vocab. Returns empty set if absent/unreadable (→ callers
    fall back to legacy behaviour, so default is unchanged)."""
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        terms = data.get("terms", []) if isinstance(data, dict) else []
        return {str(t) for t in terms if t}
    except Exception as e:
        logger.warning(f"Failed to load corpus vocab from {path}: {e}")
        return set()


class CorpusVocab:
    """In-memory holder for the corpus vocabulary with cheap lookups.

    Lazy-loads from disk on first use. ``available`` is False when no vocab has
    been built yet, which callers use to fall back to legacy behaviour.
    """

    def __init__(self, terms: set[str] | None = None, path: Path | str = DEFAULT_VOCAB_PATH):
        self._path = Path(path)
        self._terms: set[str] | None = terms

    @property
    def terms(self) -> set[str]:
        if self._terms is None:
            self._terms = load_corpus_vocab(self._path)
        return self._terms

    @property
    def available(self) -> bool:
        return len(self.terms) > 0

    def reload(self) -> None:
        self._terms = load_corpus_vocab(self._path)

    def query_subjects(self, query: str) -> list[str]:
        """Return vocab terms that appear in the query (longest first, so a
        specific subject like '小米集团' is preferred over '小米')."""
        q = query or ""
        hits = [t for t in self.terms if t and t in q]
        hits.sort(key=len, reverse=True)
        return hits


# Module-level singleton used by the refusal gate (cheap; lazy-loads).
_default_vocab = CorpusVocab()


def get_default_vocab() -> CorpusVocab:
    return _default_vocab
