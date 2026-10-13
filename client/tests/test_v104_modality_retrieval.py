"""V104 P0 — 多模态检索地基：查询模态意图检测 + 模态加权（默认关）。

纯逻辑、无重依赖、可在沙箱直跑。验收"问图/表时把对应模态结果前移"，
且无模态意图 / 池中无该模态时零变化（铁律：默认关、关闭零变化）。
"""
from hashmm.retrieval_pipeline import (
    detect_modality_intent, SearchResult, RetrievalPipeline,
)


def _mk(text, modality):
    return SearchResult(text=text, score=1.0, chunk_id=text, modality=modality)


def test_modality_intent_detection():
    cases = [
        ("第3张图显示什么趋势", "image"),
        ("表2第二行的值是多少", "table"),
        ("这个柱状图的含义", "chart"),
        ("推导这个公式", "equation"),
        ("图3中的曲线", "image"),
        ("见table 4", "table"),
        # 反例：不得误伤含"图"的常见词
        ("公司2024年营收多少", None),
        ("总结这份报告", None),
        ("我的意图是什么", None),
        ("打开地图", None),
        ("试图理解", None),
    ]
    for q, exp in cases:
        assert detect_modality_intent(q) == exp, f"{q!r} -> {detect_modality_intent(q)} (exp {exp})"


def test_modality_boost_promotes_matching_modality_stably():
    rp = RetrievalPipeline.__new__(RetrievalPipeline)  # 不触发 load()
    results = [_mk("文1", "text"), _mk("表A", "table"), _mk("文2", "text"), _mk("表B", "table")]
    boosted = rp._apply_modality_boost("表格里的数据", results)
    # 表格结果前移，且组内保持原相对顺序（表A 在 表B 前）
    assert [r.text for r in boosted] == ["表A", "表B", "文1", "文2"]


def test_modality_boost_noop_without_intent():
    rp = RetrievalPipeline.__new__(RetrievalPipeline)
    results = [_mk("文1", "text"), _mk("表A", "table"), _mk("文2", "text")]
    same = rp._apply_modality_boost("公司营收", results)
    assert [r.text for r in same] == ["文1", "表A", "文2"]


def test_modality_boost_noop_when_no_matching_modality_in_pool():
    rp = RetrievalPipeline.__new__(RetrievalPipeline)
    only_text = [_mk("文1", "text"), _mk("文2", "text")]
    same = rp._apply_modality_boost("看这张图", only_text)
    assert [r.text for r in same] == ["文1", "文2"]


def test_image_intent_loosely_matches_chart_modality():
    rp = RetrievalPipeline.__new__(RetrievalPipeline)
    mix = [_mk("文", "text"), _mk("图C", "chart")]
    assert rp._apply_modality_boost("这张图说明什么", mix)[0].text == "图C"


if __name__ == "__main__":
    test_modality_intent_detection()
    test_modality_boost_promotes_matching_modality_stably()
    test_modality_boost_noop_without_intent()
    test_modality_boost_noop_when_no_matching_modality_in_pool()
    test_image_intent_loosely_matches_chart_modality()
    print("test_v104_modality_retrieval: all passed")
