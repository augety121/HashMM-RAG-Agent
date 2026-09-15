"""tests/test_coref_rewrite.py — V103.51 多轮指代消解离线验证（regex 回退路径，无需 LLM）。

重点覆盖用户给的例子「它去年呢?」——「它」指上一轮提到的公司（含裸品牌如「网易」，
旧版会漏），且新问句的时间锚点「去年」要保留。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.chat_retrieval import _extract_recent_entity, ChatRetrieval


# ── _extract_recent_entity 纯函数 ──────────────────────────────────────────
def test_extract_bare_brand_from_user_turn():
    hist = [{"role": "user", "content": "网易2024年的游戏收入是多少?"}]
    assert _extract_recent_entity(hist) == "网易"


def test_extract_brand_from_assistant_turn():
    # 实体只出现在助手的回答里——「它」常指代它
    hist = [
        {"role": "user", "content": "这家公司去年表现如何?"},
        {"role": "assistant", "content": "网易2024年营收同比增长..."},
    ]
    assert _extract_recent_entity(hist) == "网易"


def test_extract_prefers_suffix_company_over_bare():
    hist = [{"role": "user", "content": "字节跳动集团的组织结构是怎样的?"}]
    # 带后缀的「字节跳动集团」比裸品牌更具体，应优先
    assert _extract_recent_entity(hist) == "字节跳动集团"


def test_extract_takes_most_recent():
    hist = [
        {"role": "user", "content": "小米的毛利率?"},
        {"role": "assistant", "content": "小米2024年毛利率约..."},
        {"role": "user", "content": "华为呢?"},
        {"role": "assistant", "content": "华为2024年..."},
    ]
    assert _extract_recent_entity(hist) == "华为"   # newest wins


def test_extract_empty_when_no_entity():
    hist = [{"role": "user", "content": "今天天气怎么样?"}]
    assert _extract_recent_entity(hist) == ""
    assert _extract_recent_entity([]) == ""


# ── _regex_rewrite via ChatRetrieval (LLM off → regex fallback) ─────────────
def _cr_no_llm():
    cr = ChatRetrieval.__new__(ChatRetrieval)   # avoid heavy __init__
    cr._llm_fn = None
    cr._get_llm = lambda: None                  # force regex path
    return cr


def test_rewrite_it_last_year_resolves_company_and_keeps_time():
    cr = _cr_no_llm()
    hist = [
        {"role": "user", "content": "网易2024年的游戏收入是多少?"},
        {"role": "assistant", "content": "网易2024年游戏收入约为XXX亿元[1]。"},
    ]
    out = cr.rewrite_query("它去年呢?", hist)
    assert "网易" in out          # pronoun resolved to the company
    assert "去年" in out          # time anchor preserved
    assert "它" not in out        # pronoun removed


def test_rewrite_bare_topic_followup():
    cr = _cr_no_llm()
    hist = [
        {"role": "user", "content": "华为的研发投入是多少?"},
        {"role": "assistant", "content": "华为2024年研发投入约XXX亿元。"},
    ]
    out = cr.rewrite_query("那它的员工数呢?", hist)
    assert "华为" in out
    assert "员工" in out
    assert "它" not in out


def test_rewrite_noop_when_no_pronoun_gate():
    cr = _cr_no_llm()
    hist = [{"role": "user", "content": "网易的营收?"}]
    # 不含指代/省略信号 → _NEEDS_REWRITE 不触发 → 原样返回
    out = cr.rewrite_query("台积电2024年的营收是多少?", hist)
    assert out == "台积电2024年的营收是多少?"


def test_rewrite_noop_when_history_too_short():
    cr = _cr_no_llm()
    out = cr.rewrite_query("它去年呢?", [{"role": "user", "content": "x"}])
    # history < 2 → 原样返回（rewrite_query 的早返回）
    assert out == "它去年呢?"


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
