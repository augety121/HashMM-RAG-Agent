"""V388 regressions for truthful capability and document-management surfaces."""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.regression


class _Upload:
    def __init__(self, filename: str, content: bytes, chunk_size: int = 7):
        self.filename = filename
        self._content = content
        self._offset = 0
        self._chunk_size = chunk_size

    async def read(self, _size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = min(len(self._content), self._offset + self._chunk_size)
        chunk = self._content[self._offset:end]
        self._offset = end
        return chunk


def test_document_upload_removes_path_semantics_and_returns_exact_receipt(tmp_path):
    from hashmm.api.routes.admin import _safe_doc_upload_name, _stage_document_upload

    assert _safe_doc_upload_name(r"..\..\quarterly report.docx") == "quarterly report.docx"
    with pytest.raises(HTTPException) as unsupported:
        _safe_doc_upload_name("payload.exe")
    assert unsupported.value.status_code == 415

    content = b"stable-office-version"
    target, size, digest = asyncio.run(
        _stage_document_upload(_Upload(r"..\..\quarterly report.docx", content), tmp_path),
    )
    assert target.parent == tmp_path
    assert target.name == "quarterly report.docx"
    assert target.read_bytes() == content
    assert size == len(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert not list(tmp_path.glob(".upload-*.tmp"))


def test_document_upload_is_bounded_and_cleans_partial_file(tmp_path, monkeypatch):
    from hashmm.api.routes.admin import _stage_document_upload

    monkeypatch.setenv("HASHMM_MAX_DOC_UPLOAD_BYTES", str(1024 * 1024))
    oversized = b"x" * (1024 * 1024 + 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_stage_document_upload(_Upload("large.pdf", oversized, 256 * 1024), tmp_path))
    assert exc.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_destructive_document_route_rejects_parent_object_id(monkeypatch):
    from hashmm.api.routes import admin as route

    monkeypatch.setattr(route, "require_admin", lambda _request: {"uid": "admin", "sub": "admin"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.delete_doc("..", object()))
    assert exc.value.status_code == 400


def test_document_list_does_not_publish_server_absolute_path(tmp_path, monkeypatch):
    from hashmm.api.routes import admin as route

    doc_dir = tmp_path / "data" / "docs" / "doc-safe"
    doc_dir.mkdir(parents=True)
    (doc_dir / "parsed.json").write_text(
        json.dumps({"doc_id": "doc-safe", "filename": "safe.pdf", "num_blocks": 1}),
        encoding="utf-8",
    )
    (doc_dir / "meta.json").write_text(
        json.dumps({"source_path": "/srv/private/staging/safe.pdf"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(route, "require_admin", lambda _request: {"uid": "admin"})
    payload = asyncio.run(route.list_docs(object()))
    item = payload["docs"][0]
    assert item["source_available"] is True
    assert "source_path" not in item
    assert "/srv/private" not in json.dumps(payload)
