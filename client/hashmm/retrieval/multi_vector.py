"""hashmm/retrieval/multi_vector.py — 文本多向量表示（面试资料 5.4.3「文本多向量表示」）。

思想（资料原文）：同一段文本 A 不只用它自己生成一个向量，还用它的**子块 / 摘要 / 假设性问题**
生成补充向量；召回时命中**任意补充向量**，都返回**原文 A** 喂给 LLM。其中「假设性问题」尤其有效——
「用问题召回问题」比「用问题召回答案」更容易命中（非对称检索的痛点，资料 5.3.3）。

本模块是这套机制的**纯逻辑核心**（不碰嵌入/索引，嵌入仍走项目既有 encoder）：
  · ``build_representations(chunk_text, chunk_id, llm_fn=None)`` —— 为一段原文产出多条**索引表示**
    （每条 = {repr_text, source_id, kind}）。入库时对这些 repr_text 分别求向量、都指回 source_id。
    - 无 ``llm_fn``：确定性产出「原文 + 子块」表示（零成本、可单测）。
    - 有 ``llm_fn``：额外产出「摘要」和「假设性问题」表示（资料点名的两类补充向量）。
  · ``resolve_source(matched_repr_id, repr_index)`` —— 召回侧：任一命中的 repr → 原文 chunk_id。
  · ``collapse_to_sources(retrieved, repr_index)`` —— 把命中的多条 repr **归并回去重的原文**。

工程纪律（与 mqe.py / parent_child.py 一致）：纯函数、可注入、**永不抛错**、默认关（``HASHMM_MULTI_VECTOR``）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retrieval.multi_vector")

_SUMMARY_SYS = ("用一句话概括下面这段内容的核心（≤40字，只输出概括本身，不要解释）：")
_QUESTIONS_SYS = ("阅读下面的内容，提出 3 个这段内容能够回答的、具体的问题。"
                  "每行一个问题，不要编号、不要解释：")
_MAX_SUBCHUNKS = 3
_MAX_QUESTIONS = 3


def multi_vector_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_MULTI_VECTOR")


@dataclass
class Representation:
    repr_id: str
    repr_text: str      # 真正拿去求向量、入库的文本
    source_id: str      # 命中后要返回的原文 chunk_id
    kind: str           # 'self' | 'subchunk' | 'summary' | 'question'
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"repr_id": self.repr_id, "repr_text": self.repr_text,
                "source_id": self.source_id, "kind": self.kind, **self.meta}


def _rid(source_id: str, kind: str, text: str) -> str:
    h = hashlib.md5(f"{source_id}:{kind}:{text[:80]}".encode("utf-8")).hexdigest()[:12]
    return f"repr-{kind}-{h}"


def _subchunks(text: str, n: int = _MAX_SUBCHUNKS) -> list[str]:
    """把原文切成 ≤n 段更细的子表示（按句界，确定性、无 LLM）。"""
    s = str(text or "").strip()
    if not s:
        return []
    sents = [x for x in re.split(r"(?<=[。！？.!?\n])", s) if x.strip()]
    if len(sents) <= 1:
        return []
    # 均分成 n 组，保证每组是完整句子的拼接
    size = max(1, (len(sents) + n - 1) // n)
    out = []
    for i in range(0, len(sents), size):
        seg = "".join(sents[i:i + size]).strip()
        if seg and seg != s:      # 子块不等于原文才有意义
            out.append(seg)
        if len(out) >= n:
            break
    return out


def _lines(raw: str, cap: int) -> list[str]:
    out = []
    for line in str(raw or "").splitlines():
        t = line.strip().lstrip("0123456789.、）)-*· ").strip()
        if t and len(t) >= 4:
            out.append(t)
        if len(out) >= cap:
            break
    return out


def build_representations(chunk_text: str, chunk_id: str, llm_fn=None, *,
                          want_subchunks: bool = True,
                          want_summary: bool = True,
                          want_questions: bool = True) -> list[Representation]:
    """为一段原文产出多条索引表示（含原文自身）。**永不抛错**。

    返回列表的第一条恒为 kind='self'（原文自身）；其余为补充向量。
    入库时：对每条 ``repr_text`` 求向量并写入索引，向量的 payload 记 ``source_id``。
    """
    reps: list[Representation] = []
    txt = str(chunk_text or "").strip()
    if not txt or not chunk_id:
        return reps
    # ① 原文自身（永远有）
    reps.append(Representation(_rid(chunk_id, "self", txt), txt, chunk_id, "self"))
    # ② 子块表示（确定性）
    if want_subchunks:
        try:
            for sc in _subchunks(txt):
                reps.append(Representation(_rid(chunk_id, "subchunk", sc), sc, chunk_id, "subchunk"))
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
    # ③ 摘要表示（需 LLM）
    if want_summary and callable(llm_fn):
        try:
            summ = str(llm_fn(_SUMMARY_SYS + "\n\n" + txt[:2000]) or "").strip()
            summ = summ.split("\n")[0].strip()[:80]
            if summ and summ != txt:
                reps.append(Representation(_rid(chunk_id, "summary", summ), summ, chunk_id, "summary"))
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
    # ④ 假设性问题表示（需 LLM）——资料点名最有效的一类
    if want_questions and callable(llm_fn):
        try:
            raw = str(llm_fn(_QUESTIONS_SYS + "\n\n" + txt[:2000]) or "")
            for q in _lines(raw, _MAX_QUESTIONS):
                reps.append(Representation(_rid(chunk_id, "question", q), q, chunk_id, "question"))
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
    return reps


def build_repr_index(all_reps: list[Representation]) -> dict[str, str]:
    """{repr_id: source_id}——召回命中 repr 后回原文用。"""
    idx: dict[str, str] = {}
    for r in all_reps or []:
        try:
            idx[r.repr_id] = r.source_id
        except Exception:  # noqa: BLE001
            continue
    return idx


def resolve_source(matched_repr_id: str, repr_index: dict[str, str]) -> str:
    return repr_index.get(str(matched_repr_id or ""), "")


def _matched_id(item) -> str:
    if isinstance(item, dict):
        return str(item.get("repr_id") or item.get("chunk_id") or item.get("id") or "")
    return str(getattr(item, "repr_id", getattr(item, "chunk_id", item)) or "")


def collapse_to_sources(retrieved: list, repr_index: dict[str, str],
                        source_text: dict[str, str] | None = None, *,
                        max_sources: int | None = None) -> list[str]:
    """召回侧：命中的多条 repr → 去重后的**原文**（保持命中顺序）。**永不抛错**。

    ``source_text`` 为 {source_id: 原文}；命中 repr 归并到 source_id 后取原文。
    退化安全：repr_index 里查不到 → 回退用命中记录自带的 text（不丢内容）。
    """
    source_text = source_text or {}
    out: list[str] = []
    seen: set[str] = set()
    try:
        for item in retrieved or []:
            rid = _matched_id(item)
            sid = repr_index.get(rid, "")
            if sid and sid in source_text:
                key, txt = f"S:{sid}", source_text[sid]
            elif sid:
                key, txt = f"S:{sid}", (item.get("text") if isinstance(item, dict) else "") or ""
            else:
                txt = (item.get("text") if isinstance(item, dict) else str(getattr(item, "text", ""))) or ""
                key = f"T:{txt[:32]}"
            if not str(txt).strip() or key in seen:
                continue
            seen.add(key)
            out.append(txt)
            if max_sources and len(out) >= max_sources:
                break
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return out
