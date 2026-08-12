import asyncio


def test_messages_default_path_does_not_touch_supabase(monkeypatch):
    from hashmm.api.routes import conversations as routes
    from hashmm.api import supabase_sync

    local = [{"id": "m-local", "content": "local"}]
    monkeypatch.setattr(
        routes,
        "require_conv_access",
        lambda _request, conv_id: {"id": conv_id, "user_id": "user-1"},
    )
    monkeypatch.setattr(
        routes.db,
        "get_latest_messages",
        lambda _conv_id, limit=100, before_ts=None: local,
    )
    monkeypatch.setattr(routes.db, "count_messages", lambda _conv_id: len(local))
    monkeypatch.setattr(supabase_sync, "enabled", lambda: True)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the polling fast path must not call Supabase")

    monkeypatch.setattr(supabase_sync, "pull_messages", unexpected)
    monkeypatch.setattr(supabase_sync, "backfill_messages", unexpected)

    result = asyncio.run(routes.get_conv_messages("conv-1", object()))

    assert result["messages"] == local
    assert result["page"] == {
        "total": len(local), "has_more": False, "oldest_created_at": None,
    }


def test_messages_explicit_cloud_sync_imports_remote_rows(monkeypatch):
    from hashmm.api.routes import conversations as routes
    from hashmm.api import supabase_sync

    local = [{"id": "m-local", "content": "local"}]
    remote = local + [{"id": "m-remote", "content": "remote"}]
    reads = iter((local, remote))
    imported = []

    monkeypatch.setattr(
        routes,
        "require_conv_access",
        lambda _request, conv_id: {"id": conv_id, "user_id": "user-1"},
    )
    monkeypatch.setattr(
        routes.db,
        "get_latest_messages",
        lambda _conv_id, limit=100, before_ts=None: next(reads),
    )
    monkeypatch.setattr(routes.db, "count_messages", lambda _conv_id: len(remote))
    monkeypatch.setattr(
        routes.db,
        "import_messages_local",
        lambda conv_id, rows: imported.append((conv_id, rows)),
    )
    monkeypatch.setattr(supabase_sync, "enabled", lambda: True)
    monkeypatch.setattr(supabase_sync, "pull_messages", lambda _conv_id, _uid: remote)
    monkeypatch.setattr(
        supabase_sync,
        "backfill_messages",
        lambda *_args: (_ for _ in ()).throw(AssertionError("recovery read wrote cloud")),
    )

    result = asyncio.run(
        routes.get_conv_messages("conv-1", object(), cloud_sync=1)
    )

    assert result["messages"] == remote
    assert result["page"]["total"] == len(remote)
    assert imported == [("conv-1", remote)]
