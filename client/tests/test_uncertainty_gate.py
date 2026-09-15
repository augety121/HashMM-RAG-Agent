"""tests/test_uncertainty_gate.py — V103.53 P0：免训练版 Search-R1 + UncertaintyRAG。

验证 AgenticRetriever 的「不确定性闸」：用量化置信度参与多跳停止判断。
全部用假 search_fn / llm_fn / confidence_fn，纯逻辑、不依赖模型与索引。覆盖：
  - 开局证据已强 → 提前收手（confident），只 1 跳；
  - LLM 想 finish 但置信度低 → 自己补子查询再搜（low_confidence_continue）；
  - 补检索后置信度转强 → 提前收手；
  - 不注入 confidence_fn → 行为同旧版（纯 LLM 判停）；
  - confidence_fn 抛错 → 闸自动让路，不影响检索，不抛错；
  - 阈值写反 → 回退默认，逻辑不紊乱；
  - max_hops / max_sources 硬上限始终不被闸突破。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.retrieval.agentic import AgenticRetriever, _gate_thresholds, _fallback_subquery, uncertainty_gate_enabled


def _mk_sources(n, prefix="d"):
    return [{"id": f"{prefix}{i}", "filename": f"{prefix}{i}.md", "text": f"chunk {i}", "score": 2.0} for i in range(n)]


def test_early_stop_when_confident_at_hop0():
    """开局证据就足够强 → 1 跳收手，stopped=confident，不调用 LLM。"""
    llm_calls = {"n": 0}
    def llm(_p): llm_calls["n"] += 1; return '{"action":"search","query":"x"}'
    ar = AgenticRetriever(lambda q: _mk_sources(3), llm, max_hops=5,
                          confidence_fn=lambda q, s: 0.9, gate_high=0.75, gate_low=0.45)
    r = ar.retrieve("Q")
    assert r["stopped_reason"] == "confident"
    assert r["n_hops"] == 1
    assert llm_calls["n"] == 0          # 高置信开局，LLM 一次没调（省成本）
    assert r["confidence"] == 0.9


def test_force_continue_when_llm_finishes_but_low_confidence():
    """LLM 第一轮就想 finish，但置信度低 → 闸强制再补一轮。"""
    seq = iter(['{"action":"finish"}', '{"action":"finish"}'])
    def llm(_p):
        try: return next(seq)
        except StopIteration: return '{"action":"finish"}'
    searches = {"n": 0}
    def search(q):
        searches["n"] += 1
        return _mk_sources(2, prefix=f"s{searches['n']}")
    # 置信度恒低 → 闸每次都想续，直到 max_hops 收口
    ar = AgenticRetriever(search, llm, max_hops=3,
                          confidence_fn=lambda q, s: 0.2, gate_high=0.75, gate_low=0.45)
    r = ar.retrieve("Q")
    assert searches["n"] >= 2                       # 至少补了一轮（不是 LLM 一说 finish 就停）
    assert r["stopped_reason"] in ("low_confidence_continue", "max_hops", "no_new", "repeat_or_empty")
    assert len(r["subqueries"]) >= 2


def test_continue_then_become_confident():
    """先低置信续检，补检索后转强 → 提前收手 confident。"""
    confs = iter([0.3, 0.9])     # hop0 低，hop1 后高
    def conf(q, s):
        try: return next(confs)
        except StopIteration: return 0.9
    ar = AgenticRetriever(lambda q: _mk_sources(2, prefix=os.urandom(2).hex()),
                          lambda _p: '{"action":"search","query":"more"}', max_hops=5,
                          confidence_fn=conf, gate_high=0.75, gate_low=0.45)
    r = ar.retrieve("Q")
    assert r["stopped_reason"] == "confident"
    assert r["n_hops"] == 2


def test_no_confidence_fn_behaves_like_legacy():
    """不注入 confidence_fn → 闸不参与，纯 LLM 判停（finish 即停）。"""
    ar = AgenticRetriever(lambda q: _mk_sources(2), lambda _p: '{"action":"finish"}', max_hops=5)
    r = ar.retrieve("Q")
    assert r["stopped_reason"] == "finish"
    assert r["n_hops"] == 1
    assert r["confidence"] is None


def test_confidence_fn_raises_is_safe():
    """confidence_fn 抛错 → 视作闸不参与，检索照常、不抛错。"""
    def bad_conf(q, s): raise RuntimeError("boom")
    ar = AgenticRetriever(lambda q: _mk_sources(2), lambda _p: '{"action":"finish"}', max_hops=3,
                          confidence_fn=bad_conf)
    r = ar.retrieve("Q")                 # 不应抛错
    assert r["sources"] and r["n_hops"] >= 1


def test_search_failure_is_visible_without_fabricating_evidence():
    """A retrieval outage must be reported to the caller, not look like no evidence."""
    def broken_search(_q):
        raise OSError("index unavailable")

    ar = AgenticRetriever(broken_search, lambda _p: '{"action":"finish"}', max_hops=2)
    result = ar.retrieve("Q")
    assert result["sources"] == []
    assert result["errors"]
    assert result["errors"][0]["type"] == "OSError"
    assert result["trace"][0]["action"] == "seed_error"


def test_thresholds_swapped_fall_back_to_default():
    """high<low 写反 → _gate_thresholds 回退默认 (0.75, 0.45)。"""
    os.environ["HASHMM_UNCERTAINTY_GATE_HIGH"] = "0.2"
    os.environ["HASHMM_UNCERTAINTY_GATE_LOW"] = "0.9"
    try:
        h, l = _gate_thresholds()
        assert (h, l) == (0.75, 0.45)
    finally:
        del os.environ["HASHMM_UNCERTAINTY_GATE_HIGH"]
        del os.environ["HASHMM_UNCERTAINTY_GATE_LOW"]


def test_max_sources_hard_cap_respected_with_gate():
    """即使置信度一直低想续检，max_sources 硬上限不被突破。"""
    ar = AgenticRetriever(lambda q: _mk_sources(50, prefix=os.urandom(2).hex()),
                          lambda _p: '{"action":"search","query":"more"}', max_hops=10,
                          max_sources=20, confidence_fn=lambda q, s: 0.1)
    r = ar.retrieve("Q")
    assert len(r["sources"]) <= 20


def test_fallback_subquery_skips_tried():
    """补充子查询确定性、且跳过已试过的；用尽返回空。"""
    q = "网易2024收入"
    s1 = _fallback_subquery(q, [q])
    assert s1 and s1 != q
    s2 = _fallback_subquery(q, [q, s1])
    assert s2 and s2 not in (q, s1)
    # 用尽所有后缀 → 空
    tried = [q] + [f"{q} {suf}" for suf in ["背景 细节", "相关 数据", "原因 影响", "时间 经过", "对比 其他"]]
    assert _fallback_subquery(q, tried) == ""


def test_sag_entity_expansion():
    """V103.54（吸收 SAG 精华）：低置信续检时，优先用证据里、问题里没有的实体扩展子查询。"""
    q = "收入多少"
    ev = "- 网易公司财报: 丁磊 广州 游戏业务"
    r0 = _fallback_subquery(q, [q], ev)
    # 应是实体导向（网易/丁磊/广州 之一），且不含文件名噪声 doc
    assert any(e in r0 for e in ("网易", "丁磊", "广州")), r0
    assert "doc" not in r0
    # 跳过已试过的实体扩展
    r1 = _fallback_subquery(q, [q, r0], ev)
    assert r1 and r1 != r0
    # 无证据 → 退回机械后缀（与旧行为一致）
    assert _fallback_subquery("X", ["X"], "") == "X 背景 细节"


def test_trace_records_each_hop():
    """V103.54 P3：retrieve 返回 trace，逐跳记录子查询/置信度/决策（过程监督数据）。"""
    confs = iter([0.3, 0.4, 0.9])
    def conf(q, s):
        try: return next(confs)
        except StopIteration: return 0.9
    ar = AgenticRetriever(lambda q: _mk_sources(2, prefix=os.urandom(2).hex()),
                          lambda _p: '{"action":"search","query":"' + os.urandom(2).hex() + '"}',
                          max_hops=4, confidence_fn=conf, gate_high=0.75, gate_low=0.45)
    r = ar.retrieve("Q")
    assert "trace" in r and isinstance(r["trace"], list) and len(r["trace"]) >= 1
    assert r["trace"][0]["action"] == "seed" and r["trace"][0]["hop"] == 0
    for step in r["trace"]:
        assert set(step.keys()) == {"hop", "subquery", "n_sources_after", "confidence", "action"}


def test_gate_enabled_default_on():
    """不确定性闸默认开（P0 核心增益默认生效）。"""
    assert uncertainty_gate_enabled() is True
    os.environ["HASHMM_UNCERTAINTY_GATE"] = "0"
    try:
        assert uncertainty_gate_enabled() is False
    finally:
        del os.environ["HASHMM_UNCERTAINTY_GATE"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
