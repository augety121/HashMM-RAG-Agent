import pytest

from hashmm.retrieval.contract import build_retrieval_contract
from hashmm.retrieval.run import RETRIEVAL_RUN_SCHEMA, RetrievalRun
from hashmm.evaluation.run_manifest import build_run_manifest


class _Result:
    def __init__(self, score: float):
        self.score = score


def test_retrieval_run_records_real_attempts_filters_and_degradation():
    run = RetrievalRun(
        query="比较两份报告", requested_mode="auto", requested_top_k=6,
        acl_scoped=True,
    )
    run.route("mix", reason="comparison_query", hops=2)
    run.attempt("报告 对比", [_Result(0.2), _Result(0.1)], stage="primary")
    run.attempt("报告 指标 对比", [_Result(0.8)], stage="corrective_1")
    run.select_attempt(1)
    run.filtered("document_acl", 4, 2, reason="server_side_allow_list")
    run.degraded("knowledge_graph", "index_unavailable")

    record = run.finish(evidence_count=2, total_candidates=9)

    assert record["schema"] == RETRIEVAL_RUN_SCHEMA
    assert record["status"] == "degraded"
    assert record["selected_query"] == "报告 指标 对比"
    assert record["filters"][0]["removed"] == 2
    assert record["acl_scoped"] is True
    assert record["model_self_grade"] is False
    assert "server_side_allow_list" in str(record)
    assert "allowed-doc-id" not in str(record)


def test_retrieval_contract_carries_same_run_projection():
    run = RetrievalRun(query="问题")
    run.route("naive")
    record = run.finish(status="empty")
    contract = build_retrieval_contract(
        query="问题", results=(), strategy="empty", run=record,
    )
    assert contract["run"]["run_id"] == record["run_id"]
    assert contract["run"]["status"] == "empty"


def test_empty_retrieval_run_remains_visible_in_task_graph():
    run = RetrievalRun(query="没有命中的问题", requested_mode="mix", acl_scoped=True)
    run.route("mix", reason="knowledge_query")
    run.attempt("没有命中的问题", [], stage="primary", selected=True)
    run.degraded("knowledge_graph", "index_unavailable")
    contract = build_retrieval_contract(
        query="没有命中的问题", results=(), strategy="empty",
        run=run.finish(status="degraded"),
    )

    manifest = build_run_manifest(
        run_id="retrieval-empty-graph", task_type="qa", execution_mode="rag_chat",
        retrieval_contract=contract, user_goal="没有命中的问题",
        answer_text="资料中没有可核验答案。", stop_reason="completed",
    )

    graph = manifest["evidence_graph"]
    kinds = {node["kind"] for node in graph["nodes"]}
    relations = {edge["relation"] for edge in graph["edges"]}
    assert {"retrieval", "query", "degradation"}.issubset(kinds)
    assert "retrieves_for" in relations and "degraded_by" in relations
    assert graph["summary"]["warnings"] == 1
    assert graph["integrity"]["raw_tool_arguments_included"] is False


def test_retrieval_cache_is_partitioned_by_owner_acl(tmp_path, monkeypatch):
    from hashmm.api import streaming

    class Cache:
        def __init__(self):
            self.values = {}

        def get_retrieval(self, key):
            return self.values.get(key)

        def set_retrieval(self, key, value):
            self.values[key] = value

    class ACL:
        def allowed_patterns(self, user_id):
            return [f"{user_id}-only"]

        def can_access(self, user_id, doc):
            return str(doc) == f"{user_id}.md"

    class Strategy:
        mode = "grounded"

        def __init__(self, owner):
            run = RetrievalRun(query="同一问题", acl_scoped=True).finish(evidence_count=1)
            self.retrieval_contract = build_retrieval_contract(
                query="同一问题", results=[{"filename": f"{owner}.md", "text": owner}],
                strategy="grounded", run=run,
            )

    class Retrieval:
        def __init__(self):
            self.calls = []

        def resolve_query(self, query, _history):
            return query

        def should_search(self, _query):
            return True

        def enhance(self, query, _history, *, retrieval_mode, allowed_docs=None):
            owner = str((allowed_docs or ["none"])[0]).split("-only", 1)[0]
            self.calls.append(owner)
            source = {
                "id": 1, "filename": f"{owner}.md", "page": 1,
                "text": f"仅属于 {owner}", "score": 1.0,
            }
            return [], [source], Strategy(owner)

    cache = Cache()
    retrieval = Retrieval()
    monkeypatch.setattr("hashmm.agent.cache.get_cache", lambda: cache)
    monkeypatch.setattr("hashmm.access_control.load_default_acl", lambda: ACL())
    monkeypatch.setattr("hashmm.chat_retrieval.get_chat_retrieval", lambda: retrieval)

    sink_a, sink_b = {}, {}
    first_a = streaming._do_retrieval("同一问题", [], "mix", [], "user-a", sink_a)
    first_b = streaming._do_retrieval("同一问题", [], "mix", [], "user-b", sink_b)
    cached_a = streaming._do_retrieval("同一问题", [], "mix", [], "user-a", {})

    assert retrieval.calls == ["user-a", "user-b"]
    assert first_a[0][0]["filename"] == cached_a[0][0]["filename"] == "user-a.md"
    assert first_b[0][0]["filename"] == "user-b.md"
    assert sink_a["run"]["acl_scoped"] is True and sink_b["run"]["acl_scoped"] is True


def test_malformed_acl_fails_retrieval_closed(tmp_path, monkeypatch):
    from hashmm.api import streaming
    from hashmm.access_control import ACLConfigurationError, load_default_acl

    class Retrieval:
        def __init__(self):
            self.enhance_calls = 0

        def resolve_query(self, query, _history):
            return query

        def should_search(self, _query):
            return True

        def enhance(self, *_args, **_kwargs):
            self.enhance_calls += 1
            raise AssertionError("retrieval must not run after ACL parse failure")

    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"rules": "not-an-object"}', encoding="utf-8")
    with pytest.raises(ACLConfigurationError):
        load_default_acl(str(acl_file))
    retrieval = Retrieval()
    monkeypatch.setattr("hashmm.chat_retrieval.get_chat_retrieval", lambda: retrieval)
    def _bad_acl(_path="data/acl.json"):
        raise ACLConfigurationError("invalid document ACL: ValueError")
    monkeypatch.setattr("hashmm.access_control.load_default_acl", _bad_acl)

    trace = {}
    sources, injection, strategy = streaming._do_retrieval(
        "私有资料", [], "mix", [], "user-a", trace,
    )

    assert sources == [] and injection == "" and strategy == "insufficient"
    assert retrieval.enhance_calls == 0
    assert trace["run"]["status"] == "failed"
    assert trace["run"]["degradations"][0]["stage"] == "document_acl"


def test_searchr1_bridge_filters_acl_before_policy_consumes_results(monkeypatch):
    from hashmm.access_control import DocumentACL
    from hashmm.retrieval.searchr1_serving import build_search_fn
    from hashmm import retriever_bridge

    monkeypatch.setattr(
        retriever_bridge,
        "kb_search_bridge",
        lambda _payload: {"results": [
            {"filename": "alice.pdf", "content": "allowed", "score": 0.9},
            {"filename": "bob.pdf", "content": "forbidden", "score": 1.0},
        ]},
    )
    search = build_search_fn(
        5, acl=DocumentACL(rules={"alice": ["alice.pdf"]}), principal="alice",
    )

    assert [item["filename"] for item in search("query")] == ["alice.pdf"]
