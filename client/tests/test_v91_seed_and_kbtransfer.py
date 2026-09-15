"""V91 测试：预置内容种子 + 知识库迁移。"""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from hashmm.api.kb_transfer import (EXPORT_ITEMS, apply_import_zip,
                                    build_export_zip, inspect_zip)

ROOT = Path(__file__).resolve().parents[1]


# ── kb_transfer ──────────────────────────────────────────────────────

def _make_data(tmp: Path) -> Path:
    d = tmp / "data"
    (d / "vector_index").mkdir(parents=True)
    (d / "vector_index" / "v.faiss").write_bytes(b"FAKEFAISS")
    (d / "kg" / "sub").mkdir(parents=True)
    (d / "kg" / "sub" / "g.json").write_text('{"nodes":1}', encoding="utf-8")
    (d / "skills").mkdir()
    (d / "skills" / "s.json").write_text('{"name":"x"}', encoding="utf-8")
    (d / "conversations").mkdir()                       # 白名单外，不该被打包
    (d / "conversations" / "c.json").write_text("{}", encoding="utf-8")
    con = sqlite3.connect(d / "hashmm.sqlite")
    con.execute("CREATE TABLE t(x)"); con.execute("INSERT INTO t VALUES (42)")
    con.commit(); con.close()
    return d


def test_export_whitelist_and_roundtrip(tmp_path):
    d = _make_data(tmp_path)
    out = tmp_path / "kb.zip"
    info = build_export_zip(d, out)
    assert out.exists() and info["size"] > 0
    names = zipfile.ZipFile(out).namelist()
    assert "hashmm.sqlite" in names and "vector_index/v.faiss" in names
    assert "kg/sub/g.json" in names and "skills/s.json" in names
    assert not any(n.startswith("conversations") for n in names), "白名单外不得入包"

    chk = inspect_zip(out)
    assert chk["ok"] and "vector_index" in chk["items"]

    # 导入到另一套 data，内容一致 + 旧数据备份
    d2 = tmp_path / "data2"
    (d2 / "vector_index").mkdir(parents=True)
    (d2 / "vector_index" / "old.faiss").write_bytes(b"OLD")
    r = apply_import_zip(d2, out)
    assert r["ok"] and r["needs_restart"]
    assert (d2 / "vector_index" / "v.faiss").read_bytes() == b"FAKEFAISS"
    assert not (d2 / "vector_index" / "old.faiss").exists(), "旧数据应被移入备份"
    assert r["backup"] and (Path(r["backup"]) / "vector_index" / "old.faiss").exists()
    con = sqlite3.connect(d2 / "hashmm.sqlite")
    assert con.execute("SELECT x FROM t").fetchone()[0] == 42
    con.close()


def test_import_rejects_zip_slip_and_foreign(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("_hashmm_export.json", "{}")
        zf.writestr("../evil.txt", "x")
    assert inspect_zip(bad)["ok"] is False

    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("_hashmm_export.json", "{}")
        zf.writestr("etc/passwd", "x")
    chk = inspect_zip(foreign)
    assert chk["ok"] is False and "白名单外" in chk["error"]

    notours = tmp_path / "notours.zip"
    with zipfile.ZipFile(notours, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert inspect_zip(notours)["ok"] is False

    r = apply_import_zip(tmp_path / "data3", bad)
    assert r["ok"] is False


def test_sqlite_snapshot_consistent_while_open(tmp_path):
    d = _make_data(tmp_path)
    # 模拟服务运行中：保持一个写连接打开
    live = sqlite3.connect(d / "hashmm.sqlite")
    live.execute("INSERT INTO t VALUES (7)"); live.commit()
    out = tmp_path / "live.zip"
    build_export_zip(d, out)
    with zipfile.ZipFile(out) as zf, open(tmp_path / "snap.sqlite", "wb") as f:
        f.write(zf.read("hashmm.sqlite"))
    live.close()
    con = sqlite3.connect(tmp_path / "snap.sqlite")
    rows = {r[0] for r in con.execute("SELECT x FROM t")}
    con.close()
    assert rows == {42, 7}


# ── seeds ────────────────────────────────────────────────────────────

def test_seed_only_when_empty_and_no_seed_env(tmp_path):
    import sys
    from hashmm.api import seeds

    fake_db_templates: list = []

    class FakeDB:
        @staticmethod
        def list_templates():
            return list(fake_db_templates)

        @staticmethod
        def create_template(name, cat, prompt, variables, author="builtin"):
            fake_db_templates.append(name); return "id"

    old_mod = sys.modules.get("hashmm.api.database")
    old_cwd = os.getcwd()
    old_env = os.environ.get("HASHMM_NO_SEED")
    sys.modules["hashmm.api.database"] = FakeDB  # 手动 patch，finally 还原
    os.chdir(tmp_path)
    try:
        out = seeds.seed_builtin_content()
        assert out["templates"] == len(seeds.BUILTIN_TEMPLATES)
        assert out["skills"] == len(seeds.BUILTIN_SKILLS)
        files = list((tmp_path / "data" / "skills").glob("*.json"))
        assert len(files) == len(seeds.BUILTIN_SKILLS)
        body = json.loads(files[0].read_text(encoding="utf-8"))
        assert set(body) >= {"name", "description", "triggers", "prompt", "tools"}, \
            "技能种子必须与管理后台手动创建同一格式"

        # 幂等：再跑全 0
        out2 = seeds.seed_builtin_content()
        assert out2 == {"templates": 0, "skills": 0}

        # NO_SEED 开关
        fake_db_templates.clear()
        for f in files:
            f.unlink()
        os.environ["HASHMM_NO_SEED"] = "1"
        out3 = seeds.seed_builtin_content()
        assert out3 == {"templates": 0, "skills": 0}
    finally:
        os.chdir(old_cwd)
        if old_mod is not None:
            sys.modules["hashmm.api.database"] = old_mod
        else:
            sys.modules.pop("hashmm.api.database", None)
        if old_env is None:
            os.environ.pop("HASHMM_NO_SEED", None)
        else:
            os.environ["HASHMM_NO_SEED"] = old_env


def test_templates_are_meaningful():
    from hashmm.api.seeds import BUILTIN_TEMPLATES
    assert len(BUILTIN_TEMPLATES) >= 8
    for name, cat, prompt, variables in BUILTIN_TEMPLATES:
        assert len(prompt) > 50, f"模板 {name} 过短"
        for v in variables:
            assert "{%s}" % v in prompt, f"模板 {name} 声明变量 {v} 未在正文使用"


# ── 接线（AST/文本级，无需 fastapi）────────────────────────────────

def test_admin_endpoints_wired():
    src = (ROOT / "hashmm/api/routes/admin.py").read_text(encoding="utf-8")
    assert '@router.get("/kb/export")' in src
    assert '@router.post("/kb/import")' in src
    assert "require_admin(request)" in src.split('@router.get("/kb/export")')[1][:400]
    assert "apply_import_zip" in src and "build_export_zip" in src


def test_server_seed_wired():
    src = (ROOT / "hashmm/api/server.py").read_text(encoding="utf-8")
    assert "seed_builtin_content" in src
    idx = src.index("seed_builtin_content")
    assert "except Exception" in src[idx:idx + 400], "种子必须包裹在永不抛错的 try 中"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        test_export_whitelist_and_roundtrip(Path(t))
    print("kb_transfer/seed 自检 OK")
