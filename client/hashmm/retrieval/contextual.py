"""v17 Phase 70 — Contextual Retrieval (Anthropic, 2024).

Prepend a short *situating context* to each chunk **before** it is embedded and
BM25-indexed, so retrieval isn't defeated by chunks that lost their document
context ("研发投入同比增长 12%" → 哪家公司？哪一年？). The original chunk text is
still what gets returned/displayed to the user; the prepended context is only a
retrieval aid (it improves the embedding and the BM25 term profile).

Three modes, chosen by env ``HASHMM_CONTEXTUAL_RETRIEVAL``:

  - ``off``: index the raw chunk text. (V271 起默认改为 ``enriched``——零成本的
    标题+章节路径前置，正是资料 5.4.1「短文本全局信息增强」的做法；老索引不受影响，
    重建索引后全量生效。)
  - 旧默认说明：index the raw chunk text. **Zero behavior change** — this
    is exactly what the pipeline did before, so enabling Phase 70 is opt-in.
  - ``enriched``: deterministically prepend the document title + section path
    (the chunker's ``search_text``). **No LLM, free, no latency/cost.** This was
    the originally-intended ``search_text`` for embedding, which the index path
    never actually used.
  - ``llm``: ask an LLM for a 1–2 sentence context situating the chunk within its
    document (Anthropic's Contextual Retrieval). **Best quality**; costs one cheap,
    batchable, prompt-cacheable call per chunk. Falls back to ``enriched`` when no
    ``llm_fn`` is available or the call fails/returns empty.

Design rules: provider-agnostic (uses whatever ``llm_fn`` the pipeline passes),
and **never raises** — a context-generation failure must never break ingest;
it degrades to a cheaper mode instead.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)

_VALID_MODES = ("off", "enriched", "llm")
# How much of the document to show the LLM when situating a chunk (cost cap).
_MAX_DOC_CHARS = int(os.environ.get("HASHMM_CONTEXTUAL_MAX_DOC_CHARS", "8000"))

_PROMPT = (
    "下面是一篇文档（可能被截断）：\n<document>\n{doc}\n</document>\n\n"
    "下面是该文档中的一个片段：\n<chunk>\n{chunk}\n</chunk>\n\n"
    "请用一到两句话，给出能把这个片段定位到全文背景里的简短上下文"
    "（例如它属于哪家公司/哪个时间/哪一节、在讲什么），以提升检索命中率。"
    "只输出这段简短上下文本身，不要任何前后缀、解释或引号。"
)


def mode() -> str:
    """Current contextual-retrieval mode (env-driven, defaults to 'off')."""
    m = os.environ.get("HASHMM_CONTEXTUAL_RETRIEVAL", "enriched").strip().lower()
    return m if m in _VALID_MODES else "off"


def _get(chunk: Any, attr: str, default: str = "") -> str:
    """Read an attribute from a Chunk dataclass OR a chunk dict."""
    if isinstance(chunk, dict):
        v = chunk.get(attr, default)
    else:
        v = getattr(chunk, attr, default)
    return v if isinstance(v, str) else (str(v) if v is not None else default)


def _chunk_text(chunk: Any) -> str:
    return _get(chunk, "text")


def _enriched_prefix(chunk: Any) -> str:
    """Deterministic structural context: 文档标题 + 章节路径 (no LLM)."""
    parts = []
    title = _get(chunk, "doc_title")
    section = _get(chunk, "section_path") or _get(chunk, "section")
    if title:
        parts.append(f"文档：{title}")
    if section:
        parts.append(f"章节：{section}")
    return "\n".join(parts)


def generate_context(doc_text: str, chunk_text: str, llm_fn: Callable | None,
                     max_doc_chars: int = _MAX_DOC_CHARS) -> str:
    """Ask the LLM for a 1–2 sentence situating context. Returns '' on any
    problem (caller falls back to a cheaper mode). Never raises."""
    if not callable(llm_fn) or not chunk_text.strip():
        return ""
    try:
        doc = (doc_text or "")[:max_doc_chars]
        prompt = _PROMPT.format(doc=doc, chunk=chunk_text[:2000])
        out = llm_fn(prompt)
        text = (out or "").strip() if isinstance(out, str) else str(out or "").strip()
        # Guard against a model that echoes the chunk or rambles.
        if not text or len(text) > 600:
            return text[:600].strip()
        return text
    except Exception as e:  # never break ingest
        log_suppressed(logger, e)
        return ""


def contextual_index_text(chunk: Any, doc_text: str = "", llm_fn: Callable | None = None) -> str:
    """Return the text that should be embedded / BM25-indexed for this chunk.

    The returned value is for indexing only; callers keep the original
    ``chunk.text`` for display. Honors the active mode and always degrades
    gracefully (never raises, never returns empty for a non-empty chunk).
    """
    text = _chunk_text(chunk)
    m = mode()
    if m == "off" or not text.strip():
        return text

    prefix = ""
    if m == "llm":
        ctx = generate_context(doc_text, text, llm_fn)
        if ctx:
            # Combine the LLM situating context with the structural enrichment.
            enr = _enriched_prefix(chunk)
            prefix = f"{ctx}\n{enr}" if enr else ctx
    if not prefix:  # enriched mode, or llm-mode fallback
        prefix = _enriched_prefix(chunk)

    return f"{prefix}\n\n{text}" if prefix else text


def contextualize_chunks(chunks: list, doc_text: str = "",
                         llm_fn: Callable | None = None) -> list[str]:
    """Map contextual_index_text over a document's chunks. Returns the list of
    index texts (same length/order as ``chunks``). When mode is 'off' this is
    exactly ``[c.text for c in chunks]`` — i.e. a pure no-op."""
    m = mode()
    if m == "off":
        return [_chunk_text(c) for c in chunks]
    out = []
    for c in chunks:
        try:
            out.append(contextual_index_text(c, doc_text=doc_text, llm_fn=llm_fn))
        except Exception as e:  # belt-and-suspenders: never break a batch
            log_suppressed(logger, e)
            out.append(_chunk_text(c))
    return out
