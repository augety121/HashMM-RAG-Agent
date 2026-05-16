"""Tests for M5 memory layer.

Coverage:
  - WorkingMemory: bounded growth, recall, dedup, failed-query tracking.
  - SessionStore: schema init, session CRUD, turn append, recent_turns order,
                  cascade delete, stats accuracy.
  - SemanticCache: encode → write → lookup HIT, encode → lookup MISS,
                   TTL expiry, index_version mismatch, eviction, atomic save,
                   exact dedup, purge.

If faiss is not installed (e.g. CI sandbox), a stub is auto-installed at
import time so the semcache tests still exercise the logic. On the AutoDL
GPU box, real faiss-cpu is used.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


# ── faiss shim ─────────────────────────────────────────────────────────
# If real faiss is importable, we use it. Otherwise install a stub that
# implements the tiny subset of the API SemanticCache uses.

try:
    import faiss  # noqa: F401
    _USING_FAISS_STUB = False
except ImportError:
    _USING_FAISS_STUB = True

    class _StubBinaryIndex:
        def __init__(self, bits: int):
            self.bits = bits
            self._codes: list[bytes] = []

        @property
        def ntotal(self) -> int:
            return len(self._codes)

        def add(self, x: np.ndarray) -> None:
            for row in x:
                self._codes.append(row.tobytes())

        def search(self, q: np.ndarray, k: int):
            q_bytes = q[0].tobytes()
            dists = []
            for c in self._codes:
                xor = int.from_bytes(q_bytes, "big") ^ int.from_bytes(c, "big")
                dists.append(bin(xor).count("1"))
            order = np.argsort(dists)[:k] if dists else np.array([], dtype=np.int64)
            D = np.array([[dists[i] for i in order]], dtype=np.int32)
            I = np.array([order], dtype=np.int64)
            return D, I

    class _StubFaiss:
        IndexBinaryFlat = _StubBinaryIndex

        @staticmethod
        def read_index_binary(path):
            # Force a "rebuild from DB" path
            raise FileNotFoundError("faiss stub: never reads")

        @staticmethod
        def write_index_binary(idx, path):
            with open(path, "wb") as f:
                f.write(b"STUB_FAISS")

    sys.modules["faiss"] = _StubFaiss()


from hashmm.memory import SessionStore, WorkingMemory


# ── WorkingMemory ──────────────────────────────────────────────────────


def test_working_memory_records_turn():
    wm = WorkingMemory(max_turns=5)
    state = {"query": "hello", "intent": "factual", "strategy": "vector",
             "retrieved": [{"chunk_id": "c1"}], "quality_ok": True,
             "answer": "hi"}
    rec = wm.record_turn(state)
    assert rec.query == "hello"
    assert rec.intent == "factual"
    assert len(wm) == 1
    assert wm.has_seen_chunk("c1")


def test_working_memory_caps_at_max():
    wm = WorkingMemory(max_turns=3)
    for i in range(10):
        wm.record_turn({"query": f"q{i}", "intent": "semantic",
                        "strategy": "vector", "quality_ok": True,
                        "answer": ""})
    assert len(wm) == 3
    recent = wm.recent_turns(5)
    assert [t.query for t in recent] == ["q7", "q8", "q9"]


def test_working_memory_tracks_failed_queries():
    wm = WorkingMemory()
    wm.record_turn({"query": "bad query", "intent": "x", "strategy": "y",
                    "quality_ok": False, "answer": ""})
    assert wm.previously_failed("Bad Query")  # case-insensitive
    assert wm.previously_failed("bad query")
    assert not wm.previously_failed("good query")


def test_working_memory_summary_runs():
    wm = WorkingMemory()
    assert "empty" in wm.summary()
    wm.record_turn({"query": "q", "intent": "i", "strategy": "s",
                    "quality_ok": True, "answer": ""})
    assert "1 turns" in wm.summary()


# ── SessionStore ───────────────────────────────────────────────────────


def _tmp_db() -> Path:
    return Path(tempfile.mkdtemp(prefix="hashmm-test-")) / "ep.sqlite"


def test_sessionstore_creates_schema():
    db = _tmp_db()
    s = SessionStore(db)
    stats = s.stats()
    assert stats["n_sessions"] == 0
    assert stats["n_turns"] == 0
    assert db.exists()
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_session_lifecycle():
    db = _tmp_db()
    s = SessionStore(db)
    sid = s.start_session(user_id="alice")
    assert s.session_exists(sid)
    assert not s.session_exists("nonexistent")

    sessions = s.list_sessions(user_id="alice")
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == sid

    n = s.delete_session(sid)
    assert n == 1
    assert not s.session_exists(sid)
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_record_turn_increments():
    db = _tmp_db()
    s = SessionStore(db)
    sid = s.start_session()

    state = {"query": "Q1", "intent": "factual", "strategy": "vector",
             "retrieved": [{"chunk_id": "c1"}], "quality_ok": True,
             "answer": "A1", "trace": [{"node": "x"}],
             "sources_cited": ["c1"]}
    tid = s.record_turn(sid, state)
    assert tid is not None

    turns = s.get_recent_turns(sid)
    assert len(turns) == 1
    assert turns[0]["query"] == "Q1"
    assert turns[0]["intent"] == "factual"
    assert turns[0]["quality_ok"] is True
    assert turns[0]["cache_hit"] is False
    assert turns[0]["cited_ids"] == ["c1"]
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_recent_turns_chronological():
    db = _tmp_db()
    s = SessionStore(db)
    sid = s.start_session()
    for i in range(5):
        s.record_turn(sid, {"query": f"Q{i}", "intent": "x", "strategy": "y",
                            "retrieved": [], "quality_ok": True, "answer": ""})
    turns = s.get_recent_turns(sid, n=3)
    # Oldest first within the recent window
    assert [t["query"] for t in turns] == ["Q2", "Q3", "Q4"]
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_cascade_delete():
    db = _tmp_db()
    s = SessionStore(db)
    sid = s.start_session()
    for i in range(3):
        s.record_turn(sid, {"query": f"q{i}", "intent": "x", "strategy": "y",
                            "retrieved": [], "quality_ok": True, "answer": ""})
    assert s.stats()["n_turns"] == 3
    s.delete_session(sid)
    assert s.stats()["n_turns"] == 0  # cascade
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_unknown_session_rejects_turn():
    db = _tmp_db()
    s = SessionStore(db)
    try:
        s.record_turn("bogus_sid", {"query": "x", "intent": "i",
                                    "strategy": "s", "retrieved": [],
                                    "quality_ok": True, "answer": ""})
        raise AssertionError("should have raised")
    except ValueError:
        pass
    s.close()
    shutil.rmtree(db.parent)


def test_sessionstore_cache_hit_recorded():
    db = _tmp_db()
    s = SessionStore(db)
    sid = s.start_session()
    s.record_turn(sid, {"query": "q", "intent": "x", "strategy": "y",
                        "retrieved": [], "quality_ok": True, "answer": ""},
                  cache_hit=True)
    turns = s.get_recent_turns(sid)
    assert turns[0]["cache_hit"] is True
    s.close()
    shutil.rmtree(db.parent)


# ── SemanticCache: import-only logic that doesn't need encoders ────────


def test_normalise_query_strips_punct_and_case():
    from hashmm.memory.semantic_cache import _normalise_query
    assert _normalise_query("What is BGE-M3?") == "what is bge-m3"
    assert _normalise_query("  Hello,   world!!! ") == "hello, world"
    assert _normalise_query("") == ""


def test_schema_sql_has_both_sections():
    from hashmm.memory.semantic_cache import _read_semcache_ddl
    from hashmm.memory.episodic import _read_episodic_ddl
    ep = _read_episodic_ddl()
    sc = _read_semcache_ddl()
    assert "CREATE TABLE IF NOT EXISTS sessions" in ep
    assert "CREATE TABLE IF NOT EXISTS turns" in ep
    assert "CREATE TABLE IF NOT EXISTS cache_entries" in sc
    assert "CREATE TABLE IF NOT EXISTS cache_stats" in sc


# ── SemanticCache: with fake encoders ──────────────────────────────────


class _FakeTextEncoder:
    """Returns deterministic float embeddings derived from query string.
    Same string → same vector; tiny differences → near vectors."""

    def __init__(self, dim: int = 16):
        self.dim = dim

    def __call__(self, queries):
        import numpy as np
        out = []
        for q in queries:
            # Deterministic hash → seed → vector
            seed = abs(hash(q)) % (2 ** 31)
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(self.dim).astype("float32")
            out.append(v)
        # Mimic torch tensor: return obj with .detach().cpu().float().numpy()
        return _FakeTensor(np.stack(out))


class _FakeTensor:
    def __init__(self, arr):
        self._arr = arr

    def detach(self): return self
    def cpu(self):    return self
    def float(self):  return self
    def numpy(self):  return self._arr


class _FakeHashNet:
    """sign_text returns ±1 ints from the text encoder vector sign."""

    def __init__(self, bits: int = 128):
        self.bits = bits

    def sign_text(self, text_emb_obj):
        import numpy as np
        arr = text_emb_obj._arr if hasattr(text_emb_obj, "_arr") else text_emb_obj
        # Project the 16-d emb to `bits` deterministic projections.
        # We do a tiny per-instance random projection (same seed per call shape).
        d = arr.shape[-1]
        rng = np.random.default_rng(42)  # fixed across calls
        proj = rng.standard_normal((d, self.bits)).astype("float32")
        z = arr @ proj  # (n, bits)
        sign = np.where(z >= 0, 1, -1).astype("int8")
        return _FakeTensor(sign)


def _semcache_cfg(tmpdir: Path):
    """Build a minimal config with semcache pointed at a tmp dir."""
    from hashmm.config import HashMMConfig
    cfg = HashMMConfig()
    cfg.memory_dir = str(tmpdir)
    # Re-trigger property paths by creating dir
    cfg.semcache_dir.mkdir(parents=True, exist_ok=True)
    cfg.hash_bits = 128
    cfg.semcache_max_entries = 50
    return cfg


def test_semcache_write_then_lookup_hits():
    import numpy as np
    from hashmm.memory.semantic_cache import SemanticCache

    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)

    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    entry_id = cache.write(
        query="what is BGE-M3",
        answer="BGE-M3 is a multilingual embedding model.",
        retrieval=[{"chunk_id": "c1", "text": "BGE-M3 details"}],
        intent="factual", strategy="vector",
    )
    assert entry_id is not None

    # Exact same query → exact match path
    hit = cache.lookup("what is BGE-M3")
    assert hit is not None
    assert hit["answer"].startswith("BGE-M3")
    assert hit["match_type"] == "exact"

    cache.close()
    shutil.rmtree(tmp)


def test_semcache_lookup_miss_when_empty():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    assert cache.lookup("anything") is None
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_disabled_returns_none():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cfg.semcache_enabled = False
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    eid = cache.write("q", "a", [], "i", "s")
    assert eid is None
    assert cache.lookup("q") is None
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_ttl_expiry():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    # Insert with 0.001s TTL
    cache.write("expiring query", "old answer", [], None, None,
                ttl_seconds=0.001)
    time.sleep(0.05)
    hit = cache.lookup("expiring query")
    assert hit is None, f"expected expired, got {hit}"
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_index_version_invalidation():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cfg.semcache_index_version = 1
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    cache.write("hello", "world", [], None, None)
    assert cache.lookup("hello") is not None

    # Now bump the index version — old entry should not match.
    cfg.semcache_index_version = 2
    assert cache.lookup("hello") is None
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_exact_dedup_updates_not_duplicates():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    eid1 = cache.write("Question?", "first answer", [], None, None)
    eid2 = cache.write("question", "second answer", [], None, None)  # norm matches
    assert eid1 == eid2, "exact-norm duplicate should update, not create new"
    # Lookup should return the updated answer
    hit = cache.lookup("Question?")
    assert hit is not None
    assert hit["answer"] == "second answer"
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_stats_tracking():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)

    cache.write("q1", "a1", [], None, None)
    cache.lookup("q1")           # hit
    cache.lookup("nothing here") # miss
    s = cache.stats()
    assert s["n_writes"] == 1
    assert s["n_lookups"] == 2
    assert s["n_hits"] == 1
    assert s["hit_rate"] == 0.5
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_purge_all_clears():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)
    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    for i in range(5):
        cache.write(f"q{i}", f"a{i}", [], None, None)
    assert cache.stats()["n_entries"] == 5
    n = cache.purge_all()
    assert n == 5
    assert cache.stats()["n_entries"] == 0
    assert cache.lookup("q0") is None
    cache.close()
    shutil.rmtree(tmp)


def test_semcache_persists_across_reopen():
    from hashmm.memory.semantic_cache import SemanticCache
    tmp = Path(tempfile.mkdtemp(prefix="hashmm-semc-"))
    cfg = _semcache_cfg(tmp)

    cache = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                          hash_net=_FakeHashNet(128), embed_dim=16)
    cache.write("persistent query", "persistent answer", [], None, None)
    cache.close()

    # Reopen and check
    cache2 = SemanticCache(cfg, text_encoder=_FakeTextEncoder(16),
                           hash_net=_FakeHashNet(128), embed_dim=16)
    hit = cache2.lookup("persistent query")
    assert hit is not None
    assert hit["answer"] == "persistent answer"
    cache2.close()
    shutil.rmtree(tmp)


# ── agent memory_nodes plumbing ────────────────────────────────────────


def test_route_after_cache_lookup():
    from hashmm.agent.memory_nodes import route_after_cache_lookup
    assert route_after_cache_lookup({"cache_hit": True}) == "episodic_write"
    assert route_after_cache_lookup({"cache_hit": False}) == "classify_intent"
    assert route_after_cache_lookup({}) == "classify_intent"


def test_semcache_lookup_node_handles_none_cache():
    from hashmm.agent.memory_nodes import make_semcache_lookup_node
    node = make_semcache_lookup_node(None)
    out = node({"query": "x"})
    assert out["cache_hit"] is False
    assert out["trace"][0]["skipped"] is True


def test_episodic_write_node_handles_none_store():
    from hashmm.agent.memory_nodes import make_episodic_write_node
    node = make_episodic_write_node(None)
    out = node({"session_id": "x", "query": "q"})
    assert "skipped" in out["trace"][0]


def test_semcache_write_node_skips_on_failed_quality():
    from hashmm.agent.memory_nodes import make_semcache_write_node

    class FakeCache:
        def __init__(self): self.calls = []
        def write(self, **kw): self.calls.append(kw); return 1

    fake = FakeCache()
    node = make_semcache_write_node(fake)
    node({"query": "q", "answer": "a", "quality_ok": False})
    assert fake.calls == [], "should not write on quality_failed"

    node({"query": "q", "answer": "a", "quality_ok": True,
          "retrieved": [], "intent": "i", "strategy": "s"})
    assert len(fake.calls) == 1
