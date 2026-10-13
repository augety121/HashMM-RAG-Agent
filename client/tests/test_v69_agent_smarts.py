"""V69 Agent 智能化：head+tail 截断、检索自适应指引、agentic 系统提示。"""
import pytest

pytestmark = pytest.mark.unit


def _mk_loop():
    from hashmm.agent.loop import AgentLoop
    return AgentLoop.__new__(AgentLoop)   # 不跑 __init__（纯逻辑方法测试）


class _Turn:
    pass


def test_clip_head_tail():
    from hashmm.agent.loop import AgentLoop
    t = AgentLoop._clip("A" * 5000 + "TAIL", 4200, 600)
    assert t.startswith("A" * 50) and t.endswith("TAIL") and "中段省略" in t
    assert AgentLoop._clip("short", 4200, 600) == "short"
    # 边界：刚好不超不截
    s = "B" * (4200 + 600 + 50)
    assert AgentLoop._clip(s, 4200, 600) == s


def test_format_tool_result_keeps_tail():
    loop = _mk_loop()
    long_str = "X" * 6000 + "FINAL_SUMMARY"
    out = loop._format_tool_result(long_str)
    assert out.endswith("FINAL_SUMMARY")          # 尾部总结不能丢
    assert len(out) < 6000


def test_retrieval_guidance_triggers_on_empty():
    loop = _mk_loop()
    turn = _Turn()
    g = loop._retrieval_guidance("kb_search", "", turn)
    assert g and "检索质量提示" in g
    assert turn.retrieval_guided is True


def test_retrieval_guidance_triggers_on_not_found():
    loop = _mk_loop()
    g = loop._retrieval_guidance("kb_search", "知识库中未找到相关内容。", _Turn())
    assert g is not None


def test_retrieval_guidance_once_per_turn():
    loop = _mk_loop()
    turn = _Turn()
    assert loop._retrieval_guidance("kb_search", "", turn) is not None
    assert loop._retrieval_guidance("kb_search", "", turn) is None   # 第二次不再注入


def test_retrieval_guidance_skips_good_results():
    loop = _mk_loop()
    good = "找到 5 条结果：" + "相关内容片段，包含详细的技术说明与出处。" * 10
    assert loop._retrieval_guidance("kb_search", good, _Turn()) is None


def test_retrieval_guidance_only_kb_search():
    loop = _mk_loop()
    assert loop._retrieval_guidance("web_search", "", _Turn()) is None
    assert loop._retrieval_guidance("execute_code", "未找到", _Turn()) is None


def test_agentic_prompt_sections():
    loop = _mk_loop()
    sp = loop._default_system_prompt()
    assert "Agentic 工作准则" in sp
    assert "坚持完成" in sp and "先计划后行动" in sp and "检索自适应" in sp


def test_check_one_file_html_and_json(tmp_path):
    from hashmm.agent.loop import AgentLoop
    good_html = tmp_path / "ok.html"
    good_html.write_text("<!DOCTYPE html><html><body><p>hi</p></body></html>", encoding="utf-8")
    assert AgentLoop._check_one_file(good_html) == ""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    assert AgentLoop._check_one_file(bad_json) != ""
    good_json = tmp_path / "ok.json"
    good_json.write_text('{"a": 1}', encoding="utf-8")
    assert AgentLoop._check_one_file(good_json) == ""


def test_error_guidance_on_hard_failure():
    loop = _mk_loop()
    turn = _Turn()
    g = loop._error_guidance("execute_code", "error", {"message": "权限不足"}, turn)
    assert g and "工具失败提示" in g and "权限不足" in g
    assert turn.error_guided_count == 1


def test_error_guidance_capped_per_turn():
    loop = _mk_loop()
    turn = _Turn()
    assert loop._error_guidance("t", "error", {}, turn) is not None
    assert loop._error_guidance("t", "error", {}, turn) is not None
    assert loop._error_guidance("t", "error", {}, turn) is None   # 第 3 次熔断


def test_error_guidance_skips_success_and_denied():
    loop = _mk_loop()
    assert loop._error_guidance("t", "done", {}, _Turn()) is None
    assert loop._error_guidance("t", "denied", {}, _Turn()) is None


def test_html_wellformed_uses_attr_access(tmp_path):
    """V71 回归：评分器必须用 ctx.workspace 属性（真机 ERROR 的根因是下标访问）。"""
    from hashmm.tools.agent_bench import html_wellformed, BenchContext
    ctx = BenchContext(conv_id="t", workspace=tmp_path)
    (tmp_path / "p.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    ok, _ = html_wellformed("p.html")(ctx)
    assert ok
    ok, why = html_wellformed("nope.html")(ctx)
    assert not ok and "不存在" in why


def test_scorer_exception_is_fail_not_error(tmp_path):
    """V71: 评分器抛异常 → 任务 FAIL（带原因），不是 ERROR。"""
    from hashmm.tools import agent_bench as ab

    def bomb(_ctx):
        raise TypeError("boom")
    bomb.__name__ = "bomb"

    task = ab.Task("t_bomb", "防御", turns=["q"], scorers=[bomb])
    # 假 llm：直接返回空回答（_run_turn 由脚本化 runner 驱动——这里直接打桩 run 的内部）
    import asyncio
    orig = ab._run_turn
    async def fake_run_turn(llm_fn, conv_id, q, history, **kwargs):  # V308: **kwargs 兼容 mem/user/inject_hints 等新增参数
        return ab.TurnResult(answer="done", events=[])
    ab._run_turn = fake_run_turn
    try:
        r = ab.run_task(task, llm_fn=None)
    finally:
        ab._run_turn = orig
    assert r["status"] == "FAIL", r
    assert any("评分器异常" in f for f in r["failures"])
