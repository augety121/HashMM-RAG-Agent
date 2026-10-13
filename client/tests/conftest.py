"""pytest 共享基建 —— 路径、临时环境、条件跳过。

设计原则：
- 测试**不碰真实 data**：需要 DB 的测试用临时 sqlite（tmp_db fixture）。
- 缺 GPU/模型/data 的测试**跳过而非失败**（needs_gpu/needs_data 标记 + 自动检测）。
- 每个测试独立、可反复跑、无副作用。
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# 让测试能 import hashmm（项目根加入 path）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── 根治"测试间共享真机 DB"污染 ──
# 在任何测试模块 import database 之前（conftest 在 pytest 最早期加载），把 DB 指向一个
# 全新临时 sqlite。这样所有脚本式测试（含模块顶层建用户的）都在隔离的干净 DB 上跑，
# 绝不碰真实 data/hashmm.sqlite，也不会污染真机数据。
if "HASHMM_DB_PATH" not in os.environ:
    _TEST_DB_DIR = tempfile.mkdtemp(prefix="hashmm_pytest_")
    os.environ["HASHMM_DB_PATH"] = str(Path(_TEST_DB_DIR) / "pytest.sqlite")

# ── V308：根治"测试间共享工作区 / 幂等 DB"污染 ──
# CONV_FILES_ROOT（会话工作区）与 idempotency.db（幂等缓存）都锚定 HASHMM_DATA_DIR，
# 默认指向项目内固定的 data/。并跑时多个测试写同一 data/ → 工作区文件互相覆盖、
# 幂等记录跨测试残留（典型表现：test_v50/v52/v57 单独跑全过、并跑却失败——即"假绿"
# 的另一面：结果不可复现）。把 HASHMM_DATA_DIR 也指向独立临时目录后，每次 pytest 进程
# 的工作区与幂等库都是全新的，测试顺序无关、可反复复现。
if "HASHMM_DATA_DIR" not in os.environ:
    os.environ["HASHMM_DATA_DIR"] = tempfile.mkdtemp(prefix="hashmm_pytest_data_")


# ── 排除"诊断脚本"被 pytest 收集 ──
# tests/ 里混有一些【手动诊断脚本】（如 test_llm_api.py：测 DeepSeek API 连通性，
# 顶层无 API key 就 sys.exit(1)）。它们设计成 `python tests/xxx.py` 手动跑，不是 pytest 测试。
# pytest 收集时执行顶层 sys.exit 会让【整个 pytest 崩溃】（INTERNALERROR）。
# 这里显式忽略收集这类脚本——不改你的项目/脚本，只是不让 pytest 误收集它们。
collect_ignore = [
    "test_llm_api.py",          # DeepSeek API 连通性诊断脚本（需 LLM_API_KEY，顶层 sys.exit）
]
# 额外保险：自动识别"顶层在无关键环境变量时 sys.exit"的诊断脚本，避免新增脚本再崩 pytest。
def _is_diagnostic_script(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:800]
    except Exception:
        return False
    # 顶层 sys.exit + 提示设置 API key 的，判定为诊断脚本
    return ("sys.exit(1)" in head and ("API_KEY" in head or "诊断脚本" in head))


import pytest as _pytest

_CANONICAL_DB_PATH = None
_CANONICAL_MODELS_MIRROR = None


@_pytest.fixture(scope="session", autouse=True)
def _init_test_db_schema():
    """V308 根治顺序敏感：多个测试（test_v57 roundtrip / http_integration 等）直接调
    db.create_conversation / create_user，却【假设表已存在】——只有随机顺序里恰好有
    别的测试先触发过建表它们才通过（“靠邻居建表”）。这是并跑/乱序不稳定的最后来源。
    此 fixture 在 pytest 会话开始时对隔离的测试 DB 执行一次 init_db()，
    所有测试从此天然有 schema，与执行顺序彻底解耦。init 失败不吞：直接暴露。"""
    from hashmm.api import database as _db
    _db.init_db()
    global _CANONICAL_DB_PATH, _CANONICAL_MODELS_MIRROR
    _CANONICAL_DB_PATH = _db.DB_PATH
    _CANONICAL_MODELS_MIRROR = _db._MODELS_MIRROR
    yield


@_pytest.fixture(autouse=True)
def _restore_database_module_globals(_init_test_db_schema):
    """Stop a temporary DB module assignment leaking into the next test."""
    yield
    from hashmm.api import database as _db
    _db._close_pool()
    if _CANONICAL_DB_PATH is not None:
        _db.DB_PATH = _CANONICAL_DB_PATH
    if _CANONICAL_MODELS_MIRROR is not None:
        _db._MODELS_MIRROR = _CANONICAL_MODELS_MIRROR


def pytest_ignore_collect(collection_path, config):
    """对识别为诊断脚本的文件，跳过收集（保护 pytest 不被顶层 sys.exit 搞崩）。"""
    try:
        p = Path(str(collection_path))
        if p.suffix == ".py" and p.name.startswith("test_") and _is_diagnostic_script(p):
            return True
    except Exception:
        pass
    return None


def _has_data() -> bool:
    d = Path(os.environ.get("DATA_DIR", _ROOT / "data"))
    return (d / "kg" / "graph.json").exists()


def _has_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def pytest_configure(config):
    """代码注册 markers + 根治 create_user 污染。

    在收集任何测试模块【之前】执行（测试模块顶层代码在收集时 import 执行，晚于此）。
    所以这里把 db.create_user 永久包成幂等：很多脚本式测试在模块顶层
    `create_user("alice"/"rootadmin"...)`，共享 DB 时第二个文件因用户已存在拿到 None →
    `user["id"]` 崩。包成幂等后已存在则返回现有用户行，永不返回 None（除非真正失败）。
    仅测试期生效，对生产代码零影响。"""
    # 1) markers
    for name, desc in [
        ("unit", "单元测试（纯逻辑，无需 GPU/模型/data）"),
        ("contract", "契约测试（接口返回结构稳定性）"),
        ("regression", "回归测试（主检索链等关键路径，防改坏）"),
        ("needs_data", "需要真实 data/ 语料（无 data 时跳过）"),
        ("needs_gpu", "需要 GPU/模型（无则跳过）"),
        ("needs_render", "需要渲染工具（playwright/wkhtmltoimage 等）"),
    ]:
        config.addinivalue_line("markers", f"{name}: {desc}")

    # 2) 幂等 create_user（覆盖脚本式测试的模块顶层调用）
    try:
        from hashmm.api import database as db
        if not getattr(db, "_create_user_idempotent_patched", False):
            _orig = db.create_user

            def _idempotent(username, password, display_name="", role="user"):
                u = _orig(username, password, display_name, role)
                if u:
                    return u
                try:
                    existing = db.verify_user(username, password)
                    if existing:
                        return existing
                    with db._conn() as c:
                        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                    return dict(row) if row else None
                except Exception:
                    return None

            db.create_user = _idempotent
            db._create_user_idempotent_patched = True
    except Exception:
        pass


def pytest_collection_modifyitems(config, items):
    """根据环境自动跳过缺依赖的测试，而不是让它们失败。"""
    skip_data = pytest.mark.skip(reason="无真实 data/（needs_data）")
    skip_gpu = pytest.mark.skip(reason="无 GPU/模型（needs_gpu）")
    has_data, has_gpu = _has_data(), _has_gpu()
    for item in items:
        if "needs_data" in item.keywords and not has_data:
            item.add_marker(skip_data)
        if "needs_gpu" in item.keywords and not has_gpu:
            item.add_marker(skip_gpu)


@pytest.fixture
def tmp_db(monkeypatch):
    """临时 sqlite，隔离真实 data。测试结束自动清理。"""
    d = tempfile.mkdtemp(prefix="hashmm_test_")
    db_path = str(Path(d) / "test.sqlite")
    monkeypatch.setenv("HASHMM_DB_PATH", db_path)
    monkeypatch.setenv("DATA_DIR", d)
    yield db_path
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def clean_env(monkeypatch):
    """清掉所有 HASHMM_ 开关，保证测试从'默认关'状态开始（验证零变化）。"""
    for k in list(os.environ.keys()):
        if k.startswith("HASHMM_"):
            monkeypatch.delenv(k, raising=False)
    yield
