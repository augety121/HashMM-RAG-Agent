"""Retrieval Pipeline — unified search across vector + keyword indexes.

Architecture:
  Query → preprocess → expand synonyms
    → FAISS dense search (top-50)
    + BM25 sparse search (top-50)
    → RRF fusion → top-20
    → Reranker (optional) → top-5
    → Return with metadata (filename, page, section)
"""
from __future__ import annotations
import os
import re
import json
import pickle
from pathlib import Path
from dataclasses import dataclass, field
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retrieval_pipeline")

BM25_PATH = Path("data/bm25_index.pkl")

# ── Enterprise synonym expansion (v5.0: loaded from file + hardcoded core set) ──

_SYNONYMS_CORE = {
    # Financial terms
    "营收": ["收入", "营业收入", "revenue", "总收入", "top-line"],
    "利润": ["盈利", "净利润", "profit", "纯利", "bottom-line"],
    "净利润": ["纯利", "归母净利", "net profit", "盈利"],
    "毛利": ["毛利润", "gross profit", "销售毛利"],
    "毛利率": ["gross margin", "销售毛利率"],
    "营业成本": ["销售成本", "cost of revenue", "COGS"],
    "研发": ["研发费用", "R&D", "研发投入", "研发开支"],
    "管理费用": ["行政费用", "管理开支", "admin expenses"],
    "销售费用": ["营销费用", "selling expenses", "推广费用"],
    "现金流": ["经营现金流", "cash flow", "现金流量"],
    "资产": ["总资产", "assets", "资产总额", "资产总计"],
    "负债": ["总负债", "liabilities", "负债总额"],
    "股东权益": ["净资产", "所有者权益", "equity", "shareholders equity"],
    "ROE": ["净资产收益率", "return on equity"],
    "ROA": ["资产回报率", "return on assets", "总资产收益率"],
    "EPS": ["每股收益", "每股盈利", "earnings per share"],
    "市值": ["总市值", "market cap", "市场价值"],
    "股价": ["share price", "stock price", "每股价格"],
    "市盈率": ["PE", "P/E", "price earnings ratio"],
    "分红": ["股息", "dividend", "派息", "每股分红"],

    # Personnel & Organization
    "员工": ["雇员", "职工", "人员", "staff", "employee", "员工人数"],
    "董事": ["director", "董事会成员", "board member"],
    "股东": ["shareholder", "持股人", "投资者"],
    "CEO": ["首席执行官", "总裁", "行政总裁"],
    "CFO": ["首席财务官", "财务总监"],
    "CTO": ["首席技术官", "技术总监"],
    "董事长": ["chairman", "主席", "董事局主席"],
    "子公司": ["附属公司", "全资子公司", "subsidiary", "控股子公司"],
    "母公司": ["控股公司", "parent company", "集团公司"],
    "部门": ["事业部", "业务部", "division", "department"],

    # Business operations
    "增长": ["增加", "同比增长", "growth", "上升", "提升"],
    "下降": ["减少", "下滑", "decline", "降低", "下跌"],
    "同比": ["year-on-year", "YoY", "同比变动", "较上年"],
    "环比": ["quarter-on-quarter", "QoQ", "较上季"],
    "合同": ["合约", "协议", "contract", "agreement"],
    "产品": ["产品线", "product", "业务", "商品"],
    "客户": ["用户", "customer", "client", "消费者"],
    "市场": ["market", "市场份额", "市场规模"],
    "成本": ["费用", "cost", "expense", "开支", "支出"],
    "投资": ["investment", "注资", "投入"],
    "收购": ["并购", "acquisition", "M&A", "兼并"],
    "风险": ["risk", "风险因素", "不确定性"],
    "供应商": ["供货商", "vendor", "supplier"],
    "竞争": ["competition", "竞争对手", "competitor"],
    "战略": ["策略", "strategy", "规划"],
    "品牌": ["brand", "商标", "品牌价值"],

    # Time expressions
    "去年": ["上一年", "上一财年", "上年度", "last year"],
    "今年": ["本年", "本财年", "this year", "当年"],
    "上半年": ["H1", "前六个月", "first half"],
    "下半年": ["H2", "后六个月", "second half"],
    "季度": ["quarter", "季"],
    "年报": ["年度报告", "annual report", "全年报告"],
    "中报": ["半年报", "中期报告", "interim report"],
    "月份": ["月", "月度", "month"],

    # Technology
    "人工智能": ["AI", "机器学习", "深度学习", "artificial intelligence"],
    "大模型": ["LLM", "大语言模型", "foundation model", "基础模型"],
    "云计算": ["cloud", "cloud computing", "云服务"],
    "数据中心": ["data center", "IDC", "机房"],
    "芯片": ["chip", "半导体", "处理器", "semiconductor"],
    "5G": ["第五代通信", "fifth generation"],
    "物联网": ["IoT", "internet of things", "万物互联"],
    "自动驾驶": ["autonomous driving", "无人驾驶", "智能驾驶"],
    "新能源": ["new energy", "清洁能源", "可再生能源"],
    "电动车": ["EV", "electric vehicle", "新能源汽车"],
}

# Try to load extended synonyms from file
_SYNONYMS = dict(_SYNONYMS_CORE)
try:
    import json as _json
    _syn_path = Path("data/synonyms/general.json")
    if _syn_path.exists():
        with open(_syn_path, encoding="utf-8") as _f:
            _ext = _json.load(_f)
        _SYNONYMS.update(_ext)
        logger.info(f"Loaded {len(_ext)} extended synonym groups from {_syn_path}")
except Exception as _e:
    log_suppressed(logger, _e)

# ── Chinese stopwords (v5.0) ──
_STOPWORDS = frozenset({
    # Chinese function words
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "它", "们", "那", "些", "所以", "但是", "因为", "如果", "或者",
    "可以", "可能", "应该", "已经", "正在", "将要", "而且", "虽然",
    "但", "以", "及", "等", "被", "把", "让", "从", "向", "对",
    "跟", "比", "给", "用", "为了", "关于", "按照", "通过", "之",
    "所", "以及", "其", "其中", "这个", "那个", "这些", "那些",
    "什么", "怎么", "哪", "哪个", "哪些", "为什么", "如何",
    "多少", "几", "每", "各", "另", "别", "又", "再",
    "还是", "或", "并", "且", "而", "则", "却", "只",
    "才", "就是", "不过", "然后", "然而", "因此", "于是",
    "此", "该", "本", "某", "即", "其他", "以上", "以下",
    # English stopwords
    "the", "is", "are", "was", "were", "be", "been", "being",
    "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "this", "that", "it",
    "not", "no", "so", "if", "as", "has", "had", "have",
    "do", "does", "did", "will", "would", "can", "could",
    "shall", "should", "may", "might", "must",
})


@dataclass
class SearchResult:
    """A single search result with metadata."""
    text: str
    score: float
    doc_id: str = ""
    filename: str = ""
    page: int = -1
    section: str = ""
    chunk_id: str = ""
    source_type: str = ""  # "dense" | "sparse" | "fused"
    modality: str = "text"  # V104 多模态: text | table | code | image | chart | equation
    owner_id: str = ""
    workspace_id: str = ""


@dataclass
class SearchResponse:
    """Complete search response."""
    results: list[SearchResult]
    query: str
    expanded_query: str = ""
    total_candidates: int = 0
    elapsed_ms: int = 0
    sources: list[dict] = field(default_factory=list)
    requested_top_k: int = 0
    candidate_top_k: int = 0
    dense_candidates: int = 0
    sparse_candidates: int = 0
    fused_candidates: int = 0
    rerank_method: str = "rrf"


# ── V104 多模态：查询模态意图检测 + 模态加权（默认关，HASHMM_MODALITY_BOOST=1 开启）──
# 复用 agent/nodes.py 同款关键词思路，但生产检索路自带一份、零跨模块依赖，保持独立与零风险。
_MODALITY_QUERY_KEYWORDS: dict[str, str] = {
    "图片": "image", "图像": "image", "照片": "image", "示意图": "image", "插图": "image",
    "张图": "image", "如图": "image", "见图": "image", "图中": "image", "图所示": "image",
    "picture": "image", "photo": "image", "figure": "image", "diagram": "image", "illustration": "image",
    "图表": "chart", "柱状图": "chart", "饼图": "chart", "折线图": "chart", "走势图": "chart",
    "chart": "chart", "bar chart": "chart", "pie chart": "chart", "line chart": "chart",
    "表格": "table", "表中": "table", "如表": "table", "见表": "table", "table": "table", "tabular": "table",
    "公式": "equation", "方程": "equation", "formula": "equation", "equation": "equation",
}
# 意图模态 → 命中的 chunk modality 集合（chart 与 image 互相宽松匹配，因图表常被标为 image）
_MODALITY_INTENT_MATCH: dict[str, set] = {
    "image": {"image", "chart"}, "chart": {"chart", "image"},
    "table": {"table"}, "equation": {"equation", "formula"},
}
# "图3 / 表2 / figure 1 / table 2" 这类编号引用极常见，单独用正则兜底
import re as _re_modality
_FIGREF_RE = _re_modality.compile(r"(?:图表|图)\s*\d|(?:figure|fig\.?)\s*\d", _re_modality.IGNORECASE)
_TABREF_RE = _re_modality.compile(r"表\s*\d|table\s*\d", _re_modality.IGNORECASE)


def detect_modality_intent(query: str):
    """从查询里检测显式模态意图（问"图/表/图表/公式"或"图3/表2"编号引用）。
    纯规则、可单测、永不抛错。命中返回意图模态名（image/chart/table/equation），否则 None。
    刻意不匹配裸"图"，避免误伤"意图/地图/试图"等。"""
    q = (query or "").lower()
    for kw, mod in _MODALITY_QUERY_KEYWORDS.items():
        if kw in q:
            return mod
    # 关键词没命中再看编号引用：表N 优先于 图N（"图表N"已被上面的"图表"接走）
    if _TABREF_RE.search(q):
        return "table"
    if _FIGREF_RE.search(q):
        return "image"
    return None


# ── BM25 Keyword Index ──

class BM25Index:
    """Chinese-aware BM25 keyword index.

    Uses jieba for tokenization. Falls back to character n-grams
    if jieba is not installed.
    """

    def __init__(self):
        self._bm25 = None
        self._corpus: list[dict] = []  # Metadata parallel to BM25 corpus
        self._tokenized: list[list[str]] = []
        self._jieba_ready = False
        try:
            import jieba
            jieba.setLogLevel(20)
            # v5.0: Load enterprise custom dictionary
            _custom_words = [
                # Company names (prevent splitting)
                "小米集团", "腾讯控股", "阿里巴巴", "字节跳动", "华为技术",
                "百度集团", "京东集团", "美团点评", "拼多多", "网易集团",
                "比亚迪", "宁德时代", "中芯国际", "蔚来汽车", "理想汽车",
                "招商银行", "工商银行", "建设银行", "中国银行", "农业银行",
                "中国平安", "中国人寿", "贵州茅台", "五粮液", "中国移动",
                # Financial terms
                "营业收入", "营业成本", "净利润", "毛利率", "同比增长",
                "环比增长", "每股收益", "净资产收益率", "现金流量",
                "经营性现金流", "资本开支", "研发费用", "管理费用",
                "销售费用", "财务费用", "资产负债率", "流动比率",
                "应收账款", "存货周转", "商誉减值", "折旧摊销",
                # Tech terms
                "人工智能", "大语言模型", "云计算", "数据中心",
                "自动驾驶", "物联网", "区块链", "量子计算",
                "BGE-M3", "RTX4090", "DeepSeek",
                # Time
                "同比增长", "环比增长", "上半年", "下半年",
            ]
            for w in _custom_words:
                jieba.add_word(w)
            self._jieba_ready = True
        except ImportError:
            pass

    def add(self, texts: list[str], metadata_list: list[dict]):
        """Add documents to BM25 index."""
        for text, meta in zip(texts, metadata_list):
            tokens = self._tokenize(text)
            self._tokenized.append(tokens)
            self._corpus.append(meta)
            # V205 P0-1：增量镜像到磁盘倒排（图2-③口径），冷启动/无 rank_bm25 时兜底
            try:
                from hashmm.retrieval.bm25_disk import get_store as _bm25_store
                _cid = str(meta.get("chunk_id") or f"{meta.get('doc_id','')}#{len(self._corpus)}")
                _bm25_store().add(_cid, tokens, {**meta, "text": str(meta.get("text", text))[:800]})
            except Exception as _bde:
                log_suppressed(logger, _bde)
        self._rebuild_bm25()

    def search(self, query: str, top_k: int = 50) -> list[SearchResult]:
        """Search by keywords."""
        tokens = self._tokenize(query)
        if not tokens:
            return []
        if not self._bm25 or not self._corpus:
            # V205 P0-1：内存索引不可用（rank_bm25 未装 / pickle 丢失）→ 磁盘倒排兜底
            try:
                from hashmm.retrieval.bm25_disk import get_store as _bm25_store
                hits = _bm25_store().search(tokens, top_k)
                return [SearchResult(
                    text=str(h["meta"].get("text", "")),
                    score=float(h["score"]),
                    doc_id=str(h["meta"].get("doc_id", "")),
                    filename=str(h["meta"].get("filename", "")),
                    page=int(h["meta"].get("page", -1) or -1),
                    section=str(h["meta"].get("section", "")),
                    chunk_id=str(h["meta"].get("chunk_id", "")),
                    source_type="sparse",
                    modality=str(h["meta"].get("modality", "text")),
                    owner_id=str(h["meta"].get("owner_id", "")),
                    workspace_id=str(h["meta"].get("workspace_id", "")),
                ) for h in hits]
            except Exception as _bde:
                log_suppressed(logger, _bde)
                return []

        try:
            scores = self._bm25.get_scores(tokens)
        except Exception:
            return []

        top_indices = scores.argsort()[-top_k:][::-1]
        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            meta = self._corpus[idx]
            results.append(SearchResult(
                text=meta.get("text", ""),
                score=float(scores[idx]),
                doc_id=meta.get("doc_id", ""),
                filename=meta.get("filename", ""),
                page=meta.get("page", -1),
                section=meta.get("section", ""),
                chunk_id=meta.get("chunk_id", ""),
                source_type="sparse",
                modality=meta.get("modality", "text"),
                owner_id=str(meta.get("owner_id", "")),
                workspace_id=str(meta.get("workspace_id", "")),
            ))
        return results

    def remove_by_doc(self, doc_id: str):
        """Remove documents and rebuild."""
        keep = [(t, m) for t, m in zip(self._tokenized, self._corpus)
                if m.get("doc_id") != doc_id]
        if len(keep) != len(self._corpus):
            self._tokenized = [t for t, _ in keep]
            self._corpus = [m for _, m in keep]
            self._rebuild_bm25()
        # V205 P0-1：磁盘倒排同步删（与内存态无条件对齐）
        try:
            from hashmm.retrieval.bm25_disk import get_store as _bm25_store
            _bm25_store().remove_doc(doc_id)
        except Exception as _bde:
            log_suppressed(logger, _bde)

    def _tokenize(self, text: str) -> list[str]:
        """Enterprise-grade Chinese tokenization for BM25.

        v5.0: stopword removal + custom enterprise dictionary + POS filtering.
        """
        if self._jieba_ready:
            import jieba
            tokens = list(jieba.cut(text))
            result = []
            for t in tokens:
                t = t.strip()
                if not t or len(t) < 2:
                    continue
                if t in _STOPWORDS:
                    continue
                result.append(t)
            return result
        # Fallback: character bigrams + space-separated words
        words = text.lower().split()
        tokens = [w for w in words if len(w) >= 2 and w not in _STOPWORDS]
        # Add Chinese character bigrams
        for i in range(len(text) - 1):
            if '\u4e00' <= text[i] <= '\u9fff' and '\u4e00' <= text[i + 1] <= '\u9fff':
                tokens.append(text[i:i + 2])
        return tokens

    def _rebuild_bm25(self):
        """Rebuild the BM25 index from tokenized corpus."""
        if not self._tokenized:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._tokenized)
        except ImportError:
            logger.info("rank_bm25 not installed (pip install rank-bm25), BM25 disabled")
            self._bm25 = None

    def save(self, path: Path = BM25_PATH):
        """Persist to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"corpus": self._corpus, "tokenized": self._tokenized}, f)

    def load(self, path: Path = BM25_PATH) -> bool:
        """Load from disk."""
        if not path.exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._corpus = data["corpus"]
            self._tokenized = data["tokenized"]
            self._rebuild_bm25()
            logger.info(f"Loaded BM25 index: {len(self._corpus)} documents")
            return True
        except Exception as e:
            logger.warning(f"Failed to load BM25 index: {e}")
            return False

    @property
    def size(self) -> int:
        return len(self._corpus)


# ── Unified Retrieval Pipeline ──

class RetrievalPipeline:
    """Enterprise retrieval pipeline: dense + sparse + fusion.

    Usage:
        pipeline = RetrievalPipeline()
        pipeline.load()  # Load indexes from disk
        results = pipeline.search("小米2024年营收")
    """

    def __init__(self, vector_index=None, bm25_index=None):
        from hashmm.vector_index import VectorIndex
        self.vector_index = vector_index or VectorIndex()
        self.bm25_index = bm25_index or BM25Index()
        self._reranker = None
        self._reranker_available = None  # None = not checked yet

    def _try_init_reranker(self):
        """Auto-detect and initialize reranker if FlagEmbedding is installed."""
        if self._reranker_available is not None:
            return self._reranker_available

        try:
            from FlagEmbedding import FlagReranker
            model_path = None
            # Check local path first
            for p in ["/root/autodl-tmp/.local_models/bge-reranker-v2-m3",
                      os.path.expanduser("~/.local_models/bge-reranker-v2-m3")]:
                if os.path.isdir(p):
                    model_path = p
                    break
            if not model_path:
                model_path = "BAAI/bge-reranker-v2-m3"

            self._reranker = FlagReranker(model_path, use_fp16=True)
            self._reranker_available = True
            logger.info(f"Reranker loaded: {model_path}")
            return True
        except ImportError:
            self._reranker_available = False
            logger.info("Reranker not available (pip install FlagEmbedding to enable)")
            return False
        except Exception as e:
            self._reranker_available = False
            logger.warning(f"Reranker init failed: {e}")
            return False

    def load(self):
        """Load indexes from disk."""
        self.vector_index._ensure_index()
        self.bm25_index.load()
        logger.info(f"Retrieval ready: {self.vector_index.total_vectors} vectors, "
                    f"{self.bm25_index.size} BM25 docs")

    def _get_kg(self):
        """v17 Phase 95/96/97: lazily load + cache the saved KG (and community
        manager) for KG-grounded retrieval. Cached on the instance; a rebuild +
        server reload refreshes it. Never raises."""
        if getattr(self, "_kg_loaded", False):
            return self._kg_cache
        kg, comm = None, None
        try:
            from hashmm.kg.storage import KGStorage
            kg, comm = KGStorage().load()
        except Exception as e:
            log_suppressed(logger, e)
        self._kg_cache = kg
        self._comm_cache = comm
        self._kg_loaded = True
        return kg

    def _get_comm(self):
        """v17 Phase 96: cached community manager (loaded alongside the KG)."""
        if not getattr(self, "_kg_loaded", False):
            self._get_kg()
        return getattr(self, "_comm_cache", None)

    def _fold_kg(self, sparse_results, meta: dict, source_type: str) -> int:
        """Append one KG candidate as a SearchResult if not already present
        (dedup by text prefix). Returns 1 if added, else 0."""
        text = meta.get("text", "")
        if not text:
            return 0
        if any(text[:80] == s.text[:80] for s in sparse_results):
            return 0
        sparse_results.append(SearchResult(
            text=text,
            score=float(meta.get("_kg_score", 0.0)),
            doc_id=meta.get("doc_id", ""),
            filename=meta.get("filename", ""),
            page=meta.get("page", -1),
            section=meta.get("section", ""),
            chunk_id=meta.get("chunk_id", ""),
            source_type=source_type,
            modality=meta.get("modality", "text"),
            owner_id=str(meta.get("owner_id", "")),
            workspace_id=str(meta.get("workspace_id", "")),
        ))
        return 1

    def _kg_augment(self, query: str, sparse_results: list) -> None:
        """v17 Phase 95/96/97: fold KG-grounded candidates into the sparse pool —
        entity-local subgraph (95), community-global summaries (96), and PPR
        multi-hop (97). Each behind its own default-OFF flag; the reranker is the
        final arbiter. Never raises into the answer path."""
        try:
            corpus = getattr(self.bm25_index, "_corpus", None)
            added = 0
            from hashmm.kg import kg_retrieval, kg_ppr, community_retrieval, kg_router
            # v17 Phase 100: when HASHMM_KG_AUTO is on, classify the query and let the
            # router pick strategies; otherwise fall back to each strategy's own flag
            # (default OFF → unchanged behaviour).
            auto = kg_router.kg_auto_enabled()
            route = kg_router.route_strategies(query) if auto else None
            do_local = route["local"] if auto else kg_retrieval.kg_retrieval_enabled()
            do_ppr = route["ppr"] if auto else kg_ppr.kg_ppr_enabled()
            do_global = route["global"] if auto else community_retrieval.community_global_enabled()
            # Phase 95 — entity-local subgraph
            if do_local:
                for m in kg_retrieval.kg_local_candidates(query, self._get_kg(), corpus):
                    added += self._fold_kg(sparse_results, m, "kg")
            # Phase 97 — PPR multi-hop
            if do_ppr:
                for m in kg_ppr.ppr_candidates(query, self._get_kg(), corpus):
                    added += self._fold_kg(sparse_results, m, "kg_ppr")
            # Phase 96 — community-global summaries
            if do_global:
                for m in community_retrieval.relevant_community_summaries(
                        query, self._get_kg(), self._get_comm()):
                    added += self._fold_kg(sparse_results, m, "kg_global")
            if added:
                logger.info(f"KG augment added {added} candidates (auto={auto})")
        except Exception as e:
            log_suppressed(logger, e)

    def search(self, query: str, top_k: int = 5,
               filters: dict | None = None) -> SearchResponse:
        """Full retrieval pipeline.

        Args:
            query: Search query
            top_k: Number of final results
            filters: Optional filters:
                - filename: str or list[str] — only return chunks from these files
                - page_range: tuple[int, int] — only return chunks from these pages
                - doc_id: str — only return chunks from this document
                - owner_id: str — only return chunks owned by this user (multi-tenant)
        """
        import time
        t0 = time.time()

        # Step 1: Preprocess query
        from hashmm.pipeline.text_preprocessor import preprocess
        query = preprocess(query)

        # Step 2: Expand synonyms
        expanded = self._expand_query(query)

        # Step 3: Dense search (FAISS) — increased pool for reranker
        initial_pool = 100 if self._try_init_reranker() else 50
        dense_results = []
        if self.vector_index.total_vectors > 0:
            from hashmm.encoder_pool import EncoderPool
            query_emb = EncoderPool.encode_query(query)
            raw = self.vector_index.search(query_emb, top_k=initial_pool, filters=filters)
            for r in raw:
                dense_results.append(SearchResult(
                    text=r.get("text", ""),
                    score=r.get("score", 0),
                    doc_id=r.get("doc_id", ""),
                    filename=r.get("filename", ""),
                    page=r.get("page", -1),
                    section=r.get("section", ""),
                    chunk_id=r.get("chunk_id", ""),
                    source_type="dense",
                    modality=r.get("modality", "text"),
                    owner_id=str(r.get("owner_id", "")),
                    workspace_id=str(r.get("workspace_id", "")),
                ))

        # Step 4: Sparse search (BM25)
        sparse_results = self.bm25_index.search(expanded, top_k=initial_pool)

        # v5.2: Supplementary keyword boost for year-specific queries
        # If query mentions a year, also search with year + key financial terms
        import re as _re
        year_match = _re.search(r'(20\d{2})', query)
        if year_match and self.bm25_index.size > 0:
            year = year_match.group(1)
            supplement_queries = [
                f"{year}年 收入 总收入",
                f"截至{year}年12月31日",
                f"{year}年 营业收入 利润",
            ]
            for sq in supplement_queries:
                extra = self.bm25_index.search(sq, top_k=20)
                for r in extra:
                    # Only add if not already in sparse results
                    if not any(r.text[:80] == s.text[:80] for s in sparse_results):
                        sparse_results.append(r)

        # v17 Phase 95/96/97: KG-grounded candidates (entity-local / community-global
        # / PPR multi-hop). Each behind its own default-OFF flag; folded into the
        # sparse pool so RRF + reranker treat them fairly. Never raises.
        self._kg_augment(query, sparse_results)

        # v5.1: Apply document-level filters to both result sets
        if filters:
            dense_results = self._apply_filters(dense_results, filters)
            sparse_results = self._apply_filters(sparse_results, filters)

        # Step 5: RRF Fusion
        # v5.2: Larger candidate pool when reranker available
        reranker_ready = self._reranker_available if self._reranker_available is not None else self._try_init_reranker()
        candidate_pool = min(top_k * 10, 100) if reranker_ready else top_k * 4
        fused = self._rrf_fusion(dense_results, sparse_results, top_k=candidate_pool)

        fused_candidates = len(fused)
        rerank_method = "rrf"
        # Step 6: Reranker cross-encoder (if available)
        if reranker_ready and self._reranker and len(fused) > top_k:
            fused = self._rerank(query, fused, top_k)
            rerank_method = "cross_encoder"
            logger.info(f"Reranked {candidate_pool} → {len(fused)} results")
        elif not (reranker_ready and self._reranker):
            # Step 6.5 (V98): 本地语义重排——桌面 sidecar 的小模型嵌入服务。
            # 仅当 FlagEmbedding 重排缺位、HASHMM_LOCAL_EMBED_URL 已设且服务
            # 健康时生效；模块内部任何异常原样返回（铁律 3：默认关、永不抛错）。
            try:
                from hashmm.local_semantic import maybe_local_rerank
                reordered = maybe_local_rerank(query, fused, top_k)
                if reordered is not fused:   # no-op 时返回同一对象，identity 判定零成本
                    fused = reordered
                    rerank_method = "local_semantic"
                    logger.info("Local semantic rerank applied (desktop sidecar)")
            except Exception:
                pass

        # V104 模态加权（默认关，HASHMM_MODALITY_BOOST=1）：问"图/表/图表/公式"时把对应模态结果前移。
        if os.environ.get("HASHMM_MODALITY_BOOST", "0") == "1":
            fused = self._apply_modality_boost(query, fused)

        # V103.9 MMR 多样性去冗（默认关，HASHMM_MMR_DIVERSITY=1 开启）：在 top_k 内按
        # "相关性 × 多样性"重排，剔除讲同一件事的冗余块——用更少的格子覆盖更多信息，
        # 减少上下文稀释。纯文本（字符二元组 Jaccard），不依赖嵌入/模型。
        if os.environ.get("HASHMM_MMR_DIVERSITY", "0") == "1" and len(fused) > top_k:
            final = self._diversify(fused, top_k)
        else:
            final = fused[:top_k]
        elapsed = round((time.time() - t0) * 1000)

        # Build sources for citation
        sources = []
        for i, r in enumerate(final):
            sources.append({
                "id": i + 1,
                "text": r.text[:200],
                "filename": r.filename,
                "page": r.page,
                "section": r.section,
                "score": round(r.score, 3),
                "modality": r.modality,
            })

        return SearchResponse(
            results=final,
            query=query,
            expanded_query=expanded,
            total_candidates=len(dense_results) + len(sparse_results),
            elapsed_ms=elapsed,
            sources=sources,
            requested_top_k=top_k,
            candidate_top_k=candidate_pool,
            dense_candidates=len(dense_results),
            sparse_candidates=len(sparse_results),
            fused_candidates=fused_candidates,
            rerank_method=rerank_method,
        )

    def _apply_modality_boost(self, query: str, results: list) -> list:
        """V104 模态加权（默认关）：当查询显式要"图/表/图表/公式"时，把对应模态的结果
        稳定前移（组内保持原相关性顺序），让用户真正想要的那类内容浮上来。纯排序、可单测。
        无模态意图 / 池中无该模态时返回原列表（零变化，交给 P3 自进化去补）。"""
        intent = detect_modality_intent(query)
        if not intent or not results:
            return results
        want = _MODALITY_INTENT_MATCH.get(intent, {intent})
        if not any(getattr(r, "modality", "text") in want for r in results):
            return results
        return sorted(results, key=lambda r: 0 if getattr(r, "modality", "text") in want else 1)

    def _text_sim(self, a: str, b: str) -> float:
        """字符二元组 Jaccard 相似度——廉价、中英通用、无需分词/嵌入。用于 MMR 去冗。"""
        def shingles(s: str):
            s = (s or "")[:500]   # 限长，避免长文本相似度计算 O(n) 过大
            if len(s) < 2:
                return {s} if s else set()
            return {s[i:i + 2] for i in range(len(s) - 1)}
        sa, sb = shingles(a), shingles(b)
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return inter / union if union else 0.0

    def _diversify(self, results: list, top_k: int, sim_threshold: float = 0.5) -> list:
        """去冗多样化：按相关性顺序选取，**跳过**与已选结果文本相似度 > 阈值的候选
        （即"讲同一件事"的冗余块）；若因去重不足 top_k，再按原相关性顺序回填被跳过的。

        相比纯 MMR，这个策略对"剔除明显近重复"更直接、可预测：保住相关性序，
        只在出现高相似冗余时让位给不同信息。纯文本（字符二元组 Jaccard），无模型，可单测。
        """
        if not results or top_k <= 1 or len(results) <= 1:
            return results[:top_k]
        selected: list = []
        deferred: list = []
        for c in results:   # results 已按相关性排序
            if len(selected) >= top_k:
                break
            if any(self._text_sim(c.text, s.text) > sim_threshold for s in selected):
                deferred.append(c)   # 与已选高度相似 → 冗余，先跳过
                continue
            selected.append(c)
        # 去重导致不足 top_k → 用被跳过的（保持原相关性顺序）回填
        for c in deferred:
            if len(selected) >= top_k:
                break
            selected.append(c)
        return selected[:top_k]

    def _expand_query(self, query: str) -> str:
        """Expand query with enterprise synonyms.

        v5.0: Uses 500+ synonym groups, adds top-3 synonyms per matched term.
        """
        expanded_parts = [query]
        matched = 0
        for term, synonyms in _SYNONYMS.items():
            if term in query:
                expanded_parts.extend(synonyms[:3])
                matched += 1
                if matched >= 5:  # Cap expansions to avoid query dilution
                    break
        return " ".join(expanded_parts)

    def _rrf_fusion(self, dense: list[SearchResult], sparse: list[SearchResult],
                    top_k: int = 20, k: int = 60) -> list[SearchResult]:
        """Reciprocal Rank Fusion — combine dense and sparse results.

        RRF score = sum(1 / (k + rank)) across all result lists.
        k=60 is the standard constant from the RRF paper.
        """
        scores: dict[str, float] = {}
        items: dict[str, SearchResult] = {}

        for rank, r in enumerate(dense):
            key = r.chunk_id or r.text[:100]
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            if key not in items:
                items[key] = r

        for rank, r in enumerate(sparse):
            key = r.chunk_id or r.text[:100]
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            if key not in items:
                items[key] = r

        # Sort by fused score
        sorted_keys = sorted(scores.keys(), key=lambda x: -scores[x])
        results = []
        for key in sorted_keys[:top_k]:
            r = items[key]
            r.score = scores[key]
            r.source_type = "fused"
            results.append(r)

        return results

    def _rerank(self, query: str, candidates: list[SearchResult],
                top_k: int) -> list[SearchResult]:
        """Cross-encoder reranking with score caching.

        v7.0: Caches (query, text_hash) → score to avoid redundant GPU
        inference for repeated queries hitting the same documents.
        """
        try:
            # Check cache for each candidate
            if not hasattr(self, '_rerank_cache'):
                self._rerank_cache: dict[tuple[str, str], float] = {}
                self._rerank_cache_max = 2000

            uncached_indices = []
            uncached_pairs = []
            for i, r in enumerate(candidates):
                text_key = r.text[:512]
                cache_key = (query, text_key)
                if cache_key in self._rerank_cache:
                    r.score = self._rerank_cache[cache_key]
                else:
                    uncached_indices.append(i)
                    uncached_pairs.append((query, text_key))

            # Only compute scores for uncached pairs
            if uncached_pairs:
                scores = self._reranker.compute_score(uncached_pairs)
                if isinstance(scores, (int, float)):
                    scores = [scores]
                for idx, score in zip(uncached_indices, scores):
                    s = float(score)
                    candidates[idx].score = s
                    text_key = candidates[idx].text[:512]
                    self._rerank_cache[(query, text_key)] = s

                # Evict old entries if cache too large
                if len(self._rerank_cache) > self._rerank_cache_max:
                    keys = list(self._rerank_cache.keys())
                    for k in keys[:len(keys) // 2]:
                        del self._rerank_cache[k]

            candidates.sort(key=lambda r: -r.score)
        except Exception as e:
            logger.debug(f"Reranker failed: {e}")
        return candidates[:top_k]

    @staticmethod
    def _apply_filters(results: list[SearchResult],
                       filters: dict) -> list[SearchResult]:
        """Apply document-level filters to search results.

        Supported filters:
            filename: str or list[str] — match by filename (substring)
            doc_id: str — exact match by doc_id
            page_range: tuple[int, int] — page range (inclusive)
        """
        if not filters or not results:
            return results

        filtered = results

        # Filename filter
        if "filename" in filters:
            fn = filters["filename"]
            if isinstance(fn, str):
                fn = [fn]
            filtered = [r for r in filtered
                        if any(f in (r.filename or "") for f in fn)]

        # Doc ID filter
        if "doc_id" in filters:
            did = filters["doc_id"]
            filtered = [r for r in filtered if r.doc_id == did]

        # Page range filter
        if "page_range" in filters:
            lo, hi = filters["page_range"]
            filtered = [r for r in filtered
                        if lo <= r.page <= hi]

        # v6.0: Multi-tenant owner filter
        if "owner_id" in filters:
            oid = str(filters["owner_id"])
            filtered = [r for r in filtered
                        if str(getattr(r, "owner_id", "")) == oid]

        if "workspace_id" in filters:
            wid = str(filters["workspace_id"])
            filtered = [r for r in filtered
                        if str(getattr(r, "workspace_id", "")) == wid]

        return filtered

    def list_documents(self) -> list[dict]:
        """List all indexed documents with chunk counts.

        Returns:
            [{"filename": "...", "doc_id": "...", "num_chunks": N}, ...]
        """
        docs: dict[str, dict] = {}
        for meta in self.bm25_index._corpus:
            fn = meta.get("filename", meta.get("doc_id", "unknown"))
            did = meta.get("doc_id", "")
            owner_id = str(meta.get("owner_id", ""))
            workspace_id = str(meta.get("workspace_id", ""))
            key = f"{owner_id}\0{workspace_id}\0{fn}\0{did}"
            if key not in docs:
                docs[key] = {
                    "filename": fn,
                    "doc_id": did,
                    "owner_id": owner_id,
                    "workspace_id": workspace_id,
                    "num_chunks": 0,
                }
            docs[key]["num_chunks"] += 1
        return list(docs.values())
