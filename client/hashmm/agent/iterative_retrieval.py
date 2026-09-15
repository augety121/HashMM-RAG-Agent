"""hashmm/agent/iterative_retrieval.py — 迭代检索执行器（V311）。

adaptive_rag 负责【决策】（要不要检索、多宽、最多几轮），本模块负责【执行】多轮：

    第 1 轮   原始查询检索
    评估      累计结果对查询的 bigram 覆盖率（与 crag._coverage 同一口径）
    第 2..N 轮 挑"覆盖最差的侧面"生成子查询 → 补检索 → 去重合并
    停止      三重条件任一命中：
              · early_exit   自评覆盖连续 patience 轮 ≥ 阈值（adaptive_rag.EarlyExit）
              · no_new       本轮 0 条新结果（继续也只是烧 token）
              · budget       到达路由决策的 max_iterations 上限（生产硬约束）

侧面分解是零成本规则式（不调 LLM，离线可测）：
    对比 A 和 B 的 X      → [A 的 X, B 的 X]
    先 X 再/然后 Y        → [X, Y]
    为什么 X …… 怎么/如何 Y → [X 原因, Y]
    无法分解时             → 从查询里找 bigram 全未覆盖的最长连续片段作为聚焦词补检索
LLM 版查询分解已有现成件（crag 的 MQE），接上层编排时可替换 decompose，本模块
接口不变。

开关：HASHMM_RAG_ITERATIVE=0 关闭（即使路由给了多轮预算也只跑单轮）。默认开；
但只有路由判为 multi_hop/complex（max_iterations>1）才会真正多轮，single 档
天然单轮——所以对简单查询零额外成本。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.iterative_retrieval")

__all__ = ["IterativeResult", "iterative_enabled", "decompose", "run_iterative",
           "coverage", "aspect_coverage"]


def iterative_enabled() -> bool:
    """迭代执行是否开启（默认开；HASHMM_RAG_ITERATIVE=0/false/off 关）。"""
    return (os.environ.get("HASHMM_RAG_ITERATIVE", "1").strip().lower()
            not in ("0", "false", "off", "no"))


# ---------------------------------------------------------------- 覆盖率（与 crag 同口径）
def _text_of(r) -> str:
    return (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or ""


def _grams(s: str) -> set[str]:
    s = (s or "").strip()
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def coverage(results: list, query: str) -> float:
    """查询 2-gram 在累计检索文本中的命中比例（0~1）——查询级召回代理，合并全文计。"""
    grams = _grams(query)
    if not grams or not results:
        return 0.0
    blob = "\n".join(_text_of(r) for r in results)
    return sum(1 for g in grams if g in blob) / len(grams)


def aspect_coverage(results: list, aspect: str) -> float:
    """某个【侧面】被回答的程度：取单文档内覆盖率的最大值。

    不能用合并全文——"Milvus 性能"的 bigram 可以被《Milvus 简介》和《Qdrant 性能》
    两篇各出一半拼出来，但没有任何一篇真正回答了 Milvus 的性能。侧面覆盖必须
    在同一篇文档内成立才算数。
    """
    grams = _grams(aspect)
    if not grams or not results:
        return 0.0
    best = 0.0
    for r in results:
        t = _text_of(r)
        if not t:
            continue
        best = max(best, sum(1 for g in grams if g in t) / len(grams))
    return best


# ---------------------------------------------------------------- 侧面分解（规则式）
_TAIL_STRIP = re.compile(r"(的)?(差异|区别|异同|不同|优缺点|对比|比较)[?？。!！\s]*$")
_COMPARE = re.compile(
    r"(?:对比|比较)\s*([^和与跟]{1,24}?)\s*(?:和|与|跟|vs\.?)\s*(.{1,24}?)(?:的(.{1,16}))?[?？。!！\s]*$",
    re.IGNORECASE,
)
_SEQUENTIAL = re.compile(r"先(.{2,30}?)(?:，|,)?\s*(?:再|然后|接着)(.{2,40})")
_WHY_HOW = re.compile(r"为什么(.{2,30}?)(?:[，,。？?]|$).{0,6}?(?:怎么|如何)(.{2,30})")


def _clean(s: str) -> str:
    return re.sub(r"^[，,。.\s]+|[，,。.？?！!\s]+$", "", s or "")


def decompose(query: str) -> list[str]:
    """把查询拆成 2-3 个可独立检索的侧面子查询；拆不动返回 []。

    保守优先：宁可不拆（走未覆盖片段聚焦），不可乱拆。
    """
    q = (query or "").strip()
    m = _COMPARE.search(q)
    if m:
        a, b = _clean(m.group(1)), _clean(m.group(2))
        aspect = _clean(_TAIL_STRIP.sub("", m.group(3) or ""))
        if a and b:
            suffix = f" {aspect}" if aspect else ""
            return [f"{a}{suffix}", f"{b}{suffix}"]
    m = _SEQUENTIAL.search(q)
    if m:
        a, b = _clean(m.group(1)), _clean(m.group(2))
        if a and b:
            return [a, b]
    m = _WHY_HOW.search(q)
    if m:
        a, b = _clean(m.group(1)), _clean(m.group(2))
        if a and b:
            return [f"{a} 原因", b]
    return []


def _uncovered_focus(query: str, results: list) -> str:
    """无法分解时的兜底：找查询里 bigram【全部】未被覆盖的最长连续片段作聚焦词。

    例：查询"HashMM 的部署端口配置"，已检索文本只讲了部署 → "端口配置"片段的
    bigram 全缺 → 下一轮就检索"端口配置"。片段太短（<2 字）返回空表示没抓手。
    """
    q = (query or "").strip()
    if len(q) < 2:
        return ""
    blob = "\n".join(_text_of(r) for r in results)
    miss = [q[i:i + 2] not in blob for i in range(len(q) - 1)]   # 位置 i 的 bigram 是否缺失
    best_s = best_len = cur_s = cur_len = 0
    for i, m in enumerate(miss + [False]):                        # 哨兵收尾
        if m:
            if cur_len == 0:
                cur_s = i
            cur_len += 1
        else:
            if cur_len > best_len:
                best_s, best_len = cur_s, cur_len
            cur_len = 0
    if best_len == 0:
        return ""
    frag = q[best_s:best_s + best_len + 1]                        # n 个连续缺失 bigram 覆盖 n+1 字
    frag = _clean(re.sub(r"[?？。!！，,、的了吗呢]+", " ", frag)).strip()
    return frag if len(frag) >= 2 else ""


# ---------------------------------------------------------------- 执行
@dataclass
class IterativeResult:
    results: list = field(default_factory=list)     # 去重合并、按分数降序
    rounds: int = 0
    queries: list = field(default_factory=list)     # 每轮实际用的查询
    coverages: list = field(default_factory=list)   # 每轮结束时的【累计】覆盖率
    stop_reason: str = ""                           # early_exit / no_new / budget / single / empty
    first_response: object = None                   # 首轮完整 response（kg/community 上下文在这）


def _key(r) -> str:
    cid = (r.get("chunk_id") if isinstance(r, dict) else getattr(r, "chunk_id", None)) or ""
    return str(cid) if cid else _text_of(r)[:80]


def run_iterative(retrieve_fn, query: str, max_iterations: int, top_k: int,
                  early_exit=None) -> IterativeResult:
    """多轮检索直到覆盖够/没新货/预算尽。

    Args:
        retrieve_fn: (query, top_k) -> response（response.results 为条目列表）。
        max_iterations: 路由决策给的轮数上限（single=1 时本函数就是单轮，零差异）。
        early_exit: adaptive_rag.EarlyExit；None 用默认（0.8 分、连续 2 轮）。
    """
    from hashmm.agent.adaptive_rag import EarlyExit
    ee = early_exit or EarlyExit()
    out = IterativeResult()
    seen: set[str] = set()
    max_iterations = max(1, int(max_iterations))

    def _merge(rs) -> int:
        n = 0
        for r in rs or []:
            k = _key(r)
            if k and k not in seen:
                seen.add(k)
                out.results.append(r)
                n += 1
        return n

    # ---- 第 1 轮：原始查询
    resp = retrieve_fn(query, top_k)
    out.first_response = resp
    out.rounds = 1
    out.queries.append(query)
    _merge(getattr(resp, "results", None) or [])
    if not out.results:
        out.stop_reason = "empty"          # 首轮空手而归，换措辞也难有起色，交给上游 CRAG
        return out
    out.coverages.append(round(coverage(out.results, query), 4))
    if max_iterations == 1:
        out.stop_reason = "single"
        return out

    # ---- 第 2..N 轮：补覆盖最差的侧面
    aspects = decompose(query)
    tried = {query}
    for _ in range(1, max_iterations):
        if ee.should_stop(out.coverages):
            out.stop_reason = "early_exit"
            break
        if aspects:
            # 挑当前累计结果覆盖最差的侧面（单文档口径，见 aspect_coverage）
            sub = min(aspects, key=lambda a: aspect_coverage(out.results, a))
            if aspect_coverage(out.results, sub) >= ee.threshold or sub in tried:
                aspects = [a for a in aspects if a != sub]
                if not aspects:
                    # 分解出的侧面全部覆盖达标——这就是子目标的全集，到此为止，
                    # 不再退回片段聚焦（那会造出"对比 M"这类垃圾子查询白烧一轮）。
                    out.stop_reason = "early_exit"
                    break
                continue
        else:
            sub = _uncovered_focus(query, out.results)
        if not sub or sub in tried:
            out.stop_reason = "early_exit"     # 没有可补的抓手 = 覆盖已尽力
            break
        tried.add(sub)
        try:
            resp = retrieve_fn(sub, top_k)
        except Exception as e:  # noqa: BLE001
            # 首轮成果不能被后续轮次的故障吞掉：截停、保留已合并结果。
            logger.warning("iterative-retrieval 第 %d 轮检索异常，截停保留已得结果: %s",
                           out.rounds + 1, e)
            out.stop_reason = "error"
            break
        out.rounds += 1
        out.queries.append(sub)
        if _merge(getattr(resp, "results", None) or []) == 0:
            out.coverages.append(round(coverage(out.results, query), 4))
            out.stop_reason = "no_new"
            break
        out.coverages.append(round(coverage(out.results, query), 4))
    else:
        out.stop_reason = "budget"

    out.results.sort(key=lambda r: (r.get("score", 0.0) if isinstance(r, dict)
                                    else getattr(r, "score", 0.0)), reverse=True)
    logger.info("iterative-retrieval: rounds=%d cov=%s stop=%s queries=%s",
                out.rounds, out.coverages, out.stop_reason,
                [q[:20] for q in out.queries])
    return out
