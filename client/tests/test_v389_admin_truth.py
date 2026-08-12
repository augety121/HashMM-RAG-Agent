"""V389 regressions for non-destructive templates and truthful admin actions."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.regression


class _Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def _close_test_pool(database) -> None:
    pool = database._pool
    if pool is None:
        return
    while not pool.empty():
        pool.get_nowait().close()
    database._pool = None


def test_template_database_update_preserves_identity_and_usage(tmp_path, monkeypatch):
    from hashmm.api import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "templates.sqlite")
    monkeypatch.setattr(database, "_pool", None)
    try:
        tid = database.create_template("Original", "general", "Prompt A", ["topic"], "admin")
        used = database.use_template(tid)
        assert used and used["use_count"] == 1
        created_at = used["created_at"]

        updated = database.update_template(tid, "Revised", "research", "Prompt B", ["source"])
        assert updated is not None
        assert updated["id"] == tid
        assert updated["use_count"] == 1
        assert updated["created_at"] == created_at
        assert updated["name"] == "Revised"
        assert updated["variables"] == '["source"]'
        assert database.delete_template(tid) is True
        assert database.delete_template(tid) is False
    finally:
        _close_test_pool(database)


def test_template_routes_use_atomic_database_contract(monkeypatch):
    from hashmm.api.routes import admin as route

    calls = []
    monkeypatch.setattr(route, "require_admin", lambda _request: {"uid": "u1", "sub": "admin"})
    monkeypatch.setattr(route.db, "audit", lambda *args: calls.append(("audit", args[2])))
    monkeypatch.setattr(route.db, "list_templates", lambda **_kwargs: [{"id": "stable-id"}])
    monkeypatch.setattr(route.db, "create_template", lambda *args: calls.append(("create", args)) or "stable-id")
    monkeypatch.setattr(route.db, "update_template", lambda tid, *args: calls.append(("update", tid, args)) or {"id": tid, "use_count": 9})

    payload = {"name": "Research", "category": "research", "prompt": "Use evidence", "variables": ["topic", "topic"]}
    created = asyncio.run(route.create_template(_Request(payload)))
    updated = asyncio.run(route.update_template("stable-id", _Request(payload)))

    assert created["ok"] is True and created["id"] == "stable-id"
    assert updated["template"]["use_count"] == 9
    assert any(item[0] == "create" for item in calls)
    assert any(item[0] == "update" for item in calls)


def test_template_payload_and_ids_fail_closed():
    from hashmm.api.routes.admin import _template_payload, _validated_template_id

    with pytest.raises(HTTPException) as bad_vars:
        asyncio.run(_template_payload(_Request({"name": "x", "prompt": "y", "variables": "topic"})))
    assert bad_vars.value.status_code == 400

    with pytest.raises(HTTPException) as traversal:
        _validated_template_id("../template")
    assert traversal.value.status_code == 400
