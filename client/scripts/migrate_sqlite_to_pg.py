#!/usr/bin/env python3
"""Copy all data from the existing SQLite DB into PostgreSQL.

Run AFTER scripts/init_postgres.py has created the schema.

Usage:
    export HASHMM_DB_PATH="data/hashmm.sqlite"          # source
    export HASHMM_PG_DSN="postgresql://hashmm:hashmm@127.0.0.1:5432/hashmm"  # target
    python scripts/migrate_sqlite_to_pg.py

Safe: reads SQLite, writes PG row-by-row inside a transaction per table.
Skips tables that don't exist in the source. Idempotent-ish: uses INSERT ...
ON CONFLICT DO NOTHING so re-running won't duplicate.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tables to migrate (order matters for FKs: parents before children)
TABLES = [
    "users", "models", "knowledge_bases", "audit_logs",
    "conversations", "messages", "conversation_files",
    "user_memories", "conversation_tags", "skills", "user_profiles",
    "prompt_feedback", "episodes", "skill_variants",
    "user_memory", "shared_conversations", "prompt_templates",
    "projects",
]


def main():
    src_path = os.environ.get("HASHMM_DB_PATH", "data/hashmm.sqlite")
    if not os.path.exists(src_path):
        print(f"⚠️  Source SQLite not found: {src_path}")
        sys.exit(1)

    from hashmm.api import db_backend
    if not db_backend.IS_POSTGRES:
        print("⚠️  Set HASHMM_DB_BACKEND=postgres and HASHMM_PG_DSN first.")
        sys.exit(1)

    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row
    dst = db_backend.make_pg_conn()

    total = 0
    try:
        existing = {r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in TABLES:
            if table not in existing:
                continue
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"  • {table}: 0 rows")
                continue
            cols = rows[0].keys()
            collist = ",".join(cols)
            placeholders = ",".join(["?"] * len(cols))
            sql = (f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
                   f"ON CONFLICT DO NOTHING")
            n = 0
            for row in rows:
                try:
                    dst.execute(sql, tuple(row[c] for c in cols))
                    n += 1
                except Exception as e:
                    print(f"    ⚠️ {table} row skipped: {str(e)[:80]}")
            dst.commit()
            total += n
            print(f"  ✅ {table}: {n}/{len(rows)} rows")
        print(f"\n✅ Migration complete: {total} rows copied to PostgreSQL.")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
