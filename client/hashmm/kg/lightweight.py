"""Lightweight KG Builder — extract knowledge graph from indexed chunks.

No LLM required. Uses regex patterns to extract entities and co-occurrence
relations from already-indexed chunk text. Designed to run in < 5 seconds
on 2000+ chunks.

Usage:
    from hashmm.kg.lightweight import LightweightKGBuilder
    builder = LightweightKGBuilder()
    kg = builder.build_from_bm25()  # reads from existing BM25 index
    builder.save(kg)                # persists to rag_storage/kg/
"""
from __future__ import annotations
import re
import time
from collections import Counter, defaultdict
from hashmm.kg.extractor import Entity, Relation
from hashmm.kg.graph import KnowledgeGraph
from hashmm.kg.storage import KGStorage
from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.lightweight")


def _effective_workers(llm_fn, requested: int) -> int:
    """Local HF models are GPU-serial: concurrent generate() corrupts outputs and
    returns mostly empty. Force a single worker for them; keep ``requested`` for
    remote/API models (which DO benefit from parallel HTTP calls)."""
    try:
        if getattr(llm_fn, "is_local_hf", False) and (requested or 1) != 1:
            logger.info("Local HF model detected → forcing workers=1 "
                        "(GPU-serial; concurrency would corrupt extraction).")
            return 1
    except Exception:
        pass
    return max(1, int(requested or 1))

# ── Entity patterns (regex, no LLM) ──

_COMPANY_RE = re.compile(
    r'([\u4e00-\u9fff]{2,8}(?:集团|公司|控股|银行|保险|证券|科技|汽车|电子|通信|基金|资本))'
)
_PERSON_RE = re.compile(
    r'([\u4e00-\u9fff]{2,4}(?:先生|女士|总裁|董事长|董事|总经理|秘书|主席))'
)
_MONEY_RE = re.compile(
    r'([\d,]+\.?\d*\s*(?:亿|万|百万|千万|十亿)(?:元|美元|港币|人民币)?)'
)
_PERCENT_RE = re.compile(r'(\d+\.?\d*\s*[%％])')
_DATE_RE = re.compile(r'(20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?)')
_PRODUCT_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6}(?:手机|汽车|平台|系统|服务|芯片|处理器|电视|音箱|手环|路由器))'
)
_METRIC_RE = re.compile(
    r'(营[业收]收入|净利润|毛利[润率]|研发[费投]入|总资产|净资产|'
    r'市值|股价|市盈率|负债率|现金流|ROE|ROA|EPS|'
    r'出货量|用户数|月活|DAU|MAU|GMV)'
)

# Short common words / sentence-fragment garbage to skip.
# These appear when regex captures across word boundaries (e.g. "独立非执行董事"
# → "立非执行董事", or "于本年加入本集团" → "年加入本集团").
_SKIP_ENTITIES = frozenset({
    "有限公司", "股份公司", "集团公司", "有限责任公司",
    "本公司", "本集团", "该公司", "为本公司", "则本公司", "即本公司",
    "于本公司", "在本公司", "其本公司", "成为本公司", "至本公司",
    "年加入本集团", "加入本集团", "立非执行董事", "非执行董事", "执行董事",
    "本公司董事", "我们的董事", "我们的服务", "提供的服务",
})

# An entity is garbage if it STARTS WITH one of these leading fragments —
# these are verb/particle prefixes that signal the regex grabbed mid-sentence.
_GARBAGE_PREFIXES = (
    "为", "则", "即", "于", "在", "其", "成", "至", "并", "及", "和", "或",
    "年", "立", "我们", "提供", "下载", "处", "向", "由", "把", "对", "让",
)


def _is_garbage_entity(name: str) -> bool:
    """Heuristic: drop sentence-fragment false-positive entities."""
    n = name.strip()
    if n in _SKIP_ENTITIES:
        return True
    # leading verb/particle → likely a mid-sentence capture
    if n[:1] in ("为", "则", "即", "于", "在", "其", "并", "及", "和", "或", "把", "对", "让", "向", "由"):
        return True
    # starts with a fragment like "年加入..." / "我们的..."
    if any(n.startswith(p) and len(n) <= 8 for p in ("年加入", "我们的", "提供的", "下载")):
        return True
    return False


def _classify_entity(text: str) -> str:
    """Classify an entity string into a type."""
    if _COMPANY_RE.match(text):
        return "ORG"
    if _PERSON_RE.match(text):
        return "PERSON"
    if _MONEY_RE.match(text):
        return "MONEY"
    if _DATE_RE.match(text):
        return "DATE"
    if _PRODUCT_RE.match(text):
        return "PRODUCT"
    if _METRIC_RE.match(text):
        return "METRIC"
    if _PERCENT_RE.match(text):
        return "PERCENT"
    return "CONCEPT"


class LightweightKGBuilder:
    """Build knowledge graph from existing indexed chunks using regex only.

    Extracts:
    - Organizations (公司/集团/银行)
    - People (X先生/X董事长)
    - Financial metrics (营收/净利润/毛利率)
    - Products (X手机/X汽车)
    - Dates (2024年1月)
    - Money values (3659亿元)

    Relations are based on co-occurrence within the same chunk.
    """

    def __init__(self, min_entity_freq: int = 2, llm_fn=None):
        """
        Args:
            min_entity_freq: Minimum number of chunks an entity must appear in
                             to be included (filters noise). Regex path only.
            llm_fn: optional ``fn(prompt)->str``. When set AND
                    ``HASHMM_KG_LLM_EXTRACT`` is truthy, ``build_from_chunks``
                    uses LLM semantic-triple extraction instead of regex +
                    co-occurrence. Default None → regex path (unchanged).
        """
        self.min_freq = min_entity_freq
        self.llm_fn = llm_fn

    def _llm_enabled(self) -> bool:
        import os
        flag = os.environ.get("HASHMM_KG_LLM_EXTRACT", "").strip().lower()
        return flag in ("1", "true", "yes", "on") and callable(self.llm_fn)

    def _build_with_llm(self, chunks: list[dict], max_chunks: int | None,
                        max_workers: int, on_progress=None,
                        prefilter: bool = True, compact: bool = False,
                        on_chunk=None, skip_ids=None, domain=None) -> KnowledgeGraph:
        """Opt-in: build a semantic-triple KG from chunks via the LLM extractor.

        Reuses already-indexed chunks (no PDF re-parsing). Dates/numbers are
        demoted to edge attributes by the extractor, so no date super-hubs and
        no co-occurrence ``related_to`` edges — communities can actually form.

        prefilter: skip chunks with no entity signal (saves LLM calls on
            boilerplate). compact: use the shorter extraction prompt (fewer
            input tokens). on_chunk/skip_ids: checkpoint + resume support.
        """
        from hashmm.kg.llm_extractor import LLMTripleExtractor, has_entity_signal
        t0 = time.time()
        usable = [c for c in chunks if len(str(c.get("text", "")).strip()) >= 30]
        n_before = len(usable)
        if prefilter:
            usable = [c for c in usable if has_entity_signal(c.get("text", ""))]
            logger.info(f"Pre-filter: kept {len(usable)}/{n_before} chunks "
                        f"(skipped {n_before - len(usable)} with no entity signal)")
        if max_chunks and max_chunks > 0:
            usable = usable[:max_chunks]
        # v17: a local HF model is GPU-serial — driving generate() from 8 threads
        # corrupts inputs and returns ~99% empty. Force single-worker for it.
        max_workers = _effective_workers(self.llm_fn, max_workers)
        logger.info(f"Building KG with LLM semantic-triple extraction from "
                    f"{len(usable)} chunks (workers={max_workers}, compact={compact})...")
        # v17: if the caller gave no progress hook, install a default one that logs
        # percentage + elapsed + ETA every ~30s, so long LLM builds aren't silent.
        if on_progress is None:
            _pt0 = time.time()
            _plast = [_pt0]

            def on_progress(done, total, _t0=_pt0, _last=_plast):
                now = time.time()
                if total and (done >= total or now - _last[0] >= 30):
                    _last[0] = now
                    el = now - _t0
                    rate = done / el if el > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    logger.info(f"KG LLM extract progress: {done}/{total} "
                                f"({100.0 * done / total:.0f}%), elapsed {el:.0f}s, "
                                f"ETA {eta:.0f}s ({eta/60:.0f} min)")

        extractor = LLMTripleExtractor(llm_fn=self.llm_fn, compact=compact, domain=domain)
        entities, relations = extractor.extract_from_chunks(
            usable, on_progress=on_progress, max_workers=max_workers,
            on_chunk=on_chunk, skip_ids=skip_ids,
        )
        kg = KnowledgeGraph()
        kg.add_entities(entities)
        kg.add_relations(relations)
        logger.info(f"LLM KG built: {kg.num_entities} entities, "
                    f"{kg.num_relations} relations ({time.time() - t0:.1f}s)")
        return kg

    def build_from_bm25(self) -> KnowledgeGraph:
        """Build KG from the current BM25 index (no re-parsing needed)."""
        from hashmm.retrieval_pipeline import RetrievalPipeline
        pipeline = RetrievalPipeline()
        pipeline.load()

        chunks = []
        for meta in pipeline.bm25_index._corpus:
            chunks.append({
                "text": meta.get("text", ""),
                "page": meta.get("page", -1),
                "filename": meta.get("filename", ""),
                "doc_id": meta.get("doc_id", ""),
                "chunk_id": meta.get("chunk_id", ""),
            })

        logger.info(f"Building KG from {len(chunks)} indexed chunks...")
        return self.build_from_chunks(chunks)

    def build_from_chunks(self, chunks: list[dict], max_chunks: int | None = None,
                          max_workers: int = 8, on_progress=None,
                          prefilter: bool = True, compact: bool = False,
                          on_chunk=None, skip_ids=None, domain=None) -> KnowledgeGraph:
        """Build KG from a list of chunk dicts.

        Each chunk should have: text, page, filename, doc_id, chunk_id

        When opted in (``HASHMM_KG_LLM_EXTRACT=1`` and an llm_fn was provided),
        uses LLM semantic-triple extraction (``max_chunks``/``max_workers``/
        ``prefilter``/``compact``/checkpoint control cost/speed/resume).
        Otherwise uses the original regex + co-occurrence path below (unchanged).
        """
        if self._llm_enabled():
            return self._build_with_llm(chunks, max_chunks, max_workers, on_progress,
                                        prefilter=prefilter, compact=compact,
                                        on_chunk=on_chunk, skip_ids=skip_ids, domain=domain)

        t0 = time.time()
        kg = KnowledgeGraph()

        # Phase 1: Extract entities from all chunks
        entity_counter: Counter = Counter()
        entity_types: dict[str, str] = {}
        entity_sources: dict[str, list[str]] = defaultdict(list)
        entity_pages: dict[str, list[int]] = defaultdict(list)
        chunk_entities: list[list[str]] = []  # entities per chunk

        for chunk in chunks:
            text = chunk.get("text", "")
            if not text or len(text) < 20:
                chunk_entities.append([])
                continue

            found = set()

            # Extract each entity type
            for pattern in [_COMPANY_RE, _PERSON_RE, _PRODUCT_RE, _METRIC_RE, _DATE_RE]:
                for match in pattern.finditer(text):
                    name = match.group(1).strip()
                    if name in _SKIP_ENTITIES or len(name) < 2:
                        continue
                    found.add(name)
                    entity_counter[name] += 1
                    if name not in entity_types:
                        entity_types[name] = _classify_entity(name)
                    source_id = chunk.get("chunk_id", "")
                    if source_id and source_id not in entity_sources[name]:
                        entity_sources[name].append(source_id)
                    page = chunk.get("page", -1)
                    if page > 0 and page not in entity_pages[name]:
                        entity_pages[name].append(page)

            chunk_entities.append(list(found))

        # Phase 2: Filter by frequency (remove noise) + drop garbage fragments
        valid_entities = {
            name for name, count in entity_counter.items()
            if count >= self.min_freq and not _is_garbage_entity(name)
        }

        # Phase 3: Add entities to KG
        for name in valid_entities:
            etype = entity_types.get(name, "CONCEPT")
            pages = sorted(entity_pages.get(name, []))
            desc = f"出现 {entity_counter[name]} 次"
            if pages:
                desc += f"（第 {pages[0]}-{pages[-1]} 页）"

            entity = Entity(
                name=name,
                entity_type=etype,
                description=desc,
                source_ids=entity_sources.get(name, [])[:10],
                weight=min(entity_counter[name] / 5.0, 10.0),
            )
            kg.add_entity(entity)

        # Phase 4: Build co-occurrence relations.
        # v17 Phase 16: DATE/METRIC entities are attributes, NOT semantic actors.
        # Letting "2025年" co-occur with everything makes it a super-hub and turns
        # the graph into a hairball where communities can't form. So we only build
        # relations BETWEEN semantic entities (ORG/PERSON/PRODUCT/CONCEPT), never
        # to/from dates or metrics. Dates/metrics remain as nodes but unconnected.
        _NON_RELATIONAL = {"DATE", "METRIC"}

        def _is_relational(name: str) -> bool:
            return entity_types.get(name, "CONCEPT") not in _NON_RELATIONAL

        cooccurrence: Counter = Counter()
        for entities_in_chunk in chunk_entities:
            valid_in_chunk = [e for e in entities_in_chunk
                              if e in valid_entities and _is_relational(e)]
            for i in range(len(valid_in_chunk)):
                for j in range(i + 1, len(valid_in_chunk)):
                    pair = tuple(sorted([valid_in_chunk[i], valid_in_chunk[j]]))
                    cooccurrence[pair] += 1

        # Keep relations with a stronger co-occurrence signal (>=3 chunks) so the
        # graph reflects real association, not incidental mentions. Configurable.
        import os as _os
        _cap = int(_os.environ.get("HASHMM_KG_MAX_COOCCUR", "8000"))
        _min_cooccur = int(_os.environ.get("HASHMM_KG_MIN_COOCCUR", "3"))
        for (e1, e2), count in cooccurrence.most_common(_cap):
            if count < _min_cooccur:
                continue
            relation = Relation(
                head=e1,
                head_type=entity_types.get(e1, "CONCEPT"),
                relation="related_to",
                tail=e2,
                tail_type=entity_types.get(e2, "CONCEPT"),
                weight=min(count / 3.0, 5.0),
                description=f"共现 {count} 次",
            )
            kg.add_relation(relation)

        elapsed = time.time() - t0
        logger.info(f"Lightweight KG built: {kg.num_entities} entities, "
                    f"{kg.num_relations} relations ({elapsed:.1f}s)")

        return kg

    def save(self, kg: KnowledgeGraph):
        """Save to disk (same format as full KG extractor)."""
        storage = KGStorage()
        storage.save(kg)
        logger.info(f"KG saved to {storage.kg_dir}")

    def build_and_save(self, max_chunks: int | None = None,
                       max_workers: int = 8) -> dict:
        """One-call convenience: build from BM25 index and save."""
        from hashmm.retrieval_pipeline import RetrievalPipeline
        pipeline = RetrievalPipeline()
        pipeline.load()
        chunks = [{
            "text": meta.get("text", ""), "page": meta.get("page", -1),
            "filename": meta.get("filename", ""), "doc_id": meta.get("doc_id", ""),
            "chunk_id": meta.get("chunk_id", ""),
        } for meta in pipeline.bm25_index._corpus]
        logger.info(f"Building KG from {len(chunks)} indexed chunks...")
        kg = self.build_from_chunks(chunks, max_chunks=max_chunks, max_workers=max_workers)
        # v17 Phase 94: optional entity resolution before save (default OFF).
        try:
            from hashmm.kg.entity_resolution import resolve_enabled, resolve_entities, _drop_noise_enabled, _pinyin_enabled
            if resolve_enabled():
                resolve_entities(kg, drop_noise=_drop_noise_enabled(), use_pinyin=_pinyin_enabled())
        except Exception as _e:
            from hashmm.utils import log_suppressed as _ls
            _ls(logger, _e)
        self.save(kg)
        return kg.stats()
