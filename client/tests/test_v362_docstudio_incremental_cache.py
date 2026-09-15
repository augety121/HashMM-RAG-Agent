import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]


def _request(headers=None) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/test",
        "query_string": b"", "headers": headers or [],
    })


def test_workspace_uses_database_root_and_lists_only_owned_conversations(monkeypatch, tmp_path):
    from hashmm.api import auth, database as db
    from hashmm.api.routes import files

    owned = tmp_path / "owned"
    foreign = tmp_path / "foreign"
    owned.mkdir(); foreign.mkdir()
    (owned / "report.md").write_text("owned", encoding="utf-8")
    (foreign / "secret.md").write_text("foreign", encoding="utf-8")

    monkeypatch.setattr(auth, "require_auth", lambda _request: {"uid": "u1", "sub": "u1"})
    monkeypatch.setattr(db, "CONV_FILES_ROOT", tmp_path)
    monkeypatch.setattr(db, "list_conversations", lambda uid, limit=500: [{"id": "owned"}])

    result = asyncio.run(files.list_workspace(_request()))
    assert [item["filename"] for item in result["files"]] == ["report.md"]
    source = (ROOT / "hashmm/api/routes/files.py").read_text(encoding="utf-8")
    assert "tool_registry import CONV_FILES_ROOT" not in source


def test_conversation_upload_validates_name_before_touching_workspace(monkeypatch, tmp_path):
    from hashmm.api.routes import conversations

    touched = []
    monkeypatch.setattr(conversations, "require_conv_access", lambda *_: {"id": "c1", "user_id": "u1"})
    monkeypatch.setattr(conversations.db, "conv_files_dir", lambda cid: touched.append(cid) or tmp_path)
    upload = UploadFile(filename="../secret.txt", file=io.BytesIO(b"no"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(conversations.upload_file_to_conv("c1", _request(), upload))
    assert exc.value.status_code == 400
    assert touched == []


def test_conversation_upload_returns_hash_of_committed_bytes(monkeypatch, tmp_path):
    from hashmm.api.routes import conversations

    payload = b"revision-receipt"
    monkeypatch.setattr(conversations, "require_conv_access", lambda *_: {"id": "c1", "user_id": "u1"})
    monkeypatch.setattr(conversations.db, "conv_files_dir", lambda _cid: tmp_path)
    monkeypatch.setattr(conversations.db, "add_conversation_file", lambda *_args, **_kwargs: None)
    upload = UploadFile(filename="notes.txt", file=io.BytesIO(payload))
    result = asyncio.run(conversations.upload_file_to_conv("c1", _request(), upload))

    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "notes.txt").read_bytes() == payload


def test_conversation_file_list_is_private_and_conditionally_cached(monkeypatch, tmp_path):
    import json
    from hashmm.api.routes import conversations

    target = tmp_path / "report.txt"
    target.write_text("grounded", encoding="utf-8")
    monkeypatch.setattr(conversations, "require_conv_access", lambda *_: {"id": "c1", "user_id": "u1"})
    monkeypatch.setattr(conversations.db, "conv_files_dir", lambda _cid: tmp_path)
    monkeypatch.setattr(conversations.db, "list_conversation_files", lambda _cid: [{
        "id": "f1", "filename": "report.txt", "path": r"C:\\private\\owner\\report.txt",
        "size": 1, "mime_type": "text/plain", "created_at": 1,
    }])

    first = asyncio.run(conversations.list_conv_files("c1", _request()))
    body = json.loads(first.body)
    assert body["files"][0]["size"] == len("grounded")
    assert "path" not in body["files"][0]
    assert len(body["revision"]) == 64
    etag = first.headers["etag"]
    second = asyncio.run(conversations.list_conv_files(
        "c1", _request([(b"if-none-match", etag.encode("ascii"))]),
    ))
    assert second.status_code == 304
    assert second.headers["etag"] == etag


def test_docstudio_owner_check_precedes_file_or_model_work(monkeypatch):
    from hashmm.api.routes import doc_studio

    class BodyRequest:
        async def json(self):
            return {"action": "deep_read", "conv_id": "foreign", "text": "x"}

    monkeypatch.setattr(doc_studio, "require_auth", lambda _request: {"uid": "u1", "sub": "u1"})
    monkeypatch.setattr(
        doc_studio, "require_conv_access",
        lambda *_: (_ for _ in ()).throw(HTTPException(404, "对话不存在")),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(doc_studio.docstudio_run(BodyRequest()))
    assert exc.value.status_code == 404


def test_cloud_merge_never_reassigns_an_existing_conversation():
    from hashmm.api import database as db

    conv_id = "v362-owner-boundary"
    db.create_conversation(conv_id, "owner-a", "A")
    try:
        changed = db.merge_cloud_conversation(
            {"id": conv_id, "title": "stolen", "updated_at": 9999999999}, "owner-b",
        )
        assert changed is False
        row = db.get_conversation(conv_id)
        assert row["user_id"] == "owner-a"
        assert row["title"] == "A"
    finally:
        db.delete_conversation(conv_id)


def test_client_contracts_use_real_doc_upload_and_incremental_local_cache():
    studio = (ROOT / "frontend-next/components/desktop/DocStudioView.tsx").read_text(encoding="utf-8")
    store = (ROOT / "frontend-next/lib/store.ts").read_text(encoding="utf-8")
    app_feed = Path(r"D:\sheji\agent\app\app\src\main\java\com\hashmm\app\data\remote\FeedRepository.kt").read_text(encoding="utf-8")
    assert "/api/files/upload" not in studio
    assert "/api/conversations/${encodeURIComponent(cid)}/upload" in studio
    assert "回到对话继续" in studio
    assert "hmm_conv_sync_" in store and "sync_cursor" in store
    assert "getFeedSnapshot" in app_feed and "feedMutex" in app_feed and "If-None-Match" in app_feed


def test_release_notes_and_browser_window_match_current_product_shell():
    notes = (ROOT / "frontend-next/components/HelpModals.tsx").read_text(encoding="utf-8")
    settings = (ROOT / "frontend-next/components/SettingsModal.tsx").read_text(encoding="utf-8")
    desktop = (ROOT / "desktop/main.js").read_text(encoding="utf-8")
    cockpit = (ROOT / "desktop/browser-cockpit.html").read_text(encoding="utf-8")
    assert 'version: "V518"' in notes and 'version: "v29.0"' not in notes
    assert 'mb-7 pb-4 flex items-end' in settings
    assert 'title: "HashMM · 浏览器助手", backgroundColor: "#f7f7f8"' in desktop
    assert "--bg:#f6f7f8" in cockpit


def test_feed_etag_ignores_clock_but_changes_with_visible_data():
    from hashmm.api.routes.feed import _feed_etag

    first = {"generated_at": 1, "recent_files": [{"filename": "a.pdf"}]}
    later = {"generated_at": 999, "recent_files": [{"filename": "a.pdf"}]}
    changed = {"generated_at": 999, "recent_files": [{"filename": "b.pdf"}]}
    assert _feed_etag(first) == _feed_etag(later)
    assert _feed_etag(first) != _feed_etag(changed)


def test_docstudio_run_is_visible_in_the_shared_run_timeline():
    source = (ROOT / "hashmm/api/routes/doc_studio.py").read_text(encoding="utf-8")
    assert '"run_id": f"docstudio-' in source
    assert '"task_type": "document_workshop"' in source
