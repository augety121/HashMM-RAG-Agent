import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(*, query: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "query_string": query,
        "headers": headers or [],
    })


def test_preview_uses_etag_and_returns_304_without_reparsing(monkeypatch, tmp_path):
    from hashmm.api.routes import conversations as routes

    monkeypatch.setattr(routes, "require_conv_access", lambda *_args: {"id": "conv-1", "user_id": "u1"})
    monkeypatch.setattr(routes.db, "conv_files_dir", lambda _conv_id: tmp_path)
    source = tmp_path / "notes.docx"
    source.write_bytes(b"not-a-real-office-file")

    parsed: list[str] = []
    monkeypatch.setattr(routes, "_office_rich", lambda path, ext: parsed.append(ext) or {"kind": "html", "html": "<p>cached</p>"})

    first = asyncio.run(routes.preview_conv_file("conv-1", "notes.docx", _request()))
    etag = first.headers["etag"]
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    assert json.loads(first.body)["rich"]["html"] == "<p>cached</p>"
    assert parsed == [".docx"]

    second = asyncio.run(routes.preview_conv_file(
        "conv-1", "notes.docx", _request(headers=[(b"if-none-match", etag.encode())]),
    ))
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert parsed == [".docx"], "304 必须在 Office 解析前返回"


def test_file_download_supports_inline_but_keeps_attachment_default(monkeypatch, tmp_path):
    from hashmm.api.routes import conversations as routes

    monkeypatch.setattr(routes, "require_conv_access", lambda *_args: {"id": "conv-1", "user_id": "u1"})
    monkeypatch.setattr(routes.db, "conv_files_dir", lambda _conv_id: tmp_path)
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")

    attachment = asyncio.run(routes._serve_conv_file("conv-1", "report.pdf", _request()))
    inline = asyncio.run(routes._serve_conv_file("conv-1", "report.pdf", _request(query=b"inline=1")))
    assert attachment.headers["content-disposition"].startswith("attachment;")
    assert inline.headers["content-disposition"].startswith("inline;")
    assert inline.media_type == "application/pdf"


def test_file_access_checks_owner_before_workspace_and_rejects_traversal(monkeypatch, tmp_path):
    from hashmm.api.routes import conversations as routes

    touched: list[str] = []
    monkeypatch.setattr(
        routes, "require_conv_access",
        lambda *_args: (_ for _ in ()).throw(HTTPException(404, "对话不存在")),
    )
    monkeypatch.setattr(routes.db, "conv_files_dir", lambda conv_id: touched.append(conv_id) or tmp_path)
    with pytest.raises(HTTPException) as denied:
        asyncio.run(routes._serve_conv_file("foreign", "report.pdf", _request()))
    assert denied.value.status_code == 404
    assert touched == [], "未授权请求不得解析或枚举会话工作区"

    monkeypatch.setattr(routes, "require_conv_access", lambda *_args: {"id": "conv-1", "user_id": "u1"})
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as traversal:
        asyncio.run(routes._serve_conv_file("conv-1", "../outside-secret.txt", _request()))
    assert traversal.value.status_code == 404

