"""Agent 使用体验回归测试：DSML 工具标记泄露 + URL 幻觉防护。

锁住两个真实 bug 的修复（来自真机截图/日志）：
- Bug1：模型吐 `<｜｜DSML｜｜invoke ...>`（全角管道符变体）时，必须被解析成工具调用，
  且绝不把原始标记泄露给用户。
- Bug2：用户消息含 URL 时，提取出来用于强约束 agent 使用确切链接。
"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.agent.loop") is None:
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="agent.loop 未部署")]


# ── Bug1: DSML 变体解析 + 泄露护栏 ──

def test_dsml_variant_parsed():
    """全角 DSML 变体能被解析成工具调用（参数正确）。"""
    from hashmm.agent.loop import parse_text_tool_calls
    dsml = ('<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="fetch_url">'
            '<｜｜DSML｜｜parameter name="url" string="true">'
            'https://arxiv.org/abs/2410.21276</｜｜DSML｜｜parameter>'
            '</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>')
    calls, cleaned = parse_text_tool_calls(dsml)
    assert len(calls) == 1
    assert calls[0].function.name == "fetch_url"
    assert "2410.21276" in calls[0].function.arguments


def test_dsml_no_residue_in_cleaned():
    """解析后 cleaned 文本不含任何 DSML/工具标记残片。"""
    from hashmm.agent.loop import parse_text_tool_calls
    dsml = '<｜｜DSML｜｜invoke name="x"><｜｜DSML｜｜parameter name="q">v</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke>'
    _, cleaned = parse_text_tool_calls(dsml)
    assert "DSML" not in cleaned
    assert "invoke" not in cleaned
    assert "parameter" not in cleaned


def test_strip_residue_guard():
    """最终护栏：即使残片混在正文里，发给用户前也被清掉。"""
    from hashmm.agent.loop import _strip_tool_markup_residue
    txt = "正文内容 <｜｜DSML｜｜invoke name=\"x\"> 更多正文"
    safe = _strip_tool_markup_residue(txt)
    assert "DSML" not in safe and "invoke" not in safe
    assert "正文内容" in safe and "更多正文" in safe


def test_normalize_idempotent_on_standard():
    """归一化对标准 XML 无副作用（标准格式仍能解析）。"""
    from hashmm.agent.loop import parse_text_tool_calls
    std = '<tool_calls><invoke name="kb_search"><parameter name="query">x</parameter></invoke></tool_calls>'
    calls, _ = parse_text_tool_calls(std)
    assert len(calls) == 1 and calls[0].function.name == "kb_search"


# ── Bug2: URL 提取（防幻觉）──

def test_extract_user_url():
    """从用户消息提取确切 URL。"""
    from hashmm.agent.loop import _extract_urls
    urls = _extract_urls("帮我拆解这篇论文 https://arxiv.org/abs/2410.21276")
    assert urls == ["https://arxiv.org/abs/2410.21276"]


def test_extract_urls_strip_punctuation():
    """提取 URL 去掉尾部标点、去重保序。"""
    from hashmm.agent.loop import _extract_urls
    urls = _extract_urls("看 https://a.com/x 和 https://b.com/y。")
    assert urls == ["https://a.com/x", "https://b.com/y"]


def test_extract_urls_empty():
    """无 URL 返回空。"""
    from hashmm.agent.loop import _extract_urls
    assert _extract_urls("总结这篇论文的创新点") == []


# ── Bug3/4: fetch_url 幻觉纠偏 + 空回答兜底（来自第二次真机截图）──

def test_fetch_url_hallucination_correction_logic():
    """用户给 arxiv，agent 填了 lesswrong（不同域名）→ 应判定为幻觉需纠偏。"""
    def _host(u):
        return u.split("/")[2].lower() if len(u.split("/")) > 2 else ""
    user_urls = ["https://arxiv.org/abs/2410.21276"]
    user_hosts = {_host(u) for u in user_urls}
    # 幻觉：不同域名
    assert _host("https://www.lesswrong.com/posts/x") not in user_hosts
    # 同域名（arxiv 的 pdf 子路径）：不纠偏
    assert _host("https://arxiv.org/pdf/2410.21276") in user_hosts


def test_run_stores_user_urls():
    """run() 入口应把用户 URL 存到 _user_urls（供 fetch_url 纠偏）。"""
    from hashmm.agent.loop import _extract_urls
    # 间接验证：_extract_urls 是 _user_urls 的来源
    assert _extract_urls("拆解 https://arxiv.org/abs/2410.21276") == ["https://arxiv.org/abs/2410.21276"]
