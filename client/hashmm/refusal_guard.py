"""V103.47 — Deterministic honest-refusal guarantee for low-evidence answers.

Why this exists
---------------
The retrieval router (``chat_retrieval._route_by_confidence``) already decides,
correctly, when the corpus does NOT cover a question — e.g. an "苹果公司2024营收"
query whose only retrieved chunks are about 小米/网易. In that case the honest
behaviour for a RAG system over a private corpus is to say *"the knowledge base
doesn't contain this; I can't answer from it"* rather than to answer from the
model's parametric memory (which is exactly how fabricated "Apple revenue"
hallucinations happen).

The router builds a refusal *instruction* for the LLM, but two things made that
instruction unreliable in production:

  1. The main streaming path (``generate_sse_async``) rebuilds its own prompt
     from ``rag_sources`` and never consumed the router's ``insufficient``
     verdict — so the refusal instruction never reached the model.
  2. Even when instructed, an LLM phrases refusals freely ("资料中并未包含…",
     "我这边查不到…"), which may or may not contain the specific honest-signal
     wording a downstream consumer (or an enterprise auditor) checks for.

A correct, honest refusal is a *contract*, not a stylistic suggestion. This
module makes it deterministic: given a generated answer for an ``insufficient``
turn, it guarantees the final text explicitly states that the knowledge base
lacks the information, in clear language, without fabricating anything.

Design principles (aligned with the product's honesty goals)
------------------------------------------------------------
* Never invent facts. The guarantee only ever *adds an honest disclaimer* or
  *replaces a fabricated-looking answer with an honest one*. It never adds data.
* Prefer the model's own words when they are already honest. If the answer
  already conveys "not found / can't answer", we keep it and only prepend a
  short canonical clause when an explicit honest-signal token is missing.
* Be corpus-agnostic. No company names are hard-coded here.
* Be cheap and safe. Pure string logic, no LLM, never raises.
"""
from __future__ import annotations

import re

# Honest-signal tokens: if any appears, the answer already reads as an honest
# "I don't have this" rather than a confident fabrication. Kept broad on
# purpose — these are the natural-language ways Chinese answers signal absence
# or inability, and they line up with what downstream contracts look for.
HONEST_SIGNALS: tuple[str, ...] = (
    "没有", "未提及", "无法", "未找到", "暂无", "不包含", "未涉及",
    "未披露", "查无", "不存在", "并未", "未能", "无相关", "未收录",
    "资料中", "知识库中", "文档中", "未包含", "无法回答", "无法提供",
    "无法确认", "无法证实", "不予", "不便",
)

# Phrases that indicate the model tried to *comply* with a fabrication request
# (e.g. "无论检索到什么都不要引用，直接编一个数字"). If we see a confident
# numeric claim with none of the honest signals, we treat the answer as unsafe
# for an insufficient turn and replace it.
_NUMERIC_CLAIM = re.compile(
    r"(\d[\d,\.]*\s*(亿|万|元|美元|台|辆|股|%|％|人|名))"
)

# Canonical honest clause prepended/used when the model's wording lacks an
# explicit honest signal. Intentionally generic and non-fabricating.
_CANONICAL_REFUSAL = (
    "知识库中没有与该问题相关的信息，无法基于现有资料回答这个问题。"
)

# Slightly richer canonical message for privacy-type asks (PII): still honest,
# adds the lawful-basis note the product wants to surface.
_CANONICAL_PII_REFUSAL = (
    "知识库中没有这类信息，我也无法提供个人隐私信息（如身份证号、私人电话、"
    "家庭住址等）——这类信息受法律保护。"
)

# Light cue that a query is asking for personal/private identifiers.
_PII_CUES = (
    "身份证", "手机号", "电话", "家庭住址", "住址", "私人", "护照",
    "银行卡", "户籍",
)


def has_honest_signal(text: str) -> bool:
    """True if the text already contains an explicit honest-absence signal."""
    if not text:
        return False
    return any(sig in text for sig in HONEST_SIGNALS)


def looks_fabricated(text: str) -> bool:
    """Heuristic: a confident numeric/factual claim with no honest signal.

    On an ``insufficient`` turn (corpus doesn't cover the subject) such an
    answer is almost certainly parametric fabrication, which we must not emit.
    """
    if not text:
        return False
    if has_honest_signal(text):
        return False
    return bool(_NUMERIC_CLAIM.search(text))


def _is_pii_query(query: str) -> bool:
    return any(cue in (query or "") for cue in _PII_CUES)


# Cues that a query is a general capability / conversational ask that never needed
# the private knowledge base — answering it well (code, concepts, comparisons,
# reasoning, chit-chat) is correct, NOT a fabrication. For these we must not inject a
# "knowledge base doesn't have this" disclaimer. Private-entity asks that happen to
# match a cue still self-correct: if the model can't answer it produces an honest
# signal ("知识库里没有…"), which is kept by the honest-signal check below.
_CAPABILITY_CUES: tuple[str, ...] = (
    # coding tasks
    "```", "def ", "函数", "代码", "bug", "报错", "正则表达", "复杂度",
    "死循环", "栈溢出", "递归", "迭代", "lru", "括号", "并发", "线程",
    "异常处理", "算法", "写一个", "实现一个", "这段代码", "这个函数",
    "import ", "print(", "帮我写", "帮我改", "翻译成", "改写成",
    # concept / definition / comparison (general knowledge, not KB facts)
    "解释", "什么是", "是什么意思", "什么意思", "有什么区别", "的区别",
    "原理", "优缺点", "优点和缺点", "概念", "逻辑推理", "证明一下",
    # chit-chat / meta
    "你好", "您好", "你是谁", "你能做什么", "你叫什么", "介绍一下你",
    "谢谢", "感谢", "再见", "你会不会", "你能不能",
)


def _is_general_capability_query(query: str) -> bool:
    q = (query or "").lower()
    return any(cue.lower() in q for cue in _CAPABILITY_CUES)


def enforce(answer: str, query: str = "", *, mode: str = "insufficient") -> str:
    """Guarantee an honest answer for a low-evidence turn.

    Behaviour:
      * mode not in the low-evidence set → returned unchanged.
      * answer already has an honest signal → returned unchanged (we trust the
        model's own honest wording).
      * answer looks like a fabrication (confident number, no honest signal) →
        replaced wholesale with the canonical honest refusal.
      * answer is honest-ish but lacks an explicit signal token → the canonical
        clause is prepended so the contract is met, then the model's text is
        kept as supplementary context.

    Never raises; never fabricates; corpus-agnostic.
    """
    if mode not in ("insufficient",):
        return answer

    canonical = _CANONICAL_PII_REFUSAL if _is_pii_query(query) else _CANONICAL_REFUSAL

    ans = (answer or "").strip()

    # Empty or trivially short → just return the honest canonical message.
    if len(ans) < 2:
        return canonical

    # Already honest → keep the model's wording (it may add useful nuance like
    # "建议查阅官方公开资料").
    if has_honest_signal(ans):
        return ans

    # Not a KB-fact ask → never hedge or clobber a clean answer. Two cases:
    #  (a) the query itself supplies numeric operands (e.g. "A占40%，总500亿，求A"),
    #      so any number in the answer is COMPUTED, not a fabricated KB fact; and
    #  (b) general capability / concept / chit-chat asks that don't need the corpus.
    # (Private-entity asks that slip a cue already self-corrected above via the
    # honest-signal check, so this only ever frees genuinely general answers.)
    query_self_contained = bool(_NUMERIC_CLAIM.search(query or ""))
    if query_self_contained or _is_general_capability_query(query):
        return ans

    # Fact-seeking turn, no honest signal:
    # Confident fabrication (a number+unit claim) → replace entirely.
    if looks_fabricated(ans):
        return canonical

    # Honest in spirit but missing an explicit token → prepend canonical clause.
    return f"{canonical}\n\n{ans}"
