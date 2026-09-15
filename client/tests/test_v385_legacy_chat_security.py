"""Regression tests for legacy App chat ownership and cache isolation."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from hashmm.api.core.state import PersistentMemory, SemanticCache


def test_semantic_answer_cache_is_partitioned_by_owner():
    cache = SemanticCache(threshold=0.9)
    embedding = np.asarray([[1.0, 0.0]], dtype=np.float32)
    cache.store(embedding, "same query", "alice answer", [{"filename": "alice.pdf"}], scope="alice")

    assert cache.lookup(embedding, scope="bob") is None
    assert cache.lookup(embedding, scope="alice")["answer"] == "alice answer"


def test_recreated_memory_session_drops_previous_owner_state(tmp_path: Path):
    memory = PersistentMemory(tmp_path / "agent-state")
    memory.get_or_create_session("shared-id", "Alice", "alice")
    memory.add_message("shared-id", "user", "alice secret")
    memory.profiles["shared-id"] = {"queries": 1}

    session = memory.get_or_create_session("shared-id", "Bob", "bob")

    assert session["user_id"] == "bob"
    assert memory.get_history("shared-id") == []
    assert "shared-id" not in memory.profiles


def test_all_legacy_chat_entrypoints_check_owner_before_work():
    source = (Path(__file__).parents[1] / "hashmm" / "api" / "server.py").read_text(
        encoding="utf-8",
    )
    chat = source.split("async def chat(req:", 1)[1].split('@app.post("/api/chat/agentic")', 1)[0]
    agentic = source.split("async def chat_agentic(req:", 1)[1].split('@app.post("/api/chat/stream"', 1)[0]
    stream = source.split("async def chat_stream(req:", 1)[1].split('@app.get("/api/corpus/stats")', 1)[0]
    for block in (chat, agentic, stream):
        assert "require_conv_access_or_create(request, sid" in block

    assert "enhance_with_acl" in source.split("def agent_run", 1)[1].split("@app.post", 1)[0]
    assert "sem_cache.lookup(q_emb, scope=user_id)" in source
    assert "sem_cache.store(q_emb, query, answer, sources, scope=user_id)" in source
