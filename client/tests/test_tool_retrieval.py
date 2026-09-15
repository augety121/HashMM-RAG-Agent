"""工具检索（Tool Retrieval，V310）回归测试。纯逻辑，无 GPU/编码器依赖（走关键词回退路径）。"""
import os

import pytest

from hashmm.agent import tool_retrieval as TR


def _mk(name, desc):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": {}}}}


def _many_tools():
    return [
        _mk("run_shell", "执行shell命令"), _mk("read_file", "读取文件内容"),
        _mk("create_file", "创建新文件"), _mk("browser_open", "浏览器打开网页交互"),
        _mk("browser_screenshot", "网页截图"), _mk("web_search", "联网搜索最新信息"),
        _mk("fetch_url", "抓取网页文本"), _mk("create_pptx_from_plan", "生成PPT演示文稿幻灯片"),
        _mk("create_xlsx", "生成Excel电子表格"), _mk("create_pdf", "生成PDF文档"),
        _mk("kg_query", "知识图谱实体关系查询"), _mk("video_transcript", "视频字幕文本"),
        _mk("weather", "查询天气"), _mk("memory_recall", "回忆历史对话"),
        _mk("convert_file", "文件格式转换"),
    ]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    TR.clear_cache()
    monkeypatch.setenv("HASHMM_TOOL_RETRIEVAL", "1")
    monkeypatch.setenv("HASHMM_TOOL_RETRIEVAL_MIN", "5")
    monkeypatch.setenv("HASHMM_TOOL_RETRIEVAL_TOPK", "5")


def test_chinese_bigram_tokenize():
    """中文必须按 bigram 切分，否则整串中文当一个词永远匹配不上。"""
    toks = TR._tokenize("生成PPT演示文稿")
    assert "演示" in toks and "文稿" in toks   # bigram
    assert "ppt" in toks                        # 英文词


def test_retrieval_actually_filters():
    tools = _many_tools()
    TR.clear_cache()
    sel = TR.select_tools("帮我做一个季度汇报的PPT演示文稿", tools)
    names = [t["function"]["name"] for t in sel]
    assert len(names) < len(tools)                    # 真的筛掉了一些
    assert "create_pptx_from_plan" in names           # 命中相关工具


def test_core_tools_always_kept():
    tools = _many_tools()
    TR.clear_cache()
    sel = TR.select_tools("查一下天气", tools)         # 跟文件操作无关的 query
    names = [t["function"]["name"] for t in sel]
    for core in ("run_shell", "read_file", "create_file"):
        assert core in names                           # 核心工具无条件保留


def test_web_query_hits_browser_tools():
    tools = _many_tools()
    TR.clear_cache()
    sel = TR.select_tools("打开这个网站看看内容", tools)
    names = [t["function"]["name"] for t in sel]
    assert any(n in names for n in ("browser_open", "fetch_url", "web_search"))


def test_disabled_returns_all(monkeypatch):
    monkeypatch.setenv("HASHMM_TOOL_RETRIEVAL", "0")
    TR.clear_cache()
    tools = _many_tools()
    assert len(TR.select_tools("做PPT", tools)) == len(tools)   # 关闭 = 全部工具


def test_few_tools_not_retrieved():
    tools = _many_tools()[:4]                          # 少于阈值
    TR.clear_cache()
    assert len(TR.select_tools("随便", tools)) == 4     # 不检索，返回全部


def test_empty_tools_safe():
    assert TR.select_tools("x", []) == []


def test_fail_open_on_no_match():
    """query 跟所有工具都无关时也不能返回空——回退全部（fail-open）。"""
    tools = _many_tools()
    TR.clear_cache()
    sel = TR.select_tools("zzzzz qqqqq 完全不相关的乱码", tools)
    assert len(sel) >= 3                               # 至少核心工具在，绝不空


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
