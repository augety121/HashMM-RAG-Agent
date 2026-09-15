"""tests/test_training_data.py — V103.54：P2 训练数据脚本的纯逻辑测试。

只测不依赖 pandas/GPU 的部分：Search-R1 格式转换、答案别名规整、记录校验。
（parquet 落盘 / 实际训练在你的 GPU 服务器上验证。）
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.training.build_searchr1_data import (
    _to_searchr1_record, _norm_answers, load_golden_cases, _QUESTION_TEMPLATE,
)
from hashmm.training.load_and_train import validate_record


def test_record_shape():
    r = _to_searchr1_record("Q?", "A", data_source="src", split="train", index=3)
    assert set(r.keys()) == {"data_source", "prompt", "ability", "reward_model", "extra_info"}
    assert r["prompt"][0]["role"] == "user" and "Q?" in r["prompt"][0]["content"]
    assert r["ability"] == "fact-reasoning"
    assert r["reward_model"]["style"] == "rule"
    assert r["reward_model"]["ground_truth"]["target"] == ["A"]
    assert r["extra_info"] == {"split": "train", "index": 3}


def test_template_has_searchr1_tags():
    t = _QUESTION_TEMPLATE.format(question="x")
    for tag in ("<think>", "<search>", "<information>", "<answer>"):
        assert tag in t


def test_norm_answers():
    assert _norm_answers("A") == ["A"]
    assert _norm_answers(["A", "B", ""]) == ["A", "B"]
    assert _norm_answers(None) == []
    assert _norm_answers("  ") == []


def test_validate_good_and_bad():
    good = _to_searchr1_record("Q", "A", data_source="s", split="train", index=0)
    assert validate_record(good) == []
    # 缺答案
    bad1 = _to_searchr1_record("Q", "", data_source="s", split="train", index=0)
    assert validate_record(bad1)
    # prompt 空
    bad2 = dict(good); bad2["prompt"] = []
    assert validate_record(bad2)
    # reward_model.style 错
    bad3 = json.loads(json.dumps(good)); bad3["reward_model"]["style"] = "model"
    assert validate_record(bad3)


def test_validate_never_raises():
    for junk in [{}, {"prompt": None}, {"reward_model": 123}, None.__class__ and {"extra_info": []}]:
        try:
            validate_record(junk)  # 不应抛错
        except Exception as e:
            raise AssertionError(f"validate_record raised on junk: {e}")


def test_load_golden_cases_formats():
    # 列表形态 + dict 形态 + 缺答案过滤
    cases = [
        {"query": "Q1", "reference_answer": "A1"},
        {"question": "Q2", "answer": "A2"},          # 兼容 question/answer 命名
        {"query": "Q3", "reference_answer": ""},      # 无答案 → 过滤
        {"query": "", "reference_answer": "A4"},       # 无问题 → 过滤
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(cases, f); p1 = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"cases": cases}, f); p2 = f.name
    try:
        out1 = load_golden_cases(p1)
        out2 = load_golden_cases(p2)
        assert len(out1) == 2 and len(out2) == 2          # 只保留 Q1、Q2
        assert {c["query"] for c in out1} == {"Q1", "Q2"}
    finally:
        os.unlink(p1); os.unlink(p2)


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
