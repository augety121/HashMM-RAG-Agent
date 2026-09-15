"""V103.47 — 诚实拒答兜底 (refusal_guard) 单元测试。

覆盖：语料未覆盖 (insufficient) 时，无论模型怎么措辞，最终答案必须诚实，
绝不放出凭参数记忆编造的具体数字；grounded/augmented 等模式完全不受影响。
"""
import pytest

pytestmark = pytest.mark.unit


def test_honest_answer_kept_unchanged():
    """模型自己已经诚实说'没有' → 原样保留（含它补的建议）。"""
    from hashmm.refusal_guard import enforce
    a = "知识库资料中没有关于苹果公司的财务信息，建议查阅其官方年报。"
    assert enforce(a, "苹果公司2024营收", mode="insufficient") == a


def test_fabricated_number_replaced():
    """模型在 insufficient turn 编了个具体营收 → 必须被诚实版覆盖。"""
    from hashmm.refusal_guard import enforce, has_honest_signal
    a = "苹果公司2024年营收约为3914亿美元，同比增长2%。"
    out = enforce(a, "苹果公司2024营收", mode="insufficient")
    assert has_honest_signal(out), out
    assert "3914" not in out  # 编造的数字被剔除


def test_honest_in_spirit_gets_signal_prepended():
    """措辞含糊但没有明确诚实词 → 前置一句规范诚实表述。"""
    from hashmm.refusal_guard import enforce, has_honest_signal
    a = "这个问题超出了我能从资料里确认的范围。"
    out = enforce(a, "随便问问", mode="insufficient")
    assert has_honest_signal(out)


def test_empty_answer_becomes_canonical():
    from hashmm.refusal_guard import enforce, has_honest_signal
    out = enforce("", "x", mode="insufficient")
    assert has_honest_signal(out)
    assert len(out) > 5


def test_grounded_mode_never_touched():
    """grounded 模式即使含数字也绝不改动（那是有据可查的真实答案）。"""
    from hashmm.refusal_guard import enforce
    a = "小米2024年营收3659亿元[1]。"
    assert enforce(a, "小米2024营收", mode="grounded") == a
    assert enforce(a, "小米2024营收", mode="augmented") == a


def test_pii_query_mentions_privacy():
    """隐私类问题的诚实拒答应当点明隐私受保护。"""
    from hashmm.refusal_guard import enforce
    out = enforce("", "丁磊的私人手机号是多少", mode="insufficient")
    assert "隐私" in out


def test_canonical_satisfies_refusal_contract():
    """规范诚实句必须命中评测 refuse_company 契约里的诚实信号子串。"""
    from hashmm.refusal_guard import _CANONICAL_REFUSAL
    contract = ["没有", "未提及", "无法", "未找到", "暂无",
                "不包含", "未涉及", "未披露", "查无"]
    assert any(tok in _CANONICAL_REFUSAL for tok in contract)


def test_never_raises_on_weird_input():
    from hashmm.refusal_guard import enforce
    for bad in [None, "", "   ", "🙂", "a" * 5000]:
        out = enforce(bad, "q", mode="insufficient")
        assert isinstance(out, str)
