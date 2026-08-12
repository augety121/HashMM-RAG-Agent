"""hashmm/retrieval/mqe.py — 多查询扩展 Multi-Query Expansion（SAGE 派生）。

把一个查询分解成多个角度的子查询，分别检索后合并去重，提升召回覆盖面——治"一个 query 只命中
一个角度、漏掉其它面"的问题。LLM 优先（借鉴 SAGE 的查询分解提示），无 LLM / 失败时退化为
保守的规则切分（仅在明显多主体时切，绝不乱拆）。纯逻辑、可单测、永不抛错。
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

_MQE_SYS = (
    "你是查询分解助手：把用户的检索查询拆成 {n} 个更具体、可独立理解的子问题，覆盖原查询的不同角度。\n"
    "要求：每个子问题不依赖其它子问题；不要编号、不要解释；每行一个子问题。\n"
    "示例 —— 用户查询：RAG 和传统搜索引擎的区别\n"
    "RAG 检索增强生成的工作原理是什么\n"
    "传统搜索引擎如何处理用户查询\n"
    "RAG 相比关键词检索有哪些优势"
)


def mqe_enabled() -> bool:
    return os.environ.get("HASHMM_MQE", "1").strip().lower() in ("1", "true", "yes", "on")


def _default_n() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_MQE_N", "3")))
    except (TypeError, ValueError):
        return 3


_SPLIT_RE = re.compile(r"\s*(?:和|与|、|/|，|,|；|;|\bvs\.?\b|对比|比较)\s*", re.IGNORECASE)


def _rule_split(q: str) -> list[str]:
    """仅在查询明显是「A 和 B」多主体时切分；否则返回空（不乱拆单一查询）。"""
    parts = [p.strip() for p in _SPLIT_RE.split(q) if p and len(p.strip()) >= 2]
    # 过滤掉过短/疑似连接词残留；至少 2 个有效部分才算多主体
    parts = [p for p in parts if len(p) >= 2]
    return parts if len(parts) >= 2 else []


def expand_queries(query: str, n: int | None = None,
                   llm_fn: Optional[Callable[[str], str]] = None) -> list[str]:
    """返回包含原查询在内的查询列表（原查询在首位，去重，最多 n+1 条）。永不抛错。"""
    q = str(query or "").strip()
    if not q:
        return []
    if n is None:
        n = _default_n()
    out = [q]
    seen = {q}

    def _add(s: str):
        s = str(s or "").strip().lstrip("0123456789.、）)-*· ").strip()
        if s and s not in seen and len(s) >= 2:
            seen.add(s)
            out.append(s)

    if llm_fn:
        try:
            prompt = _MQE_SYS.format(n=n) + f"\n\n用户查询：{q}\n\n子问题（每行一个，共 {n} 个）："
            raw = llm_fn(prompt)
            for line in str(raw or "").splitlines():
                _add(line)
                if len(out) >= n + 1:
                    break
        except Exception:
            pass

    # LLM 没产出（或没 LLM）→ 保守规则切分兜底（仅多主体时）
    if len(out) == 1:
        for part in _rule_split(q):
            _add(part)
            if len(out) >= n + 1:
                break

    return out[: n + 1]
