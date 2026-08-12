"""V342 multi-agent RAG integration and ownership regressions."""
from __future__ import annotations

import re
from pathlib import Path

from hashmm.agent import team


class _FakeRetrieval:
    def should_search(self, _goal):
        return True

    def enhance(self, _goal, _messages, top_k=6, retrieval_mode="mix"):
        assert top_k == 6
        assert retrieval_mode == "mix"
        return [], [
            {"filename": "a.md", "page": 2, "section": "评测", "text": "RAG 要分别评估检索和生成。", "score": 0.9},
            {"filename": "b.md", "page": 4, "section": "编排", "text": "多 Agent 要记录轨迹和终态。", "score": 0.8},
        ], object()


def test_team_retrieval_builds_one_stable_citation_namespace(monkeypatch):
    import hashmm.chat_retrieval as retrieval
    monkeypatch.setattr(retrieval, "get_chat_retrieval", lambda: _FakeRetrieval())

    context, sources, state = team._retrieve_team_evidence("评估一个多 Agent RAG 系统")

    assert state == "ready"
    assert [source["id"] for source in sources] == [1, 2]
    assert "[1] a.md 第2页 评测" in context
    assert "[2] b.md 第4页 编排" in context
    assert "不可信数据" in context


def test_role_receives_grounding_contract_and_shared_evidence():
    captured = {}

    class _LLM:
        def quick_call(self, system, user, max_tok=None):
            captured["system"] = system
            captured["user"] = user
            return "检索与生成应分开评估 [1]。"

    out = team._run_role(
        _LLM(), "评估 RAG", {"role": "研究员", "task": "给出评测方法"},
        evidence_context="【共享检索证据】\n[1] a.md\nRAG 要分别评估检索和生成。",
    )

    assert out.endswith("[1]。")
    assert "不得补造来源" in captured["system"]
    assert "[1] a.md" in captured["system"]


def test_team_status_is_non_enumerable_across_owners():
    with team._TEAMS_LOCK:
        team._TEAMS["tm-private"] = {
            "team_id": "tm-private", "uid": "owner-a", "roles": [],
            "created": 1, "status": "running", "goal": "secret",
        }
    assert team.get_team("tm-private", "owner-a")["goal"] == "secret"
    assert team.get_team("tm-private", "owner-b") is None
    # Route-level wiring regression: authenticated uid must be passed to the
    # owner-filtering accessor; a bare get_team(team_id) would re-open IDOR.
    route_source = (Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "team_ops.py").read_text(encoding="utf-8")
    status_block = route_source.split('async def team_status', 1)[1].split('@router.get("/list"', 1)[0]
    assert 'team_mod.get_team(team_id, user.get("uid", ""))' in status_block


def test_team_canvas_contains_no_emoji_characters():
    html = team._canvas_html("形成带证据的结论", [
        {"role": "研究员", "task": "检索证据"},
        {"role": "审校员", "task": "复核引用"},
    ])
    assert not re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", html)
    assert "共享证据" in html
