"""tests/test_public_data.py — V103.61：公开中文数据转换 + 合并去重的纯逻辑测试（不需网络）。"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.training.fetch_public_zh_data import _case
from hashmm.training.merge_golden import merge, _norm, load


def test_case_format_matches_golden():
    c = _case(3, "Q", "A", "出处段落", "cmrc2018")
    # 字段齐全且能被 build_sft_data 吃
    for k in ("id", "query", "reference_answer", "supporting", "relevant_docs"):
        assert k in c
    assert c["query"] == "Q" and c["reference_answer"] == "A"
    assert c["supporting"] == ["出处段落"]      # 带 supporting → retrieval 风格会教检索
    assert c["id"] == "cmrc2018_000003"


def test_case_empty_supporting():
    c = _case(0, "Q", "A", "", "x")
    assert c["supporting"] == []                 # 无出处 → 空列表，不报错


def test_case_to_sft_consumes_public_case():
    """转出的公开题能被 build_sft_data 正确转成带 <search> 的 SFT 样本。"""
    from hashmm.training.build_sft_data import case_to_sft
    c = _case(1, "小米2024收入?", "3659亿", "小米2024年收入3659亿元", "cmrc2018")
    sft = case_to_sft(c)  # 默认 retrieval 风格
    asst = sft["messages"][1]["content"]
    assert "<search>" in asst and "<answer>" in asst


def test_merge_dedup_priority():
    d = tempfile.mkdtemp()
    f1 = os.path.join(d, "ent.json")
    f2 = os.path.join(d, "pub.json")
    json.dump([{"query": "小米收入?", "reference_answer": "3659亿", "relevant_docs": ["ent"]}],
              open(f1, "w", encoding="utf-8"))
    json.dump([{"query": "小米 收入?", "reference_answer": "wrong", "relevant_docs": ["pub"]},
               {"query": "腾讯收入?", "reference_answer": "y"}],
              open(f2, "w", encoding="utf-8"))
    merged, stats = merge([f1, f2])
    assert len(merged) == 2                       # 小米重复去掉
    # 企业题优先：保留的"小米"来自 f1（答案 3659亿，不是 wrong）
    xiaomi = [c for c in merged if "小米" in c["query"]][0]
    assert xiaomi["reference_answer"] == "3659亿"


def test_norm_dedup_key():
    assert _norm("小米 收入?") == _norm("小米收入?")   # 空白无关
    assert _norm("ABC") == "abc"


def test_load_handles_dict_and_list():
    d = tempfile.mkdtemp()
    f = os.path.join(d, "x.json")
    json.dump({"cases": [{"query": "q", "reference_answer": "a"}]}, open(f, "w", encoding="utf-8"))
    assert len(load(f)) == 1                       # {"cases":[...]} 形态也能读



def test_merge_repeat_oversample():
    import tempfile, os
    d = tempfile.mkdtemp()
    ent = os.path.join(d, 'e.json'); pub = os.path.join(d, 'p.json')
    json.dump([{'query': f'E{i}', 'reference_answer': 'a'} for i in range(3)], open(ent, 'w', encoding='utf-8'))
    json.dump([{'query': f'P{i}', 'reference_answer': 'b'} for i in range(5)], open(pub, 'w', encoding='utf-8'))
    merged, stats = merge([ent, pub], repeat_first=4)
    ec = sum(1 for c in merged if c['query'].startswith('E'))
    pc = sum(1 for c in merged if c['query'].startswith('P'))
    assert ec == 12 and pc == 5      # 企业题过采样 4 份, 公开题 1 份
    assert stats[ent]['repeated'] == 4


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
