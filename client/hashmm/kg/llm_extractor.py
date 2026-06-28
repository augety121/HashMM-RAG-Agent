"""LLM-based semantic triple extractor for the knowledge graph.

Why this module exists
----------------------
The legacy ``KGExtractor`` builds the graph from regex patterns + co-occurrence.
Co-occurrence has a fatal flaw on report / financial corpora: high-frequency
tokens such as years ("2024年") and bare metrics co-occur with *everything*, so
they become super-hubs. The resulting graph is a hairball with near-zero
modularity structure, which makes community detection collapse to **zero
communities** — the exact symptom we have been chasing.

This extractor replaces the *extraction layer only* with LLM-driven semantic
triple extraction:

  - ``head`` / ``tail`` are real entities (ORG / PERSON / PRODUCT / LOCATION /
    EVENT / CONCEPT). Their union *is* the node set, so there are no orphan
    nodes and no date/number hubs.
  - dates, years, percentages, money amounts and bare numbers are **never**
    graph nodes. They are demoted to the relation's ``description`` field
    (e.g. ``营收 [2024年] = 3658亿``), exactly as GraphRAG-style systems do.
  - the output feeds the **same** ``Entity`` / ``Relation`` dataclasses, so the
    downstream graph, refiner, community detection, retriever and storage are
    completely unchanged.

Drop-in contract
----------------
``LLMTripleExtractor`` exposes the same ``extract_from_text`` /
``extract_from_chunks`` signatures as ``KGExtractor`` and reuses its
deduplication helpers, so it can be swapped in wherever a ``KGExtractor`` is
used.

Graceful degradation (never crashes an ingest run)
--------------------------------------------------
  - no ``llm_fn``           → returns ``([], [])`` and logs a warning so the
                              caller can fall back to the local extractor;
  - per-chunk LLM error     → that chunk is skipped, extraction continues;
  - malformed / non-JSON    → that chunk yields nothing, no exception.

Opt-in
------
This path is **off by default**. It activates only when an operator sets
``HASHMM_KG_LLM_EXTRACT=1`` *and* an ``llm_fn`` is available (see
``make_kg_extractor`` and the ingest pipeline). Default behaviour is therefore
byte-for-byte identical to before.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import threading
from typing import Callable

from hashmm.kg.extractor import (
    Entity,
    Relation,
    KGExtractor,
    preprocess_text,
    validate_entity_name,
)
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.kg.llm_extractor")


# ── Node typing ────────────────────────────────────────────────────────────
# Only these types may become graph nodes. Anything else (DATE/NUMBER/METRIC/…)
# is demoted to a relation attribute. We accept a generous set of surface labels
# (English + Chinese) and canonicalise them, because the LLM will not always use
# the exact tag we asked for.
_TYPE_ALIASES: dict[str, str] = {
    # ORG
    "org": "ORG", "organization": "ORG", "organisation": "ORG", "company": "ORG",
    "corp": "ORG", "corporation": "ORG", "机构": "ORG", "公司": "ORG", "组织": "ORG",
    "企业": "ORG", "集团": "ORG", "部门": "ORG", "团队": "ORG",
    # PERSON
    "person": "PERSON", "people": "PERSON", "人物": "PERSON", "人": "PERSON",
    "高管": "PERSON", "创始人": "PERSON",
    # PRODUCT
    "product": "PRODUCT", "产品": "PRODUCT", "服务": "PRODUCT", "品牌": "PRODUCT",
    "业务": "PRODUCT", "车型": "PRODUCT", "型号": "PRODUCT",
    # LOCATION
    "location": "LOCATION", "place": "LOCATION", "geo": "LOCATION", "地点": "LOCATION",
    "地区": "LOCATION", "国家": "LOCATION", "城市": "LOCATION", "市场": "LOCATION",
    # EVENT
    "event": "EVENT", "事件": "EVENT", "活动": "EVENT",
    # CONCEPT (catch-all for domain concepts: 技术/指标名/方法/赛道)
    "concept": "CONCEPT", "concepts": "CONCEPT", "概念": "CONCEPT", "技术": "CONCEPT",
    "方法": "CONCEPT", "指标": "CONCEPT", "赛道": "CONCEPT", "领域": "CONCEPT",
    "行业": "CONCEPT",
}
ALLOWED_NODE_TYPES = {"ORG", "PERSON", "PRODUCT", "LOCATION", "EVENT", "CONCEPT"}

# Types that must NEVER become nodes (demoted to relation attributes).
_FORBIDDEN_TYPES = {
    "date", "time", "datetime", "year", "quarter", "number", "num", "metric",
    "percent", "percentage", "money", "currency", "amount", "value",
    "日期", "时间", "年份", "年", "季度", "数字", "数值", "金额", "百分比", "比例",
    "货币", "数量", "指标值",
}

# Defensive guard: a node name that *looks* like a date / number / money / percent
# is rejected even if the LLM mis-typed it. This is the last line of defence
# against the super-hub problem.
_DATE_NUM_RE = re.compile(
    r"""^\s*(?:
        (?:19|20)\d{2}\s*(?:年|财年|fy|q[1-4])?              # 2024 / 2024年 / 2024Q1
      | \d{1,4}\s*(?:年|月|日|季度|周|号)                    # 12月 / 3季度
      | [-+]?\d[\d,\.]*\s*%?                                  # 12 / 1,234.5 / 45.7%
      | [-+]?\d[\d,\.]*\s*(?:亿|万|千|百|元|美元|港元|人民币|美金|股|倍|个|名|项|次)+
      | q[1-4](?:\s*(?:19|20)\d{2})?                          # Q1 / Q1 2024
      | [hH][12]                                              # H1 / H2
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Generic tails that carry no graph signal even if the LLM emits them as nodes.
_GENERIC_NODE_NAMES = {
    "公司", "集团", "企业", "我们", "本公司", "该公司", "他们", "它", "其",
    "报告", "年报", "财报", "数据", "信息", "情况", "内容", "部分", "方面",
    "the company", "company", "it", "they", "this", "that",
}

_MAX_CHUNK_CHARS = 1600          # cap chunk text fed to the LLM (cost control)
_MIN_CHUNK_CHARS = 30            # skip near-empty chunks
_MAX_TRIPLES_PER_CHUNK = 40      # sanity cap to ignore runaway outputs


def _fewshot_rich_enabled() -> bool:
    return os.environ.get("HASHMM_KG_FEWSHOT_RICH", "").strip().lower() in {"1", "true", "yes", "on"}


# v17 Phase 92: richer multi-example few-shot for the FULL finance prompt.
# Diverse, real-shaped Chinese examples (ORG↔PERSON / ORG↔PRODUCT / ORG↔ORG /
# ORG↔CONCEPT with time/number demoted into context) — small models copy the
# pattern and stop returning empty []. Used only when HASHMM_KG_FEWSHOT_RICH=1.
_RICH_FEWSHOT_FINANCE = (
    '  {"head":"小米集团","head_type":"ORG","relation":"创始人","tail":"雷军","tail_type":"PERSON","context":""},\n'
    '  {"head":"小米集团","head_type":"ORG","relation":"发布","tail":"小米SU7","tail_type":"PRODUCT","context":"2024年"},\n'
    '  {"head":"小米集团","head_type":"ORG","relation":"营收","tail":"营业收入","tail_type":"CONCEPT","context":"2024年=3658亿元"},\n'
    '  {"head":"网易","head_type":"ORG","relation":"子公司","tail":"有道","tail_type":"ORG","context":""},\n'
    '  {"head":"林斌","head_type":"PERSON","relation":"担任","tail":"总裁","tail_type":"CONCEPT","context":"小米集团"}'
)

# v17 Phase 92: gleaning continuation prompt — ask for ONLY the missed triples.
_GLEANING_TEMPLATE = (
    "下面是同一段文本，以及【已经抽取到】的三元组。请只补充**遗漏的**三元组"
    "（同样的 JSON 数组格式、同样的实体类型与规则），不要重复已有的；"
    "确实没有遗漏就输出空数组 []。只输出 JSON 数组，无解释无 markdown。\n\n"
    "## 文本\n{text}\n\n## 已抽取\n{found}\n\n## 补充的 JSON 数组："
)


_EXTRACTION_TEMPLATE = """你是知识图谱抽取专家。从下面这段中文文本中抽取**语义三元组**（头实体, 关系, 尾实体）。

## 严格规则
1. 头实体、尾实体必须是**真实体**，类型只能是：ORG（机构/公司）、PERSON（人物）、PRODUCT（产品/业务）、LOCATION（地点/市场）、EVENT（事件）、CONCEPT（技术/指标名/赛道等概念）。
2. **绝对不要**把日期、年份、季度、纯数字、百分比、金额（如"2024年""45.7%""3658亿""第三季度"）当作实体节点。
3. 如果某条事实带时间或数值，把时间/数值写进该关系的 `context` 字段，**而不是**当成尾实体。
   例：{example_rule3}
4. 关系要用**有语义的动词或名词**（{relation_hints}…），不要用"相关""提到"这种空关系。
5. 实体名用文中的规范全称（"小米集团"而非"小米的整"）；不要把句子片段当实体。
6. 只输出能从这段文本**直接读出**的关系，不要推测、不要编造。
7. 没有可靠三元组就输出空数组 `[]`。

## 输出格式（只输出 JSON 数组，不要任何解释、不要 markdown 代码块）
[
{example_full}
]

## 待抽取文本
{{text}}

## JSON 数组："""


# Compact prompt: ~60% fewer instruction tokens than the full prompt above.
# Use for local models / cost-sensitive runs. Same output contract.
_COMPACT_TEMPLATE = """从文本抽取语义三元组(头实体,关系,尾实体)。
规则: 实体类型只能是 ORG/PERSON/PRODUCT/LOCATION/EVENT/CONCEPT; 日期/数字/金额/百分比不做实体,写进 context; 关系用有意义的词({relation_hints}等),不要"相关"; 只抽文中直接说的,不编造; 没有就输出 []。
只输出 JSON 数组,无解释无 markdown:
{example_compact}

文本:
{{text}}

JSON:"""


def build_extraction_prompt(profile, compact: bool = False) -> str:
    """Fill a domain profile into the (otherwise fixed) prompt template, leaving
    the ``{text}`` placeholder for per-chunk formatting."""
    tpl = _COMPACT_TEMPLATE if compact else _EXTRACTION_TEMPLATE
    if compact:
        return tpl.format(relation_hints=profile.relation_hints,
                          example_compact=profile.example_compact)
    return tpl.format(relation_hints=profile.relation_hints,
                      example_full=profile.example_full,
                      example_rule3=profile.example_rule3)


# Back-compat constants: finance-filled prompts (the previous hard-coded text).
# Existing imports of EXTRACTION_PROMPT / COMPACT_EXTRACTION_PROMPT still work.
from hashmm.kg.kg_domains import get_profile as _get_profile  # noqa: E402
EXTRACTION_PROMPT = build_extraction_prompt(_get_profile("finance"), compact=False)
COMPACT_EXTRACTION_PROMPT = build_extraction_prompt(_get_profile("finance"), compact=True)


# An entity-signal pre-filter: skip chunks that almost certainly contain no
# extractable entity (TOC lines, page numbers, legal boilerplate, pure tables of
# figures). In the user's 300-chunk run, 103 chunks yielded nothing — those LLM
# calls were pure waste. This is conservative: it only skips a chunk when NONE of
# the signals fire, so real content is not dropped.
_ENTITY_SIGNAL_RE = re.compile(
    r"(?:公司|集团|控股|银行|保险|证券|科技|汽车|电子|通信|基金|资本|股份|"          # ORG suffixes
    r"先生|女士|总裁|董事|总经理|主席|创始人|高管|"                                   # PERSON roles
    r"手机|汽车|平台|系统|服务|芯片|处理器|电视|音箱|手环|路由器|产品|"             # PRODUCT nouns
    r"营收|利润|毛利|研发|资产|市值|股价|出货|用户|收购|发布|合作|投资|成立|"       # business verbs/metrics
    r"委员会|基金会|大学|银行)"                                                       # institutions
    r"|[A-Z][A-Za-z]{2,}",                                                            # ASCII names (Xiaomi, SU7)
)


def has_entity_signal(text: str) -> bool:
    """True if the chunk plausibly contains an extractable entity. Conservative
    (only filters obvious boilerplate)."""
    return bool(_ENTITY_SIGNAL_RE.search(text or ""))


class LLMTripleExtractor(KGExtractor):
    """Extract semantic triples with an LLM. Drop-in for :class:`KGExtractor`.

    Args:
        llm_fn: a callable. Either ``fn(prompt: str) -> str`` (preferred, matches
            ``make_llm_fn_from_model``) or ``fn(messages: list[dict]) -> str``.
            Both are supported; the signature is detected at call time.
        max_chars: truncate each chunk to this many characters before prompting
            (token-cost control; default 1600).
    """

    def __init__(self, llm_fn: Callable | None = None, max_chars: int = _MAX_CHUNK_CHARS,
                 compact: bool = False, domain: str | None = None):
        # Skip the parent's jieba init — this path is purely LLM-driven.
        self.llm_fn = llm_fn
        self._jieba_ready = False
        self.max_chars = max_chars
        self.compact = compact
        from hashmm.kg.kg_domains import get_profile
        self.domain = get_profile(domain)  # default finance → unchanged prompt
        self._prompt_tpl = build_extraction_prompt(self.domain, compact=compact)
        # v17 Phase 92: optional RICH few-shot — small models (Qwen2.5-7B) extract
        # far more when shown several diverse examples (GraphRAG auto-tuning idea).
        # Default OFF (env HASHMM_KG_FEWSHOT_RICH) → prompt byte-for-byte unchanged.
        if not compact and _fewshot_rich_enabled():
            self._prompt_tpl = self._prompt_tpl.replace(
                self.domain.example_full, _RICH_FEWSHOT_FINANCE, 1
            )
        # v17 Phase 92: optional gleanings — after the first pass, re-prompt the
        # model up to N times to extract triples it MISSED (GraphRAG gleanings).
        # Default 0 → single pass, unchanged behaviour.
        try:
            self._gleanings = max(0, int(os.environ.get("HASHMM_KG_GLEANINGS", "0")))
        except ValueError:
            self._gleanings = 0
        # Per-run diagnostics so low-yield runs are debuggable without guessing:
        # was the model returning nothing, an empty [], or unparseable text?
        self._diag_lock = threading.Lock()
        self.last_diag = {"empty_raw": 0, "empty_list": 0, "unparsed": 0,
                          "with_triples": 0, "convert_dropped": 0, "attrs_kept": 0}
        self._drop_samples: list[dict] = []  # v17: examples of dropped triples (debug)
        # v17 Phase 88: circuit breaker — if the LLM keeps failing (e.g. DeepSeek
        # 402 "Insufficient Balance"), stop hammering every chunk. Without this a
        # dead/quota-exhausted endpoint produces thousands of 402s over ~30 min.
        # After this many CONSECUTIVE failures the breaker opens and remaining
        # chunks are skipped instantly with one clear, actionable log line.
        try:
            self._fail_threshold = max(1, int(os.environ.get("HASHMM_KG_LLM_MAX_FAILS", "12")))
        except ValueError:
            self._fail_threshold = 12
        self._consecutive_fails = 0
        self._circuit_open = False
        self._circuit_logged = False

    def _bump(self, key: str):
        with self._diag_lock:
            self.last_diag[key] = self.last_diag.get(key, 0) + 1

    def _attach_attribute(self, ent, rel: str, value) -> None:
        """Fold a numeric/date fact (估值=120亿元) into the entity's description
        instead of dropping it. Deduped, length-capped, counted for diagnostics."""
        if ent is None:
            return
        rel = str(rel).strip()[:24]
        value = str(value).strip()[:40]
        if not rel or not value:
            return
        fact = f"{rel}：{value}"
        existing = ent.description or ""
        if fact in existing:
            return
        if len(existing) < 360:  # keep descriptions retrievable, not bloated
            ent.description = (existing + "；" + fact).lstrip("；") if existing else fact
        with self._diag_lock:
            self.last_diag["attrs_kept"] = self.last_diag.get("attrs_kept", 0) + 1

    def _note_drop(self, head, rel, tail, reason: str, keys):
        """Record a dropped triple (with its raw keys) so low-yield builds are
        debuggable: you can SEE whether the model used unexpected field names."""
        with self._diag_lock:
            self.last_diag["convert_dropped"] = self.last_diag.get("convert_dropped", 0) + 1
            if len(self._drop_samples) < 8:
                self._drop_samples.append({
                    "head": str(head)[:40], "relation": str(rel)[:30],
                    "tail": str(tail)[:40], "reason": reason,
                    "keys": list(keys)[:10],
                })

    def set_llm(self, llm_fn: Callable):
        self.llm_fn = llm_fn

    # ── Public API (drop-in compatible) ──────────────────────────────────────

    def extract_from_text(self, text: str, source_id: str = "") -> tuple[list[Entity], list[Relation]]:
        """Extract entities + relations from one chunk via the LLM."""
        if not text or len(text.strip()) < _MIN_CHUNK_CHARS:
            return [], []
        if not callable(self.llm_fn):
            logger.warning("LLMTripleExtractor has no callable llm_fn; returning nothing")
            return [], []

        clean = preprocess_text(text)[: self.max_chars]
        prompt = self._prompt_tpl.replace("{text}", clean)

        raw = self._call_llm(prompt)
        if raw is None or not str(raw).strip():
            self._bump("empty_raw")
            return [], []

        triples = _parse_triples_json(raw)
        if not triples:
            # Distinguish a genuine empty array (model declined) from output we
            # failed to parse (format drift) — very different fixes.
            if _looks_like_empty_array(raw):
                self._bump("empty_list")
            else:
                self._bump("unparsed")
            # v17 Phase 92: even on an empty first pass, a gleaning pass can still
            # surface triples the model skipped — try it before giving up.
            if self._gleanings > 0:
                triples = self._glean(clean, [])
            if not triples:
                return [], []
        elif self._gleanings > 0:
            # v17 Phase 92: ask for missed triples and merge (dedup by head/rel/tail).
            extra = self._glean(clean, triples)
            if extra:
                triples = _merge_triples(triples, extra)

        self._bump("with_triples")
        return self._triples_to_entities_relations(triples, source_id)

    def _glean(self, text: str, found: list[dict]) -> list[dict]:
        """v17 Phase 92: up to N follow-up passes asking only for MISSED triples.
        Never raises; respects the circuit breaker; merges/dedups each round."""
        acc: list[dict] = []
        seen = found[:]
        for _ in range(self._gleanings):
            if self._circuit_open:
                break
            try:
                found_json = json.dumps(seen, ensure_ascii=False)
            except (TypeError, ValueError):
                found_json = "[]"
            prompt = _GLEANING_TEMPLATE.format(text=text, found=found_json)
            raw = self._call_llm(prompt)
            more = _parse_triples_json(raw) if raw else []
            if not more:
                break  # nothing new → stop early
            new = _merge_triples(seen, more)
            gained = len(new) - len(seen)
            seen = new
            acc = _merge_triples(acc, more)
            if gained <= 0:
                break
        return acc

    def extract_from_chunks(
        self,
        chunks: list[dict],
        on_progress: Callable[[int, int], None] | None = None,
        max_workers: int = 1,
        on_chunk: Callable[[str, list, list], None] | None = None,
        skip_ids: set | None = None,
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract from many chunks with per-chunk isolation + global dedup.

        Args:
            max_workers: when > 1, chunks are extracted concurrently with a
                thread pool. LLM calls are network-I/O bound (GIL released), so
                threads give near-linear speedup. Default 1 keeps serial,
                deterministic behaviour.
            on_chunk: optional callback ``fn(chunk_id, entities, relations)``
                fired after each chunk — used for checkpointing (resume support).
            skip_ids: chunk_ids to skip (already processed in a prior, resumed run).
        """
        all_entities: list[Entity] = []
        all_relations: list[Relation] = []
        total = len(chunks)
        ok_chunks = 0
        skip_ids = skip_ids or set()
        with self._diag_lock:
            self.last_diag = {"empty_raw": 0, "empty_list": 0, "unparsed": 0,
                              "with_triples": 0, "convert_dropped": 0, "attrs_kept": 0}
            self._drop_samples = []

        def _cid(idx: int, c: dict) -> str:
            return str(c.get("chunk_id", c.get("doc_id", f"chunk_{idx}")))

        usable = [
            (i, c) for i, c in enumerate(chunks)
            if len(str(c.get("text", "")).strip()) >= _MIN_CHUNK_CHARS
            and _cid(i, c) not in skip_ids
        ]
        done = 0

        if max_workers and max_workers > 1 and len(usable) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = {pool.submit(self._safe_extract, i, c): (i, c) for i, c in usable}
                for fut in as_completed(futs):
                    i, c = futs[fut]
                    ents, rels = fut.result()
                    all_entities.extend(ents)
                    all_relations.extend(rels)
                    if ents or rels:
                        ok_chunks += 1
                    if on_chunk:
                        on_chunk(_cid(i, c), ents, rels)
                    done += 1
                    if on_progress and done % 10 == 0:
                        on_progress(done, len(usable))
        else:
            for i, c in usable:
                ents, rels = self._safe_extract(i, c)
                all_entities.extend(ents)
                all_relations.extend(rels)
                if ents or rels:
                    ok_chunks += 1
                if on_chunk:
                    on_chunk(_cid(i, c), ents, rels)
                done += 1
                if on_progress and done % 10 == 0:
                    on_progress(done, len(usable))

        if on_progress:
            on_progress(len(usable), len(usable))

        merged_e = self._deduplicate_entities(all_entities)
        merged_r = self._deduplicate_relations(all_relations)
        d = self.last_diag
        logger.info(
            f"LLM-extracted {len(merged_e)} entities, {len(merged_r)} relations "
            f"from {ok_chunks}/{total} usable chunks (semantic-triple mode)"
        )
        logger.info(
            f"KG extraction breakdown: 有三元组={d['with_triples']}  "
            f"返回空[]={d['empty_list']}  解析失败={d['unparsed']}  模型空返回={d['empty_raw']}  "
            f"转换丢弃={d.get('convert_dropped', 0)}  属性事实={d.get('attrs_kept', 0)}  "
            f"(prompt={'compact' if self.compact else 'full'})"
        )
        # v17: if many chunks parsed triples but few produced entities, the
        # converter is dropping them — show WHY (raw keys + reason) so the cause
        # is visible instead of guessed. This is the "1321 triples → 45 chunks" case.
        if d.get("convert_dropped", 0) > 0 and ok_chunks < max(1, d["with_triples"]) * 0.5:
            logger.warning(
                f"KG 转换丢弃偏高（{d.get('convert_dropped')} 条三元组被丢、只有 {ok_chunks} 块产出）。"
                f"典型被丢样例（看 keys 是否非 head/relation/tail）："
            )
            for s in self._drop_samples[:5]:
                logger.warning(
                    f"  丢弃[{s['reason']}] keys={s['keys']} "
                    f"head={s['head']!r} rel={s['relation']!r} tail={s['tail']!r}"
                )
        if total >= 20 and d["with_triples"] < total * 0.2:
            hint = ("产出率偏低。若 解析失败 多 → 模型没按 JSON 格式输出；"
                    "若 返回空[] 多 → 试着【去掉 --compact】用完整 prompt（小模型更需要示例），"
                    "或换更大模型。")
            logger.warning(f"KG extraction low yield: {hint}")
        return merged_e, merged_r

    def _safe_extract(self, idx: int, chunk: dict) -> tuple[list[Entity], list[Relation]]:
        """Extract one chunk, never raising (one bad chunk must not kill the run)."""
        if self._circuit_open:  # v17 Phase 88: LLM is down/quota-exhausted → skip fast
            return [], []
        text = chunk.get("text", "")
        source_id = chunk.get("chunk_id", chunk.get("doc_id", f"chunk_{idx}"))
        try:
            return self.extract_from_text(text, source_id)
        except Exception as e:  # noqa: BLE001 — isolation is intentional
            logger.warning(f"LLM KG extraction failed on chunk {source_id}: {e}")
            return [], []

    # ── Internals ────────────────────────────────────────────────────────────

    def _record_ok(self):
        with self._diag_lock:
            self._consecutive_fails = 0

    def _record_fail(self, err: str):
        with self._diag_lock:
            self._consecutive_fails += 1
            if self._consecutive_fails >= self._fail_threshold and not self._circuit_open:
                self._circuit_open = True
                if not self._circuit_logged:
                    self._circuit_logged = True
                    logger.error(
                        f"KG LLM 连续失败 {self._consecutive_fails} 次，已熔断、停止后续调用"
                        f"（最近错误：{err[:160]}）。可能原因：API 余额不足(402)/密钥失效，"
                        f"或本地模型调用异常。请按上面的『最近错误』排查后重试；"
                        f"可用 HASHMM_KG_LLM_MAX_FAILS 调整熔断阈值。"
                    )

    def _call_llm(self, prompt: str) -> str | None:
        """Call llm_fn supporting both prompt-string and messages-list signatures."""
        if self._circuit_open:  # v17 Phase 88: stop calling a dead endpoint
            return None
        try:
            takes_one = True
            try:
                sig = inspect.signature(self.llm_fn)
                # Count only REQUIRED positional params. A prompt-style fn like
                # ``fn(prompt, max_new_tokens=None)`` has exactly one required
                # positional arg → must be called with a single string, NOT a
                # messages list. (Counting the optional 2nd arg here previously
                # mis-routed the local Qwen fn and broke it with a list input.)
                required_positional = [
                    p for p in sig.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                    and p.default is p.empty
                ]
                takes_one = len(required_positional) <= 1
            except (TypeError, ValueError):
                takes_one = True  # builtins / C callables → assume single arg

            if takes_one:
                out = self.llm_fn(prompt)
            else:
                out = self.llm_fn([{"role": "user", "content": prompt}])
            self._record_ok()
            return out if isinstance(out, str) else (str(out) if out is not None else None)
        except Exception as e:
            self._record_fail(str(e))
            logger.warning(f"KG extraction LLM call failed: {e}")
            return None

    def _triples_to_entities_relations(
        self, triples: list[dict], source_id: str
    ) -> tuple[list[Entity], list[Relation]]:
        entities: dict[str, Entity] = {}
        relations: list[Relation] = []
        src = [source_id] if source_id else []

        def _register(name: str, etype: str) -> str | None:
            name = (name or "").strip()
            canon_type = _normalize_type(etype)
            if canon_type is None:
                return None
            if not _valid_node_name(name):
                return None
            key = name.lower()
            if key not in entities:
                entities[key] = Entity(name=name, entity_type=canon_type, source_ids=list(src))
            return name

        for t in triples[:_MAX_TRIPLES_PER_CHUNK]:
            if not isinstance(t, dict):
                self._note_drop("", "", "", "not-a-dict", [])
                continue
            # v17: accept many key conventions — small local models often emit
            # Chinese keys (主体/关系/客体) or subject/predicate/object instead of
            # head/relation/tail. Previously these were silently dropped.
            h_raw = _get_any(t, "head", "主体", "subject", "s", "from", "source",
                             "h", "entity1", "ent1", "head_entity", "实体1")
            t_raw = _get_any(t, "tail", "客体", "object", "o", "to", "target",
                             "t", "entity2", "ent2", "tail_entity", "实体2")
            rel = str(_get_any(t, "relation", "关系", "predicate", "rel", "r", "p",
                               "relationship", "谓词", "relation_type")).strip()
            ht = _get_any(t, "head_type", "主体类型", "subject_type", "head_entity_type",
                          "htype", "type1", "source_type", default="CONCEPT")
            tt = _get_any(t, "tail_type", "客体类型", "object_type", "tail_entity_type",
                          "ttype", "type2", "target_type", default="CONCEPT")
            head = _register(h_raw, ht)
            tail = _register(t_raw, tt)
            if rel and rel in ("相关", "提到", "related", "mentions", "related_to"):
                self._note_drop(h_raw, rel, t_raw, "generic-relation", list(t.keys()))
                continue  # reject empty co-occurrence-style relations
            # v17: ATTRIBUTE FACTS — if one endpoint is a real entity and the other
            # is a numeric/date/measure value (估值=120亿元 / 员工数=2400 / 占比=23%),
            # keep it as an attribute on the entity instead of dropping the triple.
            # These quantitative facts are exactly what users ask about.
            if rel and head and not tail and _is_attr_value(t_raw):
                self._attach_attribute(entities.get(head.lower()), rel, t_raw)
                continue
            if rel and tail and not head and _is_attr_value(h_raw):
                self._attach_attribute(entities.get(tail.lower()), rel, h_raw)
                continue
            if not head or not tail or not rel or head == tail:
                self._note_drop(h_raw, rel, t_raw,
                                f"bad-fields(head={bool(head)},tail={bool(tail)},"
                                f"rel={bool(rel)},same={head == tail and bool(head)})",
                                list(t.keys()))
                continue
            context = str(_get_any(t, "context", "描述", "description", "desc",
                                   "evidence", "证据")).strip()
            relations.append(Relation(
                head=head,
                head_type=entities[head.lower()].entity_type,
                relation=rel[:40],
                tail=tail,
                tail_type=entities[tail.lower()].entity_type,
                weight=0.9,
                description=context[:200],
                source_ids=list(src),
            ))

        return list(entities.values()), relations


# ── Module-level helpers ─────────────────────────────────────────────────────

def _get_any(d: dict, *keys, default=""):
    """Return the first non-empty value among ``keys`` in dict ``d``.

    Small local models emit triples with inconsistent field names (head vs
    主体 vs subject vs s ...); this lets the converter accept all of them.
    """
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _normalize_type(raw: str) -> str | None:
    """Map a surface type label to a canonical allowed node type, or None."""
    t = (raw or "").strip().lower()
    if not t:
        return "CONCEPT"  # untyped → treat as concept rather than drop
    if t in _FORBIDDEN_TYPES:
        return None
    if t.upper() in ALLOWED_NODE_TYPES:
        return t.upper()
    return _TYPE_ALIASES.get(t)  # None if unknown → caller drops it


_PAREN_RE = re.compile(r"[（(][^（()）]*[）)]")


def _valid_node_name(name: str) -> bool:
    """A node name must be a real entity: not empty, not a date/number, not
    a generic filler, and must pass the shared entity-name validator.

    v17: validate a parenthetical-stripped copy so legitimate names that carry
    an alias/qualifier in brackets — '驭途(Route IQ)', '小米（武汉）有限公司' —
    are not rejected just for containing () (the strict validator forbids them).
    The FULL name is still what gets registered.
    """
    if not name or len(name) < 2 or len(name) > 40:
        return False
    if name.lower() in _GENERIC_NODE_NAMES:
        return False
    if _DATE_NUM_RE.match(name):
        return False
    bare = _PAREN_RE.sub("", name).strip()
    if len(bare) < 2:
        return False
    if validate_entity_name(bare):
        return True
    # v17: rescue legitimate single English words (Harness/Platform/Insurance/
    # Marketing) that the regex-era validator rejects ONLY for its
    # ≥6-consecutive-lowercase heuristic. LLM output is clean, so this is safe.
    if _is_plain_english_word(bare) and bare.lower() not in _GENERIC_NODE_NAMES:
        return True
    return False


_PLAIN_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z]{1,29}$")


def _is_plain_english_word(s: str) -> bool:
    """A single clean English word in Title case or ALL-CAPS (Harness, NASA) —
    NOT random mixed-case (DDBHat) nor concatenated-lowercase junk (thecompanyhas).
    All-lowercase single tokens are excluded: real entity names are capitalized,
    and lowercase blobs are usually run-together garbage the strict validator
    correctly rejects."""
    s = (s or "").strip()
    if not _PLAIN_WORD_RE.match(s):
        return False
    return s.istitle() or s.isupper()


def _is_attr_value(v) -> bool:
    """True if ``v`` is a numeric/date/measure value (an attribute, not an entity):
    2400 / 120亿元 / 23% / 2020年 / 600家. Such values become entity attributes."""
    s = str(v or "").strip()
    return bool(s) and bool(_DATE_NUM_RE.match(s))


def _looks_like_empty_array(raw: str) -> bool:
    """True if the model's output is essentially an empty JSON array (it declined),
    as opposed to unparseable format drift. Strips fences/prose first."""
    if not raw:
        return True
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()
    # Find a bracket pair and check it's empty.
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end > start:
        inner = s[start + 1:end].strip()
        return inner == ""
    return False


def _parse_triples_json(raw: str) -> list[dict]:
    """Robustly extract a JSON array of triples from an LLM response.

    Tolerates: ```json fences, leading/trailing prose, a dict wrapper like
    ``{"triples": [...]}``. Returns ``[]`` on any failure (never raises).
    """
    if not raw or not isinstance(raw, str):
        return []
    s = raw.strip()

    # Strip markdown code fences.
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    def _coerce(obj) -> list[dict]:
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            for key in ("triples", "relations", "data", "items", "result"):
                if isinstance(obj.get(key), list):
                    return [x for x in obj[key] if isinstance(x, dict)]
        return []

    # Fast path: the whole thing is valid JSON.
    try:
        return _coerce(json.loads(s))
    except (json.JSONDecodeError, ValueError) as _e:
        log_suppressed(logger, _e)

    # Fallback: locate the first balanced [...] or {...} block.
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = s.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == open_ch:
                depth += 1
            elif s[i] == close_ch:
                depth -= 1
                if depth == 0:
                    block = s[start:i + 1]
                    try:
                        return _coerce(json.loads(block))
                    except (json.JSONDecodeError, ValueError):
                        break

    # v17 Phase 92: last resort — json_repair (installed) fixes truncated/missing
    # brackets, trailing commas, single quotes, etc. Only reached when the strict
    # parsers above already failed, so it can only turn an unparsed []→parsed —
    # never changes a previously-good result. Degrades silently if unavailable.
    try:
        import json_repair  # type: ignore
        repaired = json_repair.loads(s)
        coerced = _coerce(repaired)
        if coerced:
            return coerced
    except Exception as _e:  # noqa: BLE001 — module missing or unrepairable
        log_suppressed(logger, _e)
    return []


def _triple_key(t: dict) -> tuple:
    """Identity of a triple for dedup: (head, relation, tail), case/space-insensitive."""
    def norm(x):
        return str(t.get(x, "")).strip().lower()
    return (norm("head"), norm("relation"), norm("tail"))


def _merge_triples(base: list[dict], extra: list[dict]) -> list[dict]:
    """Append `extra` triples not already present in `base` (dedup by head/rel/tail)."""
    seen = {_triple_key(t) for t in base if isinstance(t, dict)}
    out = list(base)
    for t in extra:
        if not isinstance(t, dict):
            continue
        k = _triple_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def make_kg_extractor(llm_fn: Callable | None = None) -> KGExtractor:
    """Factory: return an LLM triple extractor when opted in, else the local one.

    Opt-in is controlled by ``HASHMM_KG_LLM_EXTRACT`` (1/true/yes/on). When the
    flag is off **or** no ``llm_fn`` is available, the original local
    ``KGExtractor`` is returned, so default behaviour is unchanged.
    """
    flag = os.environ.get("HASHMM_KG_LLM_EXTRACT", "").strip().lower()
    use_llm = flag in ("1", "true", "yes", "on")
    if use_llm and callable(llm_fn):
        logger.info("KG extraction: using LLM semantic-triple extractor (opt-in)")
        return LLMTripleExtractor(llm_fn=llm_fn)
    return KGExtractor(llm_fn=llm_fn)
