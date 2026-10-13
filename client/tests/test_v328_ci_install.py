"""V328 回归测试：修「CI 数据没下载下来但一路绿 → 全跳过」的四层吞错。

用户真实现象：Actions 下载步骤 41 秒"完成"，runner 全跳过。静态审计确认四层吞错叠加：
  ① swebench-venv 从不显式装 datasets（赌 harness 传递依赖）；
  ② harness 安装 `2>/dev/null || true` 静默；
  ③ 数据集 heredoc except 后照样退 0，外层再套 `|| true`；
  ④ workflow 的 `|| echo "有告警，继续"` 把最后的非零也吞掉。
本文件锁死修复：结构断言 + heredoc 负路径真实行为验证（无 datasets 解释器必须退 1）。
"""
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

_ROOT = Path(__file__).resolve().parent.parent
_INSTALL = _ROOT / "hashmm/evaluation/benchmarks/install.sh"


def _swebench_block() -> str:
    src = _INSTALL.read_text(encoding="utf-8")
    start = src.index("install_swebench()")
    end = src.index("install_terminal()")
    return src[start:end]


def _terminal_block() -> str:
    src = _INSTALL.read_text(encoding="utf-8")
    start = src.index("install_terminal()")
    end = src.index("for T in", start)
    return src[start:end]


# ─────────────── ① 结构断言：吞错层全部拆除 ───────────────
def test_swebench_installs_datasets_explicitly():
    blk = _swebench_block()
    assert "pip install -q datasets" in blk, \
        "datasets 必须显式装进 swebench-venv（不许赌 harness 传递依赖）"


def test_swebench_dataset_download_fails_loudly():
    blk = _swebench_block()
    assert "PYIN || true" not in blk, "数据集下载又被 || true 吞了"
    assert "sys.exit(1)" in blk, "heredoc 失败必须 sys.exit(1)"
    assert "return 1" in blk
    # 产物自检（非空文件）
    assert '-s "$BENCH_HOME/swebench_verified.jsonl"' in blk, "产物自检丢了——会回到'绿色但没数据'"


def test_swebench_harness_failure_visible_not_silent():
    blk = _swebench_block()
    assert 'SWE-bench" 2>/dev/null || true' not in blk, "harness 安装失败又被静默了"


def test_terminal_install_fails_loudly_with_selfcheck():
    blk = _terminal_block()
    assert '|| echo "!! 安装失败"' not in blk, "tb-venv 安装失败只 echo 不返错——又吞了"
    assert "return 1" in blk
    assert "pip show terminal" in blk, "tb-venv 产物自检丢了"


def test_workflow_no_swallow_and_has_install_logs():
    wf = (_ROOT / ".github/workflows/docker-benchmarks.yml").read_text(encoding="utf-8")
    assert '|| echo "install' not in wf, "workflow 又把安装失败吞成'有告警，继续'了"
    assert "install-logs" in wf and "::group::install" in wf
    assert "pipefail" in wf, "tee 管道需要 pipefail，否则失败码被 tee 吃掉——吞错换了个马甲"
    assert "BENCH_HOME 产物一览" in wf


def test_runner_prints_bench_home_diagnostics_on_skip():
    src = (_ROOT / "scripts/remote_bench_runner.py").read_text(encoding="utf-8")
    assert "BENCH_HOME(" in src, "runner 跳过时的产物清单诊断丢了"


def test_install_sh_bash_syntax():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("当前 Windows 运行时未安装 bash；CI 的 ubuntu job 会执行此门禁")
    r = subprocess.run([bash, "-n", str(_INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ─────────────── ② 负路径真实行为：无 datasets 时 heredoc 必须退 1 ───────────────
def test_download_heredoc_exits_nonzero_without_datasets():
    """把 install_swebench 的 heredoc 体抽出来，用【没有 datasets 的系统解释器】跑：
    旧版打一句'拉取失败'后退 0（被上层当成功）；新版必须退 1。"""
    blk = _swebench_block()
    body = blk.split("<<'PYIN'", 1)[1]
    body = body.split("\n", 1)[1]          # 切掉与 <<'PYIN' 同行的 `|| { …; return 1; }` shell 残留
    body = body.split("\nPYIN", 1)[0]
    with tempfile.TemporaryDirectory() as td:
        py = Path(td) / "dl.py"
        py.write_text(body, encoding="utf-8")
        r = subprocess.run([sys.executable, str(py)],
                           env={"HASHMM_BENCH_HOME": td, "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 1, \
            f"无 datasets 时应退 1（旧版吞错退 0），实际 rc={r.returncode}\nstdout={r.stdout}"
        assert "拉取失败" in (r.stdout + r.stderr)
        assert not (Path(td) / "swebench_verified.jsonl").exists()


def test_frontend_import_hint_points_to_install_logs():
    src = (_ROOT / "frontend-next/components/desktop/BenchmarkCards.tsx").read_text(encoding="utf-8")
    assert "install-logs" in src, "导入提示丢了 install-logs 排查指引"


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  ✓ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
