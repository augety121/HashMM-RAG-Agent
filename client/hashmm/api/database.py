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
import hashlib, json, os, secrets, sqlite3, time, uuid
from pathlib import Path
from contextlib import contextmanager
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.database")

DB_PATH = Path(os.environ.get("HASHMM_DB_PATH", "data/hashmm.sqlite"))

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
    suggestions TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'complete',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    created_at  REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, created_at);

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
from hashmm.secrets_crypto import encrypt_secret as _enc, decrypt_secret as _dec  # noqa: E402


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
# Pool sized for many concurrent users. SQLite serializes writes but allows
# concurrent reads in WAL mode; a larger pool lets read traffic flow freely.
_POOL_SIZE = int(os.environ.get("HASHMM_DB_POOL_SIZE", "16"))


def _pool_init():
    global _pool
    if _pool is not None:
        return
    _pool = Queue(maxsize=_POOL_SIZE)
    for _ in range(_POOL_SIZE):
        _pool.put(db_backend.make_conn(DB_PATH))


def _pool_get():
    """Get a connection from the pool (or create new if pool empty)."""
    _pool_init()
    try:
        return _pool.get(timeout=10)  # type: ignore
    except _QEmpty:
        # All connections busy — create a temporary one (returned, not pooled).
        return db_backend.make_conn(DB_PATH)


def _pool_put(c: sqlite3.Connection):
    """Return a connection to the pool."""
    if _pool is not None and not _pool.full():
        _pool.put(c)
    else:
        c.close()

def _recover_if_corrupt():
    """启动前检查 db 文件是否损坏；损坏就备份重建，避免整个服务起不来。

    关键：已索引的向量/BM25 在独立文件、**不在这个 sqlite 里**，所以重建只丢配置
    （用户/模型/知识库等元数据），**不丢知识库**。admin/admin123 会重新生成，模型需在后台重配。
    """
    if not DB_PATH.exists():
        return
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        conn.close()
        return
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        if not ("malformed" in msg or "not a database" in msg or "encrypted" in msg or "disk image" in msg):
            raise  # 其它 DatabaseError 不擅自处理
        ts = int(time.time())
        bak = DB_PATH.with_name(DB_PATH.name + f".corrupt-{ts}")
        try:
            DB_PATH.rename(bak)
            logger.error(
                f"[DB] 数据库文件损坏（{e}），已备份到 {bak} 并将重建新库。"
                f"注意：已索引的向量/BM25 在独立文件、不受影响；用户/模型/知识库等配置需重设"
                f"（admin/admin123 重新生成，模型在管理后台重配）。")
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


def init_db():
    _recover_if_corrupt()
    with _conn() as c:
        c.executescript(_SCHEMA)
        # Ensure default admin exists
        r = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not r:
            salt = secrets.token_hex(16)
            c.execute("INSERT INTO users (id,username,display_name,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      (_uid(), "admin", "管理员", _hash_pw("admin123", salt), salt, "admin"))
        # V103.49: DB 重建后优先从镜像恢复模型配置（在 env seed 之前）。
        # 这样 db 损坏重建不会丢掉用户在管理后台配好的模型。
        _restore_models_from_mirror(c)
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
        _NEW_VERSION = 11
        if current < _NEW_VERSION:
            if current == 0:
                c.execute("INSERT INTO schema_version (version) VALUES (?)", (_NEW_VERSION,))
            else:
                c.execute("UPDATE schema_version SET version = ?", (_NEW_VERSION,))

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

def create_conversation(conv_id: str, user_id: str, title: str = "新对话") -> dict:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO conversations (id, user_id, title) VALUES (?,?,?)",
            (conv_id, user_id, title[:100])
        )
    try:  # 云同步（best-effort，永不影响本地写入）
        from hashmm.api import supabase_sync
        if supabase_sync.enabled():
            supabase_sync.push_conversation({"id": conv_id, "user_id": user_id, "title": title, "pinned": 0})
    except Exception:
        pass
    return {"id": conv_id, "user_id": user_id, "title": title}


def get_conversation(conv_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(r) if r else None


def list_conversations(user_id: str, limit: int = 50, before: float | None = None) -> list[dict]:
    with _conn() as c:
        if before:
            rows = c.execute(
                "SELECT * FROM conversations WHERE user_id=? AND updated_at<? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                (user_id, before, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM conversations WHERE user_id=? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]


def update_conversation(conv_id: str, **kw) -> bool:
    allowed = {"title", "pinned", "metadata"}
    fields = {k: v for k, v in kw.items() if k in allowed}
    if not fields:
        return False
    fields["updated_at"] = time.time()
    with _conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE conversations SET {sets} WHERE id=?", (*fields.values(), conv_id))
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


def delete_conversation(conv_id: str) -> bool:
    """Delete conversation + cascade messages + delete files from disk."""
    _sync_uid = None
    try:
        _c = get_conversation(conv_id)
        if _c:
            _sync_uid = _c.get("user_id")
    except Exception:
        pass
    # Get file paths before deletion
    with _conn() as c:
        files = c.execute("SELECT path FROM conversation_files WHERE conv_id=?", (conv_id,)).fetchall()
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
    """Update the updated_at timestamp."""
    with _conn() as c:
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), conv_id))


# ═══════════════════════════════════════════════════════════════════
# v10.0: MESSAGE CRUD
# ═══════════════════════════════════════════════════════════════════

def _msg_id() -> str:
    return uuid.uuid4().hex[:16]


def create_message(conv_id: str, role: str, content: str = "",
                   thinking: str = "", status: str = "complete",
                   tool_calls: list | None = None,
                   files: list | None = None,
                   sources: list | None = None,
                   suggestions: list | None = None,
                   tokens_in: int = 0, tokens_out: int = 0) -> str:
    """Insert a message and return its id."""
    mid = _msg_id()
    with _conn() as c:
        c.execute(
            """INSERT INTO messages
               (id, conv_id, role, content, thinking, tool_calls, files, sources, suggestions, status, tokens_in, tokens_out)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, conv_id, role, content, thinking,
             json.dumps(tool_calls or [], ensure_ascii=False),
             json.dumps(files or [], ensure_ascii=False),
             json.dumps(sources or [], ensure_ascii=False),
             json.dumps(suggestions or [], ensure_ascii=False),
             status, tokens_in, tokens_out)
        )
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
                        "sources": sources or [], "suggestions": suggestions or [],
                        "status": status, "tokens_in": tokens_in, "tokens_out": tokens_out,
                    }, conv["user_id"])
    except Exception:
        pass
    return mid


def update_message(msg_id: str, **kw) -> bool:
    """Update message fields. For streaming intermediate saves."""
    allowed = {"content", "thinking", "tool_calls", "files", "sources",
               "suggestions", "status", "tokens_in", "tokens_out"}
    fields = {}
    for k, v in kw.items():
        if k not in allowed:
            continue
        if k in ("tool_calls", "files", "sources", "suggestions") and isinstance(v, (list, dict)):
            fields[k] = json.dumps(v, ensure_ascii=False)
        else:
            fields[k] = v
    if not fields:
        return False
    with _conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE messages SET {sets} WHERE id=?", (*fields.values(), msg_id))
        changed = c.total_changes > 0
    try:  # 云同步：仅消息 complete 时同步全文（流式中途不刷）+ 结束实时任务活动
        if fields.get("status") == "complete":
            from hashmm.api import supabase_sync
            if supabase_sync.enabled():
                mrow = _message_row(msg_id)
                if mrow:
                    conv = get_conversation(mrow.get("conv_id"))
                    if conv:
                        supabase_sync.push_message(mrow, conv["user_id"])
                        supabase_sync.remove_activity(mrow.get("conv_id"), conv["user_id"])
    except Exception:
        pass
    return changed


def _message_row(msg_id: str) -> dict | None:
    """读单条消息（供云同步用，调用方线程内同步执行）。"""
    with _conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return dict(r) if r else None


def get_active_chats(user_id: str) -> list[dict]:
    """用户名下处于 streaming（正在生成）状态的助手消息所属会话——供「客户端任务进度」。"""
    with _conn() as c:
        rows = c.execute(
            """SELECT m.conv_id AS conv_id, m.id AS msg_id, m.created_at AS created_at, co.title AS title
               FROM messages m JOIN conversations co ON co.id = m.conv_id
               WHERE co.user_id=? AND m.status='streaming'
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
            results.append(d)
        return results


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
                   (id, conv_id, role, content, thinking, tool_calls, files, sources, suggestions, status, tokens_in, tokens_out, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(?, strftime('%s','now')))""",
                (mid, conv_id, m.get("role", "user") or "user", m.get("content", "") or "", m.get("thinking", "") or "",
                 _j(m.get("tool_calls")), _j(m.get("files")), _j(m.get("sources")), _j(m.get("suggestions")),
                 m.get("status", "complete") or "complete", 0, 0, _ts(m.get("created_at")))
            )
            if cur.rowcount:
                n += 1
    return n


def get_recent_messages(conv_id: str, n: int = 12) -> list[dict]:
    """Get last N messages (for LLM context building)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, thinking FROM messages WHERE conv_id=? ORDER BY created_at DESC LIMIT ?",
            (conv_id, n)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def delete_last_assistant_message(conv_id: str) -> bool:
    """Delete the latest assistant message (for regeneration)."""
    with _conn() as c:
        r = c.execute(
            "SELECT id FROM messages WHERE conv_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
            (conv_id,)
        ).fetchone()
        if r:
            c.execute("DELETE FROM messages WHERE id=?", (r["id"],))
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
                lines.append(f"\n📎 {fname}\n")
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
                    logger.warning(f"Migration {mid}: {e}")
        if count:
            logger.info(f"{count} migrations applied")


# ═══════════════════════════════════════════════════════════════════
# v25: Project System
# ═══════════════════════════════════════════════════════════════════

def _ensure_project_table():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            custom_prompt TEXT DEFAULT '',
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now'))
        )""")
        # Add project_id to conversations if not exists
        try:
            c.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT DEFAULT NULL")
        except Exception as _e:

            pass  # Silenced: see logs if needed

def create_project(user_id: str, name: str, description: str = "", custom_prompt: str = "") -> str:
    _ensure_project_table()
    pid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute("INSERT INTO projects (id, user_id, name, description, custom_prompt) VALUES (?,?,?,?,?)",
                  (pid, user_id, name, description, custom_prompt))
    return pid

def list_projects(user_id: str) -> list[dict]:
    _ensure_project_table()
    with _conn() as c:
        rows = c.execute("""
            SELECT p.*, COUNT(cv.id) as conv_count 
            FROM projects p LEFT JOIN conversations cv ON cv.project_id = p.id
            WHERE p.user_id = ? GROUP BY p.id ORDER BY p.updated_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]

def get_project(pid: str) -> dict | None:
    _ensure_project_table()
    with _conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

def update_project(pid: str, **kwargs):
    _ensure_project_table()
    allowed = {"name", "description", "custom_prompt"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return
    set_clause = ", ".join(f"{k}=?" for k in sets)
    with _conn() as c:
        c.execute(f"UPDATE projects SET {set_clause}, updated_at=strftime('%s','now') WHERE id=?",
                  (*sets.values(), pid))

def assign_conv_to_project(conv_id: str, project_id: str | None):
    _ensure_project_table()
    with _conn() as c:
        c.execute("UPDATE conversations SET project_id=? WHERE id=?", (project_id, conv_id))


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
