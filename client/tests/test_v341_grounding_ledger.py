"""V341: stable Agent citations + claim-level grounding persistence."""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from hashmm.evaluation.grounding_ledger import (
    build_grounding_ledger,
    parse_numbered_sources,
    public_sources,
    renumber_numbered_evidence,
)


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _source(text: str, *, filename: str = "年报.pdf", page: int = 8, score: float = 0.91):
    return {
        "id": "chunk-a",
        "chunk_id": "chunk-a",
        "doc_id": "doc-a",
        "filename": filename,
        "page": page,
        "score": score,
        "text": text,
    }


def test_grounding_ledger_keeps_exact_claim_span_and_chunk_location():
    answer = "先给结论。\n- 公司2024年营收达到100亿元[1]。\n后续建议另行评估。"
    source = _source("公司2024年营收达到100亿元，同比增长12%。")

    ledger = build_grounding_ledger(answer, [source])

    assert ledger["status"] == "passed"
    assert ledger["coverage_ratio"] == 1.0
    assert ledger["semantic_entailment_verified"] is False
    assert ledger["supported_claims"] == 1
    claim = ledger["claims"][0]
    assert answer[claim["claim_span"]["start"]:claim["claim_span"]["end"]] == claim["claim_span"]["text"]
    assert claim["status"] == "supported"
    assert claim["evidence"][0]["chunk_id"] == "chunk-a"
    assert claim["evidence"][0]["doc_id"] == "doc-a"
    assert claim["evidence"][0]["page"] == 8


def test_grounding_ledger_distinguishes_inferred_unsupported_and_invalid():
    sources = [_source("公司2024年营收达到100亿元。")]
    answer = (
        "公司2024年营收达到100亿元[1]。\n"
        "公司2025年营收达到900亿元[1]。\n"
        "公司2026年营收达到1000亿元。\n"
        "公司2027年营收达到1200亿元[9]。"
    )

    ledger = build_grounding_ledger(answer, sources)

    assert ledger["status"] == "failed"
    assert [c["status"] for c in ledger["claims"]] == [
        "supported", "unsupported", "inferred", "invalid_citation",
    ]
    assert ledger["invalid_citations"] == [9]
    assert ledger["review_required"] is True


def test_runtime_observations_and_clarifying_questions_are_not_source_claims():
    """Execution state belongs to the runtime trace, not document grounding."""
    answer = (
        "上一轮 web_search 工具调用返回时被系统跳过，没有拿到实际搜索结果。"
        "本轮未获得有效的网络搜索结果，无法据此补充最新动态。\n"
        "回到核心问题：你希望我处理知识库里的资料，还是联网检索？"
    )

    ledger = build_grounding_ledger(answer, [])

    assert ledger["total_factual_claims"] == 0
    assert ledger["claims"] == []
    assert ledger["review_required"] is False


def test_agent_tool_sources_are_parsed_and_shifted_to_turn_global_ids():
    tool_text = (
        "【深度检索答案】第一项[1]，第二项[2]\n"
        "【来源】\n"
        "[1] (甲.pdf) 第一项为10亿元。\n"
        "[2] (乙.pdf) 第二项为20亿元。"
    )

    shifted, sources = renumber_numbered_evidence(tool_text, 3)

    assert "第一项[4]" in shifted and "第二项[5]" in shifted
    assert "[4] (甲.pdf)" in shifted and "[5] (乙.pdf)" in shifted
    assert [s["citation_id"] for s in sources] == [4, 5]
    assert [s["filename"] for s in sources] == ["甲.pdf", "乙.pdf"]


def test_packed_agent_sources_keep_page_score_and_bounded_public_contract():
    packed = "[1] [制度.pdf p.12] (相关度:0.88)\n报销上限为5000元。"
    parsed = parse_numbered_sources(packed)
    public = public_sources(parsed, text_limit=8)

    assert parsed[0]["page"] == 12 and parsed[0]["score"] == pytest.approx(0.88)
    assert public[0]["id"] == 1 and public[0]["filename"] == "制度.pdf"
    assert len(public[0]["text"]) <= 8


def test_grounding_ledger_roundtrips_through_message_database():
    from hashmm.api import database as db

    conv_id = "v341-grounding-roundtrip"
    db.create_conversation(conv_id, user_id="v341-user", title="grounding")
    ledger = build_grounding_ledger("公司2024年营收达到100亿元[1]。", [
        _source("公司2024年营收达到100亿元。"),
    ])
    msg_id = db.create_message(conv_id, "assistant", "pending")
    db.update_message(msg_id, content="done", sources=public_sources([_source("证据")]),
                      groundings=ledger, status="complete")

    saved = next(m for m in db.get_messages(conv_id) if m["id"] == msg_id)
    assert saved["groundings"] == ledger
    assert saved["sources"][0]["chunk_id"] == "chunk-a"
    with db._conn() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert "groundings" in columns and version >= 12
    db.delete_conversation(conv_id)


def test_supabase_message_contract_carries_ledger_when_schema_is_migrated():
    from hashmm.api.supabase_sync import _msg_row

    ledger = build_grounding_ledger("公司2024年营收达到100亿元[1]。", [
        _source("公司2024年营收达到100亿元。"),
    ])
    row = _msg_row({
        "id": "m-v341", "conv_id": "c-v341", "role": "assistant",
        "content": "answer", "groundings": ledger,
    }, "00000000-0000-0000-0000-000000000001")

    assert row["groundings"] == ledger


def test_chat_frontend_and_agent_loop_use_grounding_contract():
    streaming = (ROOT / "hashmm/api/streaming.py").read_text("utf-8")
    loop = (ROOT / "hashmm/agent/loop.py").read_text("utf-8")
    chat = (ROOT / "frontend-next/components/ChatArea.tsx").read_text("utf-8")
    bubble = (ROOT / "frontend-next/components/MsgBubble.tsx").read_text("utf-8")

    assert "build_grounding_ledger" in streaming
    assert '"groundings": groundings' in streaming
    assert "renumber_numbered_evidence" in loop
    assert "_seed_grounding_sources" in loop
    assert "groundings: data.groundings" in chat
    assert "<GroundingAudit ledger={msg.groundings}" in bubble


def test_agent_loop_continues_numbering_after_prefetched_evidence():
    from hashmm.agent.loop import AgentLoop

    class Fn:
        name = "deep_search"
        arguments = json.dumps({"query": "第二项"}, ensure_ascii=False)

    class ToolCall:
        id = "tool-v341"
        type = "function"
        function = Fn()

    class Message:
        def __init__(self, content="", tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning_content = ""

    class Response:
        def __init__(self, message):
            self.message = message

    class LLM:
        def __init__(self):
            self.calls = 0
            self.second_messages = []

        def call_with_tools(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return Response(Message(tool_calls=[ToolCall()]))
            self.second_messages = messages
            return Response(Message("第二项达到20亿元[2]。"))

    llm = LLM()
    loop = AgentLoop(llm_fn=llm, tools=[{
        "type": "function",
        "function": {"name": "deep_search", "description": "test", "parameters": {"type": "object"}},
    }], user_id="u-v341", conv_id="c-v341")
    loop._seed_grounding_sources = [_source("第一项达到10亿元。", filename="第一项.pdf")]
    loop.permissions = types.SimpleNamespace(check=lambda *_a, **_kw: (True, "ok"))

    async def fake_execute(self, name, args, user_id):
        assert name == "deep_search"
        return "【来源】\n[1] (第二项.pdf) 第二项达到20亿元。"

    loop._execute_tool = types.MethodType(fake_execute, loop)

    async def run():
        return [event async for event in loop.run("比较两项", history=[])]

    events = asyncio.run(run())
    tool_messages = [m["content"] for m in llm.second_messages if m.get("role") == "tool"]

    assert any("[2] (第二项.pdf)" in text for text in tool_messages)
    assert [s["citation_id"] for s in loop._last_grounding_sources] == [1, 2]
    assert any(kind == "token" and "[2]" in data for kind, data in events)
    assert events[-1][0] == "done"
