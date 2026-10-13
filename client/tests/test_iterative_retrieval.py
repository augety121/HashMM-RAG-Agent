"""迭代检索执行器（V311）回归测试。纯逻辑 + 假检索器，无索引/LLM 依赖。

覆盖：查询侧面分解规则、单文档口径的侧面覆盖（跨文档 bigram 拼凑不算数）、
多轮执行的三重停止条件（early_exit / no_new / budget）、后续轮异常不吞首轮、
retrieve_context 端到端（complex 多轮 / single 单轮 / 显式 top_k 零差异 / 开关）、
_exec_kb_search 的自适应分解（复用既有多查询 RRF 路径）。
"""
import sys
import types

import pytest

from hashmm.agent.adaptive_rag import EarlyExit
from hashmm.agent.iterative_retrieval import (aspect_coverage, coverage,
                                              decompose, iterative_enabled,
                                              run_iterative)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HASHMM_ADAPTIVE_RAG", "HASHMM_RAG_ITERATIVE"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- 分解规则
def test_decompose_compare_with_aspect():
    assert decompose("对比 Milvus 和 Qdrant 的性能差异") == ["Milvus 性能", "Qdrant 性能"]


def test_decompose_compare_bare():
    assert decompose("对比A和B") == ["A", "B"]


def test_decompose_sequential():
    assert decompose("先分析原因再给出解决方案") == ["分析原因", "给出解决方案"]


def test_decompose_why_how():
    assert decompose("为什么系统会变慢，如何优化") == ["系统会变慢 原因", "优化"]


def test_decompose_single_hop_untouched():
    assert decompose("什么是向量数据库") == []


# ---------------------------------------------------------------- 覆盖口径
def _mk(text, cid, score=0.8):
    return {"text": text, "score": score, "chunk_id": cid}


def test_aspect_coverage_single_doc_only():
    """跨文档 bigram 拼凑不算侧面覆盖——必须同一篇文档内成立。"""
    rs = [_mk("Milvus 是向量库", "a"), _mk("Qdrant 性能：Rust", "b")]
    assert aspect_coverage(rs, "Milvus 性能") < 0.8
    rs.append(_mk("Milvus 性能：QPS 高", "c"))
    assert aspect_coverage(rs, "Milvus 性能") >= 0.99


def test_query_coverage_union():
    rs = [_mk("对比 Milvus", "a"), _mk("Qdrant 的性能差异", "b")]
    assert coverage(rs, "对比 Milvus 和 Qdrant 的性能差异") > 0.5


# ---------------------------------------------------------------- 多轮执行
class _Resp:
    def __init__(self, rs):
        self.results = rs


_CORPUS = {
    "对比 Milvus 和 Qdrant 的性能差异": [_mk("Milvus 是向量库", "m0", 0.9)],
    "Milvus 性能": [_mk("Milvus 性能：QPS 高、延迟低", "m1", 0.85)],
    "Qdrant 性能": [_mk("Qdrant 性能：Rust 实现、过滤强", "q1", 0.8)],
    "什么是向量库": [_mk("向量库是存储 embedding 的数据库", "v1", 0.9)],
}


def _fake_factory(calls):
    def fake(q, k):
        calls.append((q, k))
        return _Resp(list(_CORPUS.get(q, [])))
    return fake


def test_run_iterative_covers_both_aspects_then_stops():
    calls = []
    res = run_iterative(_fake_factory(calls), "对比 Milvus 和 Qdrant 的性能差异",
                        max_iterations=5, top_k=12)
    assert res.rounds == 3
    assert set(res.queries) == {"对比 Milvus 和 Qdrant 的性能差异", "Milvus 性能", "Qdrant 性能"}
    assert {r["chunk_id"] for r in res.results} == {"m0", "m1", "q1"}
    assert res.results[0]["score"] >= res.results[-1]["score"]   # 分数降序
    assert res.stop_reason == "early_exit"                       # 侧面全覆盖即收手
    assert res.coverages == sorted(res.coverages)                # 累计覆盖单调不降


def test_run_iterative_single_budget_one_call():
    calls = []
    res = run_iterative(_fake_factory(calls), "什么是向量库", max_iterations=1, top_k=5)
    assert len(calls) == 1 and res.stop_reason == "single"


def test_run_iterative_early_exit_on_high_coverage():
    def full(q, k):
        return _Resp([_mk("对比 Milvus 和 Qdrant 的性能差异：答案全在此", "full", 0.95)])
    res = run_iterative(full, "对比 Milvus 和 Qdrant 的性能差异", 5, 12,
                        early_exit=EarlyExit(threshold=0.8, patience=1))
    assert res.rounds == 1 and res.stop_reason == "early_exit"


def test_run_iterative_dedup_and_no_new():
    res = run_iterative(lambda q, k: _Resp([_mk("同段", "dup", 0.9)]),
                        "先分析原因再给出解决方案", 3, 8)
    assert len(res.results) == 1 and res.stop_reason == "no_new"


def test_run_iterative_empty_first_round():
    res = run_iterative(lambda q, k: _Resp([]), "对比A和B的差异", 5, 12)
    assert res.stop_reason == "empty" and res.results == []


def test_run_iterative_later_round_error_keeps_first():
    state = {"n": 0}

    def flaky(q, k):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("boom")
        return _Resp([_mk("Milvus 是向量库", "m0", 0.9)])
    res = run_iterative(flaky, "对比 Milvus 和 Qdrant 的性能差异", 5, 12)
    assert res.stop_reason == "error" and len(res.results) == 1


def test_iterative_switch():
    assert iterative_enabled()


# ---------------------------------------------------------------- retrieve_context 端到端
class _Item:
    def __init__(self, text, cid, score):
        self.text, self.chunk_id, self.doc_id, self.score = text, cid, cid, score
        self.source, self.page = "kb", 1


class _PipeResp:
    def __init__(self, rs):
        self.results = rs
        self.kg_context = ""
        self.community_context = ""
        self.elapsed_ms = 3


_OBJ_CORPUS = {k: [_Item(r["text"], r["chunk_id"], r["score"]) for r in v]
               for k, v in _CORPUS.items()}


class _FakePipeline:
    def __init__(self, calls):
        self.calls = calls

    def retrieve(self, q, mode="mix", top_k=5):
        self.calls.append((q, top_k))
        return _PipeResp(list(_OBJ_CORPUS.get(q, [])))


def test_retrieve_context_complex_multiround_meta():
    from hashmm.auto_retrieval import AutoRetriever
    calls = []
    ctx = AutoRetriever(retriever=_FakePipeline(calls)).retrieve_context(
        "对比 Milvus 和 Qdrant 的性能差异")
    assert len(calls) == 3 and all(k == 12 for _, k in calls)
    assert len(ctx.sources) == 3
    m = ctx.route_meta
    assert m["strategy"] == "complex" and m["rounds"] == 3 and m["coverages"]
    assert "[1]" in ctx.injection    # 引用锚照常


def test_retrieve_context_single_one_round():
    from hashmm.auto_retrieval import AutoRetriever
    calls = []
    ctx = AutoRetriever(retriever=_FakePipeline(calls)).retrieve_context("什么是向量库")
    assert calls == [("什么是向量库", 5)]
    assert ctx.route_meta["rounds"] == 1 and ctx.route_meta["stop_reason"] == "single"


def test_retrieve_context_explicit_topk_no_meta():
    from hashmm.auto_retrieval import AutoRetriever
    calls = []
    ctx = AutoRetriever(retriever=_FakePipeline(calls)).retrieve_context(
        "对比 Milvus 和 Qdrant 的性能差异", top_k=2)
    assert calls == [("对比 Milvus 和 Qdrant 的性能差异", 2)]
    assert ctx.route_meta is None    # 旧调用方零差异


def test_retrieve_context_iterative_off(monkeypatch):
    from hashmm.auto_retrieval import AutoRetriever
    monkeypatch.setenv("HASHMM_RAG_ITERATIVE", "0")
    calls = []
    ctx = AutoRetriever(retriever=_FakePipeline(calls)).retrieve_context(
        "对比 Milvus 和 Qdrant 的性能差异")
    assert len(calls) == 1 and calls[0][1] == 12   # 单轮但仍宽检索
    assert ctx.route_meta["stop_reason"] == "iterative_off"


# ---------------------------------------------------------------- kb_search 自适应分解
def _stub_kb_modules(enhance_calls):
    """打桩 chat_retrieval / context_pack；返回还原函数。"""
    saved = {n: sys.modules.get(n) for n in
             ("hashmm.chat_retrieval", "hashmm.agent.context_pack")}

    def fake_enhance(q, hist, retrieval_mode="mix"):
        enhance_calls.append(q)
        return None, [{"filename": f"d-{q[:6]}", "page": 1,
                       "text": f"关于[{q}]的片段", "score": 0.8}], ""
    m1 = types.ModuleType("hashmm.chat_retrieval")
    m1.get_chat_retrieval = lambda: types.SimpleNamespace(enhance=fake_enhance)
    m2 = types.ModuleType("hashmm.agent.context_pack")
    m2.pack_sources = lambda srcs, budget=4500: (
        "\n".join(s.get("text", "") for s in srcs), {})
    sys.modules["hashmm.chat_retrieval"] = m1
    sys.modules["hashmm.agent.context_pack"] = m2

    def restore():
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod
    return restore


def test_kb_search_adaptive_decompose_complex():
    from hashmm.api.tool_registry import _exec_kb_search
    calls = []
    restore = _stub_kb_modules(calls)
    try:
        out = _exec_kb_search({"query": "对比 Milvus 和 Qdrant 的性能差异"}, {})
    finally:
        restore()
    assert out.startswith("[自适应分解·complex] [多查询融合] 3 个查询变体")
    assert calls == ["对比 Milvus 和 Qdrant 的性能差异", "Milvus 性能", "Qdrant 性能"]


def test_kb_search_model_queries_path_untouched():
    from hashmm.api.tool_registry import _exec_kb_search
    calls = []
    restore = _stub_kb_modules(calls)
    try:
        out = _exec_kb_search({"query": "A", "queries": ["B变体查询"]}, {})
    finally:
        restore()
    assert out.startswith("[多查询融合] 2 个查询变体") and "[自适应分解" not in out


def test_kb_search_simple_query_single_path():
    from hashmm.api.tool_registry import _exec_kb_search
    calls = []
    restore = _stub_kb_modules(calls)
    try:
        out = _exec_kb_search({"query": "什么是向量数据库"}, {})
    finally:
        restore()
    assert "[多查询融合]" not in out and calls == ["什么是向量数据库"]


def test_kb_search_decompose_switch_off(monkeypatch):
    from hashmm.api.tool_registry import _exec_kb_search
    monkeypatch.setenv("HASHMM_RAG_ITERATIVE", "0")
    calls = []
    restore = _stub_kb_modules(calls)
    try:
        out = _exec_kb_search({"query": "对比 Milvus 和 Qdrant 的性能差异"}, {})
    finally:
        restore()
    assert "[自适应分解" not in out and len(calls) == 1


def test_kb_search_parallel_serial_identical(monkeypatch):
    """并行变体检索与串行安全阀的融合输出必须逐字节一致（RRF 依赖保序）。"""
    from hashmm.api.tool_registry import _exec_kb_search
    args = {"query": "对比 Milvus 和 Qdrant 的性能差异"}
    calls1 = []
    restore = _stub_kb_modules(calls1)
    try:
        out_par = _exec_kb_search(dict(args), {})
        monkeypatch.setenv("HASHMM_KB_PARALLEL", "0")
        out_seq = _exec_kb_search(dict(args), {})
    finally:
        restore()
    assert out_par == out_seq
    assert out_par.startswith("[自适应分解·complex]")
