"""V104 P2 — 表格 → 结构化关系进 KG（多模态超图落地）。

验收：① 典型表 → "行键×列头→值"三元组 + 表实体 + 跨模态接地，全部 modality=table、带溯源；
② 无表头表用"列N"兜底不丢数据；③ 畸形/不足2×2 → 永不抛错返回空；④ 值==行键跳过 + 去重；
⑤ modality 进图谱能存能取，文本实体默认 text（向后兼容）。纯逻辑、无重依赖、可在沙箱直跑。
"""
from hashmm.kg.table_extractor import extract_table_kg
from hashmm.kg.graph import KnowledgeGraph
from hashmm.kg.extractor import Entity


def test_typical_table_to_triples():
    tbl = [
        ["公司", "2024营收", "2023营收"],
        ["小米集团", "3659亿", "2710亿"],
        ["比亚迪", "7771亿", "6023亿"],
    ]
    ents, rels = extract_table_kg(tbl, caption="主要公司营收对比", section="第3章 行业", source_id="doc1#t0")
    rel_strs = {f"{r.head}--{r.relation}-->{r.tail}" for r in rels}
    assert "小米集团--2024营收-->3659亿" in rel_strs
    assert "比亚迪--2023营收-->6023亿" in rel_strs
    assert "主要公司营收对比--包含行-->小米集团" in rel_strs          # 表→行 导航
    assert "主要公司营收对比--位于章节-->第3章 行业" in rel_strs       # 跨模态接地
    assert all(r.modality == "table" for r in rels)
    assert all(e.modality == "table" for e in ents)
    assert all("doc1#t0" in r.source_ids for r in rels)


def test_headerless_table_uses_column_fallback():
    tbl = [["1", "2", "3"], ["4", "5", "6"]]
    _e, rels = extract_table_kg(tbl, source_id="d2")
    assert any(r.relation.startswith("列") for r in rels)


def test_malformed_inputs_never_raise():
    for bad in [None, [], [[]], [["只一行"]], "不是表", [["a"]], [["a", "b"]]]:
        ents, rels = extract_table_kg(bad, source_id="x")
        assert ents == [] and rels == []


def test_value_equal_rowkey_skipped_and_dedup():
    tbl = [["键", "A", "B"], ["x", "x", "1"], ["x", "x", "1"]]
    _e, rels = extract_table_kg(tbl, source_id="d4")
    assert not [r for r in rels if r.head == "x" and r.tail == "x"]
    # 去重：相同 (head,relation,tail) 不重复
    keys = [(r.head, r.relation, r.tail) for r in rels]
    assert len(keys) == len(set(keys))


def test_modality_roundtrip_in_graph():
    tbl = [["公司", "2024营收"], ["小米集团", "3659亿"]]
    ents, rels = extract_table_kg(tbl, caption="营收表", source_id="d1")
    kg = KnowledgeGraph()
    kg.add_entities(ents)
    kg.add_relations(rels)
    assert kg.get_entity("小米集团").get("modality") == "table"
    edge_mods = [d.get("modality") for _h, _t, d in kg.graph.edges(data=True) if d.get("relation") == "2024营收"]
    assert edge_mods and all(m == "table" for m in edge_mods)
    # 文本实体默认 text（老数据/纯文本路向后兼容）
    kg.add_entity(Entity(name="某文本实体", entity_type="概念"))
    assert kg.get_entity("某文本实体").get("modality") == "text"


if __name__ == "__main__":
    test_typical_table_to_triples()
    test_headerless_table_uses_column_fallback()
    test_malformed_inputs_never_raise()
    test_value_equal_rowkey_skipped_and_dedup()
    test_modality_roundtrip_in_graph()
    print("test_v104_table_kg: all passed")
