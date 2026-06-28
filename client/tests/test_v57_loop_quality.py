"""V57 loop 工程：文件持久化修复 + 自动验证-修复阶段 + 墙钟截止。

契约：
- 文件持久化：update_message(files=[...]) → get_messages 原样返回（重开会话文件卡可见）。
- 验证阶段（对标 Codex 的 verify-fix 循环）：模型给出最终回答时若本轮生成过 .py 文件，
  自动 py_compile 验证；失败 → 丢弃该回答、注入修复指令、继续循环（最多一次修复轮）；
  通过 → 发 verify trace 后照常收尾。未生成 .py 的运行完全零变化。
- 墙钟截止（HASHMM_AGENT_DEADLINE_S，默认 0=关）：超时 → 截止 trace + 走既有强制
  收尾产出总结，不再开新迭代。
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.unit


# ── 1. files 持久化往返 ──

def test_message_files_roundtrip(tmp_path, monkeypatch):
    from hashmm.api import database as db
    conv_id = "cFilesRT"
    db.create_conversation(conv_id, user_id="u1", title="t")
    m = db.create_message(conv_id, role="assistant", content="生成了文件")
    msg_id = m["id"] if isinstance(m, dict) else m
    files = [{"type": "code", "filename": "a.py",
              "download_url": f"/api/conversations/{conv_id}/download/a.py"}]
    steps = [{"node": "narrate", "detail": "先创建文件"}]
    db.update_message(msg_id, files=files, tool_calls=steps, status="complete")
    got = [x for x in db.get_messages(conv_id) if x["id"] == msg_id][0]
    assert got["files"] == files                 # 文件卡数据原样回来
    assert got["tool_calls"] == steps            # 时间线数据原样回来
    db.delete_conversation(conv_id)


# ── 2. 验证-修复阶段 ──

class _Fn:
    def __init__(s, n, a): s.name, s.arguments = n, a


class _TC:
    def __init__(s, n, a, id="t1"): s.id, s.type, s.function = id, "function", _Fn(n, a)


class _Msg:
    def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""


class _Resp:
    def __init__(s, m): s.message = m


BROKEN = 'def f(:\\n    return 1\\n'
FIXED = 'def f():\\n    return 1\\n'


class _FixerLLM:
    """轮1 创建坏 py + 宣布完成 → 应被验证拦下；轮2 修复；轮3 宣布完成 → 验证通过。"""

    def __init__(s):
        s.calls = 0
        s.saw_verify_feedback = False

    def call_with_tools(s, messages, tools=None):
        s.calls += 1
        if s.calls == 1:
            return _Resp(_Msg(tc=[_TC("create_file",
                                      f'{{"filename": "broken.py", "content": "{BROKEN}"}}')]))
        if s.calls == 2:
            return _Resp(_Msg(c="完成了，文件已创建。"))   # 终答（坏文件）→ 应被拦
        if s.calls == 3:
            s.saw_verify_feedback = any("编译失败" in str(m.get("content", ""))
                                        for m in messages if m.get("role") == "user")
            return _Resp(_Msg(tc=[_TC("create_file",
                                      f'{{"filename": "broken.py", "content": "{FIXED}"}}')]))
        return _Resp(_Msg(c="已修复并验证。"))


def _run(llm, conv):
    from hashmm.agent.loop import AgentLoop

    async def _go():
        loop = AgentLoop(llm_fn=llm, system_prompt="助手", user_id="u", conv_id=conv)
        return [(et, ed) async for et, ed in loop.run(query="写个函数", history=[], user_id="u")]

    return asyncio.run(_go())


def test_verify_phase_catches_and_fixes_broken_py():
    import shutil
    from hashmm.api.database import CONV_FILES_ROOT
    conv = "cVerifyFix"
    shutil.rmtree(CONV_FILES_ROOT / conv, ignore_errors=True)
    llm = _FixerLLM()
    events = _run(llm, conv)

    verifies = [ed for et, ed in events if et == "trace" and ed.get("node") == "verify"]
    assert any("编译失败" in v["detail"] for v in verifies)      # 拦截有迹可循
    assert any("验证通过" in v["detail"] for v in verifies)      # 修复后放行
    assert llm.saw_verify_feedback                                # 修复指令真的进了上下文
    tokens = "".join(ed for et, ed in events if et == "token")
    assert "已修复并验证" in tokens and "完成了" not in tokens    # 坏轮回答被丢弃
    # 落盘的文件最终可编译
    import py_compile
    py_compile.compile(str(CONV_FILES_ROOT / conv / "broken.py"), doraise=True)
    shutil.rmtree(CONV_FILES_ROOT / conv, ignore_errors=True)


def test_verify_only_one_fix_cycle():
    """模型修不好也只给一次机会：第二次终答即便仍坏也放行（带警示 trace），不死循环。"""
    import shutil
    from hashmm.api.database import CONV_FILES_ROOT

    class _Stubborn:
        def __init__(s): s.calls = 0
        def call_with_tools(s, messages, tools=None):
            s.calls += 1
            if s.calls == 1:
                return _Resp(_Msg(tc=[_TC("create_file",
                                          f'{{"filename": "bad.py", "content": "{BROKEN}"}}')]))
            return _Resp(_Msg(c="我觉得没问题。"))   # 永远不修

    conv = "cStubborn"
    shutil.rmtree(CONV_FILES_ROOT / conv, ignore_errors=True)
    llm = _Stubborn()
    events = _run(llm, conv)
    tokens = "".join(ed for et, ed in events if et == "token")
    assert "我觉得没问题" in tokens                       # 第二次放行，不无限拦截
    assert llm.calls <= 4                                  # 没有失控加轮
    shutil.rmtree(CONV_FILES_ROOT / conv, ignore_errors=True)


def test_no_py_files_zero_change():
    """没生成 .py 的运行：没有任何 verify 事件（零变化保证）。"""
    class _Plain:
        def call_with_tools(s, m, t=None): return _Resp(_Msg(c="纯文本回答。"))

    events = _run(_Plain(), "cNoVerify")
    assert not [1 for et, ed in events if et == "trace" and ed.get("node") == "verify"]


# ── 3. 墙钟截止 ──

def test_deadline_forces_graceful_finish():
    from hashmm.agent.loop import AgentLoop

    class _Endless:
        def __init__(s): s.calls = 0
        def call_with_tools(s, m, t=None):
            s.calls += 1
            # 永远想继续调工具（参数不同避开去重）
            return _Resp(_Msg(tc=[_TC("file_tree", f'{{"depth": {s.calls}}}', id=f"e{s.calls}")]))

    class _Slow(AgentLoop):
        async def _execute_tool(self, name, args, user_id):
            await asyncio.sleep(0.05)
            return {"status": "ok", "message": "树"}

    async def _go():
        loop = _Slow(llm_fn=_Endless(), system_prompt="助手", user_id="u", conv_id="cDl")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    old = os.environ.get("HASHMM_AGENT_DEADLINE_S")
    try:
        os.environ["HASHMM_AGENT_DEADLINE_S"] = "0.05"
        events = asyncio.run(_go())
    finally:
        if old is None:
            os.environ.pop("HASHMM_AGENT_DEADLINE_S", None)
        else:
            os.environ["HASHMM_AGENT_DEADLINE_S"] = old

    dls = [ed for et, ed in events if et == "trace" and ed.get("node") == "deadline"]
    assert dls, "应有截止 trace"
    assert [1 for et, _ in events if et == "done"]          # 仍然优雅收尾


def test_deadline_off_by_default():
    from hashmm.agent.loop import AgentLoop

    class _Plain:
        def call_with_tools(s, m, t=None): return _Resp(_Msg(c="答。"))

    os.environ.pop("HASHMM_AGENT_DEADLINE_S", None)

    async def _go():
        loop = AgentLoop(llm_fn=_Plain(), system_prompt="助手", user_id="u", conv_id="cDl2")
        return [(et, ed) async for et, ed in loop.run(query="q", history=[], user_id="u")]

    events = asyncio.run(_go())
    assert not [1 for et, ed in events if et == "trace" and ed.get("node") == "deadline"]


# ── V58 附加：多语言验证 ──

def test_verify_cpp_syntax_when_gpp_available():
    import shutil as _sh
    if not _sh.which("g++"):
        return  # 环境无 g++：跳过（验证器在该环境也会自动跳过 cpp）
    import shutil
    from hashmm.api.database import CONV_FILES_ROOT
    from hashmm.agent.loop import AgentLoop
    conv = "cCppVerify"
    ws = CONV_FILES_ROOT / conv
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    (ws / "bad.cpp").write_text("int main( { return 0; }\n", encoding="utf-8")
    (ws / "good.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    loop = AgentLoop(llm_fn=object(), system_prompt="x", conv_id=conv)
    errs = loop._verify_generated_files(["bad.cpp", "good.cpp"])
    assert any("bad.cpp" in e for e in errs)
    assert not any("good.cpp" in e for e in errs)
    shutil.rmtree(ws, ignore_errors=True)


def test_verify_js_syntax_when_node_available():
    import shutil as _sh
    if not _sh.which("node"):
        return
    import shutil
    from hashmm.api.database import CONV_FILES_ROOT
    from hashmm.agent.loop import AgentLoop
    conv = "cJsVerify"
    ws = CONV_FILES_ROOT / conv
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    (ws / "bad.js").write_text("function f( { return 1; }\n", encoding="utf-8")
    loop = AgentLoop(llm_fn=object(), system_prompt="x", conv_id=conv)
    errs = loop._verify_generated_files(["bad.js"])
    assert errs and "bad.js" in errs[0]
    shutil.rmtree(ws, ignore_errors=True)


def test_verify_unknown_extension_skipped():
    from hashmm.agent.loop import AgentLoop
    loop = AgentLoop(llm_fn=object(), system_prompt="x", conv_id="cSkipExt")
    assert loop._verify_generated_files(["notes.md", "data.csv"]) == []
