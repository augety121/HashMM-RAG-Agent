"""tests/test_sft_data.py — 4090 LoRA-SFT 数据构造的纯逻辑测试（不依赖 GPU/trl/bitsandbytes）。"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.training.build_sft_data import (
    case_to_sft, build_assistant_target, _first_answer, _supporting_text, _USER_TEMPLATE, build,
)


def test_user_template_has_tags():
    t = _USER_TEMPLATE.format(question="x")
    for tag in ("<think>", "<search>", "<information>", "<answer>"):
        assert tag in t


def test_two_step_when_supporting_present():
    c = {"query": "Q", "reference_answer": "A", "supporting": ["证据段落"]}
    s = case_to_sft(c)
    assert [m["role"] for m in s["messages"]] == ["user", "assistant"]
    asst = s["messages"][1]["content"]
    assert "<search>" in asst and "<information>" in asst and "<answer>" in asst


def test_retrieval_style_teaches_search_even_without_supporting():
    """V103.58：默认 retrieval 风格——即使无 supporting，也教模型先检索再答（治脑补）。"""
    s = case_to_sft({"query": "Q", "reference_answer": "A"})
    asst = s["messages"][1]["content"]
    assert "<search>" in asst and "<information>" in asst and "<answer>" in asst


def test_direct_style_answers_directly():
    """direct 风格（旧行为）：无 supporting 时直接答，不检索。"""
    s = case_to_sft({"query": "Q", "reference_answer": "A"}, style="direct")
    asst = s["messages"][1]["content"]
    assert "<think>" in asst and "<answer>" in asst and "<search>" not in asst


def test_missing_answer_returns_none():
    assert case_to_sft({"query": "Q"}) is None
    assert case_to_sft({"reference_answer": "A"}) is None
    assert case_to_sft({}) is None


def test_first_answer_and_supporting():
    assert _first_answer(["", "A", "B"]) == "A"
    assert _first_answer("A") == "A"
    assert _first_answer(None) == ""
    assert _supporting_text({"supporting": ["x", "y"]})
    assert _supporting_text({"evidence": "z"}) == "z"
    assert _supporting_text({}) == ""


def test_build_writes_jsonl():
    cases = [{"query": f"Q{i}", "reference_answer": f"A{i}"} for i in range(20)]
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "golden.json")
        json.dump(cases, open(gp, "w", encoding="utf-8"))
        out = os.path.join(d, "sft")
        stats = build(gp, out, val_ratio=0.1)
        assert stats["train"] + stats["val"] == 20
        # train.jsonl 每行是合法 JSON 且有 messages
        with open(os.path.join(out, "train.jsonl"), encoding="utf-8") as f:
            line = json.loads(f.readline())
            assert "messages" in line and len(line["messages"]) == 2


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
