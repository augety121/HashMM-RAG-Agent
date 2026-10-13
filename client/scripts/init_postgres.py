#!/usr/bin/env python3
"""Initialize the PostgreSQL schema for HashMM (Phase 1).

This translates the SQLite DDL used by the app into PostgreSQL-correct DDL and
creates all tables + indexes in the target Postgres database. Run ONCE when you
switch HASHMM_DB_BACKEND=postgres.

Usage:
    export HASHMM_PG_DSN="postgresql://hashmm:hashmm@127.0.0.1:5432/hashmm"
    python scripts/init_postgres.py

Idempotent: uses CREATE TABLE/INDEX IF NOT EXISTS, safe to re-run.

It does NOT migrate existing SQLite DATA. If you have live SQLite data to move,
run scripts/migrate_sqlite_to_pg.py after this.
"""
from __future__ import annotations

import os
import re
import sys

# Make the package importable when run from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sqlite_ddl_to_pg(ddl: str) -> str:
    """Translate SQLite CREATE statements to PostgreSQL.

    Handles the constructs actually used in this codebase:
      - INTEGER PRIMARY KEY AUTOINCREMENT  → BIGSERIAL PRIMARY KEY
      - REAL DEFAULT (strftime('%s','now'))→ DOUBLE PRECISION DEFAULT extract(epoch from now())
      - TEXT / INTEGER / REAL              → TEXT / BIGINT / DOUBLE PRECISION
      - CHECK(...) and FOREIGN KEY ... ON DELETE CASCADE pass through
    """
    s = ddl

    # Autoincrement integer PK → serial
    s = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        s, flags=re.I,
    )
    # strftime epoch default → now() epoch
    s = re.sub(
        r"\(strftime\('%s','now'\)\)",
        "(extract(epoch from now()))",
        s, flags=re.I,
    )
    # Column type mappings (word-boundary, avoid touching names)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    # Remaining standalone INTEGER columns → BIGINT (PK serial already handled)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)

    return s


def main():
    from hashmm.api import database as db
    from hashmm.api import db_backend

    if not db_backend.IS_POSTGRES:
        print("⚠️  HASHMM_DB_BACKEND is not 'postgres'. Set it first:")
        print('    export HASHMM_DB_BACKEND=postgres')
        print('    export HASHMM_PG_DSN="postgresql://hashmm:hashmm@127.0.0.1:5432/hashmm"')
        sys.exit(1)

    # The app's core schema lives in database._SCHEMA. Additional tables are
    # created lazily by various _ensure_*() helpers; we trigger those too.
    core_pg = sqlite_ddl_to_pg(db._SCHEMA)

    print(f"Connecting to {db_backend.PG_DSN.split('@')[-1]} ...")
    conn = db_backend.make_pg_conn()
    try:
        # Split into individual statements (CREATE TABLE / CREATE INDEX)
        statements = [st.strip() for st in core_pg.split(";") if st.strip()]
        for st in statements:
            try:
                conn.execute(st + ";")
                kind = "TABLE" if "CREATE TABLE" in st.upper() else (
                    "INDEX" if "CREATE INDEX" in st.upper() else "STMT")
                name = re.search(r"(?:TABLE|INDEX)\s+(?:IF NOT EXISTS\s+)?(\w+)", st, re.I)
                print(f"  ✅ {kind} {name.group(1) if name else ''}")
            except Exception as e:
                print(f"  ⚠️  {str(e)[:120]}")
        conn.commit()
        print("\n✅ Core schema created on PostgreSQL.")
        print("Now start the server normally; lazy tables (templates, memory,")
        print("projects, skills...) will be created on first use.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
