"""V103.50 — 拒答近义词覆盖回归测试。

antihalluc_03（"网易2100年游戏收入预测"）那次失败，是因为模型给了完全正确的
诚实拒答「文档中**未提供**…」，但金标准 must_contain_any 只有 9 个词、漏了"未提供"。
这是金标准词表的缺陷，不是模型问题。本测试锁定：常见的诚实拒答措辞都被金标准认可，
避免词表再退化导致正确拒答被误判为失败。
"""
import json
import os
import pytest

pytestmark = pytest.mark.unit

_GOLDEN = os.path.join(os.path.dirname(__file__), "..", "hashmm", "evaluation", "golden_cases_100.json")

# 真实模型给过的、客观合格的诚实拒答措辞样例（来自 dump）。
_REAL_REFUSALS = [
    "文档中未提供网易2100年的游戏收入预测数据，相关内容仅涵盖至2025年实际业绩",
    "知识库中没有相关信息，无法回答这个问题",
    "资料未涉及这个内容",
    "文档未披露该数据",
    "未找到相关记录",
    "该信息不存在于现有资料中",
]


def _load():
    with open(_GOLDEN, encoding="utf-8") as f:
        return json.load(f)


def test_refusal_wordlist_covers_common_phrasings():
    """每条 refusal/adversarial 用例的词表，应覆盖所有常见诚实拒答措辞。"""
    cases = _load()
    refusal = [c for c in cases
               if c.get("category") in ("refusal", "adversarial") and c.get("must_contain_any")]
    assert refusal, "应存在带 must_contain_any 的拒答类用例"
    # 至少要认 "未提供"（antihalluc_03 那次漏的词）
    for c in refusal:
        assert "未提供" in c["must_contain_any"], f"{c['id']} 词表缺少'未提供'"


def test_real_refusal_answers_all_pass():
    """dump 里真实出现过的诚实拒答答案，用补全后的词表应全部命中。"""
    cases = _load()
    # 用任一拒答类的词表（它们现在统一补全过）
    sample = next(c for c in cases
                  if c.get("category") in ("refusal", "adversarial") and c.get("must_contain_any"))
    wl = sample["must_contain_any"]
    for ans in _REAL_REFUSALS:
        assert any(w in ans for w in wl), f"诚实拒答未被词表认可: {ans}"


def test_antihalluc_03_specifically_passes():
    """antihalluc_03 的真实答案现在必须通过。"""
    cases = {c["id"]: c for c in _load()}
    c = cases.get("antihalluc_03")
    assert c is not None
    real_answer = "文档中未提供网易2100年的游戏收入预测数据，相关内容仅涵盖至2025年实际业绩[1][3]。"
    assert any(w in real_answer for w in c["must_contain_any"])
