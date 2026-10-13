"""V317 回归测试：Context Engine 门面 + 口径对齐 + 工具检索激活 + 引用锚定 + 记忆去重。

全部离线可测，无 LLM / 无网络依赖。
"""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    for k in ("HASHMM_TOOL_RETRIEVAL", "HASHMM_TOOL_RETRIEVAL_AUTO"):
        monkeypatch.delenv(k, raising=False)


# ============================================================ Context Engine 门面
def test_context_engine_assembles_and_budgets():
    from hashmm.agent.context_engine import assemble_context
    providers = {
        "profile": lambda: "用户是资深工程师，在做 RAG-agent 项目",
        "memory": lambda: "上次讨论了向量检索召回优化",
        "episodic": lambda: "有效做法：先 web_search 再 fetch_url 核对",
        "skill": lambda: (_ for _ in ()).throw(RuntimeError("后端挂了")),  # 抛错不拦
        "task": lambda: "",
    }
    b = assemble_context(providers)
    obs = b.observability()
    assert obs["hit_count"] == 3          # profile/memory/episodic（skill抛错、task空）
    txt = b.profile_text(labeled=True)
    assert "【用户画像】" in txt and "【历史经验】" in txt


def test_context_engine_budget_truncation():
    from hashmm.agent.context_engine import assemble_context
    b = assemble_context({"memory": lambda: "长记忆" * 500}, budgets={"memory": 100})
    src = next(s for s in b.observability()["sources"] if s["key"] == "memory")
    assert src["truncated"] and src["chars"] <= 100


def test_context_engine_single_source_failure_isolated():
    """一个源抛错不影响其他源（延续 loop/streaming 的健壮性）。"""
    from hashmm.agent.context_engine import assemble_context
    b = assemble_context({
        "profile": lambda: "正常画像",
        "memory": lambda: (_ for _ in ()).throw(ValueError("炸")),
        "episodic": lambda: "正常经验",
    })
    hits = [s["key"] for s in b.observability()["sources"] if s["hit"]]
    assert "profile" in hits and "episodic" in hits and "memory" not in hits


# ============================================================ 口径对齐表
def test_parity_table_covers_runnable_benches():
    from hashmm.evaluation.benchmarks.vs_frontier import parity_table
    rows = parity_table()
    consistent = [r for r in rows if "一致" in r["verdict"]]
    assert len(consistent) >= 7          # 能跑的基准判分口径都与官方一致
    ids = {r["id"] for r in rows}
    assert {"tau2", "gaia", "swebench", "swebench_pro", "terminal"} <= ids


def test_parity_markdown_answers_the_question():
    from hashmm.evaluation.benchmarks.vs_frontier import render_parity_markdown
    md = render_parity_markdown()
    assert "和大厂一样吗" in md and "判分标准" in md
    assert "分数可直接与大厂比" in md


# ============================================================ 工具检索激活
def _mk_tools(n, prefix="tool"):
    return [{"type": "function", "function": {"name": f"{prefix}_{i}",
             "description": f"业务工具{i}", "parameters": {}}} for i in range(n)]


def test_tool_retrieval_auto_threshold_not_dead_code():
    """★ 核心：自动阈值从 9999（永不触发）改为 30——工具面变大时自动激活。"""
    from hashmm.agent.tool_retrieval import should_retrieve
    assert not should_retrieve(_mk_tools(23))   # 内置规模：不介入，零行为变化
    assert should_retrieve(_mk_tools(50))        # 接了 MCP：自动介入


def test_tool_retrieval_explicit_off_still_works(monkeypatch):
    from hashmm.agent.tool_retrieval import should_retrieve
    monkeypatch.setenv("HASHMM_TOOL_RETRIEVAL", "0")
    assert not should_retrieve(_mk_tools(100))


def test_core_tools_include_web_and_memory():
    """web_search/fetch_url/memory_recall 必须在核心白名单——裁掉=砍整类能力。"""
    from hashmm.agent.tool_retrieval import CORE_TOOLS
    assert {"web_search", "fetch_url", "memory_recall", "kb_search", "run_shell"} <= CORE_TOOLS


def test_tool_retrieval_keeps_core_prunes_rest():
    from hashmm.agent.tool_retrieval import select_tools

    def T(n, d):
        return {"type": "function", "function": {"name": n, "description": d, "parameters": {}}}
    tools = [T("kb_search", "知识库检索"), T("web_search", "联网搜索"),
             T("run_shell", "执行shell"), T("memory_recall", "召回记忆"),
             T("fetch_url", "抓取网页"),
             T("sql_query", "执行SQL查询数据库"), T("sql_schema", "查看数据库表结构")] + \
        [T(f"mcp_x{i}", f"第三方业务工具{i}") for i in range(45)]
    sel = select_tools("查一下数据库用户表结构", tools)
    names = [t["function"]["name"] for t in sel]
    assert len(sel) < len(tools)                 # 有裁剪
    for core in ("kb_search", "web_search", "memory_recall", "fetch_url"):
        assert core in names, f"核心工具 {core} 被裁"
    assert any("sql" in n for n in names)        # 任务相关工具命中


# ============================================================ 引用锚定校验
def test_citation_guard_catches_hallucinated_ref():
    from hashmm.retrieval.citation_guard import check_citations
    r = check_citations("腾讯2024年营收6600亿元[5]，同比增长8%[5]。", n_sources=3)
    assert r.level == "risk" and r["invalid"] == [5]


def test_citation_guard_catches_naked_facts():
    from hashmm.retrieval.citation_guard import check_citations
    r = check_citations("公司去年营收增长了35%，用户数达到2.3亿人。", n_sources=3)
    assert r.level == "risk" and r["hard_facts"] == 2


def test_citation_guard_partial_warn():
    from hashmm.retrieval.citation_guard import check_citations
    r = check_citations("营收6600亿元[1]。利润率是32%。", n_sources=2)
    assert r.level == "warn" and r.coverage == 0.5


def test_citation_guard_good_and_qualitative_ok():
    from hashmm.retrieval.citation_guard import check_citations
    assert check_citations("营收6600亿元[1]，利润率32%[2]。", 3).ok
    assert check_citations("这家公司主营游戏，市场地位稳固。", 3).ok  # 无硬事实不误报


# ============================================================ 分层记忆去重
def test_layered_contradicts_module_level():
    """_contradicts 提到模块级（regenerate_persona 也用）——不再是闭包。"""
    from hashmm.memory import layered as L
    assert hasattr(L, "_contradicts") and hasattr(L, "_bigram_sim") and hasattr(L, "_norm_txt")
    assert L._contradicts("用户住北京", "用户不住北京")      # 否定极性
    assert L._contradicts("TTL 300", "TTL 600")            # 关键数不同
    assert not L._contradicts("用户喜欢简洁", "用户喜欢精炼")


def test_layered_dedup_guards_intact():
    from hashmm.memory.layered import _dedup_decision, Atom

    def mk(c):
        return Atom(id="x", content=c, type="persona")
    assert _dedup_decision(mk("用户住北京"), [mk("用户不住北京")])[0] == "store"   # 矛盾不合并
    assert _dedup_decision(mk("用户喜欢简洁回答"), [mk("用户喜欢简洁的回答")])[0] == "update"  # 子串合并
    assert _dedup_decision(mk("TTL设为300"), [mk("TTL设为600")])[0] == "store"     # 数字不同不合并
