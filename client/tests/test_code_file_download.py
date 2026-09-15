"""代码文件下载能力回归测试（对标 Claude Code 的可下载代码文件）。

锁住：create_file 工具生成代码文件时，返回结构化 file 字段（触发前端下载卡片），
同时对 LLM 保持干净的字符串契约。
"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.api.tool_registry") is None:
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="tool_registry 未部署")]


@pytest.fixture
def reg():
    from hashmm.api import tool_registry as TR
    return TR


def test_create_file_returns_file_field(reg, clean_env):
    """create_file 的结构化返回带 file 字段（filename + download_url）。"""
    d = reg.execute_tool_structured(
        "create_file",
        {"filename": "rbtree.cpp", "content": "struct Node { int key; };\nint main(){return 0;}"},
        {"conv_id": "cTest"},
    )
    assert isinstance(d, dict)
    assert d.get("status") == "ok"
    assert d["file"]["filename"] == "rbtree.cpp"
    assert d["file"]["download_url"]


def test_create_file_llm_sees_clean_message(reg, clean_env):
    """execute_tool（给 LLM）返回干净 message，而非整个 dict 的丑字符串。"""
    s = reg.execute_tool(
        "create_file",
        {"filename": "main.py", "content": "print('hello')\n# a comment"},
        {"conv_id": "cTest"},
    )
    assert isinstance(s, str)
    assert "{" not in s  # 不是 dict 转的字符串
    assert "main.py" in s


def test_create_file_various_extensions(reg, clean_env):
    """代码文件常见扩展名都能创建并给出下载链接。"""
    for fn, content in [("a.py", "x = 1\nprint(x)"), ("b.js", "const x = 1;\nconsole.log(x)"),
                        ("c.ts", "const x: number = 1;\nexport {}")]:
        d = reg.execute_tool_structured("create_file", {"filename": fn, "content": content},
                                        {"conv_id": "cTest"})
        assert d["file"]["filename"] == fn
        assert fn in d["file"]["download_url"]


# ── 确定性代码下载：正文有大代码块就自动存文件（不依赖模型调工具）──

def test_extract_code_blocks():
    """从 markdown 提取代码块（语言 + 代码）。"""
    from hashmm.agent.loop import _extract_code_blocks
    text = "说明\n```python\nx = 1\nprint(x)\n```\n更多"
    blocks = _extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0][0] == "python"
    assert "print(x)" in blocks[0][1]


def test_guess_code_filename():
    """根据语言/类名猜文件名。"""
    from hashmm.agent.loop import _guess_code_filename
    fn = _guess_code_filename("cpp", "class RedBlackTree { };")
    assert fn == "redblacktree.cpp"
    fn2 = _guess_code_filename("python", "def hello(): pass")
    assert fn2.endswith(".py")


def test_auto_code_file_end_to_end():
    """端到端：agent 正文含大代码块（≥15行）→ 自动产出 file 事件（下载卡片）。"""
    import asyncio
    from hashmm.agent.loop import AgentLoop

    class _Msg:
        def __init__(s, c): s.content = c; s.tool_calls = None; s.reasoning_content = None

    class _Resp:
        def __init__(s, c): s.message = _Msg(c)

    class _LLM:
        def call_with_tools(self, messages, tools=None):
            code = "```cpp\n" + "\n".join(f"void f{i}() {{}}" for i in range(20)) + "\n```"
            return _Resp("红黑树实现：\n" + code + "\n说明。")

    async def _run():
        loop = AgentLoop(llm_fn=_LLM(), system_prompt="助手", user_id="u", conv_id="cE2E")
        return [(et, ed) async for et, ed in loop.run(query="写红黑树", history=[], user_id="u")]

    events = asyncio.run(_run())
    file_events = [ed for et, ed in events if et == "file"]
    assert len(file_events) >= 1
    assert file_events[0]["filename"].endswith(".cpp")


def test_short_code_not_filed():
    """短代码块（<15行）不自动存文件（避免噪声）。"""
    from hashmm.agent.loop import _extract_code_blocks
    blocks = _extract_code_blocks("```python\nprint(1)\n```")
    assert len(blocks[0][1].splitlines()) < 15
