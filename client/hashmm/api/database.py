"""HashMM-RAG v10.0 — SQLite persistence.

Tables:
  users              — auth
  models             — LLM config
  knowledge_bases    — KB metadata
  audit_logs         — operations log
  conversations      — chat sessions (NEW: replaces PersistentMemory.sessions)
  messages           — chat messages (NEW: replaces localStorage + PersistentMemory.history)
  conversation_files — per-chat files (NEW: replaces global data/files/)
"""
from __future__ import annotations
import hashlib, json, os, re, secrets, sqlite3, threading, time, uuid
from pathlib import Path
from contextlib import contextmanager
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.database")

# 数据根绝对路径锚点：避免相对路径在不同 cwd（如 execute_code 改变工作目录后）解析不一致，
# 导致 create_file 存盘目录 ≠ 下载读取目录（表现为预览 200、下载 404）。
# 优先用环境变量 HASHMM_DATA_DIR；否则锚定到项目根（本文件位于 hashmm/api/database.py，
# 上溯两级到项目根）。
# 数据根：进程【启动时】把相对 "data" 基于当时的工作目录绝对化一次并固定下来。
# uvicorn 在项目根（/root/autodl-tmp）启动，故 Path("data").resolve() = /root/autodl-tmp/data。
# 固定为绝对路径后，即使后续 execute_code 改变 cwd，也不会再漂移（这是之前404的根因）。
# 可用 HASHMM_DATA_DIR 显式覆盖。
_env_dir = os.environ.get("HASHMM_DATA_DIR", "").strip()
DATA_ROOT = Path(_env_dir).expanduser().resolve() if _env_dir else Path("data").resolve()
_env_db = os.environ.get("HASHMM_DB_PATH", "").strip()
DB_PATH = Path(_env_db).expanduser().resolve() if _env_db else DATA_ROOT / "hashmm.sqlite"
CONV_FILES_ROOT = (DATA_ROOT / "conversations")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    display_name TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    salt        TEXT NOT NULL,
    role        TEXT DEFAULT 'user' CHECK(role IN ('admin','user','viewer')),
    created_at  REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS models (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    provider    TEXT DEFAULT 'openai',
    base_url    TEXT DEFAULT '',
    api_key_enc TEXT DEFAULT '',
    model_name  TEXT DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    temperature REAL DEFAULT 0.1,
    max_tokens  INTEGER DEFAULT 16384,
    config_json TEXT DEFAULT '{}',
    created_by  TEXT DEFAULT '',
    created_at  REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    allowed_roles TEXT DEFAULT '["admin","user"]',
    created_by  TEXT DEFAULT '',
    created_at  REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    username TEXT DEFAULT '',
    action  TEXT,
    detail  TEXT DEFAULT '',
    ip      TEXT DEFAULT '',
    ts      REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id, ts DESC);

-- v10.0: Conversations (replaces PersistentMemory.sessions)
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'anonymous',
    title       TEXT DEFAULT '新对话',
    pinned      INTEGER DEFAULT 0,
    created_at  REAL DEFAULT (strftime('%s','now')),
    updated_at  REAL DEFAULT (strftime('%s','now')),
    content_activity_at REAL DEFAULT (strftime('%s','now')),
    metadata_updated_at REAL DEFAULT (strftime('%s','now')),
    sync_observed_at REAL DEFAULT 0,
    metadata    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at DESC);

-- v10.0: Messages (replaces localStorage hmm_s + PersistentMemory.history)
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    conv_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    content     TEXT DEFAULT '',
    thinking    TEXT DEFAULT '',
    tool_calls  TEXT DEFAULT '[]',
    files       TEXT DEFAULT '[]',
    sources     TEXT DEFAULT '[]',
    groundings  TEXT DEFAULT '{}',
    run_manifest TEXT DEFAULT '{}',
    suggestions TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'complete',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    created_at  REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, created_at);

-- V358: incremental, durable context compaction checkpoints.  Full messages
-- remain authoritative in ``messages``; this table only bounds the model's
-- working window and can be rebuilt at any time.
CREATE TABLE IF NOT EXISTS conversation_compactions (
    conv_id          TEXT PRIMARY KEY,
    summary          TEXT NOT NULL DEFAULT '',
    through_rowid    INTEGER NOT NULL DEFAULT 0,
    source_messages  INTEGER NOT NULL DEFAULT 0,
    estimated_tokens INTEGER NOT NULL DEFAULT 0,
    compaction_count INTEGER NOT NULL DEFAULT 0,
    last_trigger     TEXT NOT NULL DEFAULT 'auto',
    updated_at       REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- V348: durable, owner-bound, one-shot approvals for high-risk Agent tools.
-- The authoritative row stays server-side; only a redacted projection is
-- copied into messages.run_manifest for desktop/App presentation.
CREATE TABLE IF NOT EXISTS tool_approval_requests (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    conv_id     TEXT NOT NULL,
    message_id  TEXT DEFAULT '',
    work_run_id TEXT NOT NULL DEFAULT '',
    step_id     TEXT NOT NULL DEFAULT '',
    call_id     TEXT NOT NULL DEFAULT '',
    scope_json  TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    args_json   TEXT NOT NULL DEFAULT '{}',
    cwd         TEXT DEFAULT '',
    reason      TEXT DEFAULT '',
    risk        TEXT DEFAULT 'high',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    decided_at  REAL DEFAULT 0,
    expires_at  REAL NOT NULL,
    consumed_at REAL DEFAULT 0,
    decided_by  TEXT DEFAULT '',
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tool_approval_owner
    ON tool_approval_requests(user_id, conv_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_tool_approval_fingerprint
    ON tool_approval_requests(user_id, conv_id, fingerprint, status);
CREATE INDEX IF NOT EXISTS idx_tool_approval_run
    ON tool_approval_requests(user_id, work_run_id, status, created_at DESC);

-- V704: durable OCR work queue.  File bytes stay in the owner-scoped file
-- store; this table contains only the queue identity, retry state and a
-- content-addressed result locator.
CREATE TABLE IF NOT EXISTS ocr_jobs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    conv_id         TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    resource_id     TEXT NOT NULL DEFAULT '',
    resource_revision INTEGER NOT NULL DEFAULT 1,
    engine          TEXT NOT NULL DEFAULT 'paddleocr',
    engine_requested TEXT NOT NULL DEFAULT 'paddleocr',
    engine_resolved TEXT NOT NULL DEFAULT '',
    lang            TEXT NOT NULL DEFAULT 'chi_sim+eng',
    priority        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued',
    total_pages     INTEGER NOT NULL DEFAULT 0,
    processed_pages INTEGER NOT NULL DEFAULT 0,
    failed_pages    INTEGER NOT NULL DEFAULT 0,
    progress        REAL NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    error_code      TEXT NOT NULL DEFAULT '',
    result_path     TEXT NOT NULL DEFAULT '',
    result_state    TEXT NOT NULL DEFAULT '',
    lease_owner     TEXT NOT NULL DEFAULT '',
    lease_token     TEXT NOT NULL DEFAULT '',
    lease_expires_at REAL NOT NULL DEFAULT 0,
    heartbeat_at    REAL NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, sha256, engine, lang)
);
CREATE INDEX IF NOT EXISTS idx_ocr_jobs_claim
    ON ocr_jobs(status, next_attempt_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_ocr_jobs_owner
    ON ocr_jobs(user_id, conv_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ocr_job_links (
    job_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    conv_id         TEXT NOT NULL DEFAULT '',
    project_id      TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (job_id, user_id, conv_id, project_id, filename),
    FOREIGN KEY (job_id) REFERENCES ocr_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ocr_job_links_owner_conv
    ON ocr_job_links(user_id, conv_id, created_at DESC);

CREATE TABLE IF NOT EXISTS run_checkpoints (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    generation      INTEGER NOT NULL DEFAULT 1,
    reason          TEXT NOT NULL DEFAULT 'periodic',
    state_json      TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, run_id, generation),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_checkpoints_owner_run
    ON run_checkpoints(user_id, run_id, generation DESC);

-- V349: real user failures become human-reviewed evaluation candidates.
-- Query, answer and run evidence are captured from the authoritative message
-- row, never trusted from client-provided prose.
CREATE TABLE IF NOT EXISTS message_feedback_cases (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    conv_id         TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    rating          TEXT NOT NULL DEFAULT '',
    reason_code     TEXT DEFAULT '',
    comment         TEXT DEFAULT '',
    query           TEXT DEFAULT '',
    answer          TEXT DEFAULT '',
    evidence_json   TEXT DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    reference_answer TEXT DEFAULT '',
    eval_case_id    TEXT DEFAULT '',
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    reviewed_at     REAL DEFAULT 0,
    reviewed_by     TEXT DEFAULT '',
    UNIQUE(user_id, message_id),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feedback_review
    ON message_feedback_cases(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_owner
    ON message_feedback_cases(user_id, updated_at DESC);

-- V373: one durable, owner-bound runtime for Chat turns, long loops, Agent
-- teams and tool/artifact work. ``change_seq`` is the account feed cursor;
-- ``event_seq`` is the resumable cursor inside one run.  Raw prompts, file
-- bodies and tool arguments never belong in these tables.
CREATE TABLE IF NOT EXISTS work_runtime_meta (
    id          TEXT PRIMARY KEY,
    value       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS work_runs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    conv_id     TEXT DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'chat',
    source_id   TEXT NOT NULL,
    title       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    revision    INTEGER NOT NULL DEFAULT 1,
    event_seq   INTEGER NOT NULL DEFAULT 0,
    change_seq  INTEGER NOT NULL DEFAULT 0,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    active_generation_id TEXT NOT NULL DEFAULT '',
    project_id  TEXT NOT NULL DEFAULT '',
    execution_target_json TEXT NOT NULL DEFAULT '{}',
    autonomy_level INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, kind, source_id)
);
CREATE INDEX IF NOT EXISTS idx_work_runs_owner_change
    ON work_runs(user_id, change_seq);
CREATE INDEX IF NOT EXISTS idx_work_runs_owner_conv
    ON work_runs(user_id, conv_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS work_events (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    status      TEXT DEFAULT '',
    summary     TEXT DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    expected_revision INTEGER NOT NULL DEFAULT 0,
    generation_id TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id, seq),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_events_run_seq
    ON work_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_work_events_owner_time
    ON work_events(user_id, created_at DESC);
-- V628-V668: account-scoped replication journal.  It contains only bounded
-- Work protocol projections, never prompts, file bodies or tool arguments.
CREATE TABLE IF NOT EXISTS work_sync_changes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    change_seq  INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    operation   TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    UNIQUE(user_id, change_seq)
);
CREATE INDEX IF NOT EXISTS idx_work_sync_owner_cursor
    ON work_sync_changes(user_id, change_seq);
-- V402: a generation is constructed completely before one transaction makes
-- it visible to desktop/App readers.  Manifests contain bounded projections
-- and content hashes, never source bodies, prompts or tool arguments.
CREATE TABLE IF NOT EXISTS work_generations (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    generation      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    idempotency_key TEXT NOT NULL,
    manifest_hash   TEXT NOT NULL,
    manifest_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    activated_at    REAL NOT NULL DEFAULT 0,
    UNIQUE(run_id, generation),
    UNIQUE(user_id, run_id, idempotency_key),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_generations_owner_run
    ON work_generations(user_id, run_id, generation DESC);
-- V408: one protocol for Office, canvas, files and generated deliverables.
-- The locator is a bounded owner-scoped reference; file bodies remain in the
-- artifact store/cache and are not duplicated into SQLite.
CREATE TABLE IF NOT EXISTS artifact_revisions (
    id              TEXT PRIMARY KEY,
    artifact_id     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    media_type      TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    locator_json    TEXT NOT NULL DEFAULT '{}',
    verification    TEXT NOT NULL DEFAULT 'pending',
    created_at      REAL NOT NULL,
    UNIQUE(user_id, artifact_id, revision),
    UNIQUE(user_id, artifact_id, content_hash),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_artifact_revisions_owner_run
    ON artifact_revisions(user_id, run_id, created_at DESC);
-- V374: idempotent, owner-bound work control commands.  A command is claimed
-- before a side effect is dispatched.  An interrupted ``executing`` row is
-- intentionally not replayed automatically because external tools may have
-- already accepted the operation.
CREATE TABLE IF NOT EXISTS work_commands (
    id              TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'executing',
    result_json     TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    finished_at     REAL DEFAULT 0,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_commands_owner_run_time
    ON work_commands(user_id, run_id, created_at DESC);
-- V400: idempotent user decisions for delivery review.  These rows are
-- deliberately separate from executor commands: accepting a result or asking
-- for changes is a durable user fact, not an instruction that may be replayed
-- against an external tool after a crash.
CREATE TABLE IF NOT EXISTS work_decisions (
    id              TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'applied',
    note             TEXT NOT NULL DEFAULT '',
    result_json     TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_decisions_owner_run_time
    ON work_decisions(user_id, run_id, created_at DESC);

-- V439-V473: project-bound Work OS control plane.  Execution leases are
-- owner-scoped and generation based; an expired lease is never interpreted as
-- authority.  Artifact annotations contain only a bounded locator and note,
-- never an uploaded file body or a browser page body.
CREATE TABLE IF NOT EXISTS work_execution_leases (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    holder_type     TEXT NOT NULL,
    holder_id       TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'active',
    generation      INTEGER NOT NULL DEFAULT 1,
    acquired_at     REAL NOT NULL,
    heartbeat_at    REAL NOT NULL,
    expires_at      REAL NOT NULL,
    idempotency_key TEXT NOT NULL,
    released_at     REAL NOT NULL DEFAULT 0,
    UNIQUE(user_id, run_id, idempotency_key),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_execution_leases_owner_run
    ON work_execution_leases(user_id, run_id, acquired_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_execution_leases_expiry
    ON work_execution_leases(state, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_execution_leases_one_active
    ON work_execution_leases(run_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS work_artifact_annotations (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    artifact_id     TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL DEFAULT 0,
    target_json     TEXT NOT NULL DEFAULT '{}',
    note            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open',
    impacted_json   TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL,
    created_at      REAL NOT NULL,
    resolved_at     REAL NOT NULL DEFAULT 0,
    UNIQUE(user_id, run_id, idempotency_key),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_artifact_annotations_owner_run
    ON work_artifact_annotations(user_id, run_id, created_at DESC);

-- V459-V473: evidence-derived reusable workflows and a governed proactive
-- inbox.  A workflow cannot become published from one model-generated trace:
-- the service requires two owner-scoped runs with the same receipt signature
-- and a separate explicit user approval.  Proactive rows are suggestions,
-- never background execution authority.
CREATE TABLE IF NOT EXISTS work_workflows (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    source_run_id   TEXT NOT NULL,
    replay_run_id   TEXT NOT NULL DEFAULT '',
    action_hash     TEXT NOT NULL,
    action_json     TEXT NOT NULL DEFAULT '[]',
    parameter_json  TEXT NOT NULL DEFAULT '{}',
    risk_json       TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'candidate',
    revision        INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    published_at    REAL NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(user_id,idempotency_key),
    FOREIGN KEY (source_run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_work_workflows_owner_updated
    ON work_workflows(user_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_workflows_owner_hash
    ON work_workflows(user_id,action_hash,status);
CREATE TABLE IF NOT EXISTS proactive_work_items (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    reason          TEXT NOT NULL,
    risk            TEXT NOT NULL DEFAULT 'low',
    action          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'suggested',
    budget_key      TEXT NOT NULL DEFAULT '',
    result_json     TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    acted_at        REAL NOT NULL DEFAULT 0,
    UNIQUE(user_id,idempotency_key),
    FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_proactive_work_owner_status
    ON proactive_work_items(user_id,status,updated_at DESC);

-- V383: durable execution identity for every delegated/main Agent.  The
-- session row is authoritative; WorkRuntime contains its bounded cross-device
-- projection.  A restart reconciles in-flight rows to ``interrupted`` rather
-- than pretending a model or side-effecting tool call is still alive.
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    contract TEXT NOT NULL,
    parent_session_id TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    scope_id TEXT NOT NULL DEFAULT '',
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    steps_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    work_run_id TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    generation INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    started_at REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_owner_updated
    ON agent_sessions(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent
    ON agent_sessions(owner_id, parent_session_id, created_at);

-- V392: persistent Agent Mesh control plane.  Team JSON remains a
-- compatibility projection; task dependencies and mailbox delivery live in
-- SQLite so restarts cannot lose lineage, corrections or delivery state.
CREATE TABLE IF NOT EXISTS agent_mesh_tasks (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    parent_task_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    result_hash TEXT NOT NULL DEFAULT '',
    upstream_failures INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner_id, team_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_mesh_tasks_team
    ON agent_mesh_tasks(owner_id, team_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_mesh_tasks_session
    ON agent_mesh_tasks(owner_id, session_id);
CREATE TABLE IF NOT EXISTS agent_mesh_edges (
    owner_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    from_task_id TEXT NOT NULL,
    to_task_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'requires',
    created_at REAL NOT NULL,
    PRIMARY KEY(owner_id, team_id, from_task_id, to_task_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_agent_mesh_edges_target
    ON agent_mesh_edges(owner_id, team_id, to_task_id);
CREATE TABLE IF NOT EXISTS agent_mailbox_messages (
    message_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    sender_session_id TEXT NOT NULL DEFAULT '',
    recipient_session_id TEXT NOT NULL,
    sender_kind TEXT NOT NULL DEFAULT 'agent',
    message_type TEXT NOT NULL DEFAULT 'result',
    body TEXT NOT NULL DEFAULT '',
    body_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    lease_token_hash TEXT NOT NULL DEFAULT '',
    lease_expires_at REAL NOT NULL DEFAULT 0,
    acked_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner_id, team_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_mailbox_recipient
    ON agent_mailbox_messages(owner_id, recipient_session_id, status, available_at);
CREATE INDEX IF NOT EXISTS idx_agent_mailbox_team
    ON agent_mailbox_messages(owner_id, team_id, created_at);

-- Context lifecycle checkpoints store only bounded assembled state.  Raw
-- credentials, tool arguments and full uploaded-file bodies never belong here.
CREATE TABLE IF NOT EXISTS context_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(owner_id, session_id, generation)
);
CREATE INDEX IF NOT EXISTS idx_context_checkpoints_owner_session
    ON context_checkpoints(owner_id, session_id, generation DESC);

-- One-time App -> WebView bootstrap capabilities. Only a digest of the code
-- is stored; account tokens remain encrypted and a code is atomically consumed.
CREATE TABLE IF NOT EXISTS webview_auth_codes (
    code_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    access_cipher TEXT NOT NULL,
    refresh_cipher TEXT NOT NULL DEFAULT '',
    expires_at REAL NOT NULL,
    consumed_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webview_auth_expiry
    ON webview_auth_codes(expires_at, consumed_at);

-- V416: owner-bound remote sessions and an integrity-linked audit ledger.
-- Active/pending sessions are never resumed after a backend restart; the
-- remote registry reconciles them to ``interrupted`` before serving traffic.
CREATE TABLE IF NOT EXISTS remote_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    host_device_id TEXT NOT NULL,
    viewer_device_id TEXT NOT NULL,
    requested_scopes_json TEXT NOT NULL DEFAULT '[]',
    granted_scopes_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'pending',
    generation INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL DEFAULT 0,
    work_id TEXT NOT NULL DEFAULT '',
    work_run_id TEXT NOT NULL DEFAULT '',
    predecessor_session_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_updated
    ON remote_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_state
    ON remote_sessions(user_id, state, expires_at);
CREATE TABLE IF NOT EXISTS remote_audit_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event TEXT NOT NULL,
    at REAL NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    previous_hash TEXT NOT NULL DEFAULT '',
    event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_audit_owner_time
    ON remote_audit_events(user_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_audit_session_time
    ON remote_audit_events(user_id, session_id, at);

-- V418/V419: server-side admission of real network and soak receipts.  A row
-- records measured facts and criteria; creating a row does not itself prove
-- that a public-NAT or 24-hour run succeeded.
CREATE TABLE IF NOT EXISTS remote_acceptance_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0,
    device_count INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_acceptance_owner_kind
    ON remote_acceptance_runs(user_id, kind, created_at DESC);

-- v10.0: Per-conversation files
CREATE TABLE IF NOT EXISTS conversation_files (
    id          TEXT PRIMARY KEY,
    conv_id     TEXT NOT NULL,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL,
    size        INTEGER DEFAULT 0,
    mime_type   TEXT DEFAULT '',
    created_at  REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cf_conv ON conversation_files(conv_id);
"""

# ── API-key encryption ──
# Fernet (AES-128-CBC + HMAC) via hashmm.secrets_crypto, with transparent
# backward-compatible reading of pre-existing XOR ciphertext (no key loss on
# upgrade) and graceful degradation if cryptography is missing. The thin _enc/
# _dec wrappers keep every existing call site unchanged.
from hashmm.secrets_crypto import (  # noqa: E402
    encrypt_secret as _enc,
    decrypt_secret as _dec,
    rewrap_legacy_fernet_ciphertext as _rewrap_legacy_fernet,
)


def _uid(): return secrets.token_hex(8)


# ── V103.49: 模型配置镜像（DB 损坏不丢配置）──────────────────────────────
# 模型配置（provider/base_url/key/model_name/默认标记）原本只存在易损的 sqlite。
# 一旦 db 文件损坏重建，配置全丢，用户必须重新到管理后台配模型，否则整个系统
# 没有 LLM（agent/direct/RAG 生成全部输出"LLM 未配置"）。这对生产系统不可接受。
# 因此把模型配置镜像到一个独立 JSON（像向量索引那样独立于 sqlite），DB 重建后
# 自动恢复。镜像里的 api_key 仍用同一套 secrets_crypto 加密存储。
_MODELS_MIRROR = Path(os.environ.get("HASHMM_MODELS_MIRROR", "data/models_backup.json"))


def _mirror_models() -> None:
    """把当前 models 表完整镜像到 JSON（含加密后的 key、默认标记）。任何写模型的
    操作后调用。失败不影响主流程。"""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT id,name,provider,base_url,api_key_enc,model_name,is_default,"
                "temperature,max_tokens,config_json,created_by FROM models"
            ).fetchall()
        data = [dict(r) for r in rows]
        _MODELS_MIRROR.parent.mkdir(parents=True, exist_ok=True)
        _MODELS_MIRROR.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log_suppressed(logger, e)


def _restore_models_from_mirror(conn) -> int:
    """DB 重建后，若 models 表为空且镜像存在，则从镜像恢复。返回恢复条数。
    用传入的 conn（在 init_db 的同一事务里调用），避免重入连接池死锁。"""
    try:
        if not _MODELS_MIRROR.exists():
            return 0
        existing = conn.execute("SELECT COUNT(*) AS n FROM models").fetchone()
        if existing and int(existing["n"]) > 0:
            return 0  # 表里已有配置，不覆盖
        data = json.loads(_MODELS_MIRROR.read_text(encoding="utf-8"))
        if not data:
            return 0
        n = 0
        for m in data:
            try:
                conn.execute(
                    "INSERT INTO models (id,name,provider,base_url,api_key_enc,model_name,"
                    "is_default,temperature,max_tokens,config_json,created_by) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (m.get("id") or _uid(), m.get("name", ""), m.get("provider", ""),
                     m.get("base_url", ""), m.get("api_key_enc", ""), m.get("model_name", ""),
                     int(m.get("is_default", 0) or 0), m.get("temperature", 0.1),
                     m.get("max_tokens", 4096), m.get("config_json", "{}"),
                     m.get("created_by", "system")))
                n += 1
            except Exception as _e:
                log_suppressed(logger, _e)
        if n:
            logger.warning(f"[DB] 已从镜像恢复 {n} 个模型配置（DB 曾损坏重建）。"
                           f"无需到管理后台重配模型。")
        return n
    except Exception as e:
        log_suppressed(logger, e)
        return 0


def _rewrap_model_secrets(conn) -> int:
    """One-time authenticated rewrap for models encrypted by an old data key."""
    changed = 0
    rows = conn.execute(
        "SELECT id,api_key_enc FROM models WHERE api_key_enc<>''"
    ).fetchall()
    for row in rows:
        model_id = row["id"]
        old_cipher = str(row["api_key_enc"] or "")
        new_cipher = _rewrap_legacy_fernet(old_cipher)
        if not new_cipher:
            continue
        result = conn.execute(
            "UPDATE models SET api_key_enc=? WHERE id=? AND api_key_enc=?",
            (new_cipher, model_id, old_cipher),
        )
        changed += max(0, int(result.rowcount or 0))
    return changed


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()

@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = _pool_get()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        _pool_put(c)


# v29: Connection pool. v14: backend-aware (SQLite default, PostgreSQL opt-in).
from queue import Queue, Empty as _QEmpty
from hashmm.api import db_backend

_pool: Queue | None = None
_pool_connection_ids: set[int] = set()
_pool_lock = threading.RLock()
_db_init_lock = threading.RLock()
# Pool sized for many concurrent users. SQLite serializes writes but allows
# concurrent reads in WAL mode; a larger pool lets read traffic flow freely.
_POOL_SIZE = int(os.environ.get("HASHMM_DB_POOL_SIZE", "16"))


def _pool_init():
    global _pool, _pool_connection_ids
    with _pool_lock:
        if _pool is not None:
            return
        new_pool = Queue(maxsize=_POOL_SIZE)
        connection_ids: set[int] = set()
        for _ in range(_POOL_SIZE):
            connection = db_backend.make_conn(DB_PATH)
            connection_ids.add(id(connection))
            new_pool.put(connection)
        _pool = new_pool
        _pool_connection_ids = connection_ids


def _pool_get():
    """Get a connection from the pool (or create new if pool empty)."""
    _pool_init()
    with _pool_lock:
        active_pool = _pool
    try:
        return active_pool.get(timeout=10)  # type: ignore[union-attr]
    except _QEmpty:
        # All connections busy — create a temporary one (returned, not pooled).
        return db_backend.make_conn(DB_PATH)


def _pool_put(c: sqlite3.Connection):
    """Return a connection to the pool."""
    with _pool_lock:
        active_pool = _pool
        is_active_connection = id(c) in _pool_connection_ids
        if (
            active_pool is not None
            and is_active_connection
            and not active_pool.full()
        ):
            active_pool.put(c)
            return
    c.close()


def _close_pool() -> None:
    """Invalidate every pooled handle before replacing/recovering the DB file.

    Connections currently borrowed by another thread cannot safely be closed
    here.  Clearing their IDs makes ``_pool_put`` close them on return instead
    of leaking a handle to the pre-recovery inode back into the fresh pool.
    """
    global _pool, _pool_connection_ids
    with _pool_lock:
        old_pool = _pool
        _pool = None
        _pool_connection_ids = set()
    if old_pool is None:
        return
    while True:
        try:
            connection = old_pool.get_nowait()
        except _QEmpty:
            break
        try:
            connection.close()
        except Exception as exc:
            log_suppressed(logger, exc, "database.pool.close")

def _recover_if_corrupt():
    """启动前检查 db 文件是否损坏；损坏就备份重建，避免整个服务起不来。

    关键：已索引的向量/BM25 在独立文件、**不在这个 sqlite 里**，所以重建只丢配置
    （用户/模型/知识库等元数据），**不丢知识库**。admin/admin123 会重新生成，模型需在后台重配。
    """
    if not DB_PATH.exists():
        return
    conn = None
    healthy = True
    err: Exception | None = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        # ① 整库页扫描（quick_check 会读所有页，能发现"某张表的页损坏"这类只在读该表时才炸的问题；
        #    旧版只读 sqlite_master 的 schema 页，所以 users 表页损坏时漏检、启动才崩）。
        try:
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not (row and str(row[0]).strip().lower() == "ok"):
                healthy = False
                err = sqlite3.DatabaseError(f"quick_check: {row[0] if row else 'unknown'}")
        except sqlite3.DatabaseError as e:
            healthy, err = False, e
        # ② 逐表轻量读探针（双保险：直接摸每张已存在的表的首页，命中 malformed 立刻暴露）。
        if healthy:
            try:
                tbls = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
                for t in tbls:
                    conn.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
            except sqlite3.DatabaseError as e:
                healthy, err = False, e
    except sqlite3.DatabaseError as e:
        healthy, err = False, e
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    if healthy:
        return
    e = err or sqlite3.DatabaseError("database health check failed")
    msg = str(e).lower()
    if not ("malformed" in msg or "not a database" in msg or "encrypted" in msg
            or "disk image" in msg or "quick_check" in msg or "corrupt" in msg):
        raise e  # 其它 DatabaseError 不擅自处理
    # 损坏确认 → 备份重建（下面沿用原有恢复逻辑）
    if True:
        ts = int(time.time())
        bak = DB_PATH.with_name(DB_PATH.name + f".corrupt-{ts}")
        try:
            DB_PATH.rename(bak)
            logger.error(
                f"[DB] 数据库文件损坏（{e}），已备份到 {bak} 并将重建新库。"
                f"注意：重建会清空本地的对话与历史消息！若已正确配置 Supabase service_role key，"
                f"打开对话时会自动从云端拉回历史；否则历史将丢失。"
                f"（已索引的向量/BM25 在独立文件、不受影响；admin/admin123 重新生成，模型在管理后台重配）")
        except Exception as e2:
            log_suppressed(logger, e2)
            try:
                DB_PATH.unlink()
                logger.error(f"[DB] 数据库损坏且备份失败，已删除损坏文件并重建：{e}")
            except Exception as e3:
                log_suppressed(logger, e3)
        # 清掉 WAL/SHM 旁路文件，避免新库读到残留
        for _sfx in ("-wal", "-shm"):
            try:
                _p = DB_PATH.with_name(DB_PATH.name + _sfx)
                if _p.exists():
                    _p.unlink()
            except Exception:
                pass
        # V196: 优先用本地快照恢复历史（对话/消息/记忆等），不依赖云端 service key。
        # 校验快照可读后整文件还原；还原成功则后续 executescript 的 CREATE IF NOT EXISTS 不会覆盖数据。
        try:
            _snap = DB_PATH.with_name(DB_PATH.name + ".snapshot")
            if _snap.exists():
                _t = sqlite3.connect(str(_snap))
                # 深度校验快照（quick_check + 逐表探针）——快照本身也可能坏，坏就不还原，避免再次崩启动。
                _snap_ok = True
                try:
                    _row = _t.execute("PRAGMA quick_check(1)").fetchone()
                    _snap_ok = bool(_row) and str(_row[0]).strip().lower() == "ok"
                    if _snap_ok:
                        for _tb in [r[0] for r in _t.execute(
                                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]:
                            _t.execute(f'SELECT * FROM "{_tb}" LIMIT 1').fetchone()
                except sqlite3.DatabaseError:
                    _snap_ok = False
                _t.close()
                if _snap_ok:
                    import shutil as _sh
                    _sh.copyfile(str(_snap), str(DB_PATH))
                    logger.warning("[DB] 已从本地快照恢复对话与历史（无需云端 service key）。")
                else:
                    logger.error("[DB] 本地快照也已损坏，跳过还原，直接重建空库（历史需靠云端 service key 回填）。")
        except Exception as _e5:
            log_suppressed(logger, _e5)


_SNAPSHOT_THREAD_STARTED = False

def _snapshot_db():
    """把当前 sqlite 在线快照到 .snapshot（崩溃/损坏后本地即可恢复对话/消息/记忆，不依赖云端 service key）。
    用 SQLite 在线备份 API，WAL 安全；先写 .tmp 再原子替换，避免半截快照。失败安全。"""
    try:
        if not DB_PATH.exists():
            return
        snap = DB_PATH.with_name(DB_PATH.name + ".snapshot")
        tmp = DB_PATH.with_name(DB_PATH.name + ".snapshot.tmp")
        src = sqlite3.connect(str(DB_PATH))
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)          # 在线一致性快照（不锁写、WAL 安全）
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        chk = sqlite3.connect(str(tmp))  # 校验快照可读再替换
        try:
            chk.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        finally:
            chk.close()
        os.replace(str(tmp), str(snap))
    except Exception as e:
        log_suppressed(logger, e)

def _start_snapshot_thread(interval_sec: int = 300):
    """后台每隔 interval_sec 做一次本地快照（默认 5 分钟，RPO≈5min）。仅启动一次。"""
    global _SNAPSHOT_THREAD_STARTED
    if _SNAPSHOT_THREAD_STARTED:
        return
    _SNAPSHOT_THREAD_STARTED = True
    import threading
    def _loop():
        while True:
            time.sleep(max(60, interval_sec))
            _snapshot_db()
    threading.Thread(target=_loop, daemon=True, name="db-snapshot").start()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the columns of one trusted, internal SQLite table name."""
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    """Repair an additive column migration without masking unrelated DB errors."""
    if column in _table_columns(conn, table):
        return
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')


def _preflight_legacy_schema_indexes(conn: sqlite3.Connection) -> None:
    """Repair columns required by indexes embedded in ``_SCHEMA``.

    SQLite evaluates ``CREATE INDEX`` while executing the schema script.  For
    an existing pre-V700 table, ``CREATE TABLE IF NOT EXISTS`` does not add new
    columns, so the later normal migration phase is never reached.  Keep this
    deliberately small: only additive columns referenced by an index inside
    ``_SCHEMA`` belong here.
    """
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "tool_approval_requests" in tables:
        _ensure_column(
            conn, "tool_approval_requests", "work_run_id",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn, "tool_approval_requests", "step_id",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn, "tool_approval_requests", "call_id",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn, "tool_approval_requests", "scope_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )


def _ensure_work_runtime_v24_schema(conn: sqlite3.Connection) -> None:
    """Make a fresh, legacy, or partially upgraded work ledger V24-compatible.

    `_SCHEMA` cannot create an index over a column that is absent from an
    existing `CREATE TABLE IF NOT EXISTS` table.  Keep these additive repairs
    ahead of the V24 index, independent of `schema_version`, so a process that
    was interrupted between ALTER TABLE and the version update is recoverable.
    """
    _ensure_column(
        conn, "work_runs", "active_generation_id", "TEXT NOT NULL DEFAULT ''"
    )
    _ensure_column(
        conn, "work_events", "idempotency_key", "TEXT NOT NULL DEFAULT ''"
    )
    _ensure_column(
        conn, "work_events", "expected_revision", "INTEGER NOT NULL DEFAULT 0"
    )
    _ensure_column(
        conn, "work_events", "generation_id", "TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_work_events_owner_idempotency "
        "ON work_events(user_id,run_id,idempotency_key) "
        "WHERE idempotency_key<>''"
    )


def _ensure_remote_work_v26_schema(conn: sqlite3.Connection) -> None:
    """Repair V420 remote rows before admitting the owner/work index.

    The legacy ``work_id`` column accepted an arbitrary client string.  It is
    retained for one compatibility release, but only a same-owner WorkRuntime
    row may be copied into the new authoritative ``work_run_id`` column.
    """
    _ensure_column(
        conn, "remote_sessions", "work_run_id", "TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        "UPDATE remote_sessions SET work_run_id=work_id "
        "WHERE work_run_id='' AND work_id<>'' AND EXISTS ("
        "SELECT 1 FROM work_runs WHERE work_runs.id=remote_sessions.work_id "
        "AND work_runs.user_id=remote_sessions.user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_work "
        "ON remote_sessions(user_id,work_run_id,updated_at DESC)"
    )


def _ensure_remote_handoff_v27_schema(conn: sqlite3.Connection) -> None:
    """Add owner-scoped remote lineage without trusting legacy client text."""
    _ensure_column(
        conn, "remote_sessions", "predecessor_session_id",
        "TEXT NOT NULL DEFAULT ''",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_predecessor "
        "ON remote_sessions(user_id,predecessor_session_id,updated_at DESC)"
    )


def _ensure_work_os_v28_schema(conn: sqlite3.Connection) -> None:
    """Repair the additive V439-V473 Work OS schema after an interrupted update."""
    _ensure_column(conn, "work_runs", "project_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(
        conn, "work_runs", "execution_target_json", "TEXT NOT NULL DEFAULT '{}'"
    )
    _ensure_column(
        conn, "work_runs", "autonomy_level", "INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_runs_owner_project "
        "ON work_runs(user_id,project_id,updated_at DESC)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS work_execution_leases (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
        holder_type TEXT NOT NULL, holder_id TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'active', generation INTEGER NOT NULL DEFAULT 1,
        acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
        expires_at REAL NOT NULL, idempotency_key TEXT NOT NULL,
        released_at REAL NOT NULL DEFAULT 0,
        UNIQUE(user_id,run_id,idempotency_key),
        FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_execution_leases_owner_run "
        "ON work_execution_leases(user_id,run_id,acquired_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_execution_leases_expiry "
        "ON work_execution_leases(state,expires_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_work_execution_leases_one_active "
        "ON work_execution_leases(run_id) WHERE state='active'"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS work_artifact_annotations (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL, artifact_revision INTEGER NOT NULL DEFAULT 0,
        target_json TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open', impacted_json TEXT NOT NULL DEFAULT '[]',
        idempotency_key TEXT NOT NULL, created_at REAL NOT NULL,
        resolved_at REAL NOT NULL DEFAULT 0,
        UNIQUE(user_id,run_id,idempotency_key),
        FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_artifact_annotations_owner_run "
        "ON work_artifact_annotations(user_id,run_id,created_at DESC)"
    )


def _ensure_work_kernel_v29_schema(conn: sqlite3.Connection) -> None:
    """Repair the additive V459-V473 workflow/proactive schema."""
    conn.execute("""CREATE TABLE IF NOT EXISTS work_workflows (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
        source_run_id TEXT NOT NULL, replay_run_id TEXT NOT NULL DEFAULT '',
        action_hash TEXT NOT NULL, action_json TEXT NOT NULL DEFAULT '[]',
        parameter_json TEXT NOT NULL DEFAULT '{}', risk_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'candidate', revision INTEGER NOT NULL DEFAULT 1,
        idempotency_key TEXT NOT NULL, published_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(user_id,idempotency_key),
        FOREIGN KEY (source_run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_workflows_owner_updated "
        "ON work_workflows(user_id,updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_work_workflows_owner_hash "
        "ON work_workflows(user_id,action_hash,status)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS proactive_work_items (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, run_id TEXT NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, reason TEXT NOT NULL,
        risk TEXT NOT NULL DEFAULT 'low', action TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'suggested', budget_key TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, acted_at REAL NOT NULL DEFAULT 0,
        UNIQUE(user_id,idempotency_key),
        FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_work_owner_status "
        "ON proactive_work_items(user_id,status,updated_at DESC)"
    )


def _ensure_user_workspace_v30_schema(conn: sqlite3.Connection) -> None:
    """Add the owner-facing project brief used by desktop and App.

    The columns deliberately store user-authored intent, not model-generated
    claims.  ``success_criteria`` is JSON so clients can render a checklist,
    while ``permission_mode`` is a small product vocabulary rather than an
    internal sandbox/environment flag.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        custom_prompt TEXT DEFAULT '',
        created_at REAL DEFAULT (strftime('%s','now')),
        updated_at REAL DEFAULT (strftime('%s','now')))""")
    _ensure_column(conn, "projects", "goal", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "projects", "deliverable", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(
        conn, "projects", "success_criteria", "TEXT NOT NULL DEFAULT '[]'"
    )
    _ensure_column(
        conn, "projects", "permission_mode", "TEXT NOT NULL DEFAULT 'ask'"
    )
    _ensure_column(
        conn, "projects", "status", "TEXT NOT NULL DEFAULT 'active'"
    )
    _ensure_column(conn, "projects", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "projects", "revision", "INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_owner_active "
        "ON projects(user_id,archived,status,updated_at DESC)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS project_resources (
        project_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        conv_id TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL DEFAULT '',
        sha256 TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'reference',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        PRIMARY KEY (project_id,user_id,resource_id),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_resources_owner "
        "ON project_resources(user_id,project_id,created_at DESC)"
    )


def _ensure_conversation_continuity_v40_schema(conn: sqlite3.Connection) -> None:
    """Install the durable conversation index and cross-Chat handoff ledger.

    Conversation bodies remain in ``messages``.  These columns/tables are the
    small owner-scoped index used by the desktop sidebar and by continuity
    handoffs; they must never contain private model reasoning or credentials.
    """
    # Fresh databases still receive these legacy columns in the old numbered
    # migration block below.  Add them here first because the continuity index
    # must also work during a brand-new bootstrap.
    _ensure_column(conn, "conversations", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "conversations", "project_id", "TEXT")
    _ensure_column(conn, "conversations", "revision", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "conversations", "sync_state", "TEXT NOT NULL DEFAULT 'synced'")
    _ensure_column(conn, "conversations", "deleted_at", "REAL")
    # V1100: one overloaded ``updated_at`` made title/pin/project/sync changes
    # look like new Chat content.  Keep it as the legacy replication revision
    # clock, but give navigation a provenance-backed content clock.
    _ensure_column(conn, "conversations", "content_activity_at", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "conversations", "metadata_updated_at", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "conversations", "sync_observed_at", "REAL NOT NULL DEFAULT 0")
    conn.execute("""UPDATE conversations
        SET metadata_updated_at=COALESCE(NULLIF(updated_at,0),created_at,0)
        WHERE COALESCE(metadata_updated_at,0)<=0""")
    conn.execute("DROP INDEX IF EXISTS idx_conv_owner_scope_activity")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conv_owner_scope_activity "
        "ON conversations(user_id,archived,project_id,pinned DESC,content_activity_at DESC,id DESC)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_handoffs (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        project_id TEXT,
        source_conversation_id TEXT NOT NULL,
        target_conversation_id TEXT NOT NULL,
        source_revision INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'sealed',
        payload_json TEXT NOT NULL DEFAULT '{}',
        idempotency_key TEXT NOT NULL,
        stale_reason TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        expires_at REAL NOT NULL DEFAULT 0,
        claimed_at REAL,
        acknowledged_at REAL,
        rejected_at REAL,
        UNIQUE(owner_id, source_conversation_id, idempotency_key),
        FOREIGN KEY (source_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY (target_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )""")
    # This repair is deliberately idempotent and never uses the current time.
    # Run it only after every referenced ledger exists: fresh databases reach
    # this function before conversation_handoffs has been created.
    conn.execute("""UPDATE conversations
        SET content_activity_at = MAX(
            COALESCE(created_at, 0),
            COALESCE((SELECT MAX(m.created_at) FROM messages m
                      WHERE m.conv_id=conversations.id), 0),
            COALESCE((SELECT MAX(cf.created_at) FROM conversation_files cf
                      WHERE cf.conv_id=conversations.id), 0),
            COALESCE((SELECT MAX(wr.updated_at) FROM work_runs wr
                      WHERE wr.user_id=conversations.user_id
                        AND wr.conv_id=conversations.id), 0),
            COALESCE((SELECT MAX(ch.created_at) FROM conversation_handoffs ch
                      WHERE ch.owner_id=conversations.user_id
                        AND ch.target_conversation_id=conversations.id
                        AND ch.status IN ('sealed','claimed','acknowledged')), 0)
        )
        WHERE COALESCE(content_activity_at,0)<=0""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_handoff_target "
        "ON conversation_handoffs(owner_id,target_conversation_id,status,created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_handoff_source "
        "ON conversation_handoffs(owner_id,source_conversation_id,created_at DESC)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_mailbox (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        source_conversation_id TEXT NOT NULL,
        target_conversation_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        payload_json TEXT NOT NULL DEFAULT '{}',
        idempotency_key TEXT NOT NULL,
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        expires_at REAL NOT NULL DEFAULT 0,
        delivered_at REAL,
        read_at REAL,
        acknowledged_at REAL,
        last_error TEXT NOT NULL DEFAULT '',
        UNIQUE(owner_id, source_conversation_id, idempotency_key),
        FOREIGN KEY (source_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY (target_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_mailbox_target "
        "ON conversation_mailbox(owner_id,target_conversation_id,status,created_at DESC)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_tombstones (
        conversation_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        deleted_at REAL NOT NULL,
        reason TEXT NOT NULL DEFAULT 'user_deleted',
        PRIMARY KEY (owner_id, conversation_id)
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_tombstones_owner "
        "ON conversation_tombstones(owner_id,deleted_at,conversation_id)"
    )


def _ensure_tool_approval_v32_schema(conn: sqlite3.Connection) -> None:
    """Bind every approval to one durable run and exact tool-call position.

    Older rows remain valid but deliberately have empty lineage.  New calls use
    the authoritative WorkRun id; this prevents two identical commands in one
    conversation from accidentally sharing a pending approval.
    """
    _ensure_column(
        conn, "tool_approval_requests", "work_run_id", "TEXT NOT NULL DEFAULT ''"
    )
    _ensure_column(
        conn, "tool_approval_requests", "step_id", "TEXT NOT NULL DEFAULT ''"
    )
    _ensure_column(
        conn, "tool_approval_requests", "call_id", "TEXT NOT NULL DEFAULT ''"
    )
    _ensure_column(
        conn, "tool_approval_requests", "scope_json", "TEXT NOT NULL DEFAULT '{}'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_approval_run "
        "ON tool_approval_requests(user_id,work_run_id,status,created_at DESC)"
    )


def _ensure_ocr_queue_v34_schema(conn: sqlite3.Connection) -> None:
    """Add the OCR queue to older databases without touching file bytes."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ocr_jobs (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conv_id TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL, source_path TEXT NOT NULL, sha256 TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT 'tesseract', lang TEXT NOT NULL DEFAULT 'chi_sim+eng',
        status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3, next_attempt_at REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', result_path TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        UNIQUE(user_id, sha256, engine, lang))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_jobs_claim ON ocr_jobs(status,next_attempt_at,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_jobs_owner ON ocr_jobs(user_id,conv_id,updated_at DESC)")
    for name, declaration in (
        ("resource_id", "TEXT NOT NULL DEFAULT ''"),
        ("resource_revision", "INTEGER NOT NULL DEFAULT 1"),
        ("engine_requested", "TEXT NOT NULL DEFAULT 'paddleocr'"),
        ("engine_resolved", "TEXT NOT NULL DEFAULT ''"),
        ("priority", "INTEGER NOT NULL DEFAULT 0"),
        ("total_pages", "INTEGER NOT NULL DEFAULT 0"),
        ("processed_pages", "INTEGER NOT NULL DEFAULT 0"),
        ("failed_pages", "INTEGER NOT NULL DEFAULT 0"),
        ("progress", "REAL NOT NULL DEFAULT 0"),
        ("error_code", "TEXT NOT NULL DEFAULT ''"),
        ("result_state", "TEXT NOT NULL DEFAULT ''"),
        ("lease_owner", "TEXT NOT NULL DEFAULT ''"),
        ("lease_token", "TEXT NOT NULL DEFAULT ''"),
        ("lease_expires_at", "REAL NOT NULL DEFAULT 0"),
        ("heartbeat_at", "REAL NOT NULL DEFAULT 0"),
        ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _ensure_column(conn, "ocr_jobs", name, declaration)
    conn.execute("""CREATE TABLE IF NOT EXISTS ocr_job_links (
        job_id TEXT NOT NULL, user_id TEXT NOT NULL,
        conv_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        PRIMARY KEY (job_id,user_id,conv_id,project_id,filename),
        FOREIGN KEY (job_id) REFERENCES ocr_jobs(id) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_job_links_owner_conv ON ocr_job_links(user_id,conv_id,created_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS run_checkpoints (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT 'periodic',
        state_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        UNIQUE(user_id,run_id,generation),
        FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_checkpoints_owner_run ON run_checkpoints(user_id,run_id,generation DESC)")


def init_db():
    """Recover and migrate the database against a fresh connection pool.

    Recovery may atomically replace ``DB_PATH``.  Reusing a connection opened
    before that replacement writes the schema to the renamed/corrupt inode and
    leaves the restored database without newer tables such as ``work_runs``.
    Serialize initialization and invalidate the pool before the health check so
    every migration targets the active database file.
    """
    with _db_init_lock:
        _close_pool()
        _recover_if_corrupt()
        _init_db_unlocked()


def _init_db_unlocked():
    rewrapped_models = 0
    with _conn() as c:
        # Legacy tables must gain columns used by indexes in `_SCHEMA` before
        # executescript reaches those CREATE INDEX statements.
        _preflight_legacy_schema_indexes(c)
        c.executescript(_SCHEMA)
        # Repair real legacy/partially-upgraded databases before any V24 index
        # is admitted.  Fresh databases already contain these columns.
        _ensure_work_runtime_v24_schema(c)
        _ensure_remote_work_v26_schema(c)
        _ensure_remote_handoff_v27_schema(c)
        _ensure_work_os_v28_schema(c)
        _ensure_work_kernel_v29_schema(c)
        _ensure_user_workspace_v30_schema(c)
        _ensure_conversation_continuity_v40_schema(c)
        _ensure_tool_approval_v32_schema(c)
        _ensure_ocr_queue_v34_schema(c)
        # Ensure default admin exists
        r = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not r:
            salt = secrets.token_hex(16)
            c.execute("INSERT INTO users (id,username,display_name,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      (_uid(), "admin", "管理员", _hash_pw("admin123", salt), salt, "admin"))
        # V103.49: DB 重建后优先从镜像恢复模型配置（在 env seed 之前）。
        # 这样 db 损坏重建不会丢掉用户在管理后台配好的模型。
        _restore_models_from_mirror(c)
        rewrapped_models = _rewrap_model_secrets(c)
        # Ensure default model from env (ONLY if API key is actually set)
        r = c.execute("SELECT id FROM models WHERE is_default=1").fetchone()
        if not r:
            api_key = os.environ.get("LLM_API_KEY", "")
            base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
            model = os.environ.get("LLM_MODEL", "deepseek-chat")
            if api_key and not api_key.startswith("sk-your-"):
                c.execute("INSERT INTO models (id,name,provider,base_url,api_key_enc,model_name,is_default,created_by) VALUES (?,?,?,?,?,?,1,'system')",
                          (_uid(), f"Default ({model})", "deepseek", base_url, _enc(api_key), model))
        # Ensure default KB
        r = c.execute("SELECT id FROM knowledge_bases").fetchone()
        if not r:
            c.execute("INSERT INTO knowledge_bases (id,name,description,created_by) VALUES (?,?,?,?)",
                      (_uid(), "默认知识库", "系统默认知识库，包含全部已索引文档", "system"))

        # v30: Schema versioning
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        v = c.execute("SELECT version FROM schema_version").fetchone()
        current = v[0] if v else 0
        if current < 1:
            # v1: Add sort_order and project columns to conversations
            try: c.execute("ALTER TABLE conversations ADD COLUMN sort_order INTEGER DEFAULT 0")
            except Exception: pass
            try: c.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT DEFAULT ''")
            except Exception: pass
        if current < 2:
            # v2: Add user_memories table
            c.execute("""CREATE TABLE IF NOT EXISTS user_memories (
                id TEXT PRIMARY KEY, user_id TEXT, category TEXT DEFAULT '',
                key TEXT DEFAULT '', value TEXT DEFAULT '', created_at REAL DEFAULT (unixepoch())
            )""")
        if current < 3:
            # v3: Add conversation_tags table
            c.execute("""CREATE TABLE IF NOT EXISTS conversation_tags (
                conv_id TEXT, tag TEXT, PRIMARY KEY (conv_id, tag)
            )""")
        if current < 4:
            # v4: Add feedback column to messages + evolution tables
            try: c.execute("ALTER TABLE messages ADD COLUMN feedback TEXT DEFAULT ''")
            except Exception: pass
            c.execute("""CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                trigger_patterns TEXT, prompt_template TEXT, examples TEXT,
                quality_score REAL DEFAULT 0, use_count INTEGER DEFAULT 0,
                created_at REAL, last_used REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY, profile TEXT NOT NULL DEFAULT '{}',
                updated_at REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS prompt_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_hash TEXT, task_type TEXT, feedback TEXT,
                query_snippet TEXT, created_at REAL
            )""")
        if current < 5:
            # v5: Episodic memory + Skill A/B testing
            c.execute("""CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY, user_id TEXT DEFAULT '',
                query TEXT NOT NULL, query_type TEXT DEFAULT '',
                strategy TEXT DEFAULT '', outcome TEXT DEFAULT 'unknown',
                feedback TEXT DEFAULT '', key_entities TEXT DEFAULT '[]',
                key_insight TEXT DEFAULT '', answer_length INTEGER DEFAULT 0,
                elapsed_ms INTEGER DEFAULT 0, created_at REAL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id)")
            c.execute("""CREATE TABLE IF NOT EXISTS skill_variants (
                id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
                prompt_text TEXT NOT NULL, is_active INTEGER DEFAULT 0,
                is_original INTEGER DEFAULT 0, uses INTEGER DEFAULT 0,
                positive INTEGER DEFAULT 0, negative INTEGER DEFAULT 0,
                created_at REAL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_sv_skill ON skill_variants(skill_id)")
        if current < 6:
            # v6: Increase max_tokens for existing models
            try: c.execute("UPDATE models SET max_tokens = 16384 WHERE max_tokens <= 8192")
            except Exception: pass
        if current < 7:
            # v7: Multi-tenancy scaffold — users.tenant_id + tenants table +
            # backfill existing users to 'default' (single-tenant unchanged).
            try: c.execute("ALTER TABLE users ADD COLUMN tenant_id TEXT DEFAULT 'default'")
            except Exception: pass
            try:
                c.execute("CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, name TEXT DEFAULT '', max_users INTEGER DEFAULT 100000, max_storage_mb INTEGER DEFAULT 1000000, monthly_token_quota INTEGER DEFAULT 10000000000, tokens_used_this_month INTEGER DEFAULT 0, created_at REAL DEFAULT (strftime('%s','now')), active INTEGER DEFAULT 1)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
                c.execute("INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES ('default', 'Default Tenant', strftime('%s','now'))")
                c.execute("UPDATE users SET tenant_id = 'default' WHERE tenant_id IS NULL OR tenant_id = ''")
            except Exception as _e:
                log_suppressed(logger, _e)
        if current < 8:
            # v8: privacy_local flag — a tenant can force all LLM calls on-box.
            try: c.execute("ALTER TABLE tenants ADD COLUMN privacy_local INTEGER DEFAULT 0")
            except Exception: pass
        if current < 9:
            # v9: scheduled tasks (proactive services — interval/daily triggers).
            try:
                c.execute("""CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY, name TEXT DEFAULT '', action TEXT NOT NULL,
                    params TEXT DEFAULT '{}', schedule_kind TEXT DEFAULT 'interval',
                    interval_seconds INTEGER DEFAULT 3600, daily_at TEXT DEFAULT '',
                    tenant_id TEXT DEFAULT 'default', created_by TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1, last_run REAL DEFAULT 0,
                    next_run REAL DEFAULT 0, last_status TEXT DEFAULT '',
                    last_result TEXT DEFAULT '', run_count INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (strftime('%s','now')))""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(enabled, next_run)")
            except Exception as _e:
                log_suppressed(logger, _e)
        if current < 10:
            # v10: token_version — server-side revocation lever. Bumping it
            # invalidates all outstanding access+refresh tokens for that user
            # (logout-all / password change / admin force-logout).
            try: c.execute("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0")
            except Exception: pass
            try: c.execute("UPDATE users SET token_version = 0 WHERE token_version IS NULL")
            except Exception: pass
        if current < 11:
            # v11: KB 文档时效性（有效期/失效/归档）+ 变更历史（审计追溯）
            c.execute("""CREATE TABLE IF NOT EXISTS kb_doc_validity (
                filename TEXT PRIMARY KEY, doc_id TEXT DEFAULT '',
                effective_date REAL, expiry_date REAL,
                status TEXT DEFAULT 'active', note TEXT DEFAULT '',
                updated_by TEXT DEFAULT '', updated_at REAL DEFAULT (unixepoch()),
                created_at REAL DEFAULT (unixepoch())
            )""")
            try: c.execute("CREATE INDEX IF NOT EXISTS idx_docval_status ON kb_doc_validity(status)")
            except Exception: pass
            c.execute("""CREATE TABLE IF NOT EXISTS kb_doc_validity_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, action TEXT,
                from_status TEXT DEFAULT '', to_status TEXT DEFAULT '',
                effective_date REAL, expiry_date REAL, note TEXT DEFAULT '',
                actor TEXT DEFAULT '', ts REAL DEFAULT (unixepoch())
            )""")
            try: c.execute("CREATE INDEX IF NOT EXISTS idx_docval_hist_fn ON kb_doc_validity_history(filename, ts DESC)")
            except Exception: pass
        if current < 12:
            # v12: claim-level grounding ledger persisted with every assistant
            # RAG/Agent message. JSON object; migration is additive and keeps
            # existing histories explicitly not-evaluable rather than inventing evidence.
            try: c.execute("ALTER TABLE messages ADD COLUMN groundings TEXT DEFAULT '{}'")
            except Exception: pass
        if current < 13:
            # v13: deterministic per-message run provenance and verification.
            # Existing messages remain explicitly not instrumented (empty JSON).
            try: c.execute("ALTER TABLE messages ADD COLUMN run_manifest TEXT DEFAULT '{}'")
            except Exception: pass
        if current < 14:
            # The table is also present in _SCHEMA for fresh installs. Keep the
            # migration explicit for existing databases and future audits.
            c.execute("""CREATE TABLE IF NOT EXISTS tool_approval_requests (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conv_id TEXT NOT NULL,
                message_id TEXT DEFAULT '', fingerprint TEXT NOT NULL,
                tool_name TEXT NOT NULL, args_json TEXT NOT NULL DEFAULT '{}',
                cwd TEXT DEFAULT '', reason TEXT DEFAULT '', risk TEXT DEFAULT 'high',
                status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL,
                decided_at REAL DEFAULT 0, expires_at REAL NOT NULL,
                consumed_at REAL DEFAULT 0, decided_by TEXT DEFAULT '',
                FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tool_approval_owner ON tool_approval_requests(user_id, conv_id, status, expires_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tool_approval_fingerprint ON tool_approval_requests(user_id, conv_id, fingerprint, status)")
        if current < 15:
            c.execute("""CREATE TABLE IF NOT EXISTS message_feedback_cases (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conv_id TEXT NOT NULL,
                message_id TEXT NOT NULL, rating TEXT NOT NULL DEFAULT '',
                reason_code TEXT DEFAULT '', comment TEXT DEFAULT '',
                query TEXT DEFAULT '', answer TEXT DEFAULT '', evidence_json TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending', reference_answer TEXT DEFAULT '',
                eval_case_id TEXT DEFAULT '', created_at REAL NOT NULL,
                updated_at REAL NOT NULL, reviewed_at REAL DEFAULT 0,
                reviewed_by TEXT DEFAULT '', UNIQUE(user_id, message_id),
                FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_review ON message_feedback_cases(status, updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_feedback_owner ON message_feedback_cases(user_id, updated_at DESC)")
        if current < 16:
            c.execute("CREATE TABLE IF NOT EXISTS work_runtime_meta (id TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)")
            c.execute("""CREATE TABLE IF NOT EXISTS work_runs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conv_id TEXT DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'chat', source_id TEXT NOT NULL,
                title TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
                revision INTEGER NOT NULL DEFAULT 1, event_seq INTEGER NOT NULL DEFAULT 0,
                change_seq INTEGER NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(user_id, kind, source_id))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_work_runs_owner_change ON work_runs(user_id, change_seq)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_work_runs_owner_conv ON work_runs(user_id, conv_id, updated_at DESC)")
            c.execute("""CREATE TABLE IF NOT EXISTS work_events (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
                seq INTEGER NOT NULL, event_type TEXT NOT NULL, status TEXT DEFAULT '',
                summary TEXT DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL, UNIQUE(run_id, seq),
                FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_work_events_run_seq ON work_events(run_id, seq)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_work_events_owner_time ON work_events(user_id, created_at DESC)")
        if current < 17:
            c.execute("""CREATE TABLE IF NOT EXISTS work_commands (
                id TEXT NOT NULL, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
                action TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'executing',
                result_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
                finished_at REAL DEFAULT 0, PRIMARY KEY (user_id, id),
                FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_work_commands_owner_run_time ON work_commands(user_id, run_id, created_at DESC)")
        if current < 18:
            # V375: project pre-ledger conversations into the account work feed.
            # ``observed`` records historical evidence without inventing a
            # success or verification result that the old runtime never kept.
            c.execute(
                "INSERT INTO work_runtime_meta(id,value) VALUES('global',0) "
                "ON CONFLICT(id) DO NOTHING"
            )
            high_row = c.execute("SELECT MAX(change_seq) FROM work_runs").fetchone()
            high = int((high_row[0] if high_row else 0) or 0)
            conversations = c.execute(
                "SELECT cv.id,cv.user_id,cv.title,cv.created_at,cv.updated_at "
                "FROM conversations cv WHERE cv.user_id<>'' "
                "AND EXISTS(SELECT 1 FROM messages m WHERE m.conv_id=cv.id) "
                "AND NOT EXISTS(SELECT 1 FROM work_runs wr "
                "WHERE wr.user_id=cv.user_id AND wr.conv_id=cv.id) "
                "ORDER BY cv.updated_at ASC"
            ).fetchall()
            for conv_id, owner, title, created_at, updated_at in conversations:
                high += 1
                digest = hashlib.sha256(
                    f"history\n{owner}\n{conv_id}".encode("utf-8")
                ).hexdigest()[:24]
                run_id = f"wr_hist_{digest}"
                snapshot = json.dumps({
                    "schema": "hashmm.work-run.v1",
                    "last_event": "history_imported",
                    "historical_projection": True,
                    "verification": "not_reconstructed",
                }, ensure_ascii=False, separators=(",", ":"))
                ts_created = float(created_at or updated_at or time.time())
                ts_updated = float(updated_at or created_at or ts_created)
                c.execute(
                    "INSERT OR IGNORE INTO work_runs "
                    "(id,user_id,conv_id,kind,source_id,title,status,revision,event_seq,change_seq,snapshot_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, owner, conv_id, "chat", f"history:{conv_id}",
                     str(title or "Chat")[:240], "observed", 1, 1, high,
                     snapshot, ts_created, ts_updated),
                )
                c.execute(
                    "INSERT OR IGNORE INTO work_events "
                    "(id,run_id,user_id,seq,event_type,status,summary,payload_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"we_hist_{digest}", run_id, owner, 1, "history_imported", "observed",
                     "已同步既有对话记录；未重建历史执行与验证结论", "{}", ts_updated),
                )
            c.execute(
                "UPDATE work_runtime_meta SET value=MAX(value,?) WHERE id='global'",
                (high,),
            )
        if current < 19:
            # V383: owner/scope isolation for learned skills.  Pre-existing
            # non-builtin rows remain quarantined until an administrator
            # explicitly assigns ownership; conversation snippets must never
            # become globally visible by migration accident.
            for ddl in (
                "ALTER TABLE skills ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE skills ADD COLUMN scope TEXT NOT NULL DEFAULT 'quarantined'",
                "ALTER TABLE skills ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''",
            ):
                try: c.execute(ddl)
                except Exception: pass
            c.execute("UPDATE skills SET scope='builtin',owner_id='',workspace_id='' "
                      "WHERE id LIKE 'builtin-%'")
            c.execute("CREATE INDEX IF NOT EXISTS idx_skills_scope_owner "
                      "ON skills(scope, owner_id, workspace_id)")
            c.execute("""CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY, contract TEXT NOT NULL,
                parent_session_id TEXT NOT NULL DEFAULT '', owner_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '', role TEXT NOT NULL,
                task TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL DEFAULT '',
                allowed_tools_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                steps_json TEXT NOT NULL DEFAULT '[]', result_json TEXT NOT NULL DEFAULT '{}',
                work_run_id TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                generation INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
                started_at REAL NOT NULL DEFAULT 0, finished_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_owner_updated "
                      "ON agent_sessions(owner_id, updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent "
                      "ON agent_sessions(owner_id, parent_session_id, created_at)")
            c.execute("""CREATE TABLE IF NOT EXISTS context_checkpoints (
                checkpoint_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
                UNIQUE(owner_id, session_id, generation))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_context_checkpoints_owner_session "
                      "ON context_checkpoints(owner_id, session_id, generation DESC)")
            c.execute("""CREATE TABLE IF NOT EXISTS webview_auth_codes (
                code_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                access_cipher TEXT NOT NULL, refresh_cipher TEXT NOT NULL DEFAULT '',
                expires_at REAL NOT NULL, consumed_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_webview_auth_expiry "
                      "ON webview_auth_codes(expires_at, consumed_at)")
        if current < 20:
            c.execute("""CREATE TABLE IF NOT EXISTS agent_mesh_tasks (
                task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '', parent_task_id TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned', result_hash TEXT NOT NULL DEFAULT '',
                upstream_failures INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, UNIQUE(owner_id,team_id,task_id))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mesh_tasks_team "
                      "ON agent_mesh_tasks(owner_id,team_id,created_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mesh_tasks_session "
                      "ON agent_mesh_tasks(owner_id,session_id)")
            c.execute("""CREATE TABLE IF NOT EXISTS agent_mesh_edges (
                owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
                from_task_id TEXT NOT NULL, to_task_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'requires', created_at REAL NOT NULL,
                PRIMARY KEY(owner_id,team_id,from_task_id,to_task_id,relation))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mesh_edges_target "
                      "ON agent_mesh_edges(owner_id,team_id,to_task_id)")
            c.execute("""CREATE TABLE IF NOT EXISTS agent_mailbox_messages (
                message_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, team_id TEXT NOT NULL,
                sender_session_id TEXT NOT NULL DEFAULT '',
                recipient_session_id TEXT NOT NULL, sender_kind TEXT NOT NULL DEFAULT 'agent',
                message_type TEXT NOT NULL DEFAULT 'result', body TEXT NOT NULL DEFAULT '',
                body_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL, lease_token_hash TEXT NOT NULL DEFAULT '',
                lease_expires_at REAL NOT NULL DEFAULT 0, acked_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(owner_id,team_id,idempotency_key))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mailbox_recipient "
                      "ON agent_mailbox_messages(owner_id,recipient_session_id,status,available_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mailbox_team "
                      "ON agent_mailbox_messages(owner_id,team_id,created_at)")
        if current < 21:
            # V393: governed skill evolution. Candidate prompts are isolated
            # from production until an owner/admin makes an explicit,
            # compare-and-swap decision. Existing A/B rows remain historical
            # evidence and are never silently activated by this migration.
            for ddl in (
                "ALTER TABLE skill_variants ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE skill_variants ADD COLUMN evolution_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE skill_variants ADD COLUMN prompt_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE skill_variants ADD COLUMN status TEXT NOT NULL DEFAULT 'legacy'",
                "ALTER TABLE skill_variants ADD COLUMN security_json TEXT NOT NULL DEFAULT '{}'",
                "ALTER TABLE skill_variants ADD COLUMN evaluation_json TEXT NOT NULL DEFAULT '{}'",
                "ALTER TABLE skill_variants ADD COLUMN updated_at REAL NOT NULL DEFAULT 0",
            ):
                try: c.execute(ddl)
                except Exception: pass
            c.execute(
                "UPDATE skill_variants SET status='legacy',is_active=0 "
                "WHERE status='' OR status='legacy'"
            )
            c.execute("""CREATE TABLE IF NOT EXISTS skill_evolution_runs (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                baseline_prompt TEXT NOT NULL,
                baseline_hash TEXT NOT NULL,
                baseline_scope TEXT NOT NULL DEFAULT 'quarantined',
                permission_manifest_json TEXT NOT NULL DEFAULT '{}',
                source_run_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'review_required',
                selected_variant_id TEXT NOT NULL DEFAULT '',
                decision_reason TEXT NOT NULL DEFAULT '',
                work_run_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                decided_at REAL NOT NULL DEFAULT 0,
                decided_by TEXT NOT NULL DEFAULT ''
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_skill_evolution_owner "
                      "ON skill_evolution_runs(owner_id,updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_skill_evolution_skill "
                      "ON skill_evolution_runs(skill_id,updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_skill_variant_evolution "
                      "ON skill_variants(owner_id,evolution_id,status)")
        if current < 22:
            # V394: paired skill replay stores proof metadata only. Historical
            # prompts, queries, references and generated answers stay outside
            # the durable audit table; hashes make a replay row identifiable
            # without turning private conversations into a new data copy.
            c.execute("""CREATE TABLE IF NOT EXISTS skill_evolution_replays (
                id TEXT PRIMARY KEY,
                evolution_id TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                case_id_hash TEXT NOT NULL,
                case_kind TEXT NOT NULL DEFAULT 'historical',
                query_hash TEXT NOT NULL,
                reference_hash TEXT NOT NULL DEFAULT '',
                baseline_answer_hash TEXT NOT NULL,
                candidate_answer_hash TEXT NOT NULL,
                baseline_score REAL NOT NULL DEFAULT 0,
                candidate_score REAL NOT NULL DEFAULT 0,
                baseline_latency_ms INTEGER NOT NULL DEFAULT 0,
                candidate_latency_ms INTEGER NOT NULL DEFAULT 0,
                baseline_tokens INTEGER NOT NULL DEFAULT 0,
                candidate_tokens INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at REAL NOT NULL,
                UNIQUE(owner_id,evolution_id,variant_id,case_id_hash)
            )""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_skill_replay_run "
                "ON skill_evolution_replays(owner_id,evolution_id,variant_id)"
            )
        if current < 23:
            # V400: durable, owner-bound result-review decisions.  Payloads are
            # bounded user notes and normalized outcomes only; tool arguments,
            # credentials and file bodies never belong here.
            c.execute("""CREATE TABLE IF NOT EXISTS work_decisions (
                id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'applied',
                note TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, id),
                FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
            )""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_decisions_owner_run_time "
                "ON work_decisions(user_id,run_id,created_at DESC)"
            )
        if current < 24:
            # V402/V408: idempotent event admission, atomic generation
            # activation, and content-addressed artifact revisions.
            _ensure_work_runtime_v24_schema(c)
            c.execute("""CREATE TABLE IF NOT EXISTS work_generations (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                idempotency_key TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                activated_at REAL NOT NULL DEFAULT 0,
                UNIQUE(run_id,generation),
                UNIQUE(user_id,run_id,idempotency_key),
                FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
            )""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_generations_owner_run "
                "ON work_generations(user_id,run_id,generation DESC)"
            )
            c.execute("""CREATE TABLE IF NOT EXISTS artifact_revisions (
                id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                locator_json TEXT NOT NULL DEFAULT '{}',
                verification TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                UNIQUE(user_id,artifact_id,revision),
                UNIQUE(user_id,artifact_id,content_hash),
                FOREIGN KEY (run_id) REFERENCES work_runs(id) ON DELETE CASCADE
            )""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifact_revisions_owner_run "
                "ON artifact_revisions(user_id,run_id,created_at DESC)"
            )
        if current < 25:
            # V416-V420: durable remote-control state, integrity-linked audit,
            # and measured network/soak acceptance receipts.  Tables are also
            # present in `_SCHEMA` so fresh databases and repaired databases
            # converge on the same shape.
            c.execute("""CREATE TABLE IF NOT EXISTS remote_sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                host_device_id TEXT NOT NULL, viewer_device_id TEXT NOT NULL,
                requested_scopes_json TEXT NOT NULL DEFAULT '[]',
                granted_scopes_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL DEFAULT 'pending', generation INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, expires_at REAL NOT NULL DEFAULT 0,
                work_id TEXT NOT NULL DEFAULT '', work_run_id TEXT NOT NULL DEFAULT '',
                predecessor_session_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_updated ON remote_sessions(user_id,updated_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_remote_sessions_owner_state ON remote_sessions(user_id,state,expires_at)")
            c.execute("""CREATE TABLE IF NOT EXISTS remote_audit_events (
                event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                event TEXT NOT NULL, at REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
                previous_hash TEXT NOT NULL DEFAULT '', event_hash TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_remote_audit_owner_time ON remote_audit_events(user_id,at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_remote_audit_session_time ON remote_audit_events(user_id,session_id,at)")
            c.execute("""CREATE TABLE IF NOT EXISTS remote_acceptance_runs (
                run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL NOT NULL,
                duration_seconds REAL NOT NULL DEFAULT 0, device_count INTEGER NOT NULL DEFAULT 0,
                evidence_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_remote_acceptance_owner_kind ON remote_acceptance_runs(user_id,kind,created_at DESC)")
        if current < 26:
            # V421: remote sessions are linked only to an owner-verified
            # WorkRuntime row.  The additive repair above is deliberately run
            # independent of this marker for interrupted migrations.
            _ensure_remote_work_v26_schema(c)
        if current < 27:
            # V424: a device handoff is a new least-privilege session linked
            # to its predecessor; old grants are never silently reused.
            _ensure_remote_handoff_v27_schema(c)
        if current < 28:
            # V439-V473: project-bound work identity, cross-device execution
            # leases and region-level artifact feedback.  The repair helper is
            # idempotent so partially upgraded production databases converge.
            _ensure_work_os_v28_schema(c)
        if current < 29:
            # V459-V473: paired-replay workflow candidates and an
            # approval-only proactive inbox.
            _ensure_work_kernel_v29_schema(c)
        if current < 30:
            # V499-V518: one owner-facing project brief shared by Chat,
            # desktop and App.  The additive repair also runs independently
            # above so an interrupted migration converges on the next start.
            _ensure_user_workspace_v30_schema(c)
        if current < 31:
            c.execute("""CREATE TABLE IF NOT EXISTS work_sync_changes (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                change_seq INTEGER NOT NULL, entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL, operation TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE(user_id, change_seq))""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_sync_owner_cursor "
                "ON work_sync_changes(user_id, change_seq)"
            )
        if current < 32:
            _ensure_tool_approval_v32_schema(c)
            _ensure_ocr_queue_v34_schema(c)
        _NEW_VERSION = 32
        if current < _NEW_VERSION:
            if current == 0:
                c.execute("INSERT INTO schema_version (version) VALUES (?)", (_NEW_VERSION,))
            else:
                c.execute("UPDATE schema_version SET version = ?", (_NEW_VERSION,))

    if rewrapped_models:
        _mirror_models()
        logger.warning(
            "[DB] 已用当前 HASHMM_SECRET 重新加密 %d 个历史模型凭据；"
            "旧密钥未写入数据库或日志。",
            rewrapped_models,
        )

    # V196: 启动后立刻做一次本地快照，并起后台线程定期快照
    #       —— 崩溃/损坏重建时本地即可恢复对话与历史，不依赖云端 service key。
    try:
        _snapshot_db()
        _start_snapshot_thread()
    except Exception as _e:
        log_suppressed(logger, _e)

# ── User CRUD ──
def create_user(username: str, password: str, display_name: str = "", role: str = "user") -> dict | None:
    salt = secrets.token_hex(16)
    uid = _uid()
    try:
        with _conn() as c:
            c.execute("INSERT INTO users (id,username,display_name,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      (uid, username, display_name or username, _hash_pw(password, salt), salt, role))
        return {"id": uid, "username": username, "display_name": display_name, "role": role}
    except sqlite3.IntegrityError:
        return None

def verify_user(username: str, password: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not r: return None
        if _hash_pw(password, r["salt"]) != r["password_hash"]: return None
        return dict(r)

def get_user(user_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT id,username,display_name,role,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None

def list_users() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id,username,display_name,role,created_at FROM users ORDER BY created_at DESC").fetchall()]

def update_user(user_id: str, **kw) -> bool:
    allowed = {"display_name", "role"}
    fields = {k: v for k, v in kw.items() if k in allowed}
    if not fields: return False
    with _conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), user_id))
        return c.total_changes > 0

def delete_user(user_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM users WHERE id=? AND username != 'admin'", (user_id,))
        return c.total_changes > 0

def change_password(user_id: str, new_password: str) -> bool:
    salt = secrets.token_hex(16)
    with _conn() as c:
        c.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?",
                  (_hash_pw(new_password, salt), salt, user_id))
        return c.total_changes > 0


def get_user_token_version(user_id: str) -> int | None:
    """Current token_version for a user. None if the user does not exist
    (so verify_token rejects tokens for deleted accounts). 0 if the column is
    null (legacy rows backfilled to 0)."""
    with _conn() as c:
        r = c.execute("SELECT token_version FROM users WHERE id=?", (user_id,)).fetchone()
        if r is None:
            return None
        v = r["token_version"]
        return int(v) if v is not None else 0


def bump_token_version(user_id: str) -> int:
    """Increment token_version → revokes every outstanding token for this user.
    Returns the new value (0 if the user does not exist)."""
    with _conn() as c:
        c.execute("UPDATE users SET token_version = COALESCE(token_version, 0) + 1 WHERE id=?",
                  (user_id,))
        r = c.execute("SELECT token_version FROM users WHERE id=?", (user_id,)).fetchone()
        return int(r["token_version"]) if r and r["token_version"] is not None else 0

# ── Model CRUD ──
def create_model(name, provider, base_url, api_key, model_name, created_by="", **kw) -> dict:
    uid = _uid()
    with _conn() as c:
        c.execute("INSERT INTO models (id,name,provider,base_url,api_key_enc,model_name,temperature,max_tokens,config_json,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (uid, name, provider, base_url, _enc(api_key), model_name,
                   kw.get("temperature", 0.1), kw.get("max_tokens", 4096),
                   json.dumps(kw.get("config", {})), created_by))
    _mirror_models()  # V103.49: 写后镜像
    return {"id": uid, "name": name, "provider": provider, "model_name": model_name}

def list_models() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id,name,provider,base_url,model_name,is_default,temperature,max_tokens,config_json,created_by,created_at FROM models ORDER BY is_default DESC, created_at DESC").fetchall()
        return [dict(r) for r in rows]

def get_model(model_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if not r: return None
        d = dict(r)
        d["api_key"] = _dec(d.pop("api_key_enc", ""))
        return d

def get_default_model() -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM models WHERE is_default=1 LIMIT 1").fetchone()
        if not r: return None
        d = dict(r)
        d["api_key"] = _dec(d.pop("api_key_enc", ""))
        return d

def set_default_model(model_id: str) -> bool:
    with _conn() as c:
        c.execute("UPDATE models SET is_default=0")
        c.execute("UPDATE models SET is_default=1 WHERE id=?", (model_id,))
        _ok = c.total_changes > 0
    _mirror_models()  # V103.49
    return _ok

def update_model(model_id: str, **kw) -> bool:
    with _conn() as c:
        sets, vals = [], []
        for k in ("name", "provider", "base_url", "model_name", "temperature", "max_tokens"):
            if k in kw: sets.append(f"{k}=?"); vals.append(kw[k])
        if "api_key" in kw:
            sets.append("api_key_enc=?"); vals.append(_enc(kw["api_key"]))
        if "config" in kw:
            sets.append("config_json=?"); vals.append(json.dumps(kw["config"]))
        if not sets: return False
        vals.append(model_id)
        c.execute(f"UPDATE models SET {', '.join(sets)} WHERE id=?", vals)
        _ok = c.total_changes > 0
    _mirror_models()  # V103.49
    return _ok

def delete_model(model_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM models WHERE id=? AND is_default=0", (model_id,))
        _ok = c.total_changes > 0
    _mirror_models()  # V103.49
    return _ok

# ── Knowledge Base CRUD ──
def list_kbs() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM knowledge_bases ORDER BY created_at DESC").fetchall()]

def create_kb(name, description="", allowed_roles=None, created_by="") -> dict:
    uid = _uid()
    roles = json.dumps(allowed_roles or ["admin", "user"])
    with _conn() as c:
        c.execute("INSERT INTO knowledge_bases (id,name,description,allowed_roles,created_by) VALUES (?,?,?,?,?)",
                  (uid, name, description, roles, created_by))
    return {"id": uid, "name": name}

def update_kb(kb_id: str, **kw) -> bool:
    with _conn() as c:
        sets, vals = [], []
        for k in ("name", "description"):
            if k in kw: sets.append(f"{k}=?"); vals.append(kw[k])
        if "allowed_roles" in kw:
            sets.append("allowed_roles=?"); vals.append(json.dumps(kw["allowed_roles"]))
        if not sets: return False
        vals.append(kb_id)
        c.execute(f"UPDATE knowledge_bases SET {', '.join(sets)} WHERE id=?", vals)
        return c.total_changes > 0

def delete_kb(kb_id: str) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))
        return c.total_changes > 0

# ── Audit Log ──
def audit(user_id: str, username: str, action: str, detail: str = "", ip: str = ""):
    try:
        with _conn() as c:
            c.execute("INSERT INTO audit_logs (user_id,username,action,detail,ip) VALUES (?,?,?,?,?)",
                      (user_id, username, action, detail[:500], ip))
    except Exception: pass

def get_audit_logs(limit=100, offset=0) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM audit_logs ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()]

def get_audit_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]


def query_audit_logs(user_id: str = "", action: str = "", username: str = "",
                     start_ts: float = 0, end_ts: float = 0,
                     limit: int = 200, offset: int = 0) -> dict:
    """v15 Phase 9: filtered audit query for compliance.

    Any filter may be empty/0 to skip it. Returns {logs, total}.
    """
    where = []
    params: list = []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if username:
        where.append("username LIKE ?")
        params.append(f"%{username}%")
    if action:
        where.append("action = ?")
        params.append(action)
    if start_ts:
        where.append("ts >= ?")
        params.append(start_ts)
    if end_ts:
        where.append("ts <= ?")
        params.append(end_ts)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM audit_logs{clause}", tuple(params)).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM audit_logs{clause} ORDER BY ts DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
    return {"logs": [dict(r) for r in rows], "total": total}


# ── KB Document Validity (时效性/失效归档) ──
def upsert_doc_validity(filename: str, doc_id: str = "", effective_date=None,
                        expiry_date=None, status: str = "active",
                        note: str = "", actor: str = ""):
    now = time.time()
    with _conn() as c:
        exists = c.execute("SELECT filename FROM kb_doc_validity WHERE filename=?", (filename,)).fetchone()
        if exists:
            c.execute(
                "UPDATE kb_doc_validity SET doc_id=?, effective_date=?, expiry_date=?, "
                "status=?, note=?, updated_by=?, updated_at=? WHERE filename=?",
                (doc_id, effective_date, expiry_date, status, note, actor, now, filename),
            )
        else:
            c.execute(
                "INSERT INTO kb_doc_validity "
                "(filename, doc_id, effective_date, expiry_date, status, note, updated_by, updated_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (filename, doc_id, effective_date, expiry_date, status, note, actor, now, now),
            )


def get_doc_validity(filename: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM kb_doc_validity WHERE filename=?", (filename,)).fetchone()
        return dict(r) if r else None


def list_doc_validity(status: str = "") -> list[dict]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM kb_doc_validity WHERE status=? ORDER BY updated_at DESC", (status,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM kb_doc_validity ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def set_doc_status(filename: str, status: str, actor: str = "") -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE kb_doc_validity SET status=?, updated_by=?, updated_at=? WHERE filename=?",
            (status, actor, time.time(), filename),
        )
        return cur.rowcount > 0


def add_validity_history(filename: str, action: str, from_status: str = "",
                         to_status: str = "", effective_date=None, expiry_date=None,
                         note: str = "", actor: str = ""):
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO kb_doc_validity_history "
                "(filename, action, from_status, to_status, effective_date, expiry_date, note, actor) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (filename, action, from_status, to_status, effective_date, expiry_date, (note or "")[:500], actor),
            )
    except Exception:
        pass


def get_validity_history(filename: str, limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM kb_doc_validity_history WHERE filename=? ORDER BY ts DESC LIMIT ?",
            (filename, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# v10.0: CONVERSATION CRUD
# ═══════════════════════════════════════════════════════════════════

def create_conversation(conv_id: str, user_id: str, title: str = "新对话",
                        created_at: float | None = None, updated_at: float | None = None,
                        project_id: str | None = None) -> dict:
    # V295 关键修复：以前无条件走 INSERT OR IGNORE 且不带 created_at → 列默认 strftime('%s','now')。
    # 云端回填（list_convs/get_conv 把只在云端的会话补建到本地）因此把**老会话重新盖成"现在创建"**，
    # 于是侧栏里所有历史全挤在"今天"。现在允许传入真实 created_at/updated_at（回填时带上云端原值），
    # 老会话保留真实日期。另外：若本地已存在但日期被历史 bug 盖错，且传入的更早，则就地校正。
    with _conn() as c:
        if created_at is not None:
            c.execute(
                """INSERT OR IGNORE INTO conversations
                   (id, user_id, title, created_at, updated_at,
                    content_activity_at, metadata_updated_at, project_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (conv_id, user_id, title[:100], float(created_at),
                 float(updated_at if updated_at is not None else created_at),
                 float(created_at),
                 float(updated_at if updated_at is not None else created_at),
                 str(project_id or "").strip() or None),
            )
            # 历史校正：已存在的行若 created_at 明显晚于传入的真实值(>1h)，说明是被回填盖错的，纠回来。
            try:
                row = c.execute("SELECT created_at FROM conversations WHERE id=?", (conv_id,)).fetchone()
                if row and row["created_at"] and float(row["created_at"]) > float(created_at) + 3600:
                    c.execute("UPDATE conversations SET created_at=? WHERE id=?", (float(created_at), conv_id))
            except Exception:
                pass
        else:
            c.execute(
                """INSERT OR IGNORE INTO conversations
                   (id, user_id, title, project_id) VALUES (?,?,?,?)""",
                (conv_id, user_id, title[:100],
                 str(project_id or "").strip() or None)
            )
    try:  # 云同步（best-effort，永不影响本地写入）
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            supabase_sync.push_conversation({
                "id": conv_id,
                "user_id": user_id,
                "title": title,
                "pinned": 0,
                "project_id": str(project_id or "").strip() or None,
            })
    except Exception:
        pass
    return {
        "id": conv_id,
        "user_id": user_id,
        "title": title,
        "project_id": str(project_id or "").strip() or None,
    }


def get_conversation(conv_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(r) if r else None


def merge_cloud_conversation(row: dict, user_id: str) -> bool:
    """Merge one owner-scoped cloud row into the local conversation cache.

    This stays local-only: update_conversation would push the same row back to
    Supabase and turn an incremental read into a write loop. An id already
    owned by somebody else is never reassigned.
    """
    conv_id = str(row.get("id") or "").strip()
    if not conv_id or not user_id:
        return False

    def _epoch(value, fallback=None):
        if value is None:
            return fallback
        if isinstance(value, (int, float)):
            return float(value)
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:
            return fallback

    created = _epoch(row.get("created_at"), time.time())
    updated = _epoch(row.get("updated_at"), created)
    incoming_activity = _epoch(row.get("content_activity_at"), None)
    metadata_updated = _epoch(row.get("metadata_updated_at"), updated)
    has_project_id = "project_id" in row
    project_id = str(row.get("project_id") or "").strip() or None
    incoming_revision = max(1, int(row.get("revision") or 1))
    incoming_sync_state = str(row.get("sync_state") or "synced")[:32]
    with _conn() as c:
        tombstone = c.execute(
            "SELECT revision,deleted_at FROM conversation_tombstones WHERE owner_id=? AND conversation_id=?",
            (user_id, conv_id),
        ).fetchone()
        if tombstone and (
            incoming_revision <= int(tombstone["revision"] or 1)
            or float(updated or 0) <= float(tombstone["deleted_at"] or 0)
        ):
            return False
        existing = c.execute(
            "SELECT user_id,revision,updated_at,content_activity_at,project_id FROM conversations WHERE id=?",
            (conv_id,),
        ).fetchone()
        if existing and str(existing["user_id"]) != str(user_id):
            return False
        if existing:
            if "revision" in row and incoming_revision < int(existing["revision"] or 1):
                return False
            if "revision" not in row and float(updated or 0) < float(existing["updated_at"] or 0):
                return False
            if not has_project_id:
                project_id = str(existing["project_id"] or "").strip() or None
        # Cloud rows are untrusted input.  A project id is retained only when
        # the same owner actually owns that project; otherwise the conversation
        # remains an ungrouped recent chat instead of leaking/cross-linking
        # another account's workspace.
        if project_id:
            project = c.execute(
                "SELECT user_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if not project or str(project["user_id"]) != str(user_id):
                project_id = None
        c.execute(
            """INSERT INTO conversations
               (id,user_id,title,created_at,updated_at,content_activity_at,
                metadata_updated_at,sync_observed_at,pinned,archived,project_id,revision,sync_state)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 updated_at=excluded.updated_at,
                 content_activity_at=CASE
                   WHEN ? IS NULL THEN conversations.content_activity_at
                   ELSE MAX(conversations.content_activity_at,excluded.content_activity_at)
                 END,
                 metadata_updated_at=MAX(conversations.metadata_updated_at,excluded.metadata_updated_at),
                 sync_observed_at=excluded.sync_observed_at,
                 pinned=excluded.pinned,
                 archived=excluded.archived,
                 project_id=excluded.project_id,
                 revision=MAX(conversations.revision,excluded.revision),
                 sync_state=excluded.sync_state
               WHERE conversations.user_id=excluded.user_id""",
            (conv_id, user_id, str(row.get("title") or "对话")[:100], created, updated,
             float(incoming_activity if incoming_activity is not None else created),
             float(metadata_updated or updated or created), time.time(),
             1 if row.get("pinned") else 0, 1 if row.get("archived") else 0,
             project_id, incoming_revision, incoming_sync_state, incoming_activity),
        )
        return c.total_changes > 0


def list_conversations(
    user_id: str,
    limit: int = 50,
    before: float | None = None,
    archived: int = 0,
    offset: int = 0,
    meaningful_only: bool = False,
    *,
    scope: str = "all",
    project_id: str | None = None,
    cursor: tuple[int, float, str] | None = None,
    search_query: str = "",
) -> list[dict]:
    """archived=0 只列常规（默认，归档不进主列表）；1 只列归档区；-1 全部。
    V252：老库缺 archived 列时现场 ALTER 自愈后重试（run_migrations 此前未被调用的历史欠账）。"""
    try:
        return _list_conversations_impl(
            user_id, limit, before, archived, offset, meaningful_only,
            scope=scope, project_id=project_id, cursor=cursor,
            search_query=search_query,
        )
    except Exception as e:
        if "no such column: archived" in str(e):
            try:
                with _conn() as c:
                    c.execute("ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0")
                logger.warning("[migrate] 现场补列 conversations.archived（历史库自愈）")
            except Exception:
                pass
            return _list_conversations_impl(
                user_id, limit, before, archived, offset, meaningful_only,
                scope=scope, project_id=project_id, cursor=cursor,
                search_query=search_query,
            )
        raise


def _list_conversations_impl(
    user_id: str,
    limit: int = 50,
    before: float | None = None,
    archived: int = 0,
    offset: int = 0,
    meaningful_only: bool = False,
    *,
    scope: str = "all",
    project_id: str | None = None,
    cursor: tuple[int, float, str] | None = None,
    search_query: str = "",
) -> list[dict]:
    with _conn() as c:
        offset = max(0, int(offset or 0))
        clauses = ["conversations.user_id=?", "conversations.deleted_at IS NULL"]
        args: list[object] = [user_id]
        if archived != -1:
            clauses.append("COALESCE(conversations.archived,0)=?")
            args.append(1 if archived == 1 else 0)
        clean_scope = str(scope or "all").strip().lower()
        if clean_scope == "unassigned":
            clauses.append("(conversations.project_id IS NULL OR TRIM(conversations.project_id)='')")
        elif clean_scope == "project":
            clauses.append("conversations.project_id=?")
            args.append(str(project_id or "").strip())
        elif clean_scope != "all":
            raise ValueError("invalid conversation scope")
        query = str(search_query or "").strip()[:240]
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append(
                "(conversations.title LIKE ? ESCAPE '\\' OR "
                "EXISTS(SELECT 1 FROM messages sm WHERE sm.conv_id=conversations.id "
                "AND sm.content LIKE ? ESCAPE '\\'))"
            )
            args.extend((pattern, pattern))
        durable_sql = (
            "(EXISTS(SELECT 1 FROM messages m WHERE m.conv_id=conversations.id "
            "AND (TRIM(COALESCE(m.content,''))<>'' OR "
            "m.status IN ('streaming','waiting_input','waiting_approval'))) "
            "OR EXISTS(SELECT 1 FROM conversation_files cf "
            "WHERE cf.conv_id=conversations.id) "
            "OR EXISTS(SELECT 1 FROM work_runs wr "
            "WHERE wr.user_id=conversations.user_id "
            "AND wr.conv_id=conversations.id) "
            "OR EXISTS(SELECT 1 FROM conversation_handoffs ch "
            "WHERE ch.owner_id=conversations.user_id "
            "AND ch.target_conversation_id=conversations.id "
            "AND ch.status IN ('sealed','claimed','acknowledged')))"
        )
        if meaningful_only:
            # Feature pages may reserve a conversation id before their first
            # durable item exists.  Such shells are implementation details,
            # not user Chats.  A message, committed attachment or admitted
            # WorkRun materializes the conversation in the sidebar.
            clauses.append(durable_sql)
        if before:
            clauses.append("conversations.content_activity_at<?")
            args.append(float(before))
        if cursor is not None:
            pinned_rank, activity, conv_id = cursor
            clauses.append(
                "(COALESCE(conversations.pinned,0)<? OR "
                "(COALESCE(conversations.pinned,0)=? AND conversations.content_activity_at<?) OR "
                "(COALESCE(conversations.pinned,0)=? AND conversations.content_activity_at=? "
                "AND conversations.id<?))"
            )
            args.extend((pinned_rank, pinned_rank, activity, pinned_rank, activity, conv_id))
        rows = c.execute(
            "SELECT conversations.*, "
            f"CASE WHEN {durable_sql} THEN 1 ELSE 0 END AS has_durable_content, "
            "conversations.content_activity_at AS last_activity_at, "
            "'server' AS visibility_source "
            "FROM conversations WHERE " + " AND ".join(clauses) +
            " ORDER BY COALESCE(conversations.pinned,0) DESC, "
            "conversations.content_activity_at DESC, conversations.id DESC LIMIT ? OFFSET ?",
            (*args, max(1, int(limit or 50)), 0 if cursor is not None else offset),
        ).fetchall()
        return [dict(r) for r in rows]


def update_conversation(conv_id: str, **kw) -> bool:
    allowed = {"title", "pinned", "metadata", "archived"}
    fields = {k: v for k, v in kw.items() if k in allowed}
    if not fields:
        return False
    now = time.time()
    fields["updated_at"] = now
    fields["metadata_updated_at"] = now
    with _conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(
            f"UPDATE conversations SET {sets}, revision=COALESCE(revision,1)+1 WHERE id=?",
            (*fields.values(), conv_id),
        )
        changed = c.total_changes > 0
    try:  # 云同步
        from hashmm.api import supabase_sync
        if changed and supabase_sync.enabled():
            row = get_conversation(conv_id)
            if row:
                supabase_sync.push_conversation(row)
    except Exception:
        pass
    return changed


def _public_conversation_handoff(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item.pop("idempotency_key", None)
    try:
        payload = json.loads(item.pop("payload_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    item["payload"] = payload if isinstance(payload, dict) else {}
    item["schema"] = "hashmm.chat-continuation.v2"
    return item


def create_conversation_handoff(
    *,
    owner_id: str,
    source_conversation_id: str,
    target_conversation_id: str,
    target_title: str,
    payload: dict,
    idempotency_key: str,
    expires_at: float,
) -> tuple[dict, bool]:
    """Atomically create the target Chat and its owner-scoped handoff.

    Returns ``(handoff, created)``.  Retrying the same owner/source/key never
    creates a second target conversation.
    """
    owner = str(owner_id or "").strip()
    source_id = str(source_conversation_id or "").strip()
    target_id = str(target_conversation_id or "").strip()
    key = str(idempotency_key or "").strip()[:200]
    if not owner or not source_id or not target_id or not key:
        raise ValueError("handoff identity is incomplete")
    with _conn() as c:
        existing = c.execute(
            "SELECT * FROM conversation_handoffs WHERE owner_id=? "
            "AND source_conversation_id=? AND idempotency_key=?",
            (owner, source_id, key),
        ).fetchone()
        if existing is not None:
            return _public_conversation_handoff(existing), False
        source = c.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (source_id, owner),
        ).fetchone()
        if source is None:
            raise LookupError("source_conversation_not_found")
        if c.execute("SELECT 1 FROM conversations WHERE id=?", (target_id,)).fetchone():
            raise ValueError("target_conversation_id_exists")
        project_id = str(source["project_id"] or "").strip() or None
        now = time.time()
        c.execute(
            "INSERT INTO conversations "
            "(id,user_id,title,project_id,created_at,updated_at,revision,sync_state) "
            "VALUES (?,?,?,?,?,?,1,'synced')",
            (target_id, owner, str(target_title or "Continue")[:100], project_id, now, now),
        )
        handoff_id = "handoff-" + uuid.uuid4().hex
        body = dict(payload or {})
        body.update({
            "schema": "hashmm.chat-continuation.v2",
            "handoff_id": handoff_id,
            "owner_id": owner,
            "project_id": project_id,
            "source_conversation_id": source_id,
            "target_conversation_id": target_id,
            "source_revision": max(1, int(source["revision"] or 1)),
            "status": "sealed",
            "created_at": now,
            "expires_at": float(expires_at or 0),
        })
        c.execute(
            "INSERT INTO conversation_handoffs "
            "(id,owner_id,project_id,source_conversation_id,target_conversation_id,"
            "source_revision,status,payload_json,idempotency_key,created_at,updated_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                handoff_id, owner, project_id, source_id, target_id,
                body["source_revision"], "sealed",
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                key, now, now, float(expires_at or 0),
            ),
        )
        row = c.execute(
            "SELECT * FROM conversation_handoffs WHERE id=?", (handoff_id,),
        ).fetchone()
    return _public_conversation_handoff(row), True


def get_conversation_handoff(handoff_id: str, owner_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_handoffs WHERE id=? AND owner_id=?",
            (str(handoff_id or ""), str(owner_id or "")),
        ).fetchone()
    return _public_conversation_handoff(row)


def get_incoming_conversation_handoff(target_conversation_id: str, owner_id: str) -> dict | None:
    now = time.time()
    with _conn() as c:
        c.execute(
            "UPDATE conversation_handoffs SET status='expired',updated_at=? "
            "WHERE owner_id=? AND target_conversation_id=? "
            "AND status IN ('sealed','claimed') AND expires_at>0 AND expires_at<=?",
            (now, owner_id, target_conversation_id, now),
        )
        row = c.execute(
            "SELECT * FROM conversation_handoffs WHERE owner_id=? "
            "AND target_conversation_id=? ORDER BY created_at DESC LIMIT 1",
            (owner_id, target_conversation_id),
        ).fetchone()
    return _public_conversation_handoff(row)


def transition_conversation_handoff(
    handoff_id: str,
    owner_id: str,
    action: str,
) -> dict | None:
    transitions = {
        "claim": ({"sealed", "claimed"}, "claimed", "claimed_at"),
        "acknowledge": ({"sealed", "claimed", "acknowledged"}, "acknowledged", "acknowledged_at"),
        "reject": ({"sealed", "claimed", "rejected"}, "rejected", "rejected_at"),
        "supersede": ({"acknowledged", "superseded"}, "superseded", None),
    }
    spec = transitions.get(str(action or "").strip().lower())
    if spec is None:
        raise ValueError("invalid_handoff_action")
    allowed, target, timestamp_column = spec
    now = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_handoffs WHERE id=? AND owner_id=?",
            (handoff_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        current = str(row["status"] or "")
        if current == target:
            return _public_conversation_handoff(row)
        if current not in allowed:
            raise RuntimeError(f"invalid_handoff_transition:{current}:{target}")
        if float(row["expires_at"] or 0) > 0 and float(row["expires_at"]) <= now:
            c.execute(
                "UPDATE conversation_handoffs SET status='expired',updated_at=? WHERE id=?",
                (now, handoff_id),
            )
        else:
            stamp = f",{timestamp_column}=?" if timestamp_column else ""
            params = (target, now, now, handoff_id) if timestamp_column else (target, now, handoff_id)
            c.execute(
                f"UPDATE conversation_handoffs SET status=?,updated_at=?{stamp} WHERE id=?",
                params,
            )
        updated = c.execute(
            "SELECT * FROM conversation_handoffs WHERE id=?", (handoff_id,),
        ).fetchone()
    return _public_conversation_handoff(updated)


_MAILBOX_SECRET_KEY = re.compile(
    r"(?:private[_-]?reason|thinking|chain[_-]?of[_-]?thought|token|secret|password|"
    r"authorization|cookie|api[_-]?key|credential|arguments?|args?)",
    re.I,
)


def _mailbox_safe_value(value, *, key: str = "", depth: int = 0):
    """Build a small public message projection; mailbox data is never authority."""
    if _MAILBOX_SECRET_KEY.search(str(key or "")):
        return "[redacted]"
    if depth >= 4:
        return "[omitted]"
    if isinstance(value, dict):
        return {
            str(k)[:80]: _mailbox_safe_value(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:30]
        }
    if isinstance(value, list):
        return [_mailbox_safe_value(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str):
        text = value.replace("\x00", "")[:4000]
        return re.sub(
            r"(?i)\b(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _public_conversation_mailbox(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item.pop("idempotency_key", None)
    try:
        payload = json.loads(item.pop("payload_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    item["payload"] = payload if isinstance(payload, dict) else {}
    item["schema"] = "hashmm.chat-mailbox.v1"
    item["policy"] = {
        "execution_authority": False,
        "permissions_carried": False,
        "private_reasoning_persisted": False,
    }
    return item


def create_conversation_mailbox_message(
    *,
    owner_id: str,
    source_conversation_id: str,
    target_conversation_id: str,
    payload: dict,
    idempotency_key: str,
    expires_at: float = 0,
) -> tuple[dict, bool]:
    """Queue one owner-scoped, non-authoritative message between two Chats."""
    owner = str(owner_id or "").strip()
    source_id = str(source_conversation_id or "").strip()
    target_id = str(target_conversation_id or "").strip()
    key = str(idempotency_key or "").strip()[:200]
    if not owner or not source_id or not target_id or not key:
        raise ValueError("mailbox identity is incomplete")
    now = time.time()
    with _conn() as c:
        existing = c.execute(
            "SELECT * FROM conversation_mailbox WHERE owner_id=? "
            "AND source_conversation_id=? AND idempotency_key=?",
            (owner, source_id, key),
        ).fetchone()
        if existing is not None:
            return _public_conversation_mailbox(existing), False
        rows = c.execute(
            "SELECT id,project_id FROM conversations WHERE user_id=? "
            "AND deleted_at IS NULL AND id IN (?,?)",
            (owner, source_id, target_id),
        ).fetchall()
        chats = {str(row["id"]): str(row["project_id"] or "") for row in rows}
        if source_id not in chats or target_id not in chats:
            raise LookupError("conversation_not_found")
        if chats[source_id] != chats[target_id]:
            raise ValueError("mailbox_requires_same_project_scope")
        message_id = "mail-" + uuid.uuid4().hex
        body = _mailbox_safe_value(payload if isinstance(payload, dict) else {})
        body.update({
            "schema": "hashmm.chat-mailbox.v1",
            "message_id": message_id,
            "source_conversation_id": source_id,
            "target_conversation_id": target_id,
            "created_at": now,
        })
        c.execute(
            "INSERT INTO conversation_mailbox "
            "(id,owner_id,source_conversation_id,target_conversation_id,status,payload_json,"
            "idempotency_key,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                message_id, owner, source_id, target_id, "queued",
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                key, now, now, float(expires_at or 0),
            ),
        )
        row = c.execute("SELECT * FROM conversation_mailbox WHERE id=?", (message_id,)).fetchone()
    return _public_conversation_mailbox(row), True


def list_conversation_mailbox(
    owner_id: str,
    target_conversation_id: str,
    *,
    limit: int = 50,
) -> list[dict]:
    now = time.time()
    with _conn() as c:
        c.execute(
            "UPDATE conversation_mailbox SET status='expired',updated_at=? "
            "WHERE owner_id=? AND target_conversation_id=? AND status IN ('queued','delivered') "
            "AND expires_at>0 AND expires_at<=?",
            (now, owner_id, target_conversation_id, now),
        )
        c.execute(
            "UPDATE conversation_mailbox SET status='delivered',delivered_at=?,updated_at=? "
            "WHERE owner_id=? AND target_conversation_id=? AND status='queued'",
            (now, now, owner_id, target_conversation_id),
        )
        rows = c.execute(
            "SELECT * FROM conversation_mailbox WHERE owner_id=? AND target_conversation_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (owner_id, target_conversation_id, max(1, min(200, int(limit or 50)))),
        ).fetchall()
    return [_public_conversation_mailbox(row) for row in rows]


def get_conversation_mailbox(message_id: str, owner_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_mailbox WHERE id=? AND owner_id=?",
            (str(message_id or ""), str(owner_id or "")),
        ).fetchone()
    return _public_conversation_mailbox(row)


def transition_conversation_mailbox(message_id: str, owner_id: str, action: str) -> dict | None:
    transitions = {
        "read": ({"queued", "delivered", "read"}, "read", "read_at"),
        "acknowledge": ({"queued", "delivered", "read", "acknowledged"}, "acknowledged", "acknowledged_at"),
        "reject": ({"queued", "delivered", "read", "rejected"}, "rejected", None),
    }
    spec = transitions.get(str(action or "").strip().lower())
    if spec is None:
        raise ValueError("invalid_mailbox_action")
    allowed, target, timestamp_column = spec
    now = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_mailbox WHERE id=? AND owner_id=?",
            (message_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        current = str(row["status"] or "")
        if current == target:
            return _public_conversation_mailbox(row)
        if current not in allowed:
            raise RuntimeError(f"invalid_mailbox_transition:{current}:{target}")
        stamp = f",{timestamp_column}=?" if timestamp_column else ""
        params = (target, now, now, message_id) if timestamp_column else (target, now, message_id)
        c.execute(f"UPDATE conversation_mailbox SET status=?,updated_at=?{stamp} WHERE id=?", params)
        updated = c.execute("SELECT * FROM conversation_mailbox WHERE id=?", (message_id,)).fetchone()
    return _public_conversation_mailbox(updated)


def list_conversation_tombstones(
    owner_id: str, *, since: float = 0, after_id: str = "", limit: int = 500,
) -> list[dict]:
    """List deletion markers using a stable (deleted_at, conversation_id) cursor."""
    with _conn() as c:
        rows = c.execute(
            "SELECT conversation_id,revision,deleted_at,reason FROM conversation_tombstones "
            "WHERE owner_id=? AND (deleted_at>? OR (deleted_at=? AND conversation_id>?)) "
            "ORDER BY deleted_at,conversation_id LIMIT ?",
            (
                owner_id,
                max(0.0, float(since or 0)),
                max(0.0, float(since or 0)),
                str(after_id or ""),
                max(1, min(2001, int(limit or 500))),
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def count_conversations(user_id: str) -> int:
    """该用户名下的对话数（用于判断是否需要迁移旧对话）。"""
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM conversations WHERE user_id=?", (user_id,)).fetchone()
        return int(dict(r)["n"]) if r else 0


def reassign_conversations(from_user_id: str, to_user_id: str) -> int:
    """把 from_user_id 名下的对话整体过户给 to_user_id（旧身份→Supabase 身份迁移）。
    返回迁移条数。仅改归属，不动消息/文件内容。"""
    if not from_user_id or not to_user_id or from_user_id == to_user_id:
        return 0
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM conversations WHERE user_id=?", (from_user_id,)).fetchone()
        cnt = int(dict(n)["n"]) if n else 0
        if cnt:
            c.execute("UPDATE conversations SET user_id=? WHERE user_id=?", (to_user_id, from_user_id))
        return cnt


def delete_conversation(conv_id: str, owner_id: str | None = None) -> bool:
    """Delete conversation + cascade messages + delete files from disk."""
    _sync_uid = None
    _c = None
    try:
        _c = get_conversation(conv_id)
        if _c:
            _sync_uid = _c.get("user_id")
    except Exception:
        pass
    if not _c or (owner_id is not None and str(_c.get("user_id") or "") != str(owner_id)):
        return False
    # Get file paths before deletion and leave a monotonic deletion marker so
    # offline clients cannot resurrect a removed Chat during reconciliation.
    with _conn() as c:
        files = c.execute("SELECT path FROM conversation_files WHERE conv_id=?", (conv_id,)).fetchall()
        deleted_at = time.time()
        c.execute(
            "INSERT INTO conversation_tombstones(conversation_id,owner_id,revision,deleted_at,reason) "
            "VALUES (?,?,?,?,?) ON CONFLICT(owner_id,conversation_id) DO UPDATE SET "
            "revision=MAX(conversation_tombstones.revision,excluded.revision),"
            "deleted_at=excluded.deleted_at,reason=excluded.reason",
            (
                conv_id, str(_c.get("user_id") or ""),
                max(1, int(_c.get("revision") or 1)) + 1,
                deleted_at, "user_deleted",
            ),
        )
        c.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        c.execute("DELETE FROM conversation_files WHERE conv_id=?", (conv_id,))
        c.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    # Clean up files
    for f in files:
        try:
            Path(f["path"]).unlink(missing_ok=True)
        except Exception as _e:

            pass  # Silenced: see logs if needed
    conv_dir = CONV_FILES_ROOT / conv_id
    if conv_dir.exists():
        import shutil
        shutil.rmtree(conv_dir, ignore_errors=True)
    try:  # 云同步删除（级联删消息由外键处理）
        from hashmm.api import supabase_sync
        if _sync_uid and supabase_sync.enabled():
            supabase_sync.remove_conversation(conv_id, _sync_uid)
    except Exception:
        pass
    return True


def touch_conversation(conv_id: str):
    """Advance the user-visible content clock for a durable Chat change."""
    with _conn() as c:
        now = time.time()
        c.execute(
            "UPDATE conversations SET updated_at=?,content_activity_at=?, "
            "revision=COALESCE(revision,1)+1 WHERE id=?",
            (now, now, conv_id),
        )


# ═══════════════════════════════════════════════════════════════════
# v10.0: MESSAGE CRUD
# ═══════════════════════════════════════════════════════════════════

def _msg_id() -> str:
    return uuid.uuid4().hex[:16]


_APPROVAL_SECRET_KEY = re.compile(
    r"(?:token|secret|password|passwd|authorization|cookie|api[_-]?key|credential)",
    re.I,
)


def _approval_public_args(value, *, key: str = "", depth: int = 0):
    """Return a bounded, secret-redacted projection for desktop/App review."""
    if _APPROVAL_SECRET_KEY.search(key or ""):
        return "[已隐藏敏感值]"
    if depth >= 4:
        return "[内容过深，已省略]"
    if isinstance(value, dict):
        return {
            str(k)[:80]: _approval_public_args(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:30]
        }
    if isinstance(value, list):
        return [_approval_public_args(v, depth=depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _approval_row(row) -> dict | None:
    if not row:
        return None
    result = dict(row)
    try:
        result["arguments"] = json.loads(result.pop("args_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        result["arguments"] = {}
    try:
        result["scope"] = json.loads(result.pop("scope_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        result["scope"] = {}
    return result


def public_tool_approval(row: dict | None) -> dict | None:
    """Stable cross-client contract. Never expose the stored raw arguments."""
    if not row:
        return None
    return {
        "schema": "hashmm.tool-approval.v2",
        "request_id": str(row.get("id") or ""),
        "conversation_id": str(row.get("conv_id") or ""),
        "message_id": str(row.get("message_id") or ""),
        "work_run_id": str(row.get("work_run_id") or ""),
        "step_id": str(row.get("step_id") or ""),
        "call_id": str(row.get("call_id") or ""),
        "tool_name": str(row.get("tool_name") or "")[:96],
        "arguments": _approval_public_args(row.get("arguments") or {}),
        "cwd": str(row.get("cwd") or "")[:500],
        "reason": str(row.get("reason") or "")[:600],
        "risk": str(row.get("risk") or "high")[:24],
        "status": str(row.get("status") or "pending"),
        "created_at": float(row.get("created_at") or 0),
        "expires_at": float(row.get("expires_at") or 0),
    }


def _sync_tool_approval_message(row: dict) -> None:
    """Project authoritative approval state into the existing message channel."""
    msg_id = str(row.get("message_id") or "")
    if not msg_id:
        return
    message = _message_row(msg_id)
    if not message:
        return
    manifest = message.get("run_manifest") or {}
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest or "{}")
        except (TypeError, json.JSONDecodeError):
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest["approval_request"] = public_tool_approval(row)
    waiting = row.get("status") in ("pending", "approved")
    update_message(msg_id, run_manifest=manifest,
                   status="waiting_approval" if waiting else "resolved")


def create_tool_approval_request(*, user_id: str, conv_id: str, message_id: str,
                                 fingerprint: str, tool_name: str, arguments: dict,
                                 work_run_id: str = "", step_id: str = "",
                                 call_id: str = "", scope: dict | None = None,
                                 cwd: str = "", reason: str = "", risk: str = "high",
                                 ttl_seconds: float = 3600.0) -> dict | None:
    """Create/reuse a durable pending request bound to exact invocation data."""
    uid = str(user_id or "").strip()
    cid = str(conv_id or "").strip()
    run_id = str(work_run_id or "").strip()
    exact_step = str(step_id or "").strip()[:128]
    exact_call = str(call_id or "").strip()[:128]
    if not uid or not cid or not fingerprint or not tool_name:
        return None
    conv = get_conversation(cid)
    actor = get_user(uid)
    if not conv or (conv.get("user_id") != uid and not (actor and actor.get("role") == "admin")):
        return None
    now = time.time()
    expires = now + max(60.0, min(float(ttl_seconds or 0), 24 * 3600.0))
    row = None
    with _conn() as c:
        c.execute(
            "UPDATE tool_approval_requests SET status='expired' "
            "WHERE user_id=? AND conv_id=? AND status IN ('pending','approved') AND expires_at<=?",
            (uid, cid, now),
        )
        existing = c.execute(
            "SELECT * FROM tool_approval_requests WHERE user_id=? AND conv_id=? "
            "AND fingerprint=? AND work_run_id=? AND call_id=? "
            "AND status='pending' AND expires_at>? "
            "ORDER BY created_at DESC LIMIT 1",
            (uid, cid, fingerprint, run_id, exact_call, now),
        ).fetchone()
        if existing:
            if message_id and not existing["message_id"]:
                c.execute("UPDATE tool_approval_requests SET message_id=? WHERE id=?",
                          (message_id, existing["id"]))
            row = c.execute("SELECT * FROM tool_approval_requests WHERE id=?",
                            (existing["id"],)).fetchone()
        else:
            request_id = uuid.uuid4().hex[:24]
            c.execute(
                """INSERT INTO tool_approval_requests
                   (id,user_id,conv_id,message_id,work_run_id,step_id,call_id,scope_json,
                    fingerprint,tool_name,args_json,cwd,reason,risk,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (request_id, uid, cid, message_id or "", run_id, exact_step, exact_call,
                 json.dumps(scope or {}, sort_keys=True, ensure_ascii=False, default=str),
                 fingerprint, tool_name,
                 json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str),
                 cwd or "", reason or "", risk or "high", now, expires),
            )
            row = c.execute("SELECT * FROM tool_approval_requests WHERE id=?",
                            (request_id,)).fetchone()
    decoded = _approval_row(row)
    if decoded:
        _sync_tool_approval_message(decoded)
    return decoded


def decide_tool_approval(*, request_id: str, conv_id: str, actor_user_id: str,
                         approve: bool, actor_is_admin: bool = False) -> dict | None:
    """Owner-checked, idempotent pending -> approved/declined transition."""
    now = time.time()
    row = None
    with _conn() as c:
        found = c.execute(
            "SELECT * FROM tool_approval_requests WHERE id=? AND conv_id=?",
            (request_id, conv_id),
        ).fetchone()
        if not found or (found["user_id"] != actor_user_id and not actor_is_admin):
            return None
        if found["status"] in ("pending", "approved") and float(found["expires_at"] or 0) <= now:
            c.execute("UPDATE tool_approval_requests SET status='expired', decided_at=? WHERE id=?",
                      (now, request_id))
        elif found["status"] == "pending":
            next_status = "approved" if approve else "declined"
            c.execute(
                "UPDATE tool_approval_requests SET status=?, decided_at=?, decided_by=? "
                "WHERE id=? AND status='pending'",
                (next_status, now, actor_user_id, request_id),
            )
        row = c.execute("SELECT * FROM tool_approval_requests WHERE id=?",
                        (request_id,)).fetchone()
    decoded = _approval_row(row)
    if decoded:
        _sync_tool_approval_message(decoded)
    return decoded


def get_tool_approval_request(*, request_id: str, conv_id: str,
                              actor_user_id: str,
                              actor_is_admin: bool = False) -> dict | None:
    """Return one approval without exposing whether another user's ID exists.

    The owning conversation and actor are part of the lookup boundary.  An
    expired pending/approved request is transitioned before it is returned so
    waiters never keep a stale request alive in memory.
    """
    rid = str(request_id or "").strip()
    cid = str(conv_id or "").strip()
    uid = str(actor_user_id or "").strip()
    if not rid or not cid or not uid:
        return None
    now = time.time()
    row = None
    with _conn() as c:
        found = c.execute(
            "SELECT * FROM tool_approval_requests WHERE id=? AND conv_id=?",
            (rid, cid),
        ).fetchone()
        if not found or (found["user_id"] != uid and not actor_is_admin):
            return None
        if found["status"] in ("pending", "approved") and float(found["expires_at"] or 0) <= now:
            c.execute(
                "UPDATE tool_approval_requests SET status='expired', decided_at=? "
                "WHERE id=? AND status IN ('pending','approved')",
                (now, rid),
            )
        row = c.execute(
            "SELECT * FROM tool_approval_requests WHERE id=? AND conv_id=?",
            (rid, cid),
        ).fetchone()
    decoded = _approval_row(row)
    if decoded:
        _sync_tool_approval_message(decoded)
    return decoded


def consume_tool_approval_request(*, user_id: str, conv_id: str,
                                  request_id: str,
                                  fingerprint: str) -> dict | None:
    """Atomically consume the exact approved request awaited by a tool call."""
    uid = str(user_id or "").strip()
    cid = str(conv_id or "").strip()
    rid = str(request_id or "").strip()
    fp = str(fingerprint or "").strip()
    if not uid or not cid or not rid or not fp:
        return None
    now = time.time()
    row = None
    with _conn() as c:
        c.execute(
            "UPDATE tool_approval_requests SET status='expired', decided_at=? "
            "WHERE id=? AND user_id=? AND conv_id=? "
            "AND status IN ('pending','approved') AND expires_at<=?",
            (now, rid, uid, cid, now),
        )
        changed = c.execute(
            "UPDATE tool_approval_requests SET status='consumed', consumed_at=? "
            "WHERE id=? AND user_id=? AND conv_id=? AND fingerprint=? "
            "AND status='approved' AND expires_at>?",
            (now, rid, uid, cid, fp, now),
        ).rowcount
        if changed:
            row = c.execute(
                "SELECT * FROM tool_approval_requests WHERE id=? AND conv_id=?",
                (rid, cid),
            ).fetchone()
    decoded = _approval_row(row)
    if decoded:
        _sync_tool_approval_message(decoded)
    return decoded


def consume_tool_approval(*, user_id: str, conv_id: str, fingerprint: str) -> dict | None:
    """Atomically consume one exact approval. A successful decision is one-shot."""
    now = time.time()
    row = None
    with _conn() as c:
        c.execute(
            "UPDATE tool_approval_requests SET status='expired', decided_at=? "
            "WHERE user_id=? AND conv_id=? AND status IN ('pending','approved') AND expires_at<=?",
            (now, user_id, conv_id, now),
        )
        found = c.execute(
            "SELECT * FROM tool_approval_requests WHERE user_id=? AND conv_id=? "
            "AND fingerprint=? AND status='approved' AND expires_at>? "
            "ORDER BY decided_at DESC LIMIT 1",
            (user_id, conv_id, fingerprint, now),
        ).fetchone()
        if found:
            changed = c.execute(
                "UPDATE tool_approval_requests SET status='consumed', consumed_at=? "
                "WHERE id=? AND status='approved'",
                (now, found["id"]),
            ).rowcount
            if changed:
                row = c.execute("SELECT * FROM tool_approval_requests WHERE id=?",
                                (found["id"],)).fetchone()
    decoded = _approval_row(row)
    if decoded:
        _sync_tool_approval_message(decoded)
    return decoded


def approved_tool_calls(user_id: str, conv_id: str, limit: int = 3) -> list[dict]:
    """Private exact calls for a trusted resume hint; never return this via API."""
    now = time.time()
    with _conn() as c:
        c.execute(
            "UPDATE tool_approval_requests SET status='expired', decided_at=? "
            "WHERE user_id=? AND conv_id=? AND status IN ('pending','approved') AND expires_at<=?",
            (now, user_id, conv_id, now),
        )
        rows = c.execute(
            "SELECT * FROM tool_approval_requests WHERE user_id=? AND conv_id=? "
            "AND status='approved' AND expires_at>? ORDER BY decided_at DESC LIMIT ?",
            (user_id, conv_id, now, max(1, min(int(limit or 1), 10))),
        ).fetchall()
    return [_approval_row(row) for row in rows if row]


FEEDBACK_FAILURE_REASONS = {
    "incorrect": "事实或结论错误",
    "unsupported": "缺少证据或引用不支持",
    "retrieval_miss": "没有找到应有资料",
    "wrong_tool": "工具选择、参数或顺序错误",
    "incomplete": "任务没有真正完成",
    "instruction_miss": "没有遵守要求或上下文",
    "unsafe": "权限、安全或隐私风险",
    "too_slow": "步骤、延迟或成本过高",
    "other": "其他问题",
}


def _feedback_json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, json.JSONDecodeError):
        return default


def _feedback_public(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    evidence = _feedback_json(data.pop("evidence_json", "{}"), {})
    return {
        "schema": "hashmm.feedback-case.v1",
        "id": str(data.get("id") or ""),
        "conversation_id": str(data.get("conv_id") or ""),
        "message_id": str(data.get("message_id") or ""),
        "rating": str(data.get("rating") or ""),
        "reason_code": str(data.get("reason_code") or ""),
        "reason_label": FEEDBACK_FAILURE_REASONS.get(str(data.get("reason_code") or ""), ""),
        "comment": str(data.get("comment") or ""),
        "query": str(data.get("query") or ""),
        "answer": str(data.get("answer") or ""),
        "evidence": evidence,
        "status": str(data.get("status") or ""),
        "reference_answer": str(data.get("reference_answer") or ""),
        "eval_case_id": str(data.get("eval_case_id") or ""),
        "created_at": float(data.get("created_at") or 0),
        "updated_at": float(data.get("updated_at") or 0),
        "reviewed_at": float(data.get("reviewed_at") or 0),
    }


def record_message_feedback(*, user_id: str, conv_id: str, message_id: str,
                            rating: str, reason_code: str = "",
                            comment: str = "") -> dict | None:
    """Persist feedback and create a review candidate from authoritative data.

    The client supplies only the rating/taxonomy/comment. Query, answer and run
    evidence are read from the owner-checked message rows so a forged client
    cannot poison the evaluation set with invented conversation content.
    """
    rating = str(rating or "").strip().lower()
    if rating not in ("up", "down", "clear"):
        raise ValueError("rating must be up, down or clear")
    reason = str(reason_code or "").strip().lower()
    if rating == "down" and reason not in FEEDBACK_FAILURE_REASONS:
        raise ValueError("a supported reason_code is required for negative feedback")
    if rating != "down":
        reason = ""
    note = str(comment or "").strip()[:1000]
    now = time.time()
    with _conn() as c:
        message = c.execute(
            """SELECT m.* FROM messages m
               JOIN conversations co ON co.id=m.conv_id
               WHERE m.id=? AND m.conv_id=? AND m.role='assistant' AND co.user_id=?""",
            (message_id, conv_id, user_id),
        ).fetchone()
        if not message:
            return None
        query_row = c.execute(
            """SELECT content FROM messages
               WHERE conv_id=? AND role='user' AND created_at<=?
               ORDER BY created_at DESC LIMIT 1""",
            (conv_id, message["created_at"]),
        ).fetchone()
        query = str(query_row["content"] if query_row else "")[:4000]
        answer = str(message["content"] or "")[:12000]
        sources = _feedback_json(message["sources"], [])[:20]
        evidence = {
            "run_manifest": _feedback_json(message["run_manifest"], {}),
            "groundings": _feedback_json(message["groundings"], {}),
            "tool_calls": _feedback_json(message["tool_calls"], [])[:50],
            "sources": sources,
            "status": str(message["status"] or ""),
            "tokens_in": int(message["tokens_in"] or 0),
            "tokens_out": int(message["tokens_out"] or 0),
        }
        evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)
        if len(evidence_text) > 64000:
            evidence = {
                "truncated": True,
                "status": evidence["status"],
                "tokens_in": evidence["tokens_in"],
                "tokens_out": evidence["tokens_out"],
                "tool_names": [
                    str(item.get("name") or item.get("tool") or "")[:96]
                    for item in evidence["tool_calls"][:20] if isinstance(item, dict)
                ],
                "source_ids": [
                    str(item.get("id") or item.get("source") or item.get("filename") or "")[:200]
                    for item in sources[:20] if isinstance(item, dict)
                ],
            }
            evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)
        status = "pending" if rating == "down" else ("positive" if rating == "up" else "withdrawn")
        feedback_value = "" if rating == "clear" else rating
        existing = c.execute(
            "SELECT id FROM message_feedback_cases WHERE user_id=? AND message_id=?",
            (user_id, message_id),
        ).fetchone()
        if existing:
            c.execute(
                """UPDATE message_feedback_cases
                   SET rating=?,reason_code=?,comment=?,query=?,answer=?,evidence_json=?,
                       status=?,reference_answer='',eval_case_id='',updated_at=?,
                       reviewed_at=0,reviewed_by=''
                   WHERE id=?""",
                (feedback_value, reason, note, query, answer, evidence_text,
                 status, now, existing["id"]),
            )
            case_id = existing["id"]
        else:
            case_id = uuid.uuid4().hex[:24]
            c.execute(
                """INSERT INTO message_feedback_cases
                   (id,user_id,conv_id,message_id,rating,reason_code,comment,query,
                    answer,evidence_json,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, user_id, conv_id, message_id, feedback_value, reason,
                 note, query, answer, evidence_text, status, now, now),
            )
        c.execute("UPDATE messages SET feedback=? WHERE id=? AND conv_id=?",
                  (feedback_value, message_id, conv_id))
        row = c.execute("SELECT * FROM message_feedback_cases WHERE id=?", (case_id,)).fetchone()
    return _feedback_public(row)


def list_feedback_cases(*, status: str = "pending", limit: int = 100,
                        user_id: str = "") -> list[dict]:
    limit = max(1, min(int(limit or 100), 500))
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if user_id:
        where.append("user_id=?")
        params.append(user_id)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM message_feedback_cases{clause} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [item for item in (_feedback_public(row) for row in rows) if item]


def get_feedback_case(case_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM message_feedback_cases WHERE id=?", (case_id,)).fetchone()
    return _feedback_public(row)


def claim_feedback_case(*, case_id: str, reviewer: str,
                        stale_after_seconds: int = 300) -> dict | None:
    """Atomically reserve a pending review before mutating the eval corpus.

    ``RAGEvaluator`` persists cases in a file, so the route must first win a
    durable database claim.  A stale claim can be recovered after a worker
    crash; an active second reviewer cannot overwrite the first decision.
    """
    now = time.time()
    stale_before = now - max(30, int(stale_after_seconds or 300))
    with _conn() as c:
        changed = c.execute(
            """UPDATE message_feedback_cases
               SET status='reviewing',reviewed_by=?,reviewed_at=?,updated_at=?
               WHERE id=? AND (status='pending' OR
                   (status='reviewing' AND reviewed_at<?))""",
            (reviewer, now, now, case_id, stale_before),
        )
        if changed.rowcount != 1:
            return None
        row = c.execute("SELECT * FROM message_feedback_cases WHERE id=?", (case_id,)).fetchone()
    return _feedback_public(row)


def release_feedback_case_claim(*, case_id: str, reviewer: str) -> bool:
    """Return this reviewer's unfinished claim to the pending queue."""
    now = time.time()
    with _conn() as c:
        changed = c.execute(
            """UPDATE message_feedback_cases
               SET status='pending',reviewed_by='',reviewed_at=0,updated_at=?
               WHERE id=? AND status='reviewing' AND reviewed_by=?""",
            (now, case_id, reviewer),
        )
    return changed.rowcount == 1


def review_feedback_case(*, case_id: str, decision: str, reviewer: str,
                         reference_answer: str = "", eval_case_id: str = "",
                         expected_status: str = "pending") -> dict | None:
    decision = str(decision or "").lower()
    if decision not in ("approve", "dismiss"):
        raise ValueError("decision must be approve or dismiss")
    now = time.time()
    status = "approved" if decision == "approve" else "dismissed"
    with _conn() as c:
        found = c.execute("SELECT * FROM message_feedback_cases WHERE id=?", (case_id,)).fetchone()
        if (not found or found["status"] != expected_status or
                (expected_status == "reviewing" and found["reviewed_by"] != reviewer)):
            return None
        changed = c.execute(
            """UPDATE message_feedback_cases SET status=?,reference_answer=?,
               eval_case_id=?,reviewed_at=?,reviewed_by=?,updated_at=?
               WHERE id=? AND status=? AND (? != 'reviewing' OR reviewed_by=?)""",
            (status, str(reference_answer or "")[:12000], str(eval_case_id or "")[:120],
             now, reviewer, now, case_id, expected_status, expected_status, reviewer),
        )
        if changed.rowcount != 1:
            return None
        row = c.execute("SELECT * FROM message_feedback_cases WHERE id=?", (case_id,)).fetchone()
    return _feedback_public(row)


def create_message(conv_id: str, role: str, content: str = "",
                   thinking: str = "", status: str = "complete",
                   tool_calls: list | None = None,
                   files: list | None = None,
                   sources: list | None = None,
                   groundings: dict | None = None,
                   run_manifest: dict | None = None,
                   suggestions: list | None = None,
                   tokens_in: int = 0, tokens_out: int = 0,
                   message_id: str | None = None) -> str:
    """Insert a message and return its id."""
    mid = str(message_id or _msg_id())
    with _conn() as c:
        inserted = c.execute(
            """INSERT OR IGNORE INTO messages
               (id, conv_id, role, content, thinking, tool_calls, files, sources, groundings, run_manifest, suggestions, status, tokens_in, tokens_out)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, conv_id, role, content, thinking,
             json.dumps(tool_calls or [], ensure_ascii=False),
             json.dumps(files or [], ensure_ascii=False),
             json.dumps(sources or [], ensure_ascii=False),
             json.dumps(groundings or {}, ensure_ascii=False),
             json.dumps(run_manifest or {}, ensure_ascii=False),
             json.dumps(suggestions or [], ensure_ascii=False),
             status, tokens_in, tokens_out)
        )
        if inserted.rowcount != 1:
            existing = c.execute(
                "SELECT conv_id, role, content FROM messages WHERE id=?", (mid,),
            ).fetchone()
            if not existing or existing["conv_id"] != conv_id or existing["role"] != role or existing["content"] != content:
                raise ValueError("message_id_collision")
            return mid
    # A new user turn acknowledges every outstanding input request in this
    # conversation. Resolve it before starting the next assistant turn so
    # desktop and App cannot keep showing a stale prompt.
    if role == "user":
        resolve_pending_inputs(conv_id)
    touch_conversation(conv_id)
    try:  # 云同步：用户消息即时同步；助手开始生成时写实时任务活动；complete 时再同步全文
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            conv = get_conversation(conv_id)
            if conv:
                if status == "streaming":
                    supabase_sync.push_activity({
                        "id": conv_id, "user_id": conv["user_id"], "kind": "chat",
                        "title": conv.get("title", "对话"), "status": "active",
                    })
                else:
                    supabase_sync.push_message({
                        "id": mid, "conv_id": conv_id, "role": role, "content": content,
                        "thinking": thinking, "tool_calls": tool_calls or [], "files": files or [],
                        "sources": sources or [], "groundings": groundings or {},
                        "run_manifest": run_manifest or {},
                        "suggestions": suggestions or [],
                        "status": status, "tokens_in": tokens_in, "tokens_out": tokens_out,
                    }, conv["user_id"])
                    if status in ("waiting_input", "waiting_approval"):
                        supabase_sync.push_activity({
                            "id": conv_id, "user_id": conv["user_id"], "kind": "chat",
                            "title": conv.get("title", "对话"),
                            "status": "needs_approval" if status == "waiting_approval" else "needs_input",
                        })
    except Exception:
        pass
    return mid


def update_message(msg_id: str, **kw) -> bool:
    """Update message fields. For streaming intermediate saves."""
    allowed = {"content", "thinking", "tool_calls", "files", "sources", "groundings", "run_manifest",
               "suggestions", "status", "tokens_in", "tokens_out"}
    fields = {}
    for k, v in kw.items():
        if k not in allowed:
            continue
        if k in ("tool_calls", "files", "sources", "groundings", "run_manifest", "suggestions") and isinstance(v, (list, dict)):
            fields[k] = json.dumps(v, ensure_ascii=False)
        else:
            fields[k] = v
    if not fields:
        return False
    with _conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE messages SET {sets} WHERE id=?", (*fields.values(), msg_id))
        changed = c.total_changes > 0
    try:  # Persist every terminal/attention state; only streaming partials stay local.
        next_status = fields.get("status")
        if next_status and next_status != "streaming":
            from hashmm.api import supabase_sync
            if supabase_sync.enabled():
                mrow = _message_row(msg_id)
                if mrow:
                    conv = get_conversation(mrow.get("conv_id"))
                    if conv:
                        supabase_sync.push_message(mrow, conv["user_id"])
                        if next_status in ("waiting_input", "waiting_approval"):
                            supabase_sync.push_activity({
                                "id": mrow.get("conv_id"), "user_id": conv["user_id"],
                                "kind": "chat", "title": conv.get("title", "对话"),
                                "status": "needs_approval" if next_status == "waiting_approval" else "needs_input",
                            })
                        else:
                            supabase_sync.remove_activity(mrow.get("conv_id"), conv["user_id"])
    except Exception:
        pass
    return changed


def resolve_pending_inputs(conv_id: str) -> list[str]:
    """Resolve durable user-input requests when a new user turn arrives.

    The request id is the assistant message id. Its content is the question,
    suggestions are the choices, and ``waiting_input`` is the cross-device
    attention state. This keeps the lifecycle out of process-local SSE memory.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM messages WHERE conv_id=? AND role='assistant' "
            "AND status='waiting_input' ORDER BY created_at",
            (conv_id,),
        ).fetchall()
    ids = [str(row["id"]) for row in rows]
    for pending_id in ids:
        update_message(pending_id, status="resolved")
    return ids


def _message_row(msg_id: str) -> dict | None:
    """读单条消息（供云同步用，调用方线程内同步执行）。"""
    with _conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return dict(r) if r else None


def get_active_chats(user_id: str) -> list[dict]:
    """属主名下正在生成或等待补充的会话，供跨端任务进度与接管。"""
    with _conn() as c:
        rows = c.execute(
            """SELECT m.conv_id AS conv_id, m.id AS msg_id, m.created_at AS created_at,
                      m.status AS status, co.title AS title
               FROM messages m JOIN conversations co ON co.id = m.conv_id
               WHERE co.user_id=? AND m.status IN ('streaming','waiting_input','waiting_approval')
               ORDER BY m.created_at DESC LIMIT 20""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_messages(conv_id: str, limit: int = 100, before_ts: float | None = None) -> list[dict]:
    """Get messages for a conversation, ordered by created_at ASC."""
    with _conn() as c:
        if before_ts:
            rows = c.execute(
                "SELECT * FROM messages WHERE conv_id=? AND created_at<? ORDER BY created_at ASC LIMIT ?",
                (conv_id, before_ts, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM messages WHERE conv_id=? ORDER BY created_at ASC LIMIT ?",
                (conv_id, limit)
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            for k in ("tool_calls", "files", "sources", "suggestions"):
                try:
                    d[k] = json.loads(d[k]) if d[k] else []
                except (json.JSONDecodeError, TypeError):
                    d[k] = []
            try:
                d["groundings"] = json.loads(d.get("groundings") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["groundings"] = {}
            try:
                d["run_manifest"] = json.loads(d.get("run_manifest") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["run_manifest"] = {}
            results.append(d)
        return results


def get_latest_messages(conv_id: str, limit: int = 500,
                        before_ts: float | None = None) -> list[dict]:
    """Return the newest page, rendered oldest→newest within that page.

    The legacy ``get_messages`` intentionally starts at the beginning and is
    still used by exports. Chat reopening and live polling need this function:
    using ``ORDER BY created_at ASC LIMIT 100`` made every turn after message
    100 appear to disappear even though it was safely stored.
    """
    safe_limit = max(1, min(int(limit or 500), 2000))
    with _conn() as c:
        if before_ts is not None:
            rows = c.execute(
                """SELECT * FROM messages WHERE conv_id=? AND created_at<?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (conv_id, float(before_ts), safe_limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT * FROM messages WHERE conv_id=?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (conv_id, safe_limit),
            ).fetchall()
        results = []
        for r in reversed(rows):
            d = dict(r)
            for key in ("tool_calls", "files", "sources", "suggestions"):
                try:
                    d[key] = json.loads(d[key]) if d.get(key) else []
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
            for key in ("groundings", "run_manifest"):
                try:
                    d[key] = json.loads(d.get(key) or "{}")
                except (json.JSONDecodeError, TypeError):
                    d[key] = {}
            results.append(d)
        return results


def count_messages(conv_id: str) -> int:
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM messages WHERE conv_id=?", (conv_id,)).fetchone()
        return int(row["n"] if row else 0)


def count_messages_before(conv_id: str, before_ts: float) -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conv_id=? AND created_at<?",
            (conv_id, float(before_ts)),
        ).fetchone()
        return int(row["n"] if row else 0)


def import_messages_local(conv_id: str, msgs: list) -> int:
    """把从 Supabase 拉来的消息导入本地 SQLite（INSERT OR IGNORE，按原 id/时间），
    **不触发云同步**（避免回环），仅用于"按账号双向同步"把 App 端历史补到桌面端。返回新增条数。"""
    from datetime import datetime
    n = 0

    def _ts(ca):
        if isinstance(ca, (int, float)):
            return float(ca)
        if isinstance(ca, str) and ca:
            try:
                return datetime.fromisoformat(ca.replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        return None

    def _j(v):
        if v is None:
            return "[]"
        if isinstance(v, str):
            return v or "[]"
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return "[]"

    with _conn() as c:
        for m in msgs:
            mid = m.get("id")
            if not mid:
                continue
            cur = c.execute(
                """INSERT OR IGNORE INTO messages
                   (id, conv_id, role, content, thinking, tool_calls, files, sources, groundings, run_manifest, suggestions, status, tokens_in, tokens_out, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(?, strftime('%s','now')))""",
                (mid, conv_id, m.get("role", "user") or "user", m.get("content", "") or "", m.get("thinking", "") or "",
                 _j(m.get("tool_calls")), _j(m.get("files")), _j(m.get("sources")),
                 json.dumps(m.get("groundings") or {}, ensure_ascii=False),
                 json.dumps(m.get("run_manifest") or {}, ensure_ascii=False), _j(m.get("suggestions")),
                 m.get("status", "complete") or "complete", 0, 0, _ts(m.get("created_at")))
            )
            if cur.rowcount:
                n += 1
    # Imported cloud rows can predate the current checkpoint while receiving a
    # newer SQLite rowid. Rebuild on the next turn instead of presenting an
    # out-of-order or incomplete working context.
    if n:
        invalidate_context_compaction(conv_id)
    return n


def get_recent_messages(conv_id: str, n: int = 12) -> list[dict]:
    """Get last N messages (for LLM context building)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, thinking FROM messages WHERE conv_id=? ORDER BY created_at DESC LIMIT ?",
            (conv_id, n)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_latest_run_manifest(conv_id: str) -> dict:
    """Return the newest non-empty assistant run contract for a conversation.

    This is intentionally an internal conversation-scoped lookup.  HTTP
    callers must owner-check the conversation before reaching it; returning an
    empty mapping on malformed legacy rows keeps prompt construction fail
    closed and avoids exposing raw database errors.
    """
    with _conn() as c:
        row = c.execute(
            """SELECT run_manifest FROM messages
               WHERE conv_id=? AND role='assistant'
                 AND run_manifest IS NOT NULL
                 AND TRIM(run_manifest) NOT IN ('', '{}', 'null')
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (conv_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["run_manifest"] or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def get_context_messages_after(conv_id: str, after_rowid: int = 0) -> list[dict]:
    """Return the authoritative conversation tail in insertion order.

    ``rowid`` is used only as a local checkpoint cursor. Imported cloud history
    invalidates the checkpoint, so an older timestamp inserted later cannot be
    silently placed after the summary.
    """
    with _conn() as c:
        rows = c.execute(
            """SELECT rowid AS _rowid, id, role, content, thinking, tool_calls,
                      files, sources, groundings, run_manifest, status, created_at
               FROM messages WHERE conv_id=? AND rowid>?
               ORDER BY rowid ASC""",
            (conv_id, max(0, int(after_rowid or 0))),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for key, default in (
                ("tool_calls", []), ("files", []), ("sources", []),
                ("groundings", {}), ("run_manifest", {}),
            ):
                try:
                    parsed = json.loads(item.get(key) or json.dumps(default))
                    item[key] = parsed if isinstance(parsed, type(default)) else default
                except (json.JSONDecodeError, TypeError):
                    item[key] = default
            out.append(item)
        return out


def get_context_compaction(conv_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_compactions WHERE conv_id=?", (conv_id,)
        ).fetchone()
        return dict(row) if row else None


def save_context_compaction(conv_id: str, summary: str, through_rowid: int,
                            source_messages: int, estimated_tokens: int,
                            trigger: str = "auto",
                            expected_through_rowid: int | None = None) -> dict:
    """Persist one incremental checkpoint with optimistic concurrency control.

    Two requests for the same conversation can compact concurrently.  Without
    comparing the cursor read by the caller, the slower request could overwrite
    a newer summary and double-count source messages.  A conflict is returned
    to the caller and no row is changed; full messages always remain canonical.
    """
    now = time.time()
    trigger = "manual" if str(trigger).lower() == "manual" else "auto"
    with _conn() as c:
        if expected_through_rowid is not None:
            current = c.execute(
                "SELECT * FROM conversation_compactions WHERE conv_id=?",
                (conv_id,),
            ).fetchone()
            current_cursor = int(current["through_rowid"] or 0) if current else 0
            expected_cursor = max(0, int(expected_through_rowid or 0))
            if current_cursor != expected_cursor:
                state = dict(current) if current else {}
                return {**state, "conflict": True, "expected_through_rowid": expected_cursor}
        c.execute(
            """INSERT INTO conversation_compactions
               (conv_id, summary, through_rowid, source_messages,
                estimated_tokens, compaction_count, last_trigger, updated_at)
               VALUES (?,?,?,?,?,1,?,?)
               ON CONFLICT(conv_id) DO UPDATE SET
                 summary=excluded.summary,
                 through_rowid=excluded.through_rowid,
                 source_messages=conversation_compactions.source_messages + excluded.source_messages,
                 estimated_tokens=excluded.estimated_tokens,
                 compaction_count=conversation_compactions.compaction_count + 1,
                 last_trigger=excluded.last_trigger,
                 updated_at=excluded.updated_at""",
            (conv_id, str(summary or ""), max(0, int(through_rowid or 0)),
             max(0, int(source_messages or 0)), max(0, int(estimated_tokens or 0)),
             trigger, now),
        )
    return {**(get_context_compaction(conv_id) or {}), "conflict": False}


def invalidate_context_compaction(conv_id: str) -> None:
    """Drop a derived checkpoint after rewind/import/edit. Full history stays."""
    with _conn() as c:
        c.execute("DELETE FROM conversation_compactions WHERE conv_id=?", (conv_id,))


def delete_last_assistant_message(conv_id: str) -> bool:
    """Delete the latest assistant message (for regeneration)."""
    with _conn() as c:
        r = c.execute(
            "SELECT id FROM messages WHERE conv_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
            (conv_id,)
        ).fetchone()
        if r:
            c.execute("DELETE FROM messages WHERE id=?", (r["id"],))
            c.execute("DELETE FROM conversation_compactions WHERE conv_id=?", (conv_id,))
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# v10.0: CONVERSATION FILES
# ═══════════════════════════════════════════════════════════════════

def conv_files_dir(conv_id: str) -> Path:
    """Get/create the file directory for a conversation."""
    d = CONV_FILES_ROOT / conv_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_conversation_file(conv_id: str, filename: str, size: int = 0, mime_type: str = "") -> dict:
    fid = _msg_id()
    path = str(conv_files_dir(conv_id) / filename)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO conversation_files (id, conv_id, filename, path, size, mime_type) VALUES (?,?,?,?,?,?)",
            (fid, conv_id, filename, path, size, mime_type)
        )
    return {"id": fid, "filename": filename, "path": path, "size": size}


def list_conversation_files(conv_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, filename, path, size, mime_type, created_at FROM conversation_files WHERE conv_id=? ORDER BY created_at DESC",
            (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation_file_path(conv_id: str, filename: str) -> str | None:
    with _conn() as c:
        r = c.execute(
            "SELECT path FROM conversation_files WHERE conv_id=? AND filename=?",
            (conv_id, filename)
        ).fetchone()
        return r["path"] if r else None


# ═══════════════════════════════════════════════════════════════════
# v11.7: Usage Tracking
# ═══════════════════════════════════════════════════════════════════

def get_usage_today(user_id: str) -> dict:
    """Get token usage for today."""
    today_start = time.time() - (time.time() % 86400)
    with _conn() as c:
        r = c.execute(
            """SELECT COALESCE(SUM(tokens_in),0) as input, COALESCE(SUM(tokens_out),0) as output
               FROM messages m JOIN conversations cv ON m.conv_id = cv.id
               WHERE cv.user_id=? AND m.created_at>=? AND m.role='assistant'""",
            (user_id, today_start)
        ).fetchone()
        inp, out = r["input"], r["output"]
        # DeepSeek pricing: ~¥0.001/1K input, ~¥0.002/1K output
        cost = (inp * 0.001 + out * 0.002) / 1000
        return {"tokens_in": inp, "tokens_out": out, "cost_cny": round(cost, 4)}


def get_usage_month(user_id: str) -> dict:
    """Get token usage for current month."""
    import datetime
    now = datetime.datetime.now()
    month_start = datetime.datetime(now.year, now.month, 1).timestamp()
    with _conn() as c:
        r = c.execute(
            """SELECT COALESCE(SUM(tokens_in),0) as input, COALESCE(SUM(tokens_out),0) as output
               FROM messages m JOIN conversations cv ON m.conv_id = cv.id
               WHERE cv.user_id=? AND m.created_at>=? AND m.role='assistant'""",
            (user_id, month_start)
        ).fetchone()
        inp, out = r["input"], r["output"]
        cost = (inp * 0.001 + out * 0.002) / 1000
        return {"tokens_in": inp, "tokens_out": out, "cost_cny": round(cost, 4)}


def get_conversation_stats(conv_id: str) -> dict:
    """Get stats for a single conversation."""
    with _conn() as c:
        r = c.execute(
            """SELECT COUNT(*) as msg_count,
                      COALESCE(SUM(tokens_in),0) as tokens_in,
                      COALESCE(SUM(tokens_out),0) as tokens_out
               FROM messages WHERE conv_id=?""",
            (conv_id,)
        ).fetchone()
        return dict(r)


# ═══════════════════════════════════════════════════════════════════
# v12: User Memory (cross-session learning)
# ═══════════════════════════════════════════════════════════════════

# Add table on first use
def _ensure_memory_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS user_memory (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category TEXT DEFAULT '',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            created_at REAL DEFAULT (strftime('%s','now')),
            last_used REAL DEFAULT (strftime('%s','now'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memory(user_id)")

def save_user_memory(user_id: str, category: str, key: str, value: str) -> str:
    _ensure_memory_table()
    mid = uuid.uuid4().hex[:12]
    with _conn() as c:
        # Upsert: if same key exists, update value
        existing = c.execute(
            "SELECT id FROM user_memory WHERE user_id=? AND key=?", (user_id, key)
        ).fetchone()
        if existing:
            c.execute("UPDATE user_memory SET value=?, last_used=? WHERE id=?",
                      (value, time.time(), existing["id"]))
            mid = existing["id"]
        else:
            c.execute(
                "INSERT INTO user_memory (id, user_id, category, key, value) VALUES (?,?,?,?,?)",
                (mid, user_id, category, key, value)
            )
    try:  # 云同步
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            supabase_sync.push_memory({"id": mid, "user_id": user_id, "category": category, "key": key, "value": value})
    except Exception:
        pass
    return mid

def get_user_memories(user_id: str, limit: int = 10) -> list[dict]:
    _ensure_memory_table()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM user_memory WHERE user_id=? ORDER BY last_used DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

def delete_user_memory(memory_id: str) -> bool:
    _ensure_memory_table()
    with _conn() as c:
        c.execute("DELETE FROM user_memory WHERE id=?", (memory_id,))
        return c.total_changes > 0


# ═══════════════════════════════════════════════════════════════════
# v15 Tier 3: Export conversations
# ═══════════════════════════════════════════════════════════════════

def export_as_jupyter(conv_id: str) -> dict:
    """Export conversation as Jupyter Notebook (.ipynb) JSON."""
    messages = get_messages(conv_id)
    cells = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"**用户：**\n\n{content}"]
            })
        elif role == "assistant":
            # Split code blocks into code cells, rest as markdown
            import re
            parts = re.split(r'```(\w+)\n(.*?)```', content, flags=re.DOTALL)
            for i, part in enumerate(parts):
                if i % 3 == 0:  # Text
                    if part.strip():
                        cells.append({
                            "cell_type": "markdown",
                            "metadata": {},
                            "source": [part.strip()]
                        })
                elif i % 3 == 2:  # Code content
                    lang = parts[i - 1]
                    cells.append({
                        "cell_type": "code",
                        "metadata": {},
                        "source": [part.strip()],
                        "outputs": [],
                        "execution_count": None
                    })
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.12.0"}
        },
        "cells": cells
    }

def export_as_markdown(conv_id: str) -> str:
    """Export conversation as Markdown."""
    conv = get_conversation(conv_id)
    messages = get_messages(conv_id)
    title = conv.get("title", "对话") if conv else "对话"
    lines = [f"# {title}\n"]
    for m in messages:
        role = "**用户：**" if m["role"] == "user" else "**助手：**"
        lines.append(f"\n{role}\n")
        lines.append(m.get("content", "") + "\n")
        if m.get("files"):
            for f in m["files"]:
                fname = f.get("filename", "?") if isinstance(f, dict) else str(f)
                lines.append(f"\n[附件] {fname}\n")
    return "\n".join(lines)

def export_as_html(conv_id: str) -> str:
    """Export conversation as standalone HTML."""
    conv = get_conversation(conv_id)
    messages = get_messages(conv_id)
    title = conv.get("title", "对话") if conv else "对话"
    html_parts = [f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f8fafc; }}
.msg {{ margin: 16px 0; padding: 16px; border-radius: 12px; }}
.user {{ background: #e0f2fe; text-align: right; }}
.assistant {{ background: white; border: 1px solid #e2e8f0; }}
pre {{ background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; }}
code {{ font-family: 'Fira Code', monospace; }}
h1 {{ color: #0f172a; }}
</style></head><body>
<h1>{title}</h1>"""]
    for m in messages:
        cls = "user" if m["role"] == "user" else "assistant"
        content = m.get("content", "").replace("<", "&lt;").replace(">", "&gt;")
        content = content.replace("\n", "<br>")
        html_parts.append(f'<div class="msg {cls}">{content}</div>')
    html_parts.append("</body></html>")
    return "\n".join(html_parts)


# ═══════════════════════════════════════════════════════════════════
# v21: Conversation Sharing
# ═══════════════════════════════════════════════════════════════════

def _ensure_share_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS shared_conversations (
            share_code TEXT PRIMARY KEY,
            conv_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            password_hash TEXT DEFAULT NULL,
            expires_at REAL DEFAULT NULL,
            created_at REAL DEFAULT (strftime('%s','now')),
            view_count INTEGER DEFAULT 0
        )""")

def create_share(conv_id: str, user_id: str, password: str = None, expires_hours: int = 72) -> str:
    _ensure_share_table()
    import hashlib
    code = uuid.uuid4().hex[:10]
    pwd_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
    expires = time.time() + expires_hours * 3600 if expires_hours else None
    with _conn() as c:
        c.execute("INSERT INTO shared_conversations (share_code, conv_id, user_id, password_hash, expires_at) VALUES (?,?,?,?,?)",
                  (code, conv_id, user_id, pwd_hash, expires))
    return code

def get_shared(share_code: str) -> dict | None:
    _ensure_share_table()
    with _conn() as c:
        r = c.execute("SELECT * FROM shared_conversations WHERE share_code=?", (share_code,)).fetchone()
        if not r:
            return None
        if r["expires_at"] and time.time() > r["expires_at"]:
            return None
        c.execute("UPDATE shared_conversations SET view_count=view_count+1 WHERE share_code=?", (share_code,))
        return dict(r)


# ═══════════════════════════════════════════════════════════════════
# v21: Prompt Templates
# ═══════════════════════════════════════════════════════════════════

def _ensure_template_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS prompt_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            prompt TEXT NOT NULL,
            variables TEXT DEFAULT '[]',
            author TEXT DEFAULT 'system',
            use_count INTEGER DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now'))
        )""")

def create_template(name: str, category: str, prompt: str, variables: list = None, author: str = "system") -> str:
    _ensure_template_table()
    tid = uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO prompt_templates (id, name, category, prompt, variables, author) VALUES (?,?,?,?,?,?)",
                  (tid, name, category, prompt, json.dumps(variables or []), author))
    return tid

def update_template(tid: str, name: str, category: str, prompt: str,
                    variables: list | None = None) -> dict | None:
    """Update a prompt template in place, preserving identity and usage history."""
    _ensure_template_table()
    encoded_variables = json.dumps(variables or [], ensure_ascii=False)
    with _conn() as c:
        cur = c.execute(
            "UPDATE prompt_templates SET name=?, category=?, prompt=?, variables=? WHERE id=?",
            (name, category, prompt, encoded_variables, tid),
        )
        if cur.rowcount <= 0:
            return None
        row = c.execute("SELECT * FROM prompt_templates WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

def delete_template(tid: str) -> bool:
    """Delete exactly one template and report whether it existed."""
    _ensure_template_table()
    with _conn() as c:
        cur = c.execute("DELETE FROM prompt_templates WHERE id=?", (tid,))
        return cur.rowcount > 0

def list_templates(category: str = None) -> list[dict]:
    _ensure_template_table()
    with _conn() as c:
        if category:
            rows = c.execute("SELECT * FROM prompt_templates WHERE category=? ORDER BY use_count DESC", (category,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM prompt_templates ORDER BY use_count DESC").fetchall()
        return [dict(r) for r in rows]

def use_template(tid: str) -> dict | None:
    _ensure_template_table()
    with _conn() as c:
        c.execute("UPDATE prompt_templates SET use_count=use_count+1 WHERE id=?", (tid,))
        r = c.execute("SELECT * FROM prompt_templates WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None


# ═══════════════════════════════════════════════════════════════════
# v23: Database Migration System
# ═══════════════════════════════════════════════════════════════════

MIGRATIONS = [
    ("001_shared", "CREATE TABLE IF NOT EXISTS shared_conversations (share_code TEXT PRIMARY KEY, conv_id TEXT, user_id TEXT, password_hash TEXT, expires_at REAL, created_at REAL DEFAULT (strftime('%s','now')), view_count INTEGER DEFAULT 0)"),
    ("002_templates", "CREATE TABLE IF NOT EXISTS prompt_templates (id TEXT PRIMARY KEY, name TEXT, category TEXT DEFAULT 'general', prompt TEXT, variables TEXT DEFAULT '[]', author TEXT DEFAULT 'system', use_count INTEGER DEFAULT 0, created_at REAL DEFAULT (strftime('%s','now')))"),
    ("003_memory", "CREATE TABLE IF NOT EXISTS user_memory (id TEXT PRIMARY KEY, user_id TEXT, category TEXT DEFAULT '', key TEXT, value TEXT, confidence REAL DEFAULT 0.8, created_at REAL DEFAULT (strftime('%s','now')), last_used REAL DEFAULT (strftime('%s','now')))"),
    ("004_migrations", "SELECT 1"),  # migrations table itself
    ("005_doc_validity", "CREATE TABLE IF NOT EXISTS kb_doc_validity (filename TEXT PRIMARY KEY, doc_id TEXT DEFAULT '', effective_date REAL, expiry_date REAL, status TEXT DEFAULT 'active', note TEXT DEFAULT '', updated_by TEXT DEFAULT '', updated_at REAL DEFAULT (strftime('%s','now')), created_at REAL DEFAULT (strftime('%s','now')))"),
    ("006_doc_validity_idx", "CREATE INDEX IF NOT EXISTS idx_docval_status ON kb_doc_validity(status)"),
    ("007_doc_validity_hist", "CREATE TABLE IF NOT EXISTS kb_doc_validity_history (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, action TEXT, from_status TEXT DEFAULT '', to_status TEXT DEFAULT '', effective_date REAL, expiry_date REAL, note TEXT DEFAULT '', actor TEXT DEFAULT '', ts REAL DEFAULT (strftime('%s','now')))"),
    ("008_doc_validity_hist_idx", "CREATE INDEX IF NOT EXISTS idx_docval_hist_fn ON kb_doc_validity_history(filename, ts DESC)"),
    ("009_conv_archived", "ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0"),
    ("010_conversation_compactions", "CREATE TABLE IF NOT EXISTS conversation_compactions (conv_id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '', through_rowid INTEGER NOT NULL DEFAULT 0, source_messages INTEGER NOT NULL DEFAULT 0, estimated_tokens INTEGER NOT NULL DEFAULT 0, compaction_count INTEGER NOT NULL DEFAULT 0, updated_at REAL DEFAULT (strftime('%s','now')), FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE)"),
    ("011_conversation_compaction_trigger", "ALTER TABLE conversation_compactions ADD COLUMN last_trigger TEXT NOT NULL DEFAULT 'auto'"),
]

def run_migrations():
    """Run pending database migrations."""
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _migrations (id TEXT PRIMARY KEY, applied_at REAL)")
        applied = {r["id"] for r in c.execute("SELECT id FROM _migrations").fetchall()}
        count = 0
        for mid, sql in MIGRATIONS:
            if mid not in applied:
                try:
                    c.execute(sql)
                    c.execute("INSERT INTO _migrations (id, applied_at) VALUES (?, ?)", (mid, time.time()))
                    count += 1
                except Exception as e:
                    # Older databases may already contain a column created by a
                    # pre-migration compatibility path. Treat that idempotent
                    # state as applied so every startup does not emit a false
                    # migration alarm forever.
                    if "duplicate column name" in str(e).lower():
                        c.execute("INSERT OR IGNORE INTO _migrations (id, applied_at) VALUES (?, ?)",
                                  (mid, time.time()))
                    else:
                        logger.warning(f"Migration {mid}: {e}")
        if count:
            logger.info(f"{count} migrations applied")


# ═══════════════════════════════════════════════════════════════════
# v25: Project System
# ═══════════════════════════════════════════════════════════════════

def _ensure_project_table():
    with _conn() as c:
        _ensure_user_workspace_v30_schema(c)
        # Add project_id to conversations if not exists
        try:
            c.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT DEFAULT NULL")
        except Exception as _e:

            pass  # Silenced: see logs if needed

def create_project(
    user_id: str,
    name: str,
    description: str = "",
    custom_prompt: str = "",
    *,
    goal: str = "",
    deliverable: str = "",
    success_criteria: list[str] | None = None,
    permission_mode: str = "ask",
) -> str:
    _ensure_project_table()
    pid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            """INSERT INTO projects
               (id,user_id,name,description,custom_prompt,goal,deliverable,
                success_criteria,permission_mode)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                pid, user_id, name, description, custom_prompt, goal,
                deliverable,
                json.dumps(success_criteria or [], ensure_ascii=False),
                permission_mode,
            ),
        )
    return pid


def _project_public(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    try:
        criteria = json.loads(item.get("success_criteria") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        criteria = []
    item["success_criteria"] = [
        str(value)[:240] for value in criteria
        if isinstance(value, str) and value.strip()
    ][:12]
    item["archived"] = bool(item.get("archived"))
    item["revision"] = max(1, int(item.get("revision") or 1))
    return item


def list_projects(user_id: str) -> list[dict]:
    _ensure_project_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT p.*, COUNT(cv.id) as conv_count
            FROM projects p LEFT JOIN conversations cv ON cv.project_id = p.id
            WHERE p.user_id = ? AND p.archived=0
            GROUP BY p.id ORDER BY p.updated_at DESC
        """, (user_id,)).fetchall()
        return [_project_public(r) for r in rows]

def get_project(pid: str) -> dict | None:
    _ensure_project_table()
    with _conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return _project_public(r) if r else None

def get_project_for_user(pid: str, user_id: str) -> dict | None:
    """Return a project only when it belongs to the authenticated owner."""
    _ensure_project_table()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM projects WHERE id=? AND user_id=?",
            (str(pid or ""), str(user_id or "")),
        ).fetchone()
        return _project_public(row) if row else None


def add_project_resource(
    project_id: str,
    user_id: str,
    *,
    resource_id: str,
    conv_id: str = "",
    filename: str = "",
    sha256: str = "",
    role: str = "reference",
) -> dict | None:
    """Attach an immutable, owner-checked resource identity to a project."""
    _ensure_project_table()
    pid, owner = str(project_id or "").strip(), str(user_id or "").strip()
    rid = str(resource_id or "").strip()[:160]
    if not pid or not owner or not rid:
        return None
    with _conn() as c:
        if c.execute(
            "SELECT 1 FROM projects WHERE id=? AND user_id=?",
            (pid, owner),
        ).fetchone() is None:
            return None
        c.execute(
            "INSERT INTO project_resources "
            "(project_id,user_id,resource_id,conv_id,filename,sha256,role) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,user_id,resource_id) DO UPDATE SET "
            "conv_id=excluded.conv_id,filename=excluded.filename,"
            "sha256=excluded.sha256,role=excluded.role",
            (
                pid, owner, rid, str(conv_id or "")[:160],
                str(filename or "")[:240], str(sha256 or "")[:64],
                str(role or "reference")[:40],
            ),
        )
        row = c.execute(
            "SELECT * FROM project_resources "
            "WHERE project_id=? AND user_id=? AND resource_id=?",
            (pid, owner, rid),
        ).fetchone()
    return dict(row) if row else None


def list_project_resources(project_id: str, user_id: str) -> list[dict]:
    _ensure_project_table()
    with _conn() as c:
        rows = c.execute(
            "SELECT resource_id,conv_id,filename,sha256,role,created_at "
            "FROM project_resources WHERE project_id=? AND user_id=? "
            "ORDER BY created_at DESC",
            (str(project_id or ""), str(user_id or "")),
        ).fetchall()
    return [dict(row) for row in rows]


def remove_project_resource(project_id: str, user_id: str, resource_id: str) -> bool:
    _ensure_project_table()
    with _conn() as c:
        result = c.execute(
            "DELETE FROM project_resources "
            "WHERE project_id=? AND user_id=? AND resource_id=?",
            (str(project_id or ""), str(user_id or ""), str(resource_id or "")),
        )
    return result.rowcount == 1


def update_project(pid: str, user_id: str, **kwargs) -> bool:
    _ensure_project_table()
    allowed = {
        "name", "description", "custom_prompt", "goal", "deliverable",
        "success_criteria", "permission_mode", "status", "archived",
    }
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return False
    if "success_criteria" in sets:
        sets["success_criteria"] = json.dumps(
            sets["success_criteria"] or [], ensure_ascii=False
        )
    if "archived" in sets:
        sets["archived"] = 1 if sets["archived"] else 0
    set_clause = ", ".join(f"{k}=?" for k in sets)
    with _conn() as c:
        cursor = c.execute(
            f"UPDATE projects SET {set_clause}, "
            "revision=revision+1, updated_at=strftime('%s','now') "
            "WHERE id=? AND user_id=?",
            (*sets.values(), pid, user_id),
        )
        return cursor.rowcount == 1

def assign_conv_to_project(conv_id: str, project_id: str | None, user_id: str) -> bool:
    """Assign only owner-matched conversation/project pairs."""
    _ensure_project_table()
    with _conn() as c:
        conversation = c.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user_id),
        ).fetchone()
        if not conversation:
            return False
        clean_project = str(project_id or "").strip() or None
        if clean_project is not None:
            project = c.execute(
                "SELECT id FROM projects WHERE id=? AND user_id=?",
                (clean_project, user_id),
            ).fetchone()
            if not project:
                return False
        cursor = c.execute(
            "UPDATE conversations SET project_id=?, revision=COALESCE(revision,1)+1, "
            "updated_at=strftime('%s','now'),metadata_updated_at=strftime('%s','now') "
            "WHERE id=? AND user_id=?",
            (clean_project, conv_id, user_id),
        )
        changed = cursor.rowcount == 1
    if changed:
        try:
            from hashmm.api import supabase_sync
            row = get_conversation(conv_id)
            if row and supabase_sync.enabled():
                supabase_sync.push_conversation(row)
        except Exception:
            pass
    return changed


# ═══════════════════════════════════════════════════════════════════
# v25: Token Usage Tracking
# ═══════════════════════════════════════════════════════════════════

def get_token_usage(user_id: str = None, days: int = 7) -> dict:
    """Get token usage statistics."""
    with _conn() as c:
        base_query = """
            SELECT 
                SUM(COALESCE(tokens_in, 0)) as total_in,
                SUM(COALESCE(tokens_out, 0)) as total_out,
                COUNT(*) as message_count
            FROM messages m JOIN conversations cv ON m.conv_id = cv.id
            WHERE m.role = 'assistant' AND m.created_at > strftime('%s','now') - ?
        """
        params = [days * 86400]
        if user_id:
            base_query += " AND cv.user_id = ?"
            params.append(user_id)
        
        row = c.execute(base_query, params).fetchone()
        total_in = row["total_in"] or 0
        total_out = row["total_out"] or 0
        
        # Today's usage
        today_row = c.execute(base_query.replace("strftime('%s','now') - ?", "strftime('%s','now','start of day')"),
                              params[1:] if user_id else []).fetchone()
        today_in = today_row["total_in"] or 0 if today_row else 0
        today_out = today_row["total_out"] or 0 if today_row else 0
        
        return {
            "period_days": days,
            "total": {"tokens_in": total_in, "tokens_out": total_out, "messages": row["message_count"] or 0},
            "today": {"tokens_in": today_in, "tokens_out": today_out},
            "estimated_cost_cny": round((total_in * 0.001 + total_out * 0.002) / 1000, 2),
        }


# ═══════════════════════════════════════════════════════════════════
# v26: Conversation Tags
# ═══════════════════════════════════════════════════════════════════

def _ensure_tags_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS conversation_tags (
            conv_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at REAL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (conv_id, tag)
        )""")

def add_tag(conv_id: str, tag: str):
    _ensure_tags_table()
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO conversation_tags (conv_id, tag) VALUES (?,?)", (conv_id, tag))

def remove_tag(conv_id: str, tag: str):
    _ensure_tags_table()
    with _conn() as c:
        c.execute("DELETE FROM conversation_tags WHERE conv_id=? AND tag=?", (conv_id, tag))

def get_tags(conv_id: str) -> list[str]:
    _ensure_tags_table()
    with _conn() as c:
        rows = c.execute("SELECT tag FROM conversation_tags WHERE conv_id=?", (conv_id,)).fetchall()
        return [r["tag"] for r in rows]

def list_all_tags(user_id: str) -> list[dict]:
    _ensure_tags_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT ct.tag, COUNT(*) as count
            FROM conversation_tags ct
            JOIN conversations cv ON ct.conv_id = cv.id
            WHERE cv.user_id = ?
            GROUP BY ct.tag ORDER BY count DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]

def get_conversations_by_tag(user_id: str, tag: str) -> list[dict]:
    _ensure_tags_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT cv.* FROM conversations cv
            JOIN conversation_tags ct ON cv.id = ct.conv_id
            WHERE cv.user_id = ? AND ct.tag = ?
            ORDER BY cv.updated_at DESC
        """, (user_id, tag)).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# v29: Conversation Forking
# ═══════════════════════════════════════════════════════════════════

def fork_conversation(conv_id: str, from_message_id: str, user_id: str) -> str:
    """Create a new conversation branching from a specific message.

    Copies all messages up to and including from_message_id into a new
    conversation. Returns the new id, or "" if the source conversation is gone.

    v17 Phase 67: previously inserted a non-existent `model` column and called
    .get() on sqlite3.Row objects (Rows have no .get) — both raised, so BOTH
    fork endpoints 500'd. Fixed to match the real schema and Row access.
    """
    conv = get_conversation(conv_id)  # dict | None
    if not conv:
        return ""

    new_id = uuid.uuid4().hex[:16]
    title = f"(分支) {conv.get('title', '无标题')}"

    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
            "VALUES (?,?,?,strftime('%s','now'),strftime('%s','now'))",
            (new_id, user_id, title),
        )
        msg = c.execute("SELECT created_at FROM messages WHERE id=?", (from_message_id,)).fetchone()
        if msg:
            messages = c.execute(
                "SELECT * FROM messages WHERE conv_id=? AND created_at <= ? ORDER BY created_at",
                (conv_id, msg["created_at"]),
            ).fetchall()
            for m in messages:
                new_msg_id = uuid.uuid4().hex[:16]
                c.execute(
                    "INSERT INTO messages (id, conv_id, role, content, files, tokens_in, tokens_out, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (new_msg_id, new_id, m["role"], m["content"], m["files"],
                     m["tokens_in"], m["tokens_out"], m["created_at"]),
                )
    return new_id


# ═══════════════════════════════════════════════════════════════════
# 文件投送 / 电脑任务请求（本地直达：手机与电脑共用同一后端，无需 Supabase service key）
#   手机端写一条 pending；桌面端常驻轮询 list_pending_file_requests 接走、执行、回写状态。
#   这条链路把「跨端下发」从依赖 Supabase 改为走本机后端，少一个易错的外部密钥依赖。
# ═══════════════════════════════════════════════════════════════════
def _ensure_file_requests_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS file_requests (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            conv_id TEXT NOT NULL,
            query TEXT NOT NULL,
            target TEXT DEFAULT 'desktop',
            status TEXT DEFAULT 'pending',
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_freq_poll ON file_requests(user_id, status, target)")

def create_file_request(req_id: str, user_id: str, conv_id: str, query: str, target: str = "desktop") -> str:
    _ensure_file_requests_table()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO file_requests (id, user_id, conv_id, query, target, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'pending', strftime('%s','now'), strftime('%s','now'))",
            (req_id, user_id, conv_id, (query or "")[:500], target),
        )
    return req_id

def list_pending_file_requests(user_id: str, target: str = "desktop", limit: int = 5) -> list[dict]:
    _ensure_file_requests_table()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, user_id, conv_id, query, target, status, created_at FROM file_requests "
            "WHERE user_id=? AND status='pending' AND (target IS NULL OR target=?) "
            "ORDER BY created_at ASC LIMIT ?",
            (user_id, target, limit),
        ).fetchall()
    return [dict(r) for r in rows]

def list_file_requests_for_conversation(user_id: str, conv_id: str, limit: int = 20) -> list[dict]:
    """Return persisted desktop-task state for one owned conversation.

    The App uses this instead of an in-memory "sent" badge, so pending,
    processing, done and error survive process death and long-running tasks.
    """
    _ensure_file_requests_table()
    safe_limit = max(1, min(int(limit or 20), 100))
    with _conn() as c:
        rows = c.execute(
            "SELECT id, conv_id, query, target, status, created_at, updated_at "
            "FROM file_requests WHERE user_id=? AND conv_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, conv_id, safe_limit),
        ).fetchall()
    return [dict(r) for r in rows]

def update_file_request(req_id: str, user_id: str, status: str) -> bool:
    _ensure_file_requests_table()
    with _conn() as c:
        cur = c.execute(
            "UPDATE file_requests SET status=?, updated_at=strftime('%s','now') WHERE id=? AND user_id=?",
            (status, req_id, user_id),
        )
    try:
        return (cur.rowcount or 0) > 0
    except Exception:
        return True


def cancel_pending_file_request(req_id: str, user_id: str) -> bool:
    """Cancel an owned desktop request only before a runner has claimed it.

    The status predicate is the execution boundary: once the desktop changed
    the row to ``processing`` we must not claim that the external action was
    cancelled.
    """
    _ensure_file_requests_table()
    with _conn() as c:
        cur = c.execute(
            "UPDATE file_requests SET status='cancelled', updated_at=strftime('%s','now') "
            "WHERE id=? AND user_id=? AND status='pending'",
            (req_id, user_id),
        )
    try:
        return (cur.rowcount or 0) > 0
    except Exception:
        return False
