import asyncio
from types import SimpleNamespace


def test_manual_compaction_owner_checks_and_returns_safe_state(monkeypatch):
    from hashmm.api.routes import conversations as routes

    checked = []
    monkeypatch.setattr(
        routes, "require_conv_access",
        lambda _request, conv_id: checked.append(conv_id) or {"id": conv_id, "user_id": "u1"},
    )
    monkeypatch.setattr(
        "hashmm.agent.conv_compact.prepare_persistent_history",
        lambda _db, conv_id, force=False, trigger="auto": SimpleNamespace(
            compacted_now=True, working_tokens=900,
        ),
    )
    monkeypatch.setattr(routes.db, "get_context_compaction", lambda _conv_id: {
        "source_messages": 18,
        "estimated_tokens": 900,
        "compaction_count": 2,
        "last_trigger": "manual",
        "updated_at": 123.0,
    })

    out = asyncio.run(routes.compact_conversation_context("conv-1", object()))
    assert checked == ["conv-1"]
    assert out["compacted"] is True
    assert out["state"]["last_trigger"] == "manual"
    assert "summary" not in out["state"]
