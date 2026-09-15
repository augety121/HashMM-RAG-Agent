#!/usr/bin/env python3
"""08 — List and inspect past sessions.

Usage:
    # List recent sessions
    python scripts/08_session_resume.py --list

    # Show all turns of a session
    python scripts/08_session_resume.py --session <session_id>

    # Delete a session and all its turns
    python scripts/08_session_resume.py --session <session_id> --delete

    # Pretty-resume hint: print the command to continue this session
    python scripts/08_session_resume.py --session <session_id> --resume-cmd
"""

from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.table import Table

from hashmm.config import HashMMConfig
from hashmm.memory import SessionStore

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="show recent sessions (default if no other action)")
    ap.add_argument("--session", default=None,
                    help="session_id to inspect")
    ap.add_argument("--delete", action="store_true",
                    help="delete the named session (requires --session)")
    ap.add_argument("--user", default="default",
                    help="user_id to filter by (default 'default')")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--resume-cmd", action="store_true",
                    help="print a copy-pasteable resume command")
    args = ap.parse_args()

    cfg = HashMMConfig()
    sess = SessionStore(cfg.episodic_db_path)

    if args.session and args.delete:
        n = sess.delete_session(args.session)
        console.print(f"[yellow]deleted session {args.session}: "
                      f"{n} sessions removed[/yellow]")
        sess.close()
        return

    if args.session and args.resume_cmd:
        console.print(
            f"# Resume session {args.session}:\n"
            f"python scripts/06_agent_query.py --session {args.session} "
            f"--query \"YOUR NEXT QUERY\""
        )
        sess.close()
        return

    if args.session:
        turns = sess.get_all_turns(args.session)
        if not turns:
            console.print(f"[yellow]no turns found for session "
                          f"{args.session}[/yellow]")
            sess.close()
            return

        tbl = Table(title=f"Session {args.session}", show_header=True)
        tbl.add_column("#", width=3)
        tbl.add_column("intent", width=12)
        tbl.add_column("strategy", width=10)
        tbl.add_column("n_res", width=6)
        tbl.add_column("ok", width=4)
        tbl.add_column("cache", width=6)
        tbl.add_column("query", overflow="fold")
        for t in turns:
            tbl.add_row(
                str(t["turn_idx"]),
                t.get("intent") or "—",
                t.get("strategy") or "—",
                str(t.get("n_results", 0)),
                "✓" if t["quality_ok"] else "✗",
                "HIT" if t["cache_hit"] else "—",
                (t["query"] or "")[:80],
            )
        console.print(tbl)
        sess.close()
        return

    # Default: list
    rows = sess.list_sessions(args.user, args.limit)
    if not rows:
        console.print(f"[yellow]no sessions for user {args.user!r}[/yellow]")
        sess.close()
        return

    tbl = Table(title=f"Recent sessions (user={args.user})", show_header=True)
    tbl.add_column("session_id", width=18)
    tbl.add_column("turns", width=6)
    tbl.add_column("created", width=20)
    tbl.add_column("updated", width=20)
    import time as _time
    for r in rows:
        tbl.add_row(
            r["session_id"],
            str(r["n_turns"]),
            _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(r["created_at"])),
            _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(r["updated_at"])),
        )
    console.print(tbl)
    sess.close()


if __name__ == "__main__":
    main()
