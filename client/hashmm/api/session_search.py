"""session_search — 跨会话记忆搜索（V97，对标 Hermes FTS5 session search）。

Hermes 的核心能力之一是"搜索自己的过往对话"——用 SQLite FTS5 全文索引 +
LLM 摘要做跨会话召回。HashMM 已把所有消息持久化在 messages 表，这里补上
检索层：

- 优先用 **FTS5 虚拟表**（messages_fts）做全文匹配，BM25 排序；
- SQLite 未编译 FTS5 时**自动降级 LIKE 扫描**（功能不变，慢一点）；
- 严格按 user_id 隔离（只搜自己的会话）；
- 返回命中片段 + 所属会话标题，供"我们之前聊过的 X"这类召回。

纯标准库（sqlite3），零新依赖。索引惰性构建/增量同步。
"""
from __future__ import annotations

from hashmm.api import database as db

_FTS_READY = None  # None=未检测, True/False=FTS5 可用性


def _fts_available(conn) -> bool:
    """检测当前 SQLite 是否支持 FTS5（只检测一次）。"""
    global _FTS_READY
    if _FTS_READY is not None:
        return _FTS_READY
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        _FTS_READY = True
    except Exception:
        _FTS_READY = False
    return _FTS_READY


def ensure_index(conn) -> bool:
    """确保 FTS5 索引存在并与 messages 同步。返回是否启用了 FTS。"""
    if not _fts_available(conn):
        return False
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content, conv_id UNINDEXED, msg_id UNINDEXED, tokenize='unicode61'
        )""")
    # 增量同步：把还没进 FTS 的消息补进去（用 rowid 对齐避免全表重建）
    conn.execute("""
        INSERT INTO messages_fts (content, conv_id, msg_id)
        SELECT m.content, m.conv_id, m.id FROM messages m
        WHERE m.content != '' AND m.id NOT IN (SELECT msg_id FROM messages_fts)
    """)
    return True


def search(user_id: str, query: str, limit: int = 20) -> dict:
    """跨会话搜索用户自己的消息。返回 {ok, mode, results:[{conv_id,title,snippet,role,created_at}]}."""
    q = (query or "").strip()
    if not q:
        return {"ok": True, "mode": "empty", "results": []}
    with db._conn() as conn:
        use_fts = False
        try:
            use_fts = ensure_index(conn)
        except Exception:
            use_fts = False

        rows = []
        if use_fts:
            try:
                # FTS5 MATCH + 关联回 messages/conversations，按用户过滤、bm25 排序
                rows = conn.execute("""
                    SELECT m.conv_id AS conv_id, c.title AS title, m.content AS content,
                           m.role AS role, m.created_at AS created_at
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.msg_id
                    JOIN conversations c ON c.id = m.conv_id
                    WHERE c.user_id = ? AND messages_fts MATCH ?
                    ORDER BY bm25(messages_fts) LIMIT ?
                """, (user_id, _fts_query(q), limit)).fetchall()
            except Exception:
                use_fts = False  # MATCH 语法异常 → 落 LIKE

        if not use_fts:
            like = f"%{q}%"
            rows = conn.execute("""
                SELECT m.conv_id AS conv_id, c.title AS title, m.content AS content,
                       m.role AS role, m.created_at AS created_at
                FROM messages m JOIN conversations c ON c.id = m.conv_id
                WHERE c.user_id = ? AND m.content LIKE ?
                ORDER BY m.created_at DESC LIMIT ?
            """, (user_id, like, limit)).fetchall()

        results = []
        for r in rows:
            results.append({
                "conv_id": r["conv_id"], "title": r["title"] or "对话",
                "snippet": _snippet(r["content"], q), "role": r["role"],
                "created_at": r["created_at"],
            })
    return {"ok": True, "mode": "fts5" if use_fts else "like", "results": results}


def _fts_query(q: str) -> str:
    """把用户输入转成安全的 FTS5 查询：分词后 OR 连接，转义引号。"""
    terms = [t for t in q.replace('"', " ").split() if t]
    if not terms:
        return '""'
    # 每个词加引号做短语，OR 连接（宽松召回）
    return " OR ".join(f'"{t}"' for t in terms)


def _snippet(content: str, query: str, radius: int = 60) -> str:
    """围绕首个命中词截取片段。"""
    if not content:
        return ""
    low = content.lower()
    pos = -1
    for term in query.lower().split():
        pos = low.find(term)
        if pos >= 0:
            break
    if pos < 0:
        return content[:radius * 2].strip()
    start = max(0, pos - radius)
    end = min(len(content), pos + radius)
    snip = content[start:end].strip()
    return ("…" if start > 0 else "") + snip + ("…" if end < len(content) else "")
