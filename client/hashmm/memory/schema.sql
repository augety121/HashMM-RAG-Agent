-- HashMM-RAG memory schemas (M5).
--
-- Two separate SQLite databases:
--   1. episodic.sqlite — sessions + turns (conversation history)
--   2. semcache/meta.sqlite — semantic cache metadata + statistics
--
-- Each section below is applied to the relevant DB at first use.
--
-- All tables use:
--   * PRAGMA journal_mode=WAL  (concurrent reads alongside writes)
--   * PRAGMA synchronous=NORMAL (fast, still crash-safe with WAL)
--   * PRAGMA foreign_keys=ON

-- ════════════════════════════════════════════════════════════════════
-- EPISODIC: sessions + turns
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    n_turns    INTEGER NOT NULL DEFAULT 0,
    meta_json  TEXT  -- arbitrary expansion; e.g. domain tag, language
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
    ON sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    turn_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_idx   INTEGER NOT NULL,
    ts         REAL NOT NULL,
    query      TEXT NOT NULL,
    intent     TEXT,
    strategy   TEXT,
    n_results  INTEGER,
    quality_ok INTEGER,  -- 0/1
    cache_hit  INTEGER NOT NULL DEFAULT 0,
    answer     TEXT,
    trace_json TEXT,
    cited_ids_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_turns_session_idx
    ON turns(session_id, turn_idx);

-- ════════════════════════════════════════════════════════════════════
-- SEMANTIC CACHE: entries + stats (separate DB file)
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cache_entries (
    entry_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    faiss_row      INTEGER NOT NULL,    -- row index in the FAISS binary index
    query          TEXT NOT NULL,
    query_norm     TEXT NOT NULL,        -- lowercase + collapsed-whitespace for exact dedup
    answer         TEXT NOT NULL,
    retrieval_json TEXT,                  -- top-K chunks at time of write
    intent         TEXT,
    strategy       TEXT,
    created_at     REAL NOT NULL,
    last_hit_at    REAL NOT NULL,
    n_hits         INTEGER NOT NULL DEFAULT 0,
    ttl_seconds    REAL NOT NULL,
    index_version  INTEGER NOT NULL,     -- entries from old hash nets become invalid
    embed_row      INTEGER NOT NULL      -- row in the embeddings .npy file (= faiss_row)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_query_norm
    ON cache_entries(query_norm);
CREATE INDEX IF NOT EXISTS idx_cache_index_version
    ON cache_entries(index_version);
CREATE INDEX IF NOT EXISTS idx_cache_last_hit
    ON cache_entries(last_hit_at);

-- Singleton stats row. Guarded by CHECK (id=1).
CREATE TABLE IF NOT EXISTS cache_stats (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    n_lookups       INTEGER NOT NULL DEFAULT 0,
    n_hits          INTEGER NOT NULL DEFAULT 0,
    n_writes        INTEGER NOT NULL DEFAULT 0,
    n_evictions    INTEGER NOT NULL DEFAULT 0,
    total_lookup_ms REAL NOT NULL DEFAULT 0.0,
    last_reset_at   REAL NOT NULL
);
