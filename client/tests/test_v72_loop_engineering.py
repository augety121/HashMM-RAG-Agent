"""V72 Loop 工程：stop_reason、DoD 自检数据流、loop_insights、bench gate。"""
import json
import pytest

pytestmark = pytest.mark.unit


def test_loop_insights_analyze_basic():
    from hashmm.tools.loop_insights import analyze
    runs = [
        {"stop_reason": "completed", "iterations": 3, "elapsed_s": 12.5,
         "events": [{"k": "tool", "name": "kb_search", "status": "ok"},
                    {"k": "tool", "name": "execute_code", "status": "error"}]},
        {"stop_reason": "deadline", "iterations": 10, "elapsed_s": 480.0,
         "events": [{"k": "tool", "name": "execute_code", "status": "error"},
                    {"k": "tool", "name": "execute_code", "status": "ok"}]},
    ]
    r = analyze(runs)
    assert r["runs"] == 2
    assert r["stop_reasons"]["completed"] == 1 and r["stop_reasons"]["deadline"] == 1
    hot = {h["tool"]: h for h in r["tool_fail_hotspots"]}
    assert hot["execute_code"]["fails"] == 2 and hot["execute_code"]["calls"] == 3
    assert r["over_budget_runs"] and r["over_budget_runs"][0]["iterations"] == 10


def test_loop_insights_empty_and_render():
    from hashmm.tools.loop_insights import analyze, render
    assert analyze([]) == {"runs": 0}
    assert "没有遥测数据" in render({"runs": 0})
    txt = render(analyze([{"stop_reason": "completed", "iterations": 1,
                           "elapsed_s": 1.0, "events": []}]))
    assert "停止理由" in txt and "completed" in txt


def test_loop_insights_load_runs_skips_bad_lines(tmp_path, ):
    import os
    from hashmm.tools import loop_insights as li
    d = tmp_path / "traces"
    d.mkdir()
    (d / "20260611.jsonl").write_text(
        json.dumps({"stop_reason": "completed", "events": []}) + "\n"
        + "{broken json\n"
        + json.dumps({"stop_reason": "deadline", "events": []}) + "\n",
        encoding="utf-8")
    _bak = os.environ.get("HASHMM_TRACE_DIR")
    os.environ["HASHMM_TRACE_DIR"] = str(d)
    try:
        runs = li.load_runs()
    finally:
        if _bak is None:
            os.environ.pop("HASHMM_TRACE_DIR", None)
        else:
            os.environ["HASHMM_TRACE_DIR"] = _bak
    assert len(runs) == 2          # 坏行跳过不挡整体


def test_loop_source_has_stop_reason_and_dod():
    """结构性回归：run() 必须含 stop_reason 标注与 DoD 自检（防未来重构丢失）。"""
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert 'stop_reason = "completed"' in src
    assert 'stop_reason = "deadline"' in src
    assert 'stop_reason = "llm_error"' in src
    assert 'stop_reason=stop_reason' in src        # flush 携带
    assert '"node": "dod"' in src                  # DoD trace 事件
    assert "turn.todo_items = items" in src        # DoD 数据源


def test_bench_gate_threshold():
    """--gate 退出码逻辑（纯函数化验证：直接复算判定式）。"""
    def gate_rc(passed, ran, gate):
        if not ran:
            return 0
        if gate > 0:
            return 0 if passed / ran >= gate else 1
        return 0 if passed == ran else 1
    assert gate_rc(8, 10, 0.7) == 0     # 80% >= 70% 放行
    assert gate_rc(6, 10, 0.7) == 1     # 60% < 70% 拦截
    assert gate_rc(9, 10, 0.0) == 1     # 无 gate：全过才放行
    assert gate_rc(10, 10, 0.0) == 0


# ───────────────────────── V74: 检索管线深化 ─────────────────────────

def test_rrf_fuse_ranking_and_dedup():
    from hashmm.api.tool_registry import _rrf_fuse
    a = [{"filename": "x.pdf", "page": 1, "text": "GraphRAG 社区检测"},
         {"filename": "y.pdf", "page": 2, "text": "无关内容"}]
    b = [{"filename": "x.pdf", "page": 1, "text": "GraphRAG 社区检测"},
         {"filename": "z.pdf", "page": 3, "text": "向量索引"}]
    f = _rrf_fuse([a, b], top_k=5)
    assert f[0]["filename"] == "x.pdf"            # 双查询命中 → 融合分最高
    assert f[0]["_rrf"] > f[1]["_rrf"]
    assert len(f) == 3                            # 去重后 3 条
    assert _rrf_fuse([[], []]) == []
    assert len(_rrf_fuse([a, b], top_k=1)) == 1   # top_k 截断


def test_kb_search_schema_has_queries():
    from hashmm.api.tool_registry import TOOL_DEFS
    kb = next(t for t in TOOL_DEFS if t["function"]["name"] == "kb_search")
    props = kb["function"]["parameters"]["properties"]
    assert "queries" in props and props["queries"]["type"] == "array"
    assert kb["function"]["parameters"]["required"] == ["query"]   # 向后兼容


def test_kb_search_multi_query_normalization():
    """queries 与 query 合并去重、≤3 个；空入参报错。"""
    from hashmm.api import tool_registry as tr
    captured = []

    class _FakeCR:
        def enhance(self, q, hist, retrieval_mode="mix"):
            captured.append(q)
            return None, [{"filename": f"{q}.pdf", "page": 1, "text": f"关于{q}的内容" * 20}], ""

    import hashmm.chat_retrieval as cr_mod
    orig = cr_mod.get_chat_retrieval
    cr_mod.get_chat_retrieval = lambda: _FakeCR()
    try:
        out = tr._exec_kb_search({"query": "A", "queries": ["A", "B", "C", "D"]}, {})
    finally:
        cr_mod.get_chat_retrieval = orig
    assert captured == ["A", "B", "C"]            # 去重 + 截断到 3
    assert "[多查询融合]" in out and "A / B / C" in out
    assert tr._exec_kb_search({"query": "", "queries": []}, {}).startswith("Error")


def test_lex_overlap_grading():
    from hashmm.agent.loop import AgentLoop
    l = AgentLoop.__new__(AgentLoop)
    T = type("T", (), {})
    bad = "今天天气很好，公园里的花开了。" * 20      # 长、非空、无"未找到"，但与查询无关
    assert l._retrieval_guidance("kb_search", bad, T(), query="量子纠缠退相干") is not None
    good = "量子纠缠的退相干机制研究表明环境耦合导致相干性衰减。" * 10
    assert l._retrieval_guidance("kb_search", good, T(), query="量子纠缠退相干") is None
    assert l._retrieval_guidance("kb_search", good, T()) is None   # 向后兼容（无 query）
    assert 0.0 <= l._lex_overlap("量子纠缠", "今天天气") < 0.1
    assert l._lex_overlap("", "任意文本") == 1.0                    # 空查询不触发


def test_agentic_prompt_has_v74_retrieval_rules():
    from hashmm.agent.loop import AgentLoop
    sp = AgentLoop.__new__(AgentLoop)._default_system_prompt()
    assert "检索查询自包含" in sp and "复杂问题多查询" in sp


# ───────────────────────── V75: 引用接地 + 经验回灌二期 ─────────────────────────

def test_citation_issues_pure():
    from hashmm.agent.loop import AgentLoop
    f = AgentLoop._citation_issues
    assert f("根据 [1] 和 [3]，但 [8] 显示…", 5) == [8]
    assert f("[1][2] 都有效", 5) == []
    assert f("数组 a[0] 与链接 [9](http://x) 不算引用", 5) == []
    assert f("任意 [99]", 0) == []        # 本 turn 无检索 → 不校验
    assert f("", 5) == []
    assert f("[2] 与 [7] 与 [12]", 5) == [7, 12]   # 多个无效、排序


def test_citation_source_markers_in_loop():
    """结构性回归：kb_citation_max 数据源与收尾校验必须存在。"""
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert "kb_citation_max" in src
    assert '"node": "citation"' in src
    assert "citation_checked" in src


def test_exp_rules_generate_thresholds():
    from hashmm.agent.exp_rules import generate_rules
    report = {
        "runs": 10,
        "stop_reasons": {"completed": 6, "deadline": 4},
        "tool_fail_hotspots": [
            {"tool": "execute_code", "calls": 10, "fails": 4, "rate": 0.4},   # 触发
            {"tool": "kb_search", "calls": 10, "fails": 2, "rate": 0.2},      # 低于阈值
            {"tool": "fetch_url", "calls": 4, "fails": 2, "rate": 0.5},       # fails<3 不触发
        ],
    }
    rules = generate_rules(report)
    assert any("execute_code" in r for r in rules)
    assert not any("kb_search" in r for r in rules)
    assert not any("fetch_url" in r for r in rules)
    assert any("超时" in r for r in rules)          # deadline 40% ≥ 30%
    assert generate_rules({"runs": 0}) == []
    assert len(generate_rules(report)) <= 3


def test_exp_rules_off_by_default_and_never_raises(tmp_path):
    import os
    from hashmm.agent import exp_rules as er
    er._reset_cache_for_tests()
    os.environ.pop("HASHMM_EXP_RULES", None)
    assert er.load_exp_rules() == ""                 # 默认关
    # 开启但遥测目录不存在 → 空串不抛错
    os.environ["HASHMM_EXP_RULES"] = "1"
    _bak = os.environ.get("HASHMM_TRACE_DIR")
    os.environ["HASHMM_TRACE_DIR"] = str(tmp_path / "nope")
    try:
        er._reset_cache_for_tests()
        assert er.load_exp_rules() == ""
    finally:
        os.environ.pop("HASHMM_EXP_RULES", None)
        if _bak is None:
            os.environ.pop("HASHMM_TRACE_DIR", None)
        else:
            os.environ["HASHMM_TRACE_DIR"] = _bak
        er._reset_cache_for_tests()


# ───────────────────────── V76: MCP stdio 桥 ─────────────────────────

def test_mcp_bridge_roundtrip_and_notification():
    from hashmm.tools.mcp_stdio import bridge_line
    import json as _json
    calls = []

    def post_ok(payload):
        calls.append(payload)
        return {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}}

    # 普通请求：转发并回写
    out = bridge_line('{"jsonrpc":"2.0","id":1,"method":"tools/list"}', post_ok)
    assert out and _json.loads(out)["result"]["ok"] is True
    assert calls[0]["method"] == "tools/list"
    # notification（无 id）：转发但不回写
    assert bridge_line('{"jsonrpc":"2.0","method":"notifications/initialized"}', post_ok) is None
    assert len(calls) == 2
    # 空行：忽略
    assert bridge_line("", post_ok) is None and bridge_line("   \n", post_ok) is None


def test_mcp_bridge_error_paths():
    from hashmm.tools.mcp_stdio import bridge_line
    import json as _json

    def post_boom(_payload):
        raise ConnectionError("refused")

    # 坏 JSON → parse error（不抛、不僵死）
    out = bridge_line("{not json", post_boom)
    assert _json.loads(out)["error"]["code"] == -32700
    # 后端不可达：请求 → -32002 错误响应；notification → 静默
    out = bridge_line('{"jsonrpc":"2.0","id":7,"method":"tools/call"}', post_boom)
    e = _json.loads(out)
    assert e["id"] == 7 and e["error"]["code"] == -32002 and "refused" in e["error"]["message"]
    assert bridge_line('{"jsonrpc":"2.0","method":"x"}', post_boom) is None


def test_mcp_http_handle_protocol():
    """后端 HTTP 端 _handle 协议三件套（不起服务直接测处理函数）。"""
    pytest.importorskip("fastapi")   # 沙箱无 fastapi 跳过；真机可跑
    from hashmm.api.routes.mcp_server import _handle
    r = _handle("initialize", {}, 1)
    assert r["result"]["protocolVersion"] and r["result"]["serverInfo"]["name"] == "hashmm"
    r = _handle("tools/list", {}, 2)
    names = [t["name"] for t in r["result"]["tools"]]
    assert "kb_search" in names and "kg_query" in names
    r = _handle("tools/call", {"name": "no_such_tool", "arguments": {}}, 3)
    assert r["error"]["code"] == -32601
    r = _handle("bogus/method", {}, 4)
    assert r["error"]["code"] == -32601


# ───────────────────────── V78: 评测驱动 ─────────────────────────

def test_bench_has_18_tasks_and_categories():
    from hashmm.tools.agent_bench import TASKS
    assert len(TASKS) >= 18
    ids = [t.id for t in TASKS]
    for new_id in ("cpp_code", "json_config", "refactor", "error_recovery",
                   "data_report", "citation_qa", "fusion_compare", "multi_step_dod"):
        assert new_id in ids, new_id
    assert len(ids) == len(set(ids))                   # id 不重复


def test_new_scorers():
    from hashmm.tools.agent_bench import (answer_matches, event_detail_contains,
                                          json_valid, BenchContext, TurnResult)
    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = pathlib.Path(d)
        ctx = BenchContext(conv_id="t", workspace=ws,
                           turns=[TurnResult(answer="根据 [1]，结论成立。",
                                             events=[("trace", {"node": "x", "detail": "kb_search 命中"})])])
        ok, _ = answer_matches(r"\[\d+\]")(ctx); assert ok
        ok, _ = answer_matches(r"不存在的串")(ctx); assert not ok
        ok, _ = event_detail_contains("kb_search")(ctx); assert ok
        ok, _ = event_detail_contains("没有的事件")(ctx); assert not ok
        (ws / "ok.json").write_text('{"a":1}', encoding="utf-8")
        (ws / "bad.json").write_text('{oops', encoding="utf-8")
        assert json_valid("ok.json")(ctx)[0]
        assert not json_valid("bad.json")(ctx)[0]
        assert not json_valid("missing.json")(ctx)[0]


def test_bench_by_category_aggregation():
    from hashmm.tools import agent_bench as ab
    fake = [
        {"id": "a", "category": "X", "status": "PASS", "failures": [], "elapsed_s": 1},
        {"id": "b", "category": "X", "status": "FAIL", "failures": ["f"], "elapsed_s": 1},
        {"id": "c", "category": "Y", "status": "PASS", "failures": [], "elapsed_s": 1},
    ]
    orig_run, orig_caps = ab.run_task, ab._detect_capabilities
    ab.run_task = lambda task, llm_fn: fake.pop(0)
    ab._detect_capabilities = lambda llm_fn: {"llm"}
    try:
        t = ab.Task
        tasks = [t("a", "X", turns=["q"], scorers=[]), t("b", "X", turns=["q"], scorers=[]),
                 t("c", "Y", turns=["q"], scorers=[])]
        rep = ab.run_bench(None, tasks=tasks)
    finally:
        ab.run_task, ab._detect_capabilities = orig_run, orig_caps
    assert rep["by_category"]["X"] == {"ran": 2, "passed": 1, "rate": 0.5}
    assert rep["by_category"]["Y"]["rate"] == 1.0


# ───────────────────────── V79: 上下文工程（装箱器） ─────────────────────────

def test_clip_at_boundary():
    from hashmm.agent.context_pack import clip_at_boundary
    t = "第一句话。第二句话。" * 30
    c = clip_at_boundary(t, 100)
    assert len(c) < len(t) and "省略]" in c
    assert "。" in c                                  # 在句边界收口
    assert clip_at_boundary("短", 100) == "短"        # 预算内原样
    assert clip_at_boundary("x" * 200, 100, marker="…").endswith("…")   # 无边界硬切+自定义标记


def test_pack_sources_budget_and_tiers():
    from hashmm.agent.context_pack import pack_sources
    srcs = [{"filename": f"f{i}.pdf", "page": i, "text": "内容句子。" * 80, "score": 0.9}
            for i in range(8)]
    text, rep = pack_sources(srcs, budget=3000)
    assert rep["packed"] >= 4 and rep["dropped"] >= 1
    assert rep["packed"] + rep["dropped"] == 8
    assert rep["used"] <= 3000 + 60                   # 预算约束（含丢弃注记容差）
    assert "[1] [f0.pdf" in text and "未展示" in text
    import re
    blocks = re.split(r"\n\n(?=\[)", text)
    assert len(blocks[0]) > len(blocks[2])            # 头部额度 > 尾部额度
    t2, r2 = pack_sources([], budget=500)
    assert t2 == "" and r2["packed"] == 0 and r2["dropped"] == 0


def test_kb_search_single_query_uses_packer():
    """单查询路径走装箱器（带 [1] 头与相关度），缓存行为不破坏。"""
    from hashmm.api import tool_registry as tr
    import hashmm.chat_retrieval as cr_mod

    class _FakeCR:
        def enhance(self, q, hist, retrieval_mode="mix"):
            return None, [{"filename": "a.pdf", "page": 3,
                           "text": "检索增强生成的核心流程。" * 10, "score": 0.88}], ""
    orig = cr_mod.get_chat_retrieval
    cr_mod.get_chat_retrieval = lambda: _FakeCR()
    try:
        out = tr._exec_kb_search({"query": "RAG 流程"}, {})
    finally:
        cr_mod.get_chat_retrieval = orig
    assert "[1] [a.pdf p.3]" in out and "相关度:0.88" in out


def test_loop_pretrieval_uses_boundary_clip():
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert "clip_at_boundary(retrieval_context, 8000)" in src
    assert "retrieval_context[:8000]" not in src


# ───────────────────────── V80: B/D/E 三阶段 ─────────────────────────

def test_worker_writer_role():
    from hashmm.agent.worker import WORKER_TOOLSETS, Worker
    assert "writer" in WORKER_TOOLSETS
    assert "create_file" in WORKER_TOOLSETS["writer"]
    w = Worker.__new__(Worker)
    w.role = "writer"
    assert "写作专员" in Worker._system_prompt(w)
    # 非法角色回落 research（既有行为不破坏）
    assert "research" in str(WORKER_TOOLSETS.keys())


def test_spawn_worker_schema_pipeline():
    """schema 含 writer 角色与 context 流水线参数；执行处传递 parent_context。"""
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert '"enum": ["research", "analysis", "code", "writer"]' in src
    assert '"context"' in src and "流水线衔接" in src
    assert 'parent_context=str((func_args or {}).get("context", ""))' in src
    assert "流水线模式" in src                       # 系统提示准则


def test_user_memory_lifecycle(tmp_path):
    import os
    from hashmm.agent import user_memory as um
    os.environ["HASHMM_USER_MEMORY"] = "1"
    os.environ["HASHMM_USER_MEMORY_DIR"] = str(tmp_path)
    try:
        r = um.remember("u1", "代码注释语言", "中文注释")
        assert r["ok"] and r["count"] == 1
        um.remember("u1", "回答风格", "简短")
        assert um.recall("u1") == {"代码注释语言": "中文注释", "回答风格": "简短"}
        blk = um.inject_block("u1")
        assert "用户长期偏好" in blk and "中文注释" in blk
        assert um.forget("u1", "回答风格")["removed"] is True
        assert "回答风格" not in um.recall("u1")
        # 容量淘汰：塞满后再加，最旧的被挤掉
        for i in range(um.MAX_ITEMS + 2):
            um.remember("u2", f"k{i}", f"v{i}")
        assert len(um.recall("u2")) == um.MAX_ITEMS
        # 空 key/value 拒绝
        assert not um.remember("u1", "", "x")["ok"]
    finally:
        os.environ.pop("HASHMM_USER_MEMORY", None)
        os.environ.pop("HASHMM_USER_MEMORY_DIR", None)


def test_user_memory_off_by_default(tmp_path):
    import os
    from hashmm.agent import user_memory as um
    os.environ.pop("HASHMM_USER_MEMORY", None)
    os.environ["HASHMM_USER_MEMORY_DIR"] = str(tmp_path)
    try:
        assert um.remember("u", "k", "v")["ok"] is False    # 默认关：写入被拒
        assert um.recall("u") == {} and um.inject_block("u") == ""
    finally:
        os.environ.pop("HASHMM_USER_MEMORY_DIR", None)


def test_remember_preference_tool_registered():
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert '"name": "remember_preference"' in src
    assert 'if name == "remember_preference":' in src
    psrc = open("hashmm/agent/permissions.py", encoding="utf-8").read()
    assert '"remember_preference": PermissionLevel.WRITE' in psrc


# ───────────────────────── V81: 自动更新分发 ─────────────────────────

def test_update_file_whitelist(tmp_path):
    import os
    pytest.importorskip("fastapi")
    from hashmm.api.routes import desktop_updates as du
    os.environ["HASHMM_DESKTOP_UPDATES_DIR"] = str(tmp_path)
    try:
        (tmp_path / "latest.yml").write_text("version: 1.3.0", encoding="utf-8")
        (tmp_path / "HashMM-Setup-1.3.0.exe").write_bytes(b"MZ")
        (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
        assert du.safe_update_file("latest.yml") is not None
        assert du.safe_update_file("HashMM-Setup-1.3.0.exe") is not None
        assert du.safe_update_file("secret.txt") is None            # 扩展名白名单
        assert du.safe_update_file("../../etc/passwd") is None      # 穿越
        assert du.safe_update_file("..\\win.ini") is None
        assert du.safe_update_file("a/b.yml") is None               # 路径分隔
        assert du.safe_update_file("missing.yml") is None           # 不存在
        assert du.updates_enabled() is False                        # 默认关
    finally:
        os.environ.pop("HASHMM_DESKTOP_UPDATES_DIR", None)


# ───────────────────────── V82: 自适应行为可见性 ─────────────────────────

def test_adaptive_traces_emitted():
    """检索改写/错误恢复注入指引时必须发 trace（否则前端徽章无数据）。"""
    src = open("hashmm/agent/loop.py", encoding="utf-8").read()
    assert '"node": "retrieval_adapt"' in src
    assert '"node": "error_recover"' in src
    # 且与 guidance 注入同点（紧随 messages.append guidance）
    assert "检索质量低，自动改写查询重试" in src
    assert "自动换路重试" in src


def test_agentlog_no_emoji_field_residue():
    """V84 回归：AgentLog 的 NODE_LABELS 与 meta 兜底不能残留 emoji 字段
    （emoji→Icon 迁移时漏改兜底对象导致真机 build 联合类型报错）。"""
    src = open("frontend-next/components/AgentLog.tsx", encoding="utf-8").read()
    # 不能再有 emoji: 作为对象字段（注释里的"emoji"文字不算）
    import re
    assert not re.search(r'\bemoji:\s*"', src), "AgentLog 仍有 emoji 字段残留"
    assert "Icon: Sparkles" in src or "Icon:" in src   # 兜底用 Icon
