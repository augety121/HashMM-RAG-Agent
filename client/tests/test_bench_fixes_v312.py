"""V312 四基准修复回归测试（依据 2026-07-14 用户现场报告的真实错误逐条修复）。

锁四件事：
1. τ²：venv 缺模块自愈（现场真凶 orjson）——识别、别名映射、独立 attempt 目录、开关。
2. SWE：pip 真因提取不再被 `subprocess-exited-with-error` 包装行骗走；评过实例的失败
   带首个 pytest 输出尾部；env_error 回填不占名额；`passed` NameError 修复不回潮。
3. Terminal：/app 映射六状态全覆盖（现场真凶：悬空软链与真实目录两种状态 V309 垫片
   没处理 → 判分硬查 /app 必挂）；危险过滤收窄；apt 能力化扩池。
4. 以上均离线可测（合成目录 / monkeypatch _sh），不依赖真实数据集与网络。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

from hashmm.evaluation.benchmarks import terminal_local as TL
from hashmm.evaluation.benchmarks.swebench_local import (_extract_pip_cause,
                                                         run_tests_detail)
from hashmm.evaluation.benchmarks.tau2_full import _MOD_TO_PIP, missing_module

_BENCH = Path(__file__).resolve().parent.parent / "hashmm" / "evaluation" / "benchmarks"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HASHMM_TERMINAL_APP_PATH", "HASHMM_TERMINAL_APT", "HASHMM_BENCH_HOME",
              "HASHMM_TAU2_SELFHEAL", "HASHMM_SWE_POOL_MULT"):
        monkeypatch.delenv(k, raising=False)


# ================================================================ τ² 自愈
def test_tau2_missing_module_field_error():
    """用户现场原文：NotFoundError: No module named 'orjson' "}, "traj": ..."""
    raw = 'NotFoundError: No module named \'orjson\' "}, "traj": [], "trial": 0}'
    assert missing_module(raw) == "orjson"


def test_tau2_missing_module_submodule_and_alias():
    assert missing_module("", "ModuleNotFoundError: No module named 'yaml.parser'") == "yaml"
    assert _MOD_TO_PIP["yaml"] == "pyyaml"
    assert _MOD_TO_PIP["PIL"] == "pillow"


def test_tau2_missing_module_double_quotes_and_none():
    assert missing_module('No module named "fastapi"') == "fastapi"
    assert missing_module("401 Unauthorized / invalid api key") == ""


def test_tau2_selfheal_structure_locked():
    src = (_BENCH / "tau2_full.py").read_text(encoding="utf-8")
    assert 'attempt_dir = log_dir / f"attempt{attempt}"' in src   # 独立目录防混轮
    assert "HASHMM_TAU2_SELFHEAL" in src
    assert '"orjson", "openai"' in src                            # 白名单补齐
    assert "venv_install(py, gap)" in src


# ================================================================ SWE
def test_pip_cause_skips_wrapper_lines():
    """现场形态：包装行 error: subprocess-exited-with-error 在前，真因 use_2to3 在后。"""
    raw = ("[editable]   error: subprocess-exited-with-error\n"
           "  python setup.py egg_info did not run successfully.\n"
           "  exit code: 1\n"
           "  error in requests setup command: use_2to3 is invalid.\n"
           "  [end of output]\n"
           "  note: This error originates from a subprocess")
    got = _extract_pip_cause(raw)
    assert "use_2to3" in got
    assert "subprocess-exited-with-error" not in got


def test_pip_cause_gcc_and_fallback():
    g = _extract_pip_cause("error: subprocess-exited-with-error\n"
                           "x.c:1:10: fatal error: Python.h: No such file\n"
                           "error: command 'gcc' failed with exit status 1")
    assert "gcc" in g or "fatal error" in g
    f = _extract_pip_cause("blah\nERROR: something specific broke\nexit code: 1")
    assert "something specific" in f
    assert _extract_pip_cause("") == ""


def test_run_tests_detail_captures_first_fail(monkeypatch):
    from hashmm.evaluation.benchmarks import swebench_local as SW

    def fake_sh(cmd, cwd=None, env=None, timeout=0):
        t = cmd[-1]
        if "bad" in t:
            return 1, "E  ImportError: cannot import name 'x' (agent 补丁把包搞炸)", ""
        return 0, "1 passed", ""
    monkeypatch.setattr(SW, "_sh", fake_sh)
    ok, n, tail = SW.run_tests_detail("py", Path("."), ["tests/good1", "tests/bad", "tests/good2"])
    assert (ok, n) == (2, 3)
    assert "ImportError" in tail                     # 首个失败输出被带出
    ok2, n2 = SW.run_tests("py", Path("."), ["tests/good1"])   # 旧签名兼容
    assert (ok2, n2) == (1, 1)


def test_swe_backfill_and_nameerror_locked():
    src = (_BENCH / "swebench_local.py").read_text(encoding="utf-8")
    assert "HASHMM_SWE_POOL_MULT" in src
    assert "if evaluated >= limit:" in src            # 评满即停，env_error 不占名额
    assert "_fmt_bd(resolved, evaluated" in src       # NameError 修复：passed→resolved
    assert "_fmt_bd(passed, evaluated" not in src     # 旧写法不得回潮
    assert "首个失败输出尾部" in src


# ================================================================ Terminal /app 映射
def _mk_work(tmp: Path, name: str) -> Path:
    w = tmp / name
    w.mkdir(exist_ok=True)
    (w / "results.txt").write_text("data")
    return w


def test_map_app_absent_and_valid_link(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_TERMINAL_APP_PATH", str(tmp / "app"))
    app = tmp / "app"
    w = _mk_work(tmp, "w1")
    ok, mode, restore = TL.map_app(w)
    assert ok and (app / "results.txt").read_text() == "data"
    restore()
    assert not os.path.lexists(app)
    other = tmp / "other"; other.mkdir()
    TL._create_dir_mapping(other, app)
    ok2, mode2, r2 = TL.map_app(w)
    assert ok2 and "重建" in mode2
    r2()


def test_map_app_dangling_symlink(monkeypatch):
    """现场真凶之一：上次运行被硬杀留下悬空链 → V309 垫片永久失效。"""
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_TERMINAL_APP_PATH", str(tmp / "app"))
    app = tmp / "app"
    gone = tmp / "gone"; gone.mkdir()
    try:
        os.symlink(gone, app, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前 Windows 未授予创建悬空 symlink 的权限：{exc}")
    gone.rmdir()
    assert os.path.lexists(app) and not app.exists()   # 悬空态确认
    w = _mk_work(tmp, "w2")
    ok, mode, restore = TL.map_app(w)
    assert ok and (app / "results.txt").read_text() == "data"
    restore()


def test_map_app_empty_dir_replaced_and_restored(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_TERMINAL_APP_PATH", str(tmp / "app"))
    app = tmp / "app"; app.mkdir()
    w = _mk_work(tmp, "w3")
    ok, mode, restore = TL.map_app(w)
    assert ok and "空目录" in mode
    assert (app / "results.txt").read_text() == "data"
    restore()
    assert app.is_dir() and not app.is_symlink() and not any(app.iterdir())


def test_map_app_nonempty_dir_backup_and_restore(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_TERMINAL_APP_PATH", str(tmp / "app"))
    app = tmp / "app"; app.mkdir()
    (app / "user_data.bin").write_text("珍贵数据")
    w = _mk_work(tmp, "w4")
    ok, mode, restore = TL.map_app(w)
    assert ok and "备份" in mode
    assert (app / "results.txt").read_text() == "data"
    restore()
    assert (app / "user_data.bin").read_text() == "珍贵数据"   # 原数据完好归位


def test_map_app_file_node_fails_honestly(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_TERMINAL_APP_PATH", str(tmp / "app"))
    (tmp / "app").write_text("x")
    ok, mode, _ = TL.map_app(_mk_work(tmp, "w5"))
    assert not ok and "文件" in mode


# ================================================================ Terminal 过滤/apt
def test_danger_narrowed_to_destructive_only():
    assert not TL._DANGER.search("RUN apt-get install -y curl")
    assert not TL._DANGER.search("useradd bob && echo p | passwd bob")
    assert TL._DANGER.search("rm -rf /etc")
    assert TL._DANGER.search("dd if=/dev/zero of=/dev/sda")
    assert TL._NEEDS_SPECIAL_ENV.search("systemctl restart nginx")


def test_apt_pkgs_parse_continuation_flags_pins_vars():
    pkgs = TL._apt_pkgs(
        "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
        "    build-essential libpq-dev=1.2.3 \\\n"
        "    curl $EXTRA && rm -rf /var/lib/apt/lists/*\n"
        "RUN apt install -qq jq\n")
    assert pkgs == ["build-essential", "libpq-dev", "curl", "jq"]


def _mk_task(root: Path, name: str, dockerfile: str):
    d = root / name
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test_outputs.py").write_text(
        "def test_a():\n    assert open('/app/results.txt')\n")
    (d / "Dockerfile").write_text(dockerfile)
    (d / "task.yaml").write_text("instruction: do the thing\n")


def test_select_tasks_apt_capability_two_states(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_BENCH_HOME", str(tmp))
    root = tmp / "terminal-bench" / "original-tasks"
    _mk_task(root, "a-pip-only", "FROM python:3.11\nRUN pip install requests\n")
    _mk_task(root, "b-needs-apt", "FROM ubuntu:22.04\nRUN apt-get install -y jq curl\n")
    _mk_task(root, "c-needs-cuda", "FROM nvidia/cuda:12\nRUN pip install torch\n")
    _mk_task(root, "d-danger", "FROM python:3.11\nRUN rm -rf /etc\n")

    monkeypatch.setenv("HASHMM_TERMINAL_APT", "0")
    tasks, st = TL.select_tasks(10)
    assert st["total"] == 4 and st["apt_enabled"] is False
    assert [t["id"] for t in tasks] == ["a-pip-only"]

    monkeypatch.setenv("HASHMM_TERMINAL_APT", "1")
    tasks2, st2 = TL.select_tasks(10)
    assert [t["id"] for t in tasks2] == ["a-pip-only", "b-needs-apt"]
    assert tasks2[1]["apt_pkgs"] == ["jq", "curl"]
    assert st2["runnable_pip_only"] == 1 and st2["runnable_with_apt"] == 1


def test_tests_reference_app_detection(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("HASHMM_BENCH_HOME", str(tmp))
    root = tmp / "terminal-bench" / "original-tasks"
    _mk_task(root, "uses-app", "FROM python:3.11\n")
    assert TL.tests_reference_app(root / "uses-app") is True
    d = root / "no-app"
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    assert TL.tests_reference_app(d) is False


def test_ensure_apt_empty_idempotent():
    ok, note = TL._ensure_apt([])
    assert ok and "就绪" in note


def test_terminal_run_env_skip_structure_locked():
    src = (_BENCH / "terminal_local.py").read_text(encoding="utf-8")
    assert "env_skips" in src and "环境剔除" in src
    assert "tests_reference_app(t[\"dir\"])" in src   # 映射失败且判分依赖 /app → 剔除
    assert "app_restore()" in src and "finally:" in src
