"""Chat Retrieval v5.0 — enterprise-grade retrieval middleware.

v5.0 improvements:
  - LLM-based query rewriting (replaces regex pronoun resolution)
  - Retrieval confidence routing (high/medium/low → different answer strategies)
  - Query analysis: entity extraction + structured filters
  - Multi-entity comparison: split queries for A vs B comparisons

Usage:
    from hashmm.chat_retrieval import ChatRetrieval

    chat_rag = ChatRetrieval()
    enhanced_messages, sources, strategy = chat_rag.enhance(query, history)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.chat_retrieval")


def _filter_owner_results(results, owner_id: str | None):
    """Return only evidence owned by ``owner_id``.

    ``None`` is reserved for an explicitly unscoped/privileged caller.  An
    empty string and legacy chunks without ``owner_id`` both fail closed.
    Keeping this boundary local to ChatRetrieval ensures query planning,
    corrective retrieval, graph expansion and section navigation cannot place
    another tenant's text in the model prompt.
    """
    rows = list(results or [])
    if owner_id is None:
        return rows
    owner = str(owner_id or "")
    if not owner:
        return []

    def _owner_of(row) -> str:
        if isinstance(row, dict):
            return str(row.get("owner_id") or "")
        return str(getattr(row, "owner_id", "") or "")

    return [row for row in rows if _owner_of(row) == owner]

# v17 Phase 18f: detect when retrieved sources don't mention the query's subject.
_QUERY_STOP = ("的", "是", "多少", "公司", "查询", "年", "营收", "收入", "销量",
               "卖了", "在", "中国", "了", "请", "帮我", "一下", "怎么样", "如何",
               "预测", "财年", "总收入", "数据", "情况")

# v17 Phase 21b: particles / function words used to split a Chinese query into clean
# subject tokens. The previous greedy 2-4 char windows produced garbage tokens like
# "小米的整" from "小米的整体毛利率" — which never matched "小米集团" in the corpus,
# causing a FALSE entity-miss → wrongful refusal / missing citations on real 小米
# questions (fact_05/06/07/08/09/13/14). Split on particles instead.
_SUBJECT_SPLIT = re.compile(
    r"[的了着过地得在是和与及或把被对向从给为之其该等并且也都还就要想问"
    r"这那有哪些什么如何怎样多少各种关于针对情况业务\s"
    r"0-9A-Za-z]+"
    r"|[，,。.、；;：:？?！!（）()「」【】\u201c\u201d\u2018\u2019\"']+"
)


def _query_focused_window(text: str, query: str, max_chars: int = 500) -> str:
    """块超长时，返回与 query 最相关的 max_chars 字窗口，而不是无脑取开头。

    背景：分块上限 800 字，但注入上下文时按 text[:500] 截前 500 字——若答案落在
    500~800 字段就被丢了。这里用滑窗按"query 关键字符/词在窗口内的出现次数"打分，
    取最密集的那段。纯逻辑、无模型，可单测。文本不超长则原样返回；无 query 词则退回取开头。
    """
    text = text or ""
    if len(text) <= max_chars:
        return text
    q_terms = {t for t in re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", (query or "").lower()) if t}
    if not q_terms:
        return text[:max_chars]
    low = text.lower()
    step = max(1, max_chars // 4)
    best_start, best_score = 0, -1
    last_start = max(1, len(text) - max_chars + 1)
    for start in range(0, last_start, step):
        window = low[start:start + max_chars]
        score = sum(window.count(t) for t in q_terms)
        if score > best_score:
            best_score, best_start = score, start
    if best_score <= 0:
        return text[:max_chars]          # 窗口都没命中 → 退回取开头
    snippet = text[best_start:best_start + max_chars]
    return ("…" + snippet) if best_start > 0 else snippet


def _query_subjects(query: str) -> list[str]:
    """Extract salient subject tokens from a query, robust to Chinese particles.

    Order: known brands first (most reliable), then content tokens obtained by
    splitting on particles/digits/punct (NOT fixed-width greedy windows), then
    ASCII words. Used only as a fallback when the query names no known brand.
    """
    q = query or ""
    subs: list[str] = []
    for part in _SUBJECT_SPLIT.split(q):
        part = part.strip()
        if 2 <= len(part) <= 6 and part not in _QUERY_STOP and part not in subs:
            subs.append(part)
    for w in re.findall(r"[A-Za-z]{3,}", q):
        if w not in subs:
            subs.append(w)
    return subs


def _sources_miss_query_entity(query: str, sources: list) -> bool:
    """True if NONE of the query's salient subject tokens appear in the retrieved
    sources — meaning the corpus likely doesn't cover this subject.

    Coverage judgement is data-driven first: if a corpus vocabulary has been
    built (``data/corpus_vocab.json``, learned from whatever the customer
    actually ingested), the query's subject is whatever vocab terms it contains,
    and coverage = at least one of them appears in the retrieved sources. This
    adapts to ANY corpus, not just the hard-coded brands.

    Fallbacks preserve the previous behaviour exactly when no vocab exists:
      1) ``_BRAND_RE`` brand-anchored decision (legacy);
      2) particle-aware subject tokens.
    So with no vocab file present, this corpus behaves identically to before.
    """
    if not sources:
        return False
    blob = " ".join(
        (str(s.get("text", "")) + " " + str(s.get("filename", "")))
        for s in sources
    )

    # (0) Data-driven: corpus-learned vocabulary (adapts to any ingested corpus).
    try:
        from hashmm.corpus_vocab import get_default_vocab
        vocab = get_default_vocab()
        if vocab.available:
            subjects = vocab.query_subjects(query or "")
            if subjects:
                return not any(subj in blob for subj in subjects)
            # Vocab is the authority for this corpus: if the query names none of
            # its known subjects, we do NOT declare a miss (the query may be a
            # follow-up, a general phrasing, or chit-chat). Returning False here
            # avoids the brittle token fallback over-refusing.
            return False
    except Exception:
        pass  # any vocab issue → legacy behaviour below

    # (1) Legacy brand-anchored decision (precise for the built-in brand list).
    brands = [m.group(0) for m in _BRAND_RE.finditer(query or "")]
    if brands:
        return not any(b in blob for b in brands)
    # (2) Fallback for entities not in the brand list: particle-aware subject tokens.
    subjects = _query_subjects(query)
    if not subjects:
        return False
    return not any(subj in blob for subj in subjects)


# ── Retrieval decision patterns ──

_SKIP_SIGNALS = re.compile(
    r'^(你好|hi|hello|嗨|谢谢|thanks|ok|好的|明白|'
    r'再见|bye|请继续|继续|是的|对|不是|不对)[\s!！。.]*$',
    re.IGNORECASE,
)

_MUST_SEARCH = re.compile(
    r'(文档|文件|论文|报告|合同|数据|年报|财报|'
    r'营收|利润|收入|成本|毛利|净利|增长率|'
    r'根据|按照|参考|查找|搜索|检索|'
    r'document|file|paper|report|search|find)',
    re.IGNORECASE,
)

_CODE_GEN = re.compile(
    # Pattern 1: Chinese verb + noun (帮我写一个XX代码/实现/程序)
    r'(帮我写|写一个|生成|创建|实现|编写|帮写|帮忙写).{0,20}'
    r'(代码|脚本|程序|函数|class|script|组件|模块|算法|接口|爬虫|工具|实现|实例)|'
    # Pattern 2: English verb + noun
    r'(code|implement|write|create|build|develop).{0,10}'
    r'(function|class|module|script|component|api|server|algorithm|tree|sort)|'
    # Pattern 3: "用XX语言写/实现"
    r'(用.{0,8}(?:python|java|js|go|rust|c\+\+|C\+\+|typescript|react|vue|c语言).{0,8}(?:写|实现|做|开发))|'
    # Pattern 4: ML/DL frameworks
    r'(pytorch|tensorflow|keras|numpy|pandas|sklearn).{0,10}(实现|写)|'
    # Pattern 5: "XX的XX实现" — language + data structure/algorithm
    r'((?:C\+\+|python|java|go|rust|c语言).{0,6}(?:的|版).{0,10}'
    r'(?:实现|代码|程序|示例|demo))|'
    # Pattern 6: Data structures and algorithms (always code tasks)
    r'((?:红黑树|B树|AVL|哈希表|链表|队列|堆|图|二叉树|排序|搜索|遍历|动态规划|回溯|贪心|分治)'
    r'.{0,10}(?:实现|代码|算法|写|编写))|'
    # Pattern 7: Reverse — verb + data structure
    r'((?:实现|写|编写|帮我写).{0,8}'
    r'(?:红黑树|B树|AVL|哈希表|链表|队列|堆|二叉树|排序|搜索|遍历|动态规划|回溯|贪心|分治))|'
    # Pattern 8: Direct "写/实现" + language name
    r'((?:写|实现|编写).{0,6}(?:C\+\+|python|java|JavaScript|TypeScript|go|rust))',
    re.IGNORECASE,
)

# ── Query analysis patterns ──

# v17 Phase 21: general capability tasks that should be answered from the model's
# own ability, NOT routed through KB grounding/refusal. Only consulted AFTER the
# company/metric/document checks in should_search, so a match here is already known
# to carry no private-corpus entity — keeping it safe from intercepting real KB
# questions like "小米的毛利率" (caught earlier by _METRIC_RE).
_CAPABILITY_GEN = re.compile(
    # translation
    r'(翻译|译成|译为|translate)|'
    # text transformation of inline/quoted content (改写成 / 润色一下 / 换个说法 ...)
    r'((改写|润色|缩写|扩写|精简|换个?说法|换种说法|正式表达|地道).{0,8}'
    r'(成|为|一?下|表达|说法|版本|句子))|'
    r'(把["\u201c\u2018\'].{1,60}["\u201d\u2019\'].{0,12}(翻译|改写|润色|改成|换成|缩写|扩写))|'
    # formatting tasks — markdown/table/list (no corpus entity, since we passed the
    # company/metric checks already)
    r'((markdown|表格|列表).{0,8}(列出|生成|展示|做|输出|呈现|形式))|'
    r'((列出|生成|做|给我|整理).{0,8}(markdown|表格|列表))|'
    # arithmetic / unit conversion / pure-format number tasks
    r'(等于多少|加到\s*\d|阶乘|千分位|摄氏|华氏|平方根|开方|多少度|进制转换|转成?\d*进制)|'
    # one-liner / snippet code tasks not caught by _CODE_GEN
    r'(一行.{0,3}代码|反转字符串|字符串反转|代码示例|示例代码)|'
    # general-knowledge concept explanation. SAFE here because _BRAND_RE /
    # _COMPANY_RE / _METRIC_RE already returned True earlier for corpus questions
    # (解释小米业务 → caught by 小米), so anything reaching this clause names no
    # private-corpus entity — it is a generic concept best answered directly.
    r'(什么是|解释(?:一?下)?什么是|科普一?下|怎么理解)|'
    r'(有什么区别|有何不同|的区别是?|的作用是?|是做什么的)|'
    r'((解释|介绍)(?:一?下)?[\u4e00-\u9fff A-Za-z]{1,12}(?:的作用|是什么|的概念|的原理)?$)',
    re.IGNORECASE,
)

_COMPANY_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6}(?:集团|公司|控股|银行|保险|证券|科技|汽车|手机|电子|通信))'
)
_TIME_RE = re.compile(
    r'((?:20\d{2}|去年|今年|上一?年|前年)(?:年度|年)?|'
    r'(?:第?[一二三四1-4]季度|Q[1-4]|上半年|下半年|H[12])|'
    r'(?:\d{1,2}月(?:份)?))',
    re.IGNORECASE,
)
_METRIC_RE = re.compile(
    r'(营[业收]收入|净利润|毛利[润率]|ROE|ROA|EPS|'
    r'市值|股价|市盈率|负债率|资产总[额计]|'
    r'营业成本|管理费用|研发[费投]入|'
    r'员工[数人]|门店数|用户[数量]|DAU|MAU|GMV|'
    # v17 Phase 21: business-volume metrics — data lookups about a named subject
    # that must reach the KB/refuse path (华为出货量 / 蔚来交付量 / 快手日活),
    # instead of bypassing search and being free-answered from the model's memory.
    r'出货量|销[量售额]|交付量|订单量|日活|月活|活跃用户|活跃买家|装机量|市场份额)',
    re.IGNORECASE,
)

# v17 Phase 21: well-known company brands lacking a 公司/集团 suffix. A factual
# question naming one of these is a data lookup that must reach the KB/refuse path
# (so an out-of-corpus subject is honestly refused, not free-answered from memory).
# Capability tasks (translate/table/math) name no brand, so they stay unaffected.
_BRAND_RE = re.compile(
    r'(小米|网易|华为|蔚来|快手|美团|抖音|字节跳动|字节|比亚迪|理想汽车|小鹏|'
    r'拼多多|百度|腾讯|阿里巴巴|阿里|京东|特斯拉|苹果|微软|谷歌|亚马逊|英伟达|'
    r'三星|英特尔|台积电|奈飞)'
    r'|\b(?:vivo|oppo|meta|netflix|tiktok|huawei|xiaomi)\b',
    re.IGNORECASE,
)

# ── Rewrite indicators ──
_NEEDS_REWRITE = re.compile(
    r'(它|他们|她们|这个|那个|这些|那些|其|该|'
    r'上面的?|刚才的?|你说的|之前的?)|'
    r'(呢|吧|嘛)\s*[？?]?\s*$|'
    r'^(那|还有|另外|对比|比较)',
)


def _extract_recent_entity(history: list[dict]) -> str:
    """从对话历史里抽取最近提到的实体(公司/品牌)，用于指代消解。纯函数、可单测。

    扫描顺序：历史从新到旧，**用户与助手两侧都看**——「它去年呢」里的「它」常指上一轮
    助手答案里的公司。匹配优先级：先带后缀的公司名(更具体，如「网易公司/字节跳动集团」)，
    再裸品牌(_BRAND_RE，如「网易/小米/华为」)。找不到返回空串。

    只看最近若干轮(默认 6)以避免把很久以前的实体错误地带进来。
    """
    suffix_re = re.compile(r'([\u4e00-\u9fff]{2,8}(?:集团|公司|控股|银行|保险|证券|科技|'
                           r'汽车|手机|电子|通信|年报|报告|合同|产品|业务))')
    for msg in reversed((history or [])[-6:]):
        content = str(msg.get("content", "") or "")
        if not content:
            continue
        m = suffix_re.search(content)
        if m:
            return m.group(1)
        b = _BRAND_RE.search(content)
        if b:
            return b.group(0)
    return ""


# ── Rewrite indicators (legacy alias kept above) ──



@dataclass
class AnswerStrategy:
    """Determines how the LLM should use retrieval results."""
    mode: str = "direct"        # "grounded" | "augmented" | "supplement" | "direct" | "insufficient"
    instruction: str = ""       # System prompt addition for LLM
    confidence: float = 0.0     # Top retrieval score
    retrieval_contract: dict = field(default_factory=dict)

    @property
    def should_cite(self) -> bool:
        return self.mode in ("grounded", "augmented")


@dataclass
class QueryAnalysis:
    """Extracted entities and constraints from a query."""
    companies: list[str]
    times: list[str]
    metrics: list[str]
    is_compare: bool = False
    original_query: str = ""
    rewritten_query: str = ""


class ChatRetrieval:
    """v5.0 Enterprise retrieval middleware with LLM query rewriting.

    v7.0: Module-level singleton — use get_chat_retrieval() instead of
    constructing directly. Avoids re-initialization on every request.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pipeline = None
            cls._instance._initialized = False
            cls._instance._llm_fn = None
        return cls._instance

    def __init__(self):
        # __new__ handles init; avoid resetting state on repeated __init__ calls
        pass

    def reset(self):
        """Force re-initialization (e.g. after new documents indexed)."""
        self._initialized = False
        self._pipeline = None
        self._llm_fn = None

    def _ensure_pipeline(self):
        """Lazy-load the retrieval pipeline — uses global singleton."""
        if self._initialized:
            return
        self._initialized = True
        try:
            # v6.0: Use global singleton from retriever_bridge (already loaded at startup)
            from hashmm.retriever_bridge import get_pipeline
            self._pipeline = get_pipeline()
            if self._pipeline and self._pipeline.vector_index.total_vectors > 0:
                logger.info(f"Chat retrieval ready: "
                            f"{self._pipeline.vector_index.total_vectors} vectors")
            elif self._pipeline:
                logger.info("Chat retrieval: no indexed documents yet")
            else:
                # Fallback: create new pipeline
                from hashmm.retrieval_pipeline import RetrievalPipeline
                self._pipeline = RetrievalPipeline()
                self._pipeline.load()
        except Exception as e:
            logger.warning(f"Chat retrieval init failed: {e}")
            self._pipeline = None

    def _get_llm(self):
        """Get LLM function for query rewriting."""
        if self._llm_fn:
            return self._llm_fn
        try:
            from hashmm.api import app_state
            self._llm_fn = app_state.llm_fn
        except Exception as _e:
            log_suppressed(logger, _e)
        return self._llm_fn

    def resolve_query(self, query: str, history: list[dict]) -> str:
        """对外暴露的指代消解：把含代词/省略的问句改写成独立完整查询。

        与 ``enhance`` 内部用的是同一个 ``rewrite_query``。单独暴露是为了让调用方
        (如 streaming 的检索缓存) 能用**消解后的独立查询**做缓存键——否则「它去年呢?」
        这种问句在不同对话里指向不同公司，却会因原文相同而命中同一缓存。失败时安全
        退回原 query。
        """
        try:
            return self.rewrite_query(query, history or [])
        except Exception as e:
            log_suppressed(logger, e)
            return query

    # ── Decision: should we search? ──

    def should_search(self, query: str) -> bool:
        """Determine if a query needs knowledge base search.

        Principles:
        1. Factual questions (谁/什么/多少/何时) → search
        2. Queries mentioning specific entities → search
        3. Code generation / creative tasks → skip
        4. Greetings / short confirmations → skip
        5. If in doubt and query is long enough → search
        """
        query = query.strip()
        if len(query) < 3:
            return False
        if _SKIP_SIGNALS.match(query):
            return False
        if _CODE_GEN.search(query):
            return False
        # v17 Phase 21 — ordering matters. Decide in this priority:
        #   (1) corpus entity (brand/company/metric)  → SEARCH (and let the refuse
        #       path handle out-of-corpus subjects honestly).
        #   (2) general capability/concept task       → DIRECT (answer from ability;
        #       never force these through KB grounding/refusal).
        #   (3) explicit document intent (文档/报告/根据…) → SEARCH.
        #   (4) question mark / long query            → SEARCH.
        # (1) before (2) so "解释小米的营收" (brand+metric) searches; (2) before (3)
        # so "什么是检索增强生成" / "向量数据库的作用" are NOT mis-triggered by the
        # incidental substrings 检索/数据 inside _MUST_SEARCH.
        if _COMPANY_RE.search(query) or _METRIC_RE.search(query) or _BRAND_RE.search(query):
            return True
        if _CAPABILITY_GEN.search(query):
            return False
        if _MUST_SEARCH.search(query):
            return True
        if query.endswith("?") or query.endswith("？"):
            return True
        if len(query) > 15:
            return True
        return False

    # ── Query analysis: extract entities + constraints ──

    def analyze_query(self, query: str) -> QueryAnalysis:
        """Extract entities, time periods, and metrics from query."""
        companies = _COMPANY_RE.findall(query)
        times = _TIME_RE.findall(query)
        metrics = _METRIC_RE.findall(query)
        is_compare = (len(companies) > 1
                      or bool(re.search(r'(对比|比较|vs|VS|区别|差异)', query)))

        return QueryAnalysis(
            companies=companies,
            times=times,
            metrics=metrics,
            is_compare=is_compare,
            original_query=query,
        )

    # ── Query rewriting: LLM-based with regex fallback ──

    def rewrite_query(self, query: str, history: list[dict]) -> str:
        """Rewrite query using conversation context.

        v5.0: Uses LLM for complex cases, regex for simple pronoun resolution.
        """
        if len(history) < 2:
            return query
        if not _NEEDS_REWRITE.search(query):
            return query

        # Try LLM rewriting first
        llm = self._get_llm()
        if llm and len(query) < 50:
            rewritten = self._llm_rewrite(query, history, llm)
            if rewritten and rewritten != query:
                return rewritten

        # Fallback: regex-based rewriting
        return self._regex_rewrite(query, history)

    def _llm_rewrite(self, query: str, history: list[dict], llm_fn) -> str:
        """Use LLM to resolve pronouns and incomplete references."""
        hist_text = ""
        for msg in history[-4:]:
            role = "用户" if msg["role"] == "user" else "助手"
            hist_text += f"{role}: {msg['content'][:150]}\n"

        prompt = (
            "你是查询改写专家。根据对话历史，把最新问题改写为独立完整的检索查询。\n"
            "规则：补全代词和省略，保留原始意图，只返回改写后的查询，不解释。\n"
            "如果不需要改写，原样返回。\n\n"
            f"对话历史：\n{hist_text}\n"
            f"最新问题：{query}\n"
            f"改写后："
        )
        try:
            if hasattr(llm_fn, 'quick_call'):
                result = llm_fn.quick_call("你是查询改写专家", prompt, max_tok=100)
            else:
                result = llm_fn(prompt)
            rewritten = result.strip().split('\n')[0][:200]
            if rewritten and len(rewritten) > 3:
                logger.info(f"LLM query rewrite: '{query}' → '{rewritten}'")
                return rewritten
        except Exception as e:
            logger.debug(f"LLM rewrite failed: {e}")
        return query

    def _regex_rewrite(self, query: str, history: list[dict]) -> str:
        """Regex-based coreference resolution (fallback when LLM rewrite is off/declined).

        V103.51: 真正的指代消解，对标「它去年呢?」这类问句。改进点：
          1. 实体来源同时用 ``_BRAND_RE``（网易/小米/华为…无后缀品牌）和带后缀的公司名——
             旧版只认「集团/公司/年报…」后缀，会漏掉「网易」这种裸品牌（正是用户的例子）。
          2. 实体在**用户和助手**两侧的历史里都找——「它去年呢」中的「它」常指上一轮**助手
             答案**里提到的公司，而不只是用户上一句。
          3. **保留新问句里的时间锚点**（去年/今年/2024…）。「它去年呢」→「网易 去年」，
             而不是丢掉「去年」只剩公司名。
        """
        entity = _extract_recent_entity(history)
        # 去掉句首/句中的指代词，保留其余（含时间词）。
        pronoun_re = re.compile(r'(它的?|他们的?|她们的?|这个?的?|那个?的?|其|该|上面的?|刚才的?|之前的?)')
        cleaned = pronoun_re.sub('', query).strip()
        # 句首遗留的连接词/标点也清掉（"那它呢"→去"它"后剩"那呢"→"呢"）。
        cleaned = re.sub(r'^[那这,，、。\s]+', '', cleaned).strip()

        if entity:
            # 实体 + 剩余（剩余可能只是"去年呢""的毛利率"等）。剩余为空时退回实体本身。
            rewritten = f"{entity} {cleaned}".strip() if cleaned else entity
            if rewritten and rewritten != query:
                logger.info(f"Regex coref rewrite: '{query}' → '{rewritten}'")
                return rewritten
            return query

        # 没抓到实体：退回旧的「上一条用户问题前缀 + 去指代后的查询」启发式。
        recent_topic = ""
        for msg in reversed(history[-6:]):
            if msg.get("role") == "user" and len(msg.get("content", "")) > 5:
                recent_topic = msg["content"]
                break
        if not recent_topic:
            return query
        topic_prefix = recent_topic[:15].split("？")[0].split("?")[0]
        rewritten = f"{topic_prefix} {cleaned}".strip()
        if rewritten and rewritten != query:
            logger.info(f"Regex topic rewrite: '{query}' → '{rewritten}'")
            return rewritten
        return query

    # ── Retrieval confidence routing ──

    def _route_by_confidence(self, top_score: float, query: str = "",
                             sources: list | None = None) -> AnswerStrategy:
        """Determine answer strategy based on retrieval quality.

        Adapts to different score scales:
        - Reranker logits: -10 to +10 (>2 = good, >0 = ok, <0 = poor)
        - RRF scores: 0 to ~0.05 (>0.03 = good, >0.02 = ok)

        v17 Phase 18f: also refuse when the retrieved sources don't mention the
        query's subject entity — a positive score doesn't mean the corpus has the
        answer (e.g. an Apple query scoring 0.61 whose sources are all 小米/网易).
        """
        import os as _os
        _refuse_enabled = _os.environ.get("HASHMM_REFUSE_ON_LOW_EVIDENCE", "1") == "1"

        # Entity-presence refusal: clear subject in query but absent from sources.
        if (_refuse_enabled and query and sources
                and _sources_miss_query_entity(query, sources)):
            return AnswerStrategy(
                mode="insufficient",
                instruction=(
                    "检索到的资料里没有这个问题所问的主体对象，知识库未覆盖它。请这样回答：\n"
                    "1. 先坦诚、明确地告诉用户：知识库中没有关于这个主体的资料，无法据此给出确切答案。\n"
                    "2. 然后尽量帮忙——可以说明你为什么这么判断、提供与之相关的背景或思路、"
                    "或建议用户补充哪类文档/换个问法，让回答仍然对用户有用。\n"
                    "3. 但绝不要用你记忆里的具体数字、营收、销量等去填空或估算——这类事实必须来自检索资料；"
                    "也不要声称'据公开报道/联网搜索'等编造来源（本次没有外部检索结果）。\n"
                    "目标：诚实说明覆盖缺口的同时，仍然给用户有用的方向。"
                ),
                confidence=top_score,
            )

        if top_score > 1.0:
            # Reranker logit scores
            high_threshold, med_threshold = 1.5, 0.0
        else:
            # RRF/cosine scores
            high_threshold, med_threshold = 0.03, 0.02

        # v17 Phase 18: When retrieval is clearly irrelevant (negative reranker
        # score), the corpus almost certainly does NOT contain the answer. For a
        # RAG system over a private corpus, the honest behavior is to REFUSE —
        # tell the user the documents don't cover this — NOT to invent an answer
        # from the model's parametric knowledge (that's how hallucinations like
        # fabricated "Apple/Tesla revenue" happen). This is core to matching
        # Claude's honesty. Controlled by HASHMM_REFUSE_ON_LOW_EVIDENCE (default on).
        import os as _os
        _refuse_enabled = _os.environ.get("HASHMM_REFUSE_ON_LOW_EVIDENCE", "1") == "1"
        if top_score < 0:
            if _refuse_enabled:
                return AnswerStrategy(
                    mode="insufficient",
                    instruction=(
                        "知识库检索结果与问题几乎无关，文档里没有对应答案。请这样回答：\n"
                        "1. 坦诚告诉用户知识库没有相关资料，无法据此回答。\n"
                        "2. 如果这是通用常识/概念问题，可基于你自身知识简要、清楚地帮忙"
                        "（标明这是一般了解、非来自知识库）；若是需要具体数据/事实的问题，则不要编造具体数字或事实。\n"
                        "3. 不要声称任何外部来源（本次无外部检索结果）。涉及个人隐私（身份证号、住址等）"
                        "同样不提供，并说明此类信息受保护、知识库也没有。\n"
                        "在诚实的前提下，尽量给用户有用的方向。"
                    ),
                    confidence=top_score,
                )
            return AnswerStrategy(
                mode="direct",
                instruction="检索结果与问题无关，请完全基于自身知识回答。",
                confidence=top_score,
            )

        if top_score >= high_threshold:
            return AnswerStrategy(
                mode="grounded",
                instruction=(
                    "以下检索结果与问题高度相关。请直接、清楚、有条理地回答用户的问题——"
                    "先给结论，再按需要展开；能一句说清就别啰嗦，复杂内容才分点。\n"
                    "1. 答案要扎根于这些检索内容；给出来自文档的关键事实或数字时，在该句末尾用"
                    "方括号角标标注来源编号（如 [1]），对应下方【相关文档段落】，方便用户核对——"
                    "自然标注即可，不必每句都加。\n"
                    "2. 同比/对比/增长类问题，请写清被比较的双方、各自数值与对应年份"
                    "（如'2025年4,573亿元 vs 2024年3,659亿元'），并据此算出最终结果"
                    "（差额、增长率等），写出算式与一位小数；资料缺某个数则说明无法计算、不要硬凑。\n"
                    "3. 检索内容里没有的部分，如实说'文档中未提及'，不要编造数字或事实。"
                ),
                confidence=top_score,
            )
        elif top_score >= med_threshold:
            return AnswerStrategy(
                mode="augmented",
                instruction=(
                    "以下检索结果与问题有一定相关性。请结合这些内容，清楚、有条理地回答用户的问题，"
                    "先给可靠的部分，再说明哪些是文档没覆盖的。\n"
                    "1. 来自文档的信息用方括号角标标注来源（如 [1][2]），对应下方文档段落，便于核对；"
                    "你自己补充的常识请标明'（一般了解）'以便区分。\n"
                    "2. 如果用户问的是某个具体实体的具体数据（某公司营收、某产品销量等），"
                    "而检索结果里没有该实体的明确数据，不要编造——如实说明文档中没有这项信息，"
                    "并可给出你能提供的相关背景或下一步建议。"
                ),
                confidence=top_score,
            )
        else:
            return AnswerStrategy(
                mode="supplement",
                instruction=(
                    "检索结果与问题相关性较低。请仍然尽量帮到用户：\n"
                    "1. 通用常识或概念性问题，可基于你自身知识清楚作答（文档外的内容标明'（一般了解）'）。\n"
                    "2. 若涉及具体实体的具体数据或事实（财报数字、特定事件细节等）而检索未提供，"
                    "不要编造——如实说明知识库里没有直接资料，并给出力所能及的背景、思路，"
                    "或建议下一步（补充哪类文档、换个问法）。\n"
                    "宁可诚实又有帮助，也不要给没有依据的具体数字。"
                ),
                confidence=top_score,
            )

    # ── Main entry point ──

    def enhance(self, query: str, messages: list[dict],
                top_k: int = 5,
                retrieval_mode: str = "mix",
                allowed_docs: list | None = None,
                owner_id: str | None = None) -> tuple[list[dict], list[dict], AnswerStrategy]:
        """Enhance conversation with KB retrieval.

        v8.0: Supports three retrieval modes:
          - "naive": FAISS + BM25 only (original behavior)
          - "kg":    KG entity/relation VDB + graph traversal only
          - "mix":   Combine naive + kg results (recommended)

        Args:
            query: Current user query
            messages: Message history
            top_k: Number of results
            retrieval_mode: "naive", "kg", or "mix"
            allowed_docs: Explicit document ACL patterns, when configured.
            owner_id: Exact authenticated owner scope when no ACL is configured.

        Returns:
            (enhanced_messages, sources, strategy)
        """
        self._ensure_pipeline()

        from hashmm.retrieval.contract import build_retrieval_contract
        from hashmm.retrieval.run import RetrievalRun
        retrieval_run = RetrievalRun(
            query=query, requested_mode=retrieval_mode,
            requested_top_k=top_k,
            acl_scoped=allowed_docs is not None or owner_id is not None,
        )
        empty_strategy = AnswerStrategy(
            mode="direct", instruction="", confidence=0,
            retrieval_contract=build_retrieval_contract(
                query=query, requested_top_k=top_k, results=(), strategy="direct"))
        if not self._pipeline:
            retrieval_run.degraded("pipeline", "retrieval_pipeline_unavailable")
            empty_strategy.retrieval_contract["run"] = retrieval_run.finish(
                status="degraded")
            return messages, [], empty_strategy
        if not self.should_search(query):
            retrieval_run.degraded("routing", "query_does_not_require_retrieval")
            empty_strategy.retrieval_contract["run"] = retrieval_run.finish(
                status="skipped")
            return messages, [], empty_strategy

        # Query-adaptive routing: translate "auto" into a concrete mode based on
        # the query (relationship→kg, short lookup→naive, else mix). Explicit
        # naive/kg/mix pass through unchanged — current behaviour is preserved.
        self._last_route_reason = ""
        self._last_route_hops = 1
        try:
            from hashmm.retrieval.query_router import resolve_mode, route_query
            retrieval_mode, _route = resolve_mode(retrieval_mode, query, default="mix")
            if _route is not None:
                self._last_route_reason = _route.reason
                self._last_route_hops = getattr(_route, "hops", 1)
            else:
                # mode arrived explicit (e.g. server already resolved auto→kg);
                # still detect multi-hop intent from the query so KG traversal
                # depth adapts even when the mode wasn't chosen here.
                self._last_route_hops = route_query(query).hops
            retrieval_run.route(
                retrieval_mode, reason=self._last_route_reason,
                hops=self._last_route_hops,
            )
            logger.info(f"[Router] {query[:40]} → {retrieval_mode} (hops={self._last_route_hops})")
        except Exception as _e:
            log_suppressed(logger, _e)
            retrieval_run.route(retrieval_mode)
            retrieval_run.degraded("routing", type(_e).__name__)

        try:
            # Step 1: Analyze query
            analysis = self.analyze_query(query)
            search_filters: dict = {}
            if allowed_docs is not None:
                # A user-selected scope contains exact filenames and can be
                # pushed into the pipeline. ACL policies can contain globs such
                # as ``*.pdf``; the legacy filename filter is not glob-aware, so
                # those remain enforced by the post-search ACL gate.
                scoped_docs = list(allowed_docs)
                if scoped_docs and all(
                    not any(marker in str(name) for marker in ("*", "?", "["))
                    for name in scoped_docs
                ):
                    search_filters["filename"] = scoped_docs
            if owner_id is not None:
                search_filters["owner_id"] = str(owner_id or "")
            search_filters = search_filters or None

            def _search(term: str, *, limit: int):
                if search_filters:
                    return self._pipeline.search(
                        term, top_k=limit, filters=search_filters,
                    )
                return self._pipeline.search(term, top_k=limit)

            # Step 2: Rewrite query for better retrieval
            search_query = self.rewrite_query(query, messages)
            analysis.rewritten_query = search_query

            # Step 3: Plan and search
            from hashmm.query_planner import QueryPlanner, execute_plan
            planner = QueryPlanner()
            plan = planner.plan(search_query, messages)

            if plan.is_multi_step:
                # Multi-step: execute plan (comparison, timeline, etc.)
                merged = execute_plan(plan, self._pipeline, filters=search_filters)
                # Convert to SearchResult format
                from hashmm.retrieval_pipeline import SearchResponse, SearchResult
                results = [SearchResult(
                    text=r["text"], score=r["score"],
                    filename=r.get("filename", ""), page=r.get("page", -1),
                    section=r.get("section", ""), doc_id="",
                    chunk_id="", source_type="planned",
                    owner_id=str(r.get("owner_id", "")),
                    workspace_id=str(r.get("workspace_id", "")),
                ) for r in merged[:top_k * 2]]
                response = SearchResponse(results=results, query=search_query,
                                          total_candidates=len(merged))
                if plan.output_hint:
                    # Inject output format hint for LLM
                    search_query = f"{search_query}\n\n{plan.output_hint}"
            elif analysis.is_compare and len(analysis.companies) > 1:
                response = self._compare_search(
                    analysis, top_k, filters=search_filters,
                )
            else:
                # v16 Phase 12: optional HyDE + multi-query + RRF fusion
                from hashmm.retrieval import advanced as _adv
                use_hyde = _adv.hyde_enabled()
                use_mq = _adv.multiquery_enabled()
                if use_hyde or use_mq:
                    llm = self._get_llm()
                    # F11 cloud/local routing: HyDE + multi-query are cheap, high-volume
                    # rewrite tasks → prefer the local model when routing is enabled.
                    # Behaviour-preserving: route_llm returns the same `llm` when routing
                    # is off / no local model, so this is a no-op unless opted in.
                    try:
                        from hashmm.llm_router import route_llm, record_routing
                        _routed, _backend = route_llm("multiquery", llm,
                                                      user_id=getattr(self, "user_id", None))
                        llm = _routed or llm
                        record_routing("multiquery", _backend)
                    except Exception:
                        pass  # nosem: observability-fallback
                    search_terms = [search_query]
                    if use_mq:
                        search_terms = _adv.generate_multiqueries(search_query, llm, n=3)
                    if use_hyde:
                        hyde_doc = _adv.generate_hyde(query, llm)
                        if hyde_doc:
                            search_terms.append(hyde_doc)
                    # Search each term, fuse by RRF
                    result_lists = []
                    for term in search_terms:
                        try:
                            resp = _search(term, limit=top_k)
                            if resp.results:
                                result_lists.append(resp.results)
                        except Exception:
                            continue
                    if result_lists:
                        from hashmm.retrieval_pipeline import SearchResponse
                        fused = _adv.rrf_fuse(result_lists)[: top_k * 2]
                        response = SearchResponse(results=fused, query=search_query,
                                                  total_candidates=len(fused))
                    else:
                        response = _search(search_query, limit=top_k)
                else:
                    response = _search(search_query, limit=top_k)

            retrieval_run.attempt(search_query, response.results, stage="primary")

            # v6.0: Iterative retrieval — retry with rewritten query if score too low
            low_score_threshold = 1.0  # below this = probably wrong results
            max_retries = 2
            retry_queries_tried = {search_query}

            if (response.results and response.results[0].score < low_score_threshold
                    and not analysis.is_compare):
                llm = self._get_llm()
                for retry_i in range(max_retries):
                    if not llm:
                        break
                    # Generate alternative query
                    alt_query = self._generate_retry_query(
                        query, search_query, response.results[0].text[:100],
                        retry_i, llm
                    )
                    if not alt_query or alt_query in retry_queries_tried:
                        break
                    retry_queries_tried.add(alt_query)

                    alt_response = _search(alt_query, limit=top_k)
                    retrieval_run.attempt(
                        alt_query, alt_response.results,
                        stage=f"corrective_{retry_i + 1}",
                    )
                    if (alt_response.results
                            and alt_response.results[0].score > response.results[0].score):
                        response = alt_response
                        search_query = alt_query
                        logger.info(f"Retry {retry_i+1} improved: "
                                    f"'{alt_query}' score={alt_response.results[0].score:.3f}")
                        if response.results[0].score >= low_score_threshold:
                            break  # Good enough
                    else:
                        break  # No improvement, stop retrying

            selected_index = next(
                (index for index, item in enumerate(retrieval_run.attempts)
                 if item.get("query") == search_query),
                max(0, len(retrieval_run.attempts) - 1),
            )
            retrieval_run.select_attempt(selected_index)

            # Tenant boundary must run before confidence routing, KG prose and
            # prompt construction.  Search remains tolerant of heterogeneous
            # legacy indexes, but legacy rows without an owner are never
            # treated as globally shared evidence.
            if owner_id is not None:
                _before_owner = len(response.results)
                response.results = _filter_owner_results(response.results, owner_id)
                retrieval_run.filtered(
                    "owner_scope", _before_owner, len(response.results),
                    reason="exact_authenticated_owner",
                )

            if not response.results:
                run_record = retrieval_run.finish(
                    evidence_count=0, total_candidates=response.total_candidates,
                )
                empty_strategy.retrieval_contract = build_retrieval_contract(
                    query=query, rewritten_query=search_query, requested_top_k=top_k,
                    total_candidates=response.total_candidates,
                    candidate_top_k=getattr(response, "candidate_top_k", 0),
                    results=(), elapsed_ms=response.elapsed_ms, strategy="empty",
                    rerank_method=getattr(response, "rerank_method", ""),
                    run=run_record,
                )
                return messages, [], empty_strategy

            # v5.1: Score cutoff — adaptive to score scale
            # Reranker scores: -10~+10 range → keep within 3 points of top
            # RRF/cosine scores: 0~0.05 range → keep above 50% of top
            top_score = response.results[0].score
            if len(response.results) > 2:
                if top_score > 1.0:
                    # Reranker logit scores — keep within 3 points AND above 0
                    threshold = max(top_score - 3.0, 0.0)  # Never keep negative scores
                else:
                    # RRF/cosine scores
                    threshold = top_score * 0.50

                all_results = list(response.results)
                filtered = [r for r in all_results if r.score >= threshold]

                # Always keep at least 3 results for LLM context
                if len(filtered) < 3:
                    filtered = all_results[:3]

                if len(filtered) < len(all_results):
                    logger.info(f"Score cutoff: {len(all_results)} → {len(filtered)} "
                                f"(threshold={threshold:.4f})")
                retrieval_run.filtered(
                    "score_cutoff", len(all_results), len(filtered),
                    reason=f"threshold={threshold:.6f}",
                )
                response.results = filtered

            # Step 4: Route by confidence (+ entity-presence refusal)
            top_score = response.results[0].score
            _src_dicts = [{"text": getattr(r, "text", ""),
                           "filename": getattr(r, "filename", "")}
                          for r in response.results]
            strategy = self._route_by_confidence(top_score, query, _src_dicts)

            # Step 4.5: KG-enhanced retrieval (v8.0 Graph-RAG)
            kg_entities_ctx = ""
            kg_relations_ctx = ""
            kg_result = None  # v9.0: initialized for analytics tracking
            if retrieval_mode in ("kg", "mix"):
                try:
                    from hashmm.kg.kg_retriever import get_kg_retriever
                    kg_ret = get_kg_retriever()
                    if kg_ret.is_available:
                        # v9.0: Extract HL/LL keywords for targeted KG search
                        hl_kw, ll_kw = [], []
                        try:
                            from hashmm.kg.keyword_extractor import extract_keywords
                            llm = self._get_llm()
                            kw = extract_keywords(query, llm_fn=llm)
                            hl_kw = kw.get("hl", [])
                            ll_kw = kw.get("ll", [])
                        except Exception as _e:
                            log_suppressed(logger, _e)

                        kg_result = kg_ret.search(
                            query, mode=retrieval_mode,
                            hl_keywords=hl_kw or None,
                            ll_keywords=ll_kw or None,
                            graph_hops=getattr(self, "_last_route_hops", 1),
                        )
                        # Build entity context
                        if kg_result.entities:
                            ent_lines = []
                            for e in kg_result.entities[:6]:
                                ent_lines.append(
                                    f"- {e['name']} ({e['type']}): {e.get('description', '')[:150]}"
                                )
                            kg_entities_ctx = "## 知识图谱实体\n" + "\n".join(ent_lines)
                        # Build relation context
                        if kg_result.relations:
                            rel_lines = []
                            for r in kg_result.relations[:6]:
                                desc = r.get("description", "")
                                rel_lines.append(
                                    f"- {r['head']} → {r.get('relation', '')} → {r['tail']}"
                                    + (f": {desc[:100]}" if desc else "")
                                )
                            kg_relations_ctx = "## 知识图谱关系\n" + "\n".join(rel_lines)
                        logger.info(
                            f"KG retrieval: {len(kg_result.entities)} entities, "
                            f"{len(kg_result.relations)} relations"
                        )
                except Exception as e:
                    logger.debug(f"KG retrieval skipped: {e}")
                    retrieval_run.degraded("knowledge_graph", type(e).__name__)

            # Step 5: Build context injection (three-layer: entities + relations + chunks)
            context_parts = []
            sources = []

            # Layer 1/2: KG summaries are only safe in the single-tenant path.
            # With an explicit document allow-list, KG nodes/edges may aggregate
            # support from several documents, so only the ACL-checked original
            # evidence chunks below may enter model context.
            if kg_entities_ctx and allowed_docs is None and owner_id is None:
                context_parts.append(kg_entities_ctx)
            if kg_relations_ctx and allowed_docs is None and owner_id is None:
                context_parts.append(kg_relations_ctx)
            # Layer 3: Document chunks
            chunk_parts = []
            # v17 Phase 31: ② doc-level ACL filter (no-op when allowed_docs is None,
            # i.e. single-tenant default) + ⑦ optional retrieval diversification
            # (opt-in via env; validate on real eval before trusting).
            _results = _filter_owner_results(response.results, owner_id)
            _before_acl = len(_results)
            _doc_acl = None
            try:
                if allowed_docs is not None:
                    from hashmm.access_control import DocumentACL, filter_results
                    _doc_acl = DocumentACL(default=list(allowed_docs))
                    _results = filter_results(_results, _doc_acl, principal="_")
                import os as _os
                if _os.environ.get("HASHMM_RETRIEVAL_DIVERSIFY") == "1":
                    from hashmm.retrieval_quality import diversify
                    _results = diversify(
                        _results,
                        max_per_doc=int(_os.environ.get("HASHMM_DIVERSIFY_MAX_PER_DOC", "3")))
                if allowed_docs is not None:
                    retrieval_run.filtered(
                        "document_acl", _before_acl, len(_results),
                        reason="server_side_allow_list",
                    )
            except Exception:
                # An ACL failure must never fall back to the unfiltered result
                # set.  Single-tenant retrieval can keep its previous graceful
                # degradation behavior.
                _results = [] if allowed_docs is not None else response.results
                retrieval_run.degraded("document_acl", "filter_failed_closed")

            # Graph Engineering: KGRetriever previously returned source chunk IDs
            # but Chat only injected entity/relation prose and never fetched those
            # chunks.  Join query-local graph support back to the original corpus,
            # bound the expansion, then re-apply the same document ACL.
            _graph_expansion = None
            try:
                if kg_result and getattr(kg_result, "evidence", None):
                    _before_graph = len(_results)
                    from hashmm.retrieval.graph_engineering import expand_graph_evidence
                    _corpus = getattr(getattr(self, "_pipeline", None), "vector_index", None)
                    _corpus = getattr(_corpus, "_metadata", None) or []
                    _graph_expansion = expand_graph_evidence(
                        _results, _corpus, kg_result.evidence,
                        acl=_doc_acl, principal="_",
                    )
                    _results = _graph_expansion.results
                    if allowed_docs is not None:
                        from hashmm.access_control import DocumentACL, filter_results
                        _results = filter_results(
                            _results, DocumentACL(default=list(allowed_docs)), principal="_",
                        )
                    retrieval_run.expanded(
                        "knowledge_graph", _before_graph, len(_results),
                        considered=_graph_expansion.considered,
                        skipped_missing=_graph_expansion.skipped_missing,
                        skipped_forbidden=_graph_expansion.skipped_forbidden,
                    )
                    if _graph_expansion.added_chunk_ids:
                        logger.info(
                            "Graph Engineering: added %d evidence chunks (considered=%d, missing=%d, forbidden=%d)",
                            len(_graph_expansion.added_chunk_ids),
                            _graph_expansion.considered,
                            _graph_expansion.skipped_missing,
                            _graph_expansion.skipped_forbidden,
                        )
            except Exception as _e:
                logger.debug("Graph Engineering evidence expansion skipped: %s", _e)
                retrieval_run.degraded("graph_expansion", type(_e).__name__)

            # V174→深化：Navigate 扩展接入【LLM 上下文】（不止 sources 层）。命中块沿单文档结构图
            # （section 树 + chunk 连接）把相邻块/本节首块/同节兄弟补进 _results，让综述能看到整节上下文。
            # 仅 HASHMM_NAVIGATE_EXPAND=1 时启用；语料取 _pipeline 向量索引 _metadata；任何异常都安全降级。
            try:
                from hashmm.retrieval import section_graph as _sg
                if _sg.navigate_enabled() and _results:
                    _corpus = getattr(getattr(self, "_pipeline", None), "vector_index", None)
                    _corpus = getattr(_corpus, "_metadata", None)
                    if _corpus:
                        import types as _types
                        _graph = _sg.build_chunk_graph(_corpus)
                        _lut = {str(c.get("chunk_id") or ""): c for c in _corpus}
                        _seeds = [getattr(r, "chunk_id", "") for r in _results if getattr(r, "chunk_id", "")]
                        _seen = set(_seeds)
                        for _cid in _sg.navigate_expand(_seeds, _graph):
                            if _cid in _seen:
                                continue
                            _ch = _lut.get(_cid)
                            if not _ch or not str(_ch.get("text") or "").strip():
                                continue
                            _seen.add(_cid)
                            _results.append(_types.SimpleNamespace(
                                text=str(_ch.get("text") or ""), score=0.0,
                                doc_id=_ch.get("doc_id", ""),
                                filename=_ch.get("doc_title") or _ch.get("filename") or "",
                                page=_ch.get("page", -1),
                                section=_ch.get("section", "") or _ch.get("section_path", ""),
                                chunk_id=_cid, source_type="navigate", display_text=None,
                                owner_id=str(_ch.get("owner_id") or ""),
                                workspace_id=str(_ch.get("workspace_id") or "")))
            except Exception as _e:
                retrieval_run.degraded("section_navigation", type(_e).__name__)

            # Final context boundary.  Graph and section navigation both append
            # results after the initial retrieval filter, therefore permission
            # enforcement is repeated immediately before prompt construction.
            if allowed_docs is not None:
                _before_final_acl = len(_results)
                try:
                    from hashmm.access_control import DocumentACL, filter_results
                    _results = filter_results(
                        _results,
                        _doc_acl or DocumentACL(default=list(allowed_docs)),
                        principal="_",
                    )
                    retrieval_run.filtered(
                        "final_document_acl", _before_final_acl, len(_results),
                        reason="post_expansion_recheck",
                    )
                except Exception:
                    _results = []
                    retrieval_run.degraded("final_document_acl", "filter_failed_closed")
            if owner_id is not None:
                _before_final_owner = len(_results)
                _results = _filter_owner_results(_results, owner_id)
                retrieval_run.filtered(
                    "final_owner_scope", _before_final_owner, len(_results),
                    reason="post_expansion_recheck",
                )

            for i, r in enumerate(_results):
                idx = i + 1
                source_info = r.filename or r.doc_id
                page_str = f" 第{r.page}页" if r.page > 0 else ""
                section_str = f" [{r.section}]" if r.section else ""

                text = getattr(r, 'display_text', None) or r.text
                # v17 Phase 29 (OWASP LLM01 indirect injection): strip any embedded
                # instruction lines from retrieved content — treat it as data, not
                # instructions. No-op safe.
                try:
                    from hashmm.rag_security import sanitize_chunk_text
                    text, _flagged = sanitize_chunk_text(text)
                except Exception as _e:
                    log_suppressed(logger, _e)
                # V103.11 查询聚焦截断（默认关，HASHMM_QUERY_FOCUSED_CHUNKS=1 开）：块超长时
                # 取与问题最相关的 500 字窗口，而非无脑取开头——避免答案落在 500~800 字段被截掉。
                import os as _os
                if _os.environ.get("HASHMM_QUERY_FOCUSED_CHUNKS", "0") == "1":
                    _shown = _query_focused_window(text, query, 500)
                else:
                    _shown = text[:500]
                chunk_parts.append(
                    f"[{idx}] {source_info}{page_str}{section_str}\n{_shown}"
                )
                sources.append({
                    "id": idx,
                    "text": r.text[:200],
                    "chunk_id": getattr(r, "chunk_id", ""),
                    "doc_id": getattr(r, "doc_id", ""),
                    "filename": r.filename,
                    "page": r.page,
                    "section": r.section,
                    "score": round(r.score, 4),
                    "method": getattr(r, "source_type", "") or "retrieval",
                    "modality": getattr(r, "modality", "text"),
                    "graph_support": getattr(r, "graph_support", None),
                })

            if chunk_parts:
                context_parts.append("## 相关文档段落\n" + "\n\n".join(chunk_parts))

            _graph_stats = {}
            if _graph_expansion is not None:
                _graph_stats = {
                    "considered": getattr(_graph_expansion, "considered", 0),
                    "added": len(getattr(_graph_expansion, "added_chunk_ids", []) or []),
                    "missing": getattr(_graph_expansion, "skipped_missing", 0),
                    "forbidden": getattr(_graph_expansion, "skipped_forbidden", 0),
                }
            _filtered_total = sum(item.get("removed", 0) for item in retrieval_run.filters)
            _run_record = retrieval_run.finish(
                evidence_count=len(sources), total_candidates=response.total_candidates,
            )
            strategy.retrieval_contract = build_retrieval_contract(
                query=query,
                rewritten_query=search_query,
                requested_top_k=top_k,
                total_candidates=response.total_candidates,
                candidate_top_k=getattr(response, "candidate_top_k", 0),
                results=sources,
                elapsed_ms=response.elapsed_ms,
                strategy=strategy.mode,
                rerank_method=getattr(response, "rerank_method", ""),
                graph=_graph_stats,
                filtered_count=_filtered_total,
                run=_run_record,
            )
            if sources:
                # Stored with the first source so Chat, desktop and App receive
                # the same deterministic trace without a singleton side channel.
                sources[0]["retrieval_contract"] = strategy.retrieval_contract

            retrieval_text = "\n\n".join(context_parts)
            injection = (
                f"\n\n## 知识库检索结果\n"
                f"{strategy.instruction}\n\n"
                f"{retrieval_text}"
            )

            # Step 6: Inject into messages
            enhanced = list(messages)
            sys_idx = next((i for i, m in enumerate(enhanced)
                           if m["role"] == "system"), -1)
            if sys_idx >= 0:
                enhanced[sys_idx] = {
                    "role": "system",
                    "content": enhanced[sys_idx]["content"] + injection,
                }
            else:
                enhanced.insert(0, {"role": "system", "content": injection})

            logger.info(f"Chat retrieval: {len(sources)} results, "
                        f"strategy={strategy.mode} (score={top_score:.4f}), "
                        f"{response.elapsed_ms}ms")

            # v9.0: Record retrieval analytics
            try:
                from hashmm.evaluation.analytics import record_retrieval
                record_retrieval(
                    query=query, top_score=top_score,
                    strategy=strategy.mode, elapsed_ms=response.elapsed_ms,
                    num_sources=len(sources),
                    kg_entities=len(kg_result.entities) if kg_result else 0,
                    kg_relations=len(kg_result.relations) if kg_result else 0,
                    retrieval_mode=retrieval_mode,
                )
            except Exception as _e:
                log_suppressed(logger, _e)

            return enhanced, sources, strategy

        except Exception as e:
            logger.warning(f"Chat retrieval failed: {e}")
            retrieval_run.degraded("retrieval", f"{type(e).__name__}: {str(e)[:160]}")
            empty_strategy.retrieval_contract = build_retrieval_contract(
                query=query, requested_top_k=top_k, results=(), strategy="failed",
                run=retrieval_run.finish(status="failed"),
            )
            return messages, [], empty_strategy

    def _compare_search(
        self,
        analysis: QueryAnalysis,
        top_k: int,
        *,
        filters: dict | None = None,
    ):
        """Search separately for each entity in a comparison query."""
        from hashmm.retrieval_pipeline import SearchResponse, SearchResult

        all_results = []
        for company in analysis.companies[:3]:
            sub_query = company
            if analysis.metrics:
                sub_query += " " + " ".join(analysis.metrics[:2])
            if analysis.times:
                sub_query += " " + analysis.times[0]

            if filters:
                response = self._pipeline.search(
                    sub_query, top_k=top_k, filters=filters,
                )
            else:
                response = self._pipeline.search(sub_query, top_k=top_k)
            all_results.extend(response.results)

        # Deduplicate by chunk_id
        seen = set()
        unique = []
        for r in all_results:
            key = r.chunk_id or r.text[:80]
            if key not in seen:
                seen.add(key)
                unique.append(r)

        # Sort by score
        unique.sort(key=lambda x: -x.score)

        return SearchResponse(
            results=unique[:top_k],
            query=analysis.original_query,
            total_candidates=len(all_results),
        )

    def format_citation_footer(self, sources: list[dict]) -> str:
        """Generate citation footer text."""
        if not sources:
            return ""
        lines = ["\n\n---\n**参考来源：**"]
        for s in sources:
            page_str = f" p.{s['page']}" if s.get('page', -1) > 0 else ""
            lines.append(f"[{s['id']}] {s.get('filename', '')}{page_str}")
        return "\n".join(lines)

    def _generate_retry_query(self, original_query: str, tried_query: str,
                               top_result_preview: str, retry_idx: int,
                               llm_fn) -> str | None:
        """Generate an alternative search query when the first attempt scored low.

        Uses LLM to reformulate the query with different keywords.
        """
        prompt = (
            f"搜索知识库时，以下查询没有找到好的结果：\n"
            f"查询：{tried_query}\n"
            f"当前最佳结果（不太相关）：{top_result_preview[:80]}\n\n"
            f"请用不同的关键词重新表述这个查询，使其更可能匹配到正确的文档段落。\n"
            f"原始问题：{original_query}\n"
            f"只返回新的查询（一行，不解释）："
        )
        try:
            if hasattr(llm_fn, 'quick_call'):
                result = llm_fn.quick_call("你是搜索查询优化专家", prompt, max_tok=60)
            else:
                result = llm_fn(prompt)
            rewritten = result.strip().split('\n')[0][:150]
            if rewritten and len(rewritten) > 3 and rewritten != tried_query:
                return rewritten
        except Exception as e:
            logger.debug(f"Retry query generation failed: {e}")
        return None


# ── Module-level singleton accessor ──

_chat_retrieval_instance: ChatRetrieval | None = None


def get_chat_retrieval() -> ChatRetrieval:
    """Get the global ChatRetrieval singleton.

    Preferred over ChatRetrieval() for clarity, though both return
    the same instance thanks to __new__.
    """
    global _chat_retrieval_instance
    if _chat_retrieval_instance is None:
        _chat_retrieval_instance = ChatRetrieval()
    return _chat_retrieval_instance
