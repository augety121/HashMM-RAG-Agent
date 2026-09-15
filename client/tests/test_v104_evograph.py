"""V104 P3 — 自进化检索循环（吸收 EvoGraph-R1 的 MDP：GraphRetrieve/GraphEdit/Answer）。

验收：① 初始 STRONG → 直接 Answer 不扰动；② WEAK → 多轮累积证据池 → 转 STRONG 终止；
③ GraphEdit/INSERT 经注入 propose_fn 提案（不直接写主图，走审批闸门）；④ 证据池累积去重；
⑤ search_fn 抛异常 → 永不抛错。纯函数 + 全注入，无 GPU/活索引即可跑。
"""
from hashmm.agent.evograph import (
    agentic_evolve, STRONG, ANSWER, GRAPH_RETRIEVE, GRAPH_EDIT,
)


class _R:
    def __init__(self, text, cid):
        self.text = text
        self.chunk_id = cid


_Q = "小米2024营收"


def test_initial_strong_answers_immediately():
    res = [_R("小米2024营收是3659亿元", "c1"), _R("小米2024营收同比增长", "c2")]
    out = agentic_evolve(_Q, res, search_fn=lambda x: [])
    assert out["actions"] == [ANSWER]
    assert out["rounds"] == 0


def test_weak_accumulates_pool_until_strong():
    def sfn(rq):
        return [_R("小米2024营收是3659亿元", "c1"), _R("小米2024营收创新高", "c2")]
    out = agentic_evolve(_Q, [], search_fn=sfn)
    assert len(out["results"]) >= 2
    assert out["label"] == STRONG
    assert any(a.startswith(GRAPH_RETRIEVE) for a in out["actions"])
    assert out["actions"][-1] == ANSWER
    assert out["corrected"] is True


def test_graph_edit_insert_routes_through_propose_fn():
    calls = []

    def pfn(h, rel, t, confidence=0.6):
        calls.append((h, rel, t))
        return True

    def efn(pool, query):
        return [("小米集团", "2024营收", "3659亿")]

    def sfn(rq):
        return [_R("小米2024营收是3659亿元", "c1"), _R("小米2024营收创新高", "c2")]

    out = agentic_evolve(_Q, [_R("无关", "c0")], search_fn=sfn, propose_fn=pfn, extract_fn=efn)
    assert out["proposed"] >= 1
    assert calls == [("小米集团", "2024营收", "3659亿")]
    assert any(a.startswith(GRAPH_EDIT) for a in out["actions"])


def test_pool_dedup_by_chunk_id():
    def sfn_dup(rq):
        return [_R("小米2024营收是3659亿元", "c1"), _R("小米2024营收是3659亿元", "c1")]
    out = agentic_evolve(_Q, [], search_fn=sfn_dup)
    keys = [r.chunk_id for r in out["results"]]
    assert len(keys) == len(set(keys))


def test_never_raises_on_bad_search_fn():
    def bad(rq):
        raise RuntimeError("boom")
    out = agentic_evolve(_Q, [], search_fn=bad)
    assert isinstance(out, dict)
    assert out["actions"][-1] == ANSWER


if __name__ == "__main__":
    test_initial_strong_answers_immediately()
    test_weak_accumulates_pool_until_strong()
    test_graph_edit_insert_routes_through_propose_fn()
    test_pool_dedup_by_chunk_id()
    test_never_raises_on_bad_search_fn()
    print("test_v104_evograph: all passed")
