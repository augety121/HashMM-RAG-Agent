"""tests/test_searchr1_policy.py — 训练好的策略模型 ↔ 回路 的适配器逻辑测试（不需 GPU）。

只测纯逻辑 decision_from_model_text：把模型的 <search>/<answer> 输出翻译成回路决策。
模型加载与生成需 GPU，在服务器上验证。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.training.searchr1_policy import decision_from_model_text


def test_search_takes_priority_over_answer():
    """同时有 search 和 answer（模型常见输出）→ 优先 search 继续检索真证据。"""
    text = ("<think>需要查</think><search>网易 收入</search>"
            "<information>脑补的</information><answer>746亿</answer>")
    d = decision_from_model_text(text)
    assert d == {"action": "search", "query": "网易 收入"}


def test_answer_only_finishes():
    assert decision_from_model_text("<think>知道</think><answer>北京</answer>") == {"action": "finish"}


def test_empty_search_finishes():
    assert decision_from_model_text("<search>   </search>") == {"action": "finish"}


def test_quote_noise_stripped():
    d = decision_from_model_text('<search>"网易 收入"</search>')
    assert d["action"] == "search" and d["query"] == "网易 收入"


def test_empty_and_garbage_safe():
    assert decision_from_model_text("") == {"action": "finish"}
    assert decision_from_model_text("毫无标签的文本") == {"action": "finish"}
    # 不抛错
    for junk in (None, "<search>", "<answer>", "<search></search>"):
        try:
            decision_from_model_text(junk if junk is not None else "")
        except Exception as e:
            raise AssertionError(f"raised on {junk!r}: {e}")


def test_multiline_search():
    text = "<think>\n多行\n推理\n</think>\n<search>\n  跨行 子查询  \n</search>"
    d = decision_from_model_text(text)
    assert d["action"] == "search" and d["query"] == "跨行 子查询"


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
