"""V358 regressions: durable long-chat checkpoints and resilient Supabase auth."""
from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from hashmm.agent.conv_compact import (
    PERSISTENT_SUMMARY_MARK,
    prepare_persistent_history,
)
from hashmm.api import supabase_auth
from hashmm.api import database
from hashmm.context_builder import TokenBudgetBuilder


class _FakeContextDb:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.state: dict | None = None

    def get_context_compaction(self, _conv_id: str):
        return dict(self.state) if self.state else None

    def get_context_messages_after(self, _conv_id: str, after_rowid: int = 0):
        return [dict(row) for row in self.rows if row["_rowid"] > after_rowid]

    def get_recent_messages(self, _conv_id: str, n: int = 80):
        return [{"role": row["role"], "content": row["content"]} for row in self.rows[-n:]]

    def invalidate_context_compaction(self, _conv_id: str):
        self.state = None

    @contextmanager
    def _conn(self):
        class _Conn:
            def __init__(self, outer): self.outer = outer
            def execute(self, _sql, args):
                rowid = int(args[0])
                found = any(row["_rowid"] == rowid for row in self.outer.rows)
                class _Cursor:
                    def fetchone(self): return (1,) if found else None
                return _Cursor()
        yield _Conn(self)

    def save_context_compaction(self, _conv_id: str, summary: str, through_rowid: int,
                                source_messages: int, estimated_tokens: int,
                                trigger: str = "auto"):
        previous_count = int((self.state or {}).get("compaction_count") or 0)
        previous_sources = int((self.state or {}).get("source_messages") or 0)
        self.state = {
            "summary": summary,
            "through_rowid": through_rowid,
            "source_messages": previous_sources + source_messages,
            "estimated_tokens": estimated_tokens,
            "compaction_count": previous_count + 1,
            "last_trigger": trigger,
        }
        return dict(self.state)


def _long_rows(start: int, count: int, *, goal: str = "") -> list[dict]:
    rows = []
    for offset in range(count):
        rowid = start + offset
        role = "user" if offset % 2 == 0 else "assistant"
        if offset == 0 and goal:
            content = goal + "；必须保留原始目标。" + "约束" * 260
        elif role == "user":
            content = f"第{rowid}轮要求：保持既有决定。" + "细节" * 260
        else:
            content = f"第{rowid}轮已报告进展，仍需验证。" + "结果" * 260
        rows.append({"_rowid": rowid, "id": f"m{rowid}", "role": role,
                     "content": content, "files": [], "created_at": float(rowid)})
    return rows


def test_durable_compaction_keeps_goal_before_old_eighty_message_slice():
    goal = "主线目标：完成可恢复的长对话系统"
    rows = _long_rows(1, 120, goal=goal)
    original = [dict(row) for row in rows]
    db = _FakeContextDb(rows)

    prepared = prepare_persistent_history(db, "conv", token_budget=4000, keep_recent_tokens=2000)

    assert prepared.compacted_now
    assert prepared.source_messages > 0
    assert prepared.history[0]["content"].startswith(PERSISTENT_SUMMARY_MARK)
    assert goal in prepared.history[0]["content"]
    assert db.state and db.state["through_rowid"] > 0
    assert rows == original, "compaction must never rewrite or delete full messages"


def test_incremental_compaction_survives_restart_and_advances_checkpoint():
    goal = "原始目标：跨数百轮持续完成项目"
    db = _FakeContextDb(_long_rows(1, 80, goal=goal))
    first = prepare_persistent_history(db, "conv", token_budget=4000, keep_recent_tokens=2000)
    first_cursor = first.through_rowid
    db.rows.extend(_long_rows(81, 60))

    second = prepare_persistent_history(db, "conv", token_budget=4000, keep_recent_tokens=2000)

    assert second.compacted_now
    assert second.through_rowid > first_cursor
    assert second.compaction_count == 2
    assert goal in second.history[0]["content"]


def test_token_budget_builder_reserves_checkpoint_instead_of_summarizing_it_away():
    checkpoint = PERSISTENT_SUMMARY_MARK + "\n【原始目标】不能丢失的主线"
    history = [{"role": "system", "content": checkpoint}]
    history.extend({"role": "user" if i % 2 == 0 else "assistant", "content": f"最近{i}"}
                   for i in range(8))
    messages = TokenBudgetBuilder(total_budget=8000).build(
        task_type="chat", query="继续", system_prompt="系统", history=history,
    )
    assert any(m["role"] == "system" and "不能丢失的主线" in m["content"] for m in messages)


def _opaque_jwt(exp: int) -> str:
    def enc(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp})}.signature"


def test_previously_verified_supabase_session_survives_transient_provider_outage(monkeypatch):
    now = int(time.time())
    token = _opaque_jwt(now + 1800)
    calls = {"remote": 0}
    supabase_auth._verify_cache.clear()
    monkeypatch.setattr(supabase_auth, "enabled", lambda: True)
    monkeypatch.setattr(supabase_auth, "_verify_jwks", lambda _token: None)

    def remote(_token):
        calls["remote"] += 1
        return {"sub": "u", "email": "user@example.com"} if calls["remote"] == 1 else None

    monkeypatch.setattr(supabase_auth, "_verify_remote", remote)
    first = supabase_auth.verify_token(token)
    second = supabase_auth.verify_token(token)
    assert first and first["uid"] == "sb_u" and first["role"] == "user"
    assert second and second["uid"] == "sb_u" and second["role"] == "user"
    assert calls["remote"] == 1, "a verified immutable token should not be re-rejected during an outage"


def test_concurrent_requests_singleflight_supabase_verification(monkeypatch):
    token = _opaque_jwt(int(time.time()) + 1800)
    calls = {"remote": 0}
    supabase_auth._verify_cache.clear()
    supabase_auth._verify_locks.clear()
    monkeypatch.setattr(supabase_auth, "enabled", lambda: True)
    monkeypatch.setattr(supabase_auth, "_verify_jwks", lambda _token: None)

    def remote(_token):
        calls["remote"] += 1
        time.sleep(0.04)
        return {"sub": "same-user", "email": "user@example.com"}

    monkeypatch.setattr(supabase_auth, "_verify_remote", remote)
    with ThreadPoolExecutor(max_workers=8) as pool:
        users = list(pool.map(supabase_auth.verify_token, [token] * 8))

    assert all(user and user["uid"] == "sb_same-user" for user in users)
    assert calls["remote"] == 1


def test_reopened_long_chat_returns_latest_page_in_chronological_order(monkeypatch):
    rows = []
    for index in (5, 4, 3):  # SQL returns newest first
        rows.append({
            "id": f"m{index}", "conv_id": "conv", "role": "user",
            "content": str(index), "thinking": "", "tool_calls": "[]",
            "files": "[]", "sources": "[]", "groundings": "{}",
            "run_manifest": "{}", "suggestions": "[]", "status": "complete",
            "tokens_in": 0, "tokens_out": 0, "created_at": float(index),
        })

    @contextmanager
    def fake_conn():
        class _Cursor:
            def fetchall(self): return rows
        class _Conn:
            def execute(self, _sql, _args): return _Cursor()
        yield _Conn()

    monkeypatch.setattr(database, "_conn", fake_conn)
    page = database.get_latest_messages("conv", limit=3)
    assert [item["id"] for item in page] == ["m3", "m4", "m5"]


def test_manual_compaction_runs_below_auto_threshold_and_records_trigger():
    db = _FakeContextDb(_long_rows(1, 12, goal="手动压缩也必须保留目标"))
    prepared = prepare_persistent_history(
        db, "conv", token_budget=24000, keep_recent_tokens=12000,
        force=True, trigger="manual",
    )
    assert prepared.compacted_now is True
    assert db.state and db.state["last_trigger"] == "manual"
    assert db.state["source_messages"] > 0
    assert db.state["through_rowid"] > 0
    assert db.state["summary"].startswith(PERSISTENT_SUMMARY_MARK)


def test_compaction_preserves_tool_only_evidence_and_failure_status():
    rows = _long_rows(1, 24, goal="需要保留运行证据")
    rows[1].update({
        "content": "",
        "tool_calls": [
            {"name": "run_shell", "status": "failed"},
            {"name": "create_document", "status": "completed"},
        ],
        "files": [{"filename": "交付.docx"}],
        "sources": [{"url": "https://example.invalid/source-id"}],
        "run_manifest": {"checks": [{"name": "typecheck", "status": "failed"}]},
    })
    db = _FakeContextDb(rows)

    prepared = prepare_persistent_history(
        db, "conv", token_budget=4000, keep_recent_tokens=2000, force=True,
    )

    assert prepared.compacted_now is True
    checkpoint = prepared.history[0]["content"]
    assert "run_shell [failed]" in checkpoint
    assert "交付.docx" in checkpoint
    assert "https://example.invalid/source-id" in checkpoint
    assert "验证 typecheck: failed" in checkpoint


def test_compaction_retries_after_optimistic_cursor_conflict():
    class _ConflictOnceDb(_FakeContextDb):
        def __init__(self, rows):
            super().__init__(rows)
            self.conflicts = 0

        def save_context_compaction(self, _conv_id: str, summary: str,
                                    through_rowid: int, source_messages: int,
                                    estimated_tokens: int, trigger: str = "auto",
                                    expected_through_rowid: int | None = None):
            if self.conflicts == 0:
                self.conflicts += 1
                self.state = {
                    "summary": PERSISTENT_SUMMARY_MARK + "\n【此前检查点】并发请求",
                    "through_rowid": 2,
                    "source_messages": 2,
                    "estimated_tokens": 30,
                    "compaction_count": 1,
                    "last_trigger": "auto",
                }
                return {**self.state, "conflict": True}
            return super().save_context_compaction(
                _conv_id, summary, through_rowid, source_messages,
                estimated_tokens, trigger,
            )

    db = _ConflictOnceDb(_long_rows(1, 40, goal="并发压缩不能倒退"))
    prepared = prepare_persistent_history(
        db, "conv", token_budget=4000, keep_recent_tokens=2000,
    )

    assert db.conflicts == 1
    assert prepared.compacted_now is True
    assert prepared.through_rowid > 2
    assert prepared.degraded is False
