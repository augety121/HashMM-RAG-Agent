"""V319 回归测试：交互 Agent（对外通信安全网关）+ 工具检索 observability。

（协作前端 UI 是 tsx，沙箱无 tsc，做了语法平衡检查；此处测后端逻辑。）
"""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def collab_env():
    db = Path(tempfile.mkdtemp()) / "collab.sqlite3"
    from hashmm.collab.audit import get_audit_log
    from hashmm.collab.trust import get_trust_store
    ts = get_trust_store(db)
    audit = get_audit_log(db)
    return ts, audit


def _bob_agent(task):
    return f"完成:{task[:15]}。内部key sk-secret-abc123def456ghi789xyz"


# ============================================================ 交互 Agent 入站
def test_inbound_normal_collab_redacted(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ts.request_friend("alice", "bob")
    ts.accept_friend("bob", "alice")
    ia = InteractionAgent(trust_store=ts, audit=audit)
    r = ia.handle_inbound({"from_user": "alice", "scope": "answer_question", "task": "算1523*47"},
                          agent_executor=_bob_agent, my_user_id="bob")
    assert r["ok"] and "sk-secret" not in r["result"]      # 出站脱敏兜底


def test_inbound_rate_limit(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ts.request_friend("alice", "bob")
    ts.accept_friend("bob", "alice")
    ia = InteractionAgent(trust_store=ts, audit=audit, rate_max=3, rate_window=60)
    for i in range(3):
        ia.handle_inbound({"from_user": "alice", "scope": "answer_question", "task": f"q{i}"},
                          agent_executor=_bob_agent, my_user_id="bob")
    r = ia.handle_inbound({"from_user": "alice", "scope": "answer_question", "task": "再来"},
                          agent_executor=_bob_agent, my_user_id="bob")
    assert r.get("rate_limited") and not r["ok"]           # 超频限流


def test_inbound_rate_limit_per_user_isolated(collab_env):
    """速率限制按用户隔离——一个用户超频不影响另一个。"""
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    for u in ("alice", "carol"):
        ts.request_friend(u, "bob")
        ts.accept_friend("bob", u)
    ia = InteractionAgent(trust_store=ts, audit=audit, rate_max=2, rate_window=60)
    for i in range(2):
        ia.handle_inbound({"from_user": "alice", "scope": "answer_question", "task": f"a{i}"},
                          agent_executor=_bob_agent, my_user_id="bob")
    # alice 已满，但 carol 仍可请求
    r_carol = ia.handle_inbound({"from_user": "carol", "scope": "answer_question", "task": "c"},
                                agent_executor=_bob_agent, my_user_id="bob")
    assert r_carol["ok"]


def test_inbound_forces_my_identity(collab_env):
    """to_user 伪造防护：请求声称发给别人也被强制为本机身份。"""
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ts.request_friend("alice", "bob")
    ts.accept_friend("bob", "alice")
    ia = InteractionAgent(trust_store=ts, audit=audit)
    # 请求声称 to_user=carol，但 my_user_id=bob → 按 alice-bob 好友关系判定（放行）
    r = ia.handle_inbound({"from_user": "alice", "to_user": "carol", "scope": "answer_question", "task": "x"},
                          agent_executor=_bob_agent, my_user_id="bob")
    assert r["ok"]


def test_inbound_stranger_rejected(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ia = InteractionAgent(trust_store=ts, audit=audit)
    r = ia.handle_inbound({"from_user": "stranger", "scope": "answer_question", "task": "x"},
                          agent_executor=_bob_agent, my_user_id="bob")
    assert not r["ok"]


# ============================================================ 交互 Agent 出站
def test_outbound_redacts_sensitive(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ts.request_friend("bob", "alice")
    ts.accept_friend("alice", "bob")
    ia = InteractionAgent(trust_store=ts, audit=audit)

    def fake_transport(req):
        return {"ok": True, "result": f"收到:{req['task'][:20]}"}
    r = ia.send_outbound("bob", "alice", "answer_question",
                         "看下配置 password: hunter2000 对不对", transport=fake_transport)
    assert r["ok"] and r["outbound_scan"]["redacted"]      # 出站敏感信息脱敏
    combined = str(r.get("response", "")) + str(r.get("would_send", ""))
    assert "hunter2000" not in combined


def test_outbound_blocks_untrusted(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ia = InteractionAgent(trust_store=ts, audit=audit)
    r = ia.send_outbound("bob", "stranger", "answer_question", "任务",
                         transport=lambda req: {"ok": True, "result": "x"})
    assert not r["ok"] and "不可信" in r["rejected_reason"]


def test_outbound_dry_run_no_transport(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ts.request_friend("bob", "alice")
    ts.accept_friend("alice", "bob")
    ia = InteractionAgent(trust_store=ts, audit=audit)
    r = ia.send_outbound("bob", "alice", "answer_question", "任务")   # 不传 transport
    assert r["ok"] and r.get("dry_run")


def test_interaction_agent_status(collab_env):
    from hashmm.collab.interaction_agent import InteractionAgent
    ts, audit = collab_env
    ia = InteractionAgent(trust_store=ts, audit=audit)
    st = ia.status()
    assert "速率限制" in st["guards"] and "入站出站双向脱敏" in st["guards"]
    assert len(st["guards"]) >= 6


# ============================================================ 工具检索 observability
def _mk_tools(n, prefix="tool"):
    return [{"type": "function", "function": {"name": f"{prefix}_{i}",
             "description": f"业务工具{i}", "parameters": {}}} for i in range(n)]


def test_tool_retrieval_observed_small_toolset(monkeypatch):
    for k in ("HASHMM_TOOL_RETRIEVAL", "HASHMM_TOOL_RETRIEVAL_AUTO"):
        monkeypatch.delenv(k, raising=False)
    from hashmm.agent.tool_retrieval import select_tools_observed
    _, obs = select_tools_observed("查数据库", _mk_tools(10))
    assert not obs["retrieved"] and obs["kept"] == 10 and obs["dropped"] == 0


def test_tool_retrieval_observed_large_toolset(monkeypatch):
    for k in ("HASHMM_TOOL_RETRIEVAL", "HASHMM_TOOL_RETRIEVAL_AUTO"):
        monkeypatch.delenv(k, raising=False)
    from hashmm.agent.tool_retrieval import select_tools_observed

    def T(n, d):
        return {"type": "function", "function": {"name": n, "description": d, "parameters": {}}}
    tools = [T("kb_search", "知识库检索"), T("web_search", "联网"), T("memory_recall", "记忆"),
             T("sql_query", "SQL查询数据库"), T("sql_schema", "数据库表结构")] + \
        [T(f"mcp_{i}", f"第三方工具{i}") for i in range(45)]
    sel, obs = select_tools_observed("查数据库用户表结构", tools)
    assert obs["retrieved"] and obs["dropped"] > 0
    assert obs["kept"] < obs["total"]
    assert set(obs["core"]) >= {"kb_search", "web_search", "memory_recall"}
    assert "sql_schema" in obs["picked"] or "sql_query" in obs["picked"]
