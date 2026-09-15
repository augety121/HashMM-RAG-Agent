"""User-owned Skill imports and Chat injection regressions."""
from __future__ import annotations

import socket
import io
import zipfile
from pathlib import Path

import pytest

from hashmm.agent import skill_packs


def _write_skill(root: Path) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\n"
        "name: 研究验收\n"
        "description: 对网页研究结果做证据验收\n"
        "triggers: [研究, 验收]\n"
        "---\n"
        "先列证据，再逐项检查结论是否被证据支持。\n",
        encoding="utf-8",
    )
    (source / "helper.py").write_text(
        "raise RuntimeError('imported skill support files must never execute')\n",
        encoding="utf-8",
    )
    return source


def test_personal_skills_are_owner_isolated_and_progressively_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(skill_packs, "_data_root", lambda: tmp_path / "data")
    skill_packs._user_managers.clear()
    alice = skill_packs.get_user_skill_pack_manager("alice@example.com")
    bob = skill_packs.get_user_skill_pack_manager("bob@example.com")
    installed = alice.install_from_dir(_write_skill(tmp_path), "upload:test.zip")

    assert installed.name == "研究验收"
    assert [item.name for item in alice.list_packs()] == ["研究验收"]
    assert bob.list_packs() == []
    assert "alice@example.com" not in str(alice.root)
    assert alice.root != bob.root

    unrelated = [{"role": "user", "content": "今天好"}]
    alice.inject("今天好", unrelated)
    assert "研究验收" in unrelated[-1]["content"]
    assert "先列证据" not in unrelated[-1]["content"]

    related = [{"role": "user", "content": "验收这份网页研究"}]
    alice.inject("验收这份网页研究", related)
    assert "研究验收" in related[-1]["content"]
    assert "先列证据" in related[-1]["content"]


def test_website_skill_import_rejects_private_and_non_https_destinations(
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(ValueError, match="HTTPS"):
        skill_packs._validate_public_https_url("http://example.com/SKILL.md")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="本机、内网"):
        skill_packs._validate_public_https_url("https://skills.example/SKILL.md")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )
    assert (
        skill_packs._validate_public_https_url(
            "https://skills.example/SKILL.md"
        )
        == "https://skills.example/SKILL.md"
    )


def test_skill_zip_rejects_declared_decompression_bombs(tmp_path: Path):
    manager = skill_packs.SkillPackManager(tmp_path / "packs")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo("large/SKILL.md")
        info.file_size = skill_packs.MAX_SKILL_FILE_BYTES + 1
        archive.writestr(info, b"x")
    # zipfile normalizes file_size to the actual body, so patch the central
    # directory view to deterministically exercise the admission boundary.
    original = zipfile.ZipFile.infolist

    def oversized(self):
        rows = original(self)
        rows[0].file_size = skill_packs.MAX_SKILL_FILE_BYTES + 1
        return rows

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(zipfile.ZipFile, "infolist", oversized)
        with pytest.raises(ValueError, match="单个文件"):
            manager.install_from_zip_bytes(payload.getvalue(), "upload:test.zip")


def test_personal_skill_routes_use_authenticated_owner_library():
    source = (
        Path(__file__).parents[1]
        / "hashmm"
        / "api"
        / "routes"
        / "skill_packs.py"
    ).read_text(encoding="utf-8")
    resolver = source.split("def _user_mgr", 1)[1].split("# ──", 1)[0]
    personal = source.split("# ── V571", 1)[1].split(
        '@router.get("/{pack_id}")', 1
    )[0]
    assert "require_auth(request)" in resolver
    assert "get_user_skill_pack_manager(owner)" in resolver
    assert '@router.get("/mine")' in personal
    assert '@router.post("/mine/upload")' in personal
    assert '@router.delete("/mine/{pack_id}")' in personal
