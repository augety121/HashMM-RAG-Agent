"""Closed regressions for Chat attachment readiness and source isolation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _resource_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HASHMM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HASHMM_RESOURCE_CACHE_DIR", str(tmp_path / "cache"))


def _write_real_pdf(path: Path, text: str, *, password: str = "") -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font_ref = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 11 Tf 48 720 Td ({escaped}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def test_real_pdf_backends_extract_native_text_and_detect_encryption(monkeypatch, tmp_path):
    pytest.importorskip("pypdf")
    pytest.importorskip("pdfplumber")
    from hashmm.pipeline.resource_pipeline import parse_resource

    _resource_env(monkeypatch, tmp_path)
    native = tmp_path / "native-real.pdf"
    encrypted = tmp_path / "encrypted-real.pdf"
    text = "Real native PDF text remains page addressable for Chat evidence. " * 5
    _write_real_pdf(native, text)
    _write_real_pdf(encrypted, text, password="secret")

    native_record = parse_resource(native)
    encrypted_record = parse_resource(encrypted)

    assert native_record["parse_state"] == "ready"
    assert native_record["page_count"] == 1
    assert native_record["readable_pages"] == 1
    assert "Real native PDF text" in native_record["text"]
    assert encrypted_record["parse_state"] == "encrypted"
    assert encrypted_record["text"] == ""


def test_native_pdf_is_page_addressable_and_cached(monkeypatch, tmp_path):
    from hashmm.pipeline import resource_pipeline
    from hashmm.pipeline.content_block import ContentBlock, ParsedDocument

    _resource_env(monkeypatch, tmp_path)
    path = tmp_path / "paper.pdf"
    sentence = "Native PDF evidence for the selected paper. " * 6
    path.write_bytes(b"%PDF-1.7\nfixture")

    class Parser:
        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def parse(_path):
            return ParsedDocument(
                doc_id="paper",
                filename="paper.pdf",
                file_type="pdf",
                parser_used="fixture-native",
                blocks=[ContentBlock(type="text", content=sentence, page=1)],
            )

    monkeypatch.setattr(resource_pipeline, "DocumentParser", Parser)
    monkeypatch.setattr(resource_pipeline, "_pdf_preflight", lambda _path: ("ok", 1, []))

    first = resource_pipeline.parse_resource(path)
    second = resource_pipeline.parse_resource(path)

    assert first["parse_state"] == "ready"
    assert first["page_count"] == 1
    assert first["readable_pages"] == 1
    assert first["readable_ratio"] == 1.0
    assert sentence.strip()[:60] in first["text"]
    assert any(block["anchor"] == "p.1" for block in first["blocks"])
    assert second["sha256"] == first["sha256"]
    assert second["parser_version"] == "3"


def test_encrypted_and_corrupted_pdfs_never_become_binary_model_text(monkeypatch, tmp_path):
    from hashmm.pipeline import resource_pipeline

    _resource_env(monkeypatch, tmp_path)
    encrypted = tmp_path / "encrypted.pdf"
    broken = tmp_path / "broken.pdf"
    encrypted.write_bytes(b"%PDF-1.7\nencrypted fixture")
    broken.write_bytes(b"%PDF-1.7\nthis is not a complete PDF")
    monkeypatch.setattr(
        resource_pipeline,
        "_pdf_preflight",
        lambda path: (
            ("encrypted", 0, ["PDF 已加密"])
            if path.name == "encrypted.pdf"
            else ("corrupted", 0, ["PDF 结构损坏"])
        ),
    )

    encrypted_record = resource_pipeline.parse_resource(encrypted)
    broken_record = resource_pipeline.parse_resource(broken)

    assert encrypted_record["parse_state"] == "encrypted"
    assert encrypted_record["text"] == ""
    assert broken_record["parse_state"] == "corrupted"
    assert broken_record["text"] == ""


def test_selected_resources_are_confined_to_the_owned_directory(monkeypatch, tmp_path):
    from hashmm.pipeline.resource_pipeline import build_resource_context

    _resource_env(monkeypatch, tmp_path)
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "paper-a.txt").write_text("ONLY_SELECTED_PAPER", encoding="utf-8")
    (owned / "unrelated.txt").write_text("INTERVIEW_AND_ANNUAL_REPORT", encoding="utf-8")

    context, resources = build_resource_context(
        owned, ["paper-a.txt", "../unrelated.txt", "missing.txt"]
    )

    assert "ONLY_SELECTED_PAPER" in context
    assert "INTERVIEW_AND_ANNUAL_REPORT" not in context
    states = {item["filename"]: item["parse_state"] for item in resources}
    assert states["paper-a.txt"] == "ready"
    assert states["../unrelated.txt"] == "invalid_name"
    assert states["missing.txt"] == "missing"


def test_explicit_attachments_block_global_private_retrieval_and_memory():
    from hashmm.agent.loop import AgentLoop

    loop = AgentLoop(
        None,
        user_id="owner",
        conv_id="conv",
        attachment_scope=["paper-a.pdf", "paper-b.pdf"],
    )

    async def collect():
        return {
            name: await loop._execute_tool(name, {"query": "paper method"}, "owner")
            for name in ("kb_search", "deep_search", "memory_recall")
        }

    results = asyncio.run(collect())
    assert all(item["status"] == "blocked" for item in results.values())
    assert all(item["reason"] == "explicit_attachment_scope_only" for item in results.values())
    assert all(item["allowed_attachments"] == ["paper-a.pdf", "paper-b.pdf"] for item in results.values())

    mixed_scope = AgentLoop(
        None,
        user_id="owner",
        conv_id="conv",
        attachment_scope=["paper-a.pdf"],
        document_filter=["explicit-kb-document"],
    )
    memory = asyncio.run(
        mixed_scope._execute_tool("memory_recall", {"query": "old task"}, "owner")
    )
    assert memory["reason"] == "explicit_attachment_scope_only"


def test_context_builder_does_not_inject_other_files_or_cross_task_memory(monkeypatch, tmp_path):
    from hashmm.api import context as context_module

    root = tmp_path / "conversations"
    conv = root / "conv-scope"
    conv.mkdir(parents=True)
    (conv / "paper.txt").write_text("paper", encoding="utf-8")
    (conv / "other-task.txt").write_text("other", encoding="utf-8")
    monkeypatch.setattr(context_module, "CONV_FILES_ROOT", root)

    class DB:
        @staticmethod
        def get_user_memories(_user_id):
            return [{"key": "old task", "value": "AI daily briefing"}]

        @staticmethod
        def get_conversation(_conv_id):
            return {"id": _conv_id, "user_id": "owner", "project_id": ""}

    built = context_module.ContextBuilder("conv-scope", "owner").build(
        [],
        db=DB(),
        resource_scope=["paper.txt"],
        include_user_memory=False,
        include_recent_actions=False,
    )
    assert "paper.txt" in built
    assert "other-task.txt" not in built
    assert "AI daily briefing" not in built


def test_conversation_upload_returns_real_resource_state(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from hashmm.api import database as db
    from hashmm.api.routes import conversations

    _resource_env(monkeypatch, tmp_path)
    owned = tmp_path / "conversation-files"
    monkeypatch.setattr(conversations, "require_conv_access", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(db, "conv_files_dir", lambda _conv_id: owned)
    monkeypatch.setattr(db, "add_conversation_file", lambda *_args, **_kwargs: {})

    class Upload:
        filename = "paper.txt"
        content_type = "text/plain"

        async def read(self):
            return b"server-owned attachment text"

    result = asyncio.run(conversations.upload_file_to_conv("conv", object(), Upload()))
    assert result["ok"] is True
    assert result["resource"]["parse_state"] == "ready"
    assert result["resource"]["text_chars"] == len("server-owned attachment text")
    assert result["download_url"] == "/api/conversations/conv/download/paper.txt"


def test_document_promise_without_file_fails_completion_gate():
    from hashmm.agent.loop import AgentLoop

    class Message:
        content = "Word 文档已经完成。"
        tool_calls = []
        reasoning_content = ""

    class LLM:
        @staticmethod
        def call_with_tools(_messages, tools=None):
            return type("Response", (), {"message": Message()})()

    loop = AgentLoop(
        LLM(), user_id="owner", conv_id="conv-delivery", max_iterations=1,
    )

    async def collect():
        return [item async for item in loop.run("生成 Word 文档", history=[])]

    events = asyncio.run(collect())
    done = [payload for kind, payload in events if kind == "done"][-1]
    assert done["stop_reason"] == "delivery_incomplete"
    assert not done["files"]
