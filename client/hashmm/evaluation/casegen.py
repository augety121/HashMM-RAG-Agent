"""Golden eval case auto-generator (V17 Phase 16/17).

Hand-writing 100 quality eval cases is slow. This generates CANDIDATE cases
from your actual corpus: sample chunks, ask the LLM to produce a question whose
answer is in the chunk + the reference answer + the source doc. A human then
reviews/keeps them (so it's not "self-judging" — the cases are grounded in real
corpus text, and you approve them).

Output cases are compatible with evaluation.TestCase and can be merged into
data/eval/golden_cases.json.
"""
from __future__ import annotations

import json
import random
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.casegen")

_GEN_SYSTEM = (
    "你是评测用例出题专家。给你一段来自企业文档的文本，请基于其中的【具体事实】"
    "出一道问答题，要求：\n"
    "1. 问题答案必须能在这段文本里找到（不能编）。\n"
    "2. 给出简短参考答案（含关键数字/事实）。\n"
    "3. 列出答案里必须出现的 1-3 个关键词（数字/专名优先）。\n"
    "只输出 JSON：{\"query\":\"问题\",\"reference_answer\":\"参考答案\","
    "\"must_contain\":[\"关键词\"],\"category\":\"factual\"}。"
    "如果这段文本没有适合出题的具体事实，输出 {\"skip\": true}。"
)


def generate_cases_from_chunks(chunks: list[dict], llm_fn: Any,
                               n: int = 20, seed: int = 0) -> list[dict]:
    """Generate up to n candidate golden cases from corpus chunks.

    chunks: [{"text": ..., "doc_id"/"filename": ...}, ...]
    Returns a list of TestCase-compatible dicts (candidates for human review).
    """
    if not llm_fn or not chunks:
        return []
    rng = random.Random(seed)
    # Prefer longer, fact-dense chunks
    candidates = [c for c in chunks if len(str(c.get("text", ""))) > 200]
    rng.shuffle(candidates)
    out = []
    idx = 0
    for c in candidates:
        if len(out) >= n:
            break
        text = str(c.get("text", ""))[:1500]
        doc = str(c.get("filename") or c.get("doc_id") or "")
        try:
            if hasattr(llm_fn, "quick_call"):
                raw = llm_fn.quick_call(_GEN_SYSTEM, text, 400)
            elif callable(llm_fn):
                raw = llm_fn(f"{_GEN_SYSTEM}\n\n文本：\n{text}")
            else:
                break
            raw = (raw or "").strip()
            s, e = raw.find("{"), raw.rfind("}")
            if s < 0 or e < 0:
                continue
            data = json.loads(raw[s:e + 1])
            if data.get("skip") or not data.get("query"):
                continue
            idx += 1
            case = {
                "id": f"gen_{idx:03d}",
                "query": str(data["query"])[:200],
                "category": data.get("category", "factual"),
                "must_contain": [str(x)[:40] for x in data.get("must_contain", [])][:3],
                "must_cite": True,
                "min_sources": 1,
                "reference_answer": str(data.get("reference_answer", ""))[:500],
                "relevant_docs": [_doc_key(doc)] if doc else [],
            }
            out.append(case)
        except Exception as ex:
            logger.debug(f"case gen skipped one chunk: {ex}")
            continue

    # Dedup by normalized query so we don't emit near-identical cases
    seen = set()
    deduped = []
    for c in out:
        key = c["query"].strip().lower().replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    logger.info(f"[CaseGen] generated {len(deduped)} candidate cases "
                f"({len(out) - len(deduped)} duplicates removed)")
    return deduped


def _doc_key(doc: str) -> str:
    """Extract a stable doc identity fragment for relevant_docs matching."""
    # Use the leading org/name part before separators
    for sep in ["-", "_", " ", "."]:
        if sep in doc:
            head = doc.split(sep)[0].strip()
            if len(head) >= 2:
                return head
    return doc[:20]
