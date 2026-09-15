#!/usr/bin/env python3
"""Verify that a packaged HashMM server can upgrade a legacy approval DB."""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile


LEGACY_APPROVAL_SCHEMA = """
CREATE TABLE tool_approval_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    conv_id TEXT NOT NULL,
    message_id TEXT DEFAULT '',
    fingerprint TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    cwd TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    risk TEXT DEFAULT 'high',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    decided_at REAL DEFAULT 0,
    expires_at REAL NOT NULL,
    consumed_at REAL DEFAULT 0,
    decided_by TEXT DEFAULT ''
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--release", default="V1000")
    args = parser.parse_args()
    archive = args.archive.resolve()
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise SystemExit(f"server ZIP is unavailable: {archive}")

    sys.path.insert(0, str(archive))
    import hashmm
    from hashmm.api import database as db

    if hashmm.RELEASE != args.release or str(archive) not in str(hashmm.__file__):
        raise SystemExit(
            f"wrong packaged backend: release={hashmm.RELEASE} file={hashmm.__file__}"
        )

    with tempfile.TemporaryDirectory(prefix="hashmm-server-upgrade-") as temp:
        database_path = Path(temp) / "legacy.sqlite"
        with closing(sqlite3.connect(str(database_path))) as conn:
            with conn:
                conn.execute(LEGACY_APPROVAL_SCHEMA)
                conn.execute(
                    "INSERT INTO tool_approval_requests"
                    "(id,user_id,conv_id,fingerprint,tool_name,created_at,expires_at) "
                    "VALUES('old','alice','conv','fp','shell',1,9999999999)"
                )

        db._close_pool()
        db.DB_PATH = database_path
        db._MODELS_MIRROR = Path(temp) / "no-model-mirror.json"
        db.init_db()
        db._close_pool()
        with closing(sqlite3.connect(str(database_path))) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info('tool_approval_requests')")
            }
            indexes = {
                str(row[1])
                for row in conn.execute("PRAGMA index_list('tool_approval_requests')")
            }
            row = conn.execute(
                "SELECT id,user_id,work_run_id,step_id,call_id,scope_json "
                "FROM tool_approval_requests WHERE id='old'"
            ).fetchone()

        required = {"work_run_id", "step_id", "call_id", "scope_json"}
        if not required <= columns:
            raise SystemExit(f"legacy migration columns missing: {sorted(required - columns)}")
        if "idx_tool_approval_run" not in indexes:
            raise SystemExit("legacy migration index missing: idx_tool_approval_run")
        if tuple(row or ()) != ("old", "alice", "", "", "", "{}"):
            raise SystemExit(f"legacy approval row changed unexpectedly: {row!r}")

    print(
        f"[server-upgrade] OK: release={hashmm.RELEASE} "
        f"zip-import=true legacy-row-preserved=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
