"""V103.49 — 模型配置镜像 + DB 损坏自动恢复 测试。

根因：DB（sqlite）损坏重建后，models 表清空，用户配好的模型全丢，整个系统没有
LLM（生成全部输出"LLM 未配置"占位）。本测试验证：模型配置会镜像到独立 JSON，
DB 重建后能自动恢复（含加密 key），无需到管理后台重配。
"""
import os
import pytest

pytestmark = pytest.mark.unit


def _fresh_db(tmp_path, monkeypatch):
    """指向临时 db + 镜像路径，import 一个干净的 database 模块实例。"""
    monkeypatch.setenv("HASHMM_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("HASHMM_MODELS_MIRROR", str(tmp_path / "models_backup.json"))
    import importlib
    from hashmm.api import database as db
    importlib.reload(db)
    return db


@pytest.fixture(autouse=True)
def _restore_database_module():
    """V308 修跨文件污染：_fresh_db 的 importlib.reload(database) 是【进程级】副作用——
    reload 时 CONV_FILES_ROOT/DATA_ROOT/DB 路径全部按【当时的环境变量】重算，且
    monkeypatch 撤销环境变量【不会】撤销模块重算的结果。若此前有测试泄漏过
    HASHMM_DATA_DIR（如 test_benchmarks 旧写法），database 模块就永久指向了那个
    一次性临时目录，与其它早已 `from database import CONV_FILES_ROOT` 的模块形成双脑。
    此 fixture 在本文件每个测试结束后把 database 再 reload 一次（此时 monkeypatch
    已恢复环境变量），让模块状态回到与进程其余部分一致。"""
    yield
    import importlib
    from hashmm.api import database as db
    importlib.reload(db)


def test_model_mirror_written_on_create(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    db.init_db()
    m = db.create_model("我的模型", "deepseek", "https://api.deepseek.com/v1",
                        "sk-secret-key", "deepseek-chat", created_by="admin")
    db.set_default_model(m["id"])
    # 镜像文件应已写出
    assert (tmp_path / "models_backup.json").exists()
    d = db.get_default_model()
    assert d and d["api_key"] == "sk-secret-key"  # key 正确加解密


def test_model_restored_after_db_corruption(tmp_path, monkeypatch):
    """核心：DB 损坏重建后，模型配置（含 key）自动从镜像恢复。"""
    db = _fresh_db(tmp_path, monkeypatch)
    db.init_db()
    m = db.create_model("生产模型", "deepseek", "https://api.deepseek.com/v1",
                        "sk-prod-key-xyz", "deepseek-chat", created_by="admin")
    db.set_default_model(m["id"])

    # 模拟 DB 文件损坏
    (tmp_path / "t.sqlite").write_text("GARBAGE NOT A DATABASE", encoding="utf-8")

    # 重新 init（触发损坏重建 + 从镜像恢复）
    import importlib
    importlib.reload(db)
    db.init_db()

    d = db.get_default_model()
    assert d is not None, "DB 重建后模型应自动恢复，而不是 None"
    assert d["model_name"] == "deepseek-chat"
    assert d["api_key"] == "sk-prod-key-xyz"  # 加密 key 也恢复了


def test_corruption_recovery_reopens_pool_and_reapplies_full_schema(
    tmp_path, monkeypatch
):
    """恢复快照后不能继续复用旧 inode 的连接，所有运行表必须重新迁移。"""
    import shutil
    import sqlite3

    db = _fresh_db(tmp_path, monkeypatch)
    db.init_db()
    database_path = tmp_path / "t.sqlite"
    snapshot_path = tmp_path / "t.sqlite.snapshot"

    db._close_pool()
    shutil.copy2(database_path, snapshot_path)
    with sqlite3.connect(snapshot_path) as conn:
        conn.execute("DROP TABLE work_runs")
        conn.commit()

    database_path.write_text("GARBAGE NOT A DATABASE", encoding="utf-8")
    db.init_db()

    with db._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='work_runs'"
        ).fetchone()
    assert row is not None, "恢复后的活动数据库必须包含 work_runs 全量 schema"


def test_no_mirror_no_crash(tmp_path, monkeypatch):
    """没有镜像文件时，init_db 正常工作（不报错、models 表为空是合法的）。"""
    db = _fresh_db(tmp_path, monkeypatch)
    db.init_db()
    # 没配过模型、也没镜像 → default 为 None，但不崩
    assert db.get_default_model() is None or isinstance(db.get_default_model(), dict)
