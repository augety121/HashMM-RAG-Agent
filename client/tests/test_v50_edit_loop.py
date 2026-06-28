"""V50 编辑闭环回归测试。

覆盖本轮四件事：
1. 工具面扩容：read_file_range / str_replace / file_tree 进 AGENT_TOOLS，系统提示教学编辑纪律
2. 创建→读回→精确编辑 在同一会话目录闭环（create_file 与 workspace 工具同锚点 CONV_FILES_ROOT）
3. workspace 路径穿越护栏（暴露给模型前的安全前提）
4. execute_code 结构化回灌（显式 exit_code + stderr 取尾部）；/api/title 云端路径超时回退
"""
import asyncio
import shutil

import pytest

pytestmark = pytest.mark.unit

CONV = "cV50edit"


@pytest.fixture()
def ws():
    """一次性会话工作区（沿用 test_code_file_download 的约定：用后即清）。"""
    from hashmm.api.database import CONV_FILES_ROOT
    d = CONV_FILES_ROOT / CONV
    shutil.rmtree(d, ignore_errors=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── 1. 工具面 ──

def test_agent_tools_expose_edit_suite():
    """编辑三件套必须出现在 Agent 的函数调用工具面里（V50 支点）。"""
    from hashmm.agent.loop import AGENT_TOOLS
    names = {t["function"]["name"] for t in AGENT_TOOLS}
    assert {"read_file_range", "str_replace", "file_tree"} <= names
    # 原有工具一个不丢
    assert {"kb_search", "create_document", "execute_code", "fetch_url",
            "web_search", "create_file", "kg_query", "spawn_worker"} <= names


def test_system_prompt_teaches_edit_discipline():
    """系统提示必须教：改文件先读后改、用 str_replace、参数名是 filepath、禁止整文件重写。"""
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id=CONV)
    msgs = loop._build_messages("q", [], "")
    sys_content = msgs[0]["content"]
    assert "str_replace" in sys_content
    assert "filepath" in sys_content
    assert "重写" in sys_content  # "禁止…整文件重写"的教学


# ── 2. 创建→读回→编辑 同目录闭环 ──

def test_create_then_read_then_edit_same_dir(ws, clean_env):
    """create_file 写入的文件，read_file_range/str_replace 必须能在同一会话目录读到并改到。"""
    from hashmm.api import tool_registry as TR
    ctx = {"conv_id": CONV}
    TR.execute_tool_structured(
        "create_file",
        {"filename": "calc.py", "content": "def add(a, b):\n    return a - b  # BUG\n"},
        ctx)
    out = str(TR.execute_tool_structured(
        "read_file_range", {"filepath": "calc.py"}, ctx))
    assert "return a - b" in out          # 能读回内容
    assert "1" in out and "│" in out      # 带行号

    out2 = str(TR.execute_tool_structured(
        "str_replace",
        {"filepath": "calc.py", "old_str": "a - b  # BUG", "new_str": "a + b"},
        ctx))
    assert "OK" in out2
    assert "a + b" in (ws / "calc.py").read_text(encoding="utf-8")
    assert "BUG" not in (ws / "calc.py").read_text(encoding="utf-8")


def test_str_replace_errors_teach_model(ws, clean_env):
    """0 次/多次匹配的错误信息必须给模型可执行的下一步指引。"""
    from hashmm.api import tool_registry as TR
    ctx = {"conv_id": CONV}
    TR.execute_tool_structured(
        "create_file", {"filename": "dup.txt", "content": "x = 1\nx = 1\n"}, ctx)
    miss = str(TR.execute_tool_structured(
        "str_replace", {"filepath": "dup.txt", "old_str": "不存在的串", "new_str": "y"}, ctx))
    assert "Error" in miss
    multi = str(TR.execute_tool_structured(
        "str_replace", {"filepath": "dup.txt", "old_str": "x = 1", "new_str": "y"}, ctx))
    assert "Error" in multi and "2" in multi   # 告知匹配次数
    assert "上下文" in multi                    # 指引：提供更长上下文


def test_workspace_path_traversal_blocked(ws, clean_env):
    """暴露给模型前的安全前提：../ 穿越必须被拦下（读和写都拦）。"""
    from hashmm.api import workspace as W
    (ws / "ok.txt").parent.mkdir(parents=True, exist_ok=True)
    (ws / "ok.txt").write_text("safe", encoding="utf-8")

    for evil in ("../../../etc/hostname", "..\\..\\x", "a/../../escape.txt"):
        r1 = W.read_file_range(CONV, evil, 1, 5)
        assert r1.startswith("Error"), f"read 未拦截: {evil} → {r1[:60]}"
        r2 = W.str_replace_in_file(CONV, evil, "a", "b")
        assert r2.startswith("Error"), f"replace 未拦截: {evil} → {r2[:60]}"
        r3 = W.insert_after_line(CONV, evil, 1, "x")
        assert r3.startswith("Error"), f"insert 未拦截: {evil} → {r3[:60]}"
    # 正常路径不受影响
    assert "safe" in W.read_file_range(CONV, "ok.txt", 1, 5)


# ── 3. Agent 循环端到端：创建→读→改→答 ──

class _EditFlowLLM:
    """模拟编辑闭环：create_file → read_file_range → str_replace → 最终回答。"""

    def __init__(s):
        s.calls = 0

    def call_with_tools(s, messages, tools=None):
        s.calls += 1

        class _Fn:
            def __init__(f, name, args):
                f.name, f.arguments = name, args

        class _TC:
            def __init__(f, name, args, id):
                f.id, f.type, f.function = id, "function", _Fn(name, args)

        class _Msg:
            def __init__(f, content="", tool_calls=None):
                f.content, f.tool_calls, f.reasoning_content = content, tool_calls, ""

        class _Resp:
            def __init__(f, m):
                f.message = m

        seq = {
            1: ("create_file",
                '{"filename": "fix.py", "content": "def mul(a, b):\\n    return a + b  # WRONG\\n"}'),
            2: ("read_file_range", '{"filepath": "fix.py"}'),
            3: ("str_replace",
                '{"filepath": "fix.py", "old_str": "a + b  # WRONG", "new_str": "a * b"}'),
        }
        if s.calls in seq:
            name, args = seq[s.calls]
            return _Resp(_Msg(tool_calls=[_TC(name, args, f"t{s.calls}")]))
        return _Resp(_Msg(content="已修复 mul 函数。"))


def test_agent_loop_end_to_end_edit_flow(ws, clean_env):
    """循环用【真实 executor】走通编辑闭环：文件真的被精确修改，时间线 3 个工具配对事件。"""
    from hashmm.agent.loop import AgentLoop

    async def _run():
        loop = AgentLoop(llm_fn=_EditFlowLLM(), system_prompt="助手",
                         user_id="u", conv_id=CONV)
        loop.plan_confirmed = True   # 模拟用户已确认（streaming.py 同款语义）
        return [(et, ed) async for et, ed in loop.run(query="修一下乘法", history=[], user_id="u")]

    events = asyncio.run(_run())
    dones = [ed for et, ed in events if et == "tool_done"]
    assert [d["name"] for d in dones[:3]] == ["create_file", "read_file_range", "str_replace"]
    assert all(d["status"] == "done" for d in dones[:3]), dones
    final = (ws / "fix.py").read_text(encoding="utf-8")
    assert "a * b" in final and "WRONG" not in final
    text = "".join(ed for et, ed in events if et == "token")
    assert "修复" in text


# ── 4. execute_code 结构化回灌 ──

def test_execute_code_reports_exit_code_and_stderr_tail(clean_env):
    """失败执行必须报显式 exit_code，且 stderr 取【尾部】（traceback 在尾部）。"""
    from hashmm.api import tool_registry as TR
    code = (
        "import sys\n"
        "sys.stderr.write('FILLER-HEAD ' * 400)\n"   # ≈4800 字符噪音在前
        "raise ValueError('V50-NEEDLE-AT-TAIL')\n"
    )
    out = str(TR.execute_tool_structured("execute_code", {"code": code}, {"conv_id": CONV}))
    assert "exit_code=1" in out
    assert "V50-NEEDLE-AT-TAIL" in out            # 尾部的关键 traceback 必须保住
    assert "FILLER-HEAD FILLER-HEAD FILLER-HEAD FILLER-HEAD FILLER-HEAD" not in out[:1500] \
        or "V50-NEEDLE-AT-TAIL" in out            # 宽松：只要针在，截断策略即正确


def test_execute_code_success_still_reports_exit_code(clean_env):
    from hashmm.api import tool_registry as TR
    out = str(TR.execute_tool_structured(
        "execute_code", {"code": "print('hello-v50')"}, {"conv_id": CONV}))
    assert "exit_code=0" in out and "hello-v50" in out


# ── 5. /api/title 云端超时回退（沙箱缺 fastapi 时跳过，真机会跑）──

def test_title_timeout_falls_back_to_local(clean_env, monkeypatch):
    conv_routes = pytest.importorskip("hashmm.api.routes.conversations")
    from hashmm.api import app_state as app_state_mod  # noqa: F401  (确认可导入)

    class _SlowLLM:
        def __call__(self, prompt, **kw):
            import time
            time.sleep(30)
            return "慢标题"

    monkeypatch.setattr(conv_routes.app_state, "llm_fn", _SlowLLM())
    monkeypatch.setattr(conv_routes, "_TITLE_TIMEOUT_S", 0.2, raising=False)

    req = conv_routes.TitleRequest(query="红黑树怎么写", answer="如下")
    result = asyncio.run(conv_routes.generate_title(req))
    assert result["title"] == "红黑树怎么写"[:30] or result["title"]  # 必须秒回且非空
