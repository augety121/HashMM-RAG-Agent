"""自适应 RAG 路由（V311）回归测试。纯逻辑，无 GPU/索引/LLM 依赖。

覆盖：复杂度分类（四档×典型样例）、生成任务边界、路由开关（关闭=旧行为零差异）、
预算映射、EarlyExit 提前退出、AutoRetriever 三处接入（should_retrieve /
route_query / retrieve_context 自适应 top_k 且显式传值零影响）。
"""
import pytest

from hashmm.agent.adaptive_rag import EarlyExit, RouteStrategy, classify, enabled, route


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HASHMM_ADAPTIVE_RAG", raising=False)


# ---------------------------------------------------------------- 分类四档
def test_greeting_no_retrieval():
    assert classify("你好").strategy == RouteStrategy.NO_RETRIEVAL


def test_thanks_no_retrieval():
    assert classify("谢谢").strategy == RouteStrategy.NO_RETRIEVAL


def test_codegen_no_retrieval():
    assert classify("帮我写一个快速排序").strategy == RouteStrategy.NO_RETRIEVAL


def test_translate_no_retrieval():
    assert classify("把这段翻译成英文").strategy == RouteStrategy.NO_RETRIEVAL


def test_fact_question_single():
    assert classify("合同里的违约金是多少？").strategy == RouteStrategy.SINGLE


def test_definition_single():
    assert classify("什么是向量数据库").strategy == RouteStrategy.SINGLE


def test_compare_complex():
    assert classify("对比 Milvus 和 Qdrant 的性能差异").strategy == RouteStrategy.COMPLEX


def test_enumerate_complex():
    assert classify("列举所有支持的文件格式").strategy == RouteStrategy.COMPLEX


def test_stepwise_multi_hop():
    d = classify("先分析原因再给出解决方案，为什么系统会变慢")
    assert d.strategy == RouteStrategy.MULTI_HOP


# ---------------------------------------------------------------- 边界
def test_generation_with_doc_ref_still_retrieves():
    """带知识库指向的生成任务不能被当成纯生成放过。"""
    d = classify("根据知识库文档写一份部署方案")
    assert d.should_retrieve


def test_should_retrieve_property():
    assert not classify("你好").should_retrieve
    assert classify("合同违约金是多少").should_retrieve


# ---------------------------------------------------------------- 预算映射
def test_budgets_per_strategy():
    assert (classify("什么是A").top_k, classify("什么是A").max_iterations) == (5, 1)
    d = classify("先查原因再给方案")
    assert (d.top_k, d.max_iterations) == (8, 3)
    c = classify("对比A和B的差异")
    assert (c.top_k, c.max_iterations) == (12, 5)
    n = classify("你好")
    assert (n.top_k, n.max_iterations) == (0, 0)


# ---------------------------------------------------------------- 开关
def test_switch_off_fixed_single(monkeypatch):
    monkeypatch.setenv("HASHMM_ADAPTIVE_RAG", "0")
    d = route("对比A和B")
    assert d.strategy == RouteStrategy.SINGLE
    assert d.top_k == 5 and d.max_iterations == 1
    assert not enabled()


def test_switch_default_on():
    assert enabled()
    assert route("对比A和B的差异").strategy == RouteStrategy.COMPLEX


# ---------------------------------------------------------------- EarlyExit
def test_early_exit_needs_consecutive_high():
    ee = EarlyExit(threshold=0.8, patience=2)
    assert not ee.should_stop([])
    assert not ee.should_stop([0.9])
    assert not ee.should_stop([0.5, 0.9])
    assert ee.should_stop([0.5, 0.85, 0.9])
    assert ee.should_stop([0.8, 0.8])


def test_early_exit_custom_threshold():
    ee = EarlyExit(threshold=0.6, patience=1)
    assert ee.should_stop([0.6])
    assert not ee.should_stop([0.59])


# ---------------------------------------------------------------- AutoRetriever 接入
def test_autoretriever_should_retrieve_matrix():
    from hashmm.auto_retrieval import AutoRetriever
    ar = AutoRetriever()
    assert ar.should_retrieve("你好") is False
    assert ar.should_retrieve("帮我写个排序") is False
    assert ar.should_retrieve("合同违约金是多少？") is True
    assert ar.should_retrieve("对比A和B的差异") is True


def test_autoretriever_route_query_budgets():
    from hashmm.auto_retrieval import AutoRetriever
    ar = AutoRetriever()
    assert ar.route_query("对比 Milvus 和 Qdrant 的性能差异").top_k == 12
    assert ar.route_query("什么是向量库").top_k == 5


def test_autoretriever_legacy_kept_and_used_when_off(monkeypatch):
    from hashmm.auto_retrieval import AutoRetriever
    ar = AutoRetriever()
    assert ar._should_retrieve_legacy("合同违约金是多少？") is True
    monkeypatch.setenv("HASHMM_ADAPTIVE_RAG", "0")
    assert ar.should_retrieve("你好") is False                 # legacy skip
    assert ar.should_retrieve("对比A和B的差异有哪些？") is True   # legacy 问号规则


class _FakeResp:
    def __init__(self):
        self.results = []
        self.kg_context = ""
        self.community_context = ""
        self.elapsed_ms = 0


class _FakeRetriever:
    def __init__(self, box):
        self.box = box

    def retrieve(self, q, mode="mix", top_k=5):
        self.box["top_k"] = top_k
        return _FakeResp()


def test_retrieve_context_adaptive_topk():
    from hashmm.auto_retrieval import AutoRetriever
    box: dict = {}
    ar = AutoRetriever(retriever=_FakeRetriever(box))
    ar.retrieve_context("对比 Milvus 和 Qdrant 的性能差异")   # None → 路由 12
    assert box["top_k"] == 12
    ar.retrieve_context("什么是向量库")
    assert box["top_k"] == 5


def test_retrieve_context_explicit_topk_untouched():
    from hashmm.auto_retrieval import AutoRetriever
    box: dict = {}
    ar = AutoRetriever(retriever=_FakeRetriever(box))
    ar.retrieve_context("对比A和B", top_k=2)
    assert box["top_k"] == 2
