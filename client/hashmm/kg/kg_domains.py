"""Domain profiles for KG extraction — adapt the prompt to the corpus domain.

The KG extraction prompt's *rules* (entity types, demote dates/numbers to edge
attributes, no fragments, no fabrication) are domain-neutral and fixed. Only its
*examples* and *relation hints* were hard-coded to finance. A domain profile
supplies just those corpus-specific bits, injected into the fixed template, so
extraction quality stays high while relations match the corpus.

``finance`` is the default and reproduces the previous prompt byte-for-byte, so
behaviour is unchanged unless a different domain is selected via
``HASHMM_KG_DOMAIN`` (finance/medical/legal/generic/auto) or the CLI flag.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainProfile:
    name: str
    relation_hints: str       # steers what relations to look for
    example_full: str         # multi-line JSON objects for the full prompt
    example_compact: str      # single-line example for the compact prompt
    example_rule3: str        # inline example used in rule 3 (date/number demotion)


_FINANCE = DomainProfile(
    name="finance",
    relation_hints="收购、子公司、发布、合作、竞争对手、营收、毛利率、研发投入",
    example_full=(
        '  {"head":"实体A","head_type":"ORG","relation":"收购","tail":"实体B","tail_type":"ORG","context":""},\n'
        '  {"head":"实体A","head_type":"ORG","relation":"营收","tail":"营业收入","tail_type":"CONCEPT","context":"2024年=3658亿元"}'
    ),
    example_compact='[{"head":"实体A","head_type":"ORG","relation":"收购","tail":"实体B","tail_type":"ORG","context":"2024年"}]',
    example_rule3='{"head":"小米集团","head_type":"ORG","relation":"营收","tail":"营业收入","tail_type":"CONCEPT","context":"2024年=3658亿元"}',
)

_MEDICAL = DomainProfile(
    name="medical",
    relation_hints="患病、症状、治疗、用药、副作用、适应症、剂量、并发症、检查、确诊",
    example_full=(
        '  {"head":"高血压","head_type":"CONCEPT","relation":"治疗","tail":"氨氯地平","tail_type":"PRODUCT","context":""},\n'
        '  {"head":"阿司匹林","head_type":"PRODUCT","relation":"副作用","tail":"胃肠道出血","tail_type":"CONCEPT","context":"长期服用"}'
    ),
    example_compact='[{"head":"高血压","head_type":"CONCEPT","relation":"治疗","tail":"氨氯地平","tail_type":"PRODUCT","context":""}]',
    example_rule3='{"head":"阿司匹林","head_type":"PRODUCT","relation":"推荐剂量","tail":"剂量","tail_type":"CONCEPT","context":"成人每日=100mg"}',
)

_LEGAL = DomainProfile(
    name="legal",
    relation_hints="签订、违约、管辖、适用、赔偿、解除、生效、约定、当事人、依据条款",
    example_full=(
        '  {"head":"甲方","head_type":"ORG","relation":"签订合同","tail":"乙方","tail_type":"ORG","context":""},\n'
        '  {"head":"本协议","head_type":"CONCEPT","relation":"适用法律","tail":"中华人民共和国合同法","tail_type":"CONCEPT","context":""}'
    ),
    example_compact='[{"head":"甲方","head_type":"ORG","relation":"签订合同","tail":"乙方","tail_type":"ORG","context":""}]',
    example_rule3='{"head":"本协议","head_type":"CONCEPT","relation":"违约金","tail":"违约责任","tail_type":"CONCEPT","context":"合同金额的=20%"}',
)

_GENERIC = DomainProfile(
    name="generic",
    relation_hints="隶属、合作、参与、负责、位于、发布、包含、导致、属于",
    example_full=(
        '  {"head":"实体A","head_type":"ORG","relation":"隶属","tail":"实体B","tail_type":"ORG","context":""},\n'
        '  {"head":"实体A","head_type":"PERSON","relation":"负责","tail":"实体C","tail_type":"CONCEPT","context":""}'
    ),
    example_compact='[{"head":"实体A","head_type":"ORG","relation":"隶属","tail":"实体B","tail_type":"ORG","context":""}]',
    example_rule3='{"head":"实体A","head_type":"ORG","relation":"指标","tail":"某指标","tail_type":"CONCEPT","context":"2024年=数值"}',
)

_PROFILES: dict[str, DomainProfile] = {
    "finance": _FINANCE, "medical": _MEDICAL, "legal": _LEGAL, "generic": _GENERIC,
}

_AUTO_SIGNALS: dict[str, tuple[str, ...]] = {
    "finance": ("营收", "毛利", "净利", "财报", "年报", "股东", "营业收入", "研发投入", "上市"),
    "medical": ("患者", "症状", "治疗", "药物", "剂量", "临床", "诊断", "适应症", "副作用", "疗效"),
    "legal": ("合同", "协议", "条款", "违约", "甲方", "乙方", "管辖", "赔偿", "当事人", "法律"),
}


def get_profile(name: str | None = None) -> DomainProfile:
    """Resolve a profile by name. Unknown/empty → finance (unchanged default)."""
    key = (name or os.environ.get("HASHMM_KG_DOMAIN", "finance")).strip().lower()
    return _PROFILES.get(key, _FINANCE)


def detect_domain(corpus_sample: list[str], min_signal: int = 5) -> str:
    """Pick a domain from a text sample by keyword document-frequency.
    Returns the top-scoring domain if it clears ``min_signal``, else ``generic``."""
    scores = {d: 0 for d in _AUTO_SIGNALS}
    for text in corpus_sample:
        t = str(text)
        for domain, kws in _AUTO_SIGNALS.items():
            if any(kw in t for kw in kws):
                scores[domain] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] >= min_signal else "generic"


def resolve_domain(name: str | None, corpus_sample: list[str] | None = None) -> DomainProfile:
    """Resolve, supporting ``auto`` against a corpus sample."""
    key = (name or os.environ.get("HASHMM_KG_DOMAIN", "finance")).strip().lower()
    if key == "auto":
        key = detect_domain(corpus_sample or [])
    return _PROFILES.get(key, _FINANCE)
