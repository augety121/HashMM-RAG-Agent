"""外部基准(SWE-bench / Terminal-bench)的探测 + 运行 + 优雅降级。

沙箱/未安装环境下这些基准跑不了(需 Docker + 仓库)。这里统一：
  · detect_*()：探测本机是否具备运行条件(命令/仓库/镜像在不在)；
  · run_*(mode)：full 模式跑真集(装了才行)；未装则回退 smoke(证明适配管线通)或 SKIP。
真集接入点用清晰的 TODO 标出——它依赖 install.sh 拉起的目录结构，需在 AutoDL 真机对接。
"""
from __future__ import annotations

import os
import shutil
import subprocess


def _has_docker() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def _bench_home() -> str:
    from ._paths import bench_home
    return str(bench_home())


def detect_swebench() -> dict:
    home = _bench_home()
    repo = os.path.join(home, "SWE-bench")
    have = os.path.isdir(repo) and _has_docker()
    return {"installed": have, "docker": _has_docker(), "repo": os.path.isdir(repo),
            "hint": "" if have else "未装：运行 benchmarks/install.sh 安装 SWE-bench 并确保 Docker 可用"}


def detect_terminal_bench() -> dict:
    home = _bench_home()
    repo = os.path.join(home, "terminal-bench")
    have = os.path.isdir(repo) and _has_docker()
    return {"installed": have, "docker": _has_docker(), "repo": os.path.isdir(repo),
            "hint": "" if have else "未装：运行 benchmarks/install.sh 安装 Terminal-bench 并确保 Docker 可用"}


def run_swebench(adapter, mode: str = "smoke", limit: int = 3) -> dict:
    """full: 跑真实 SWE-bench 子集(需安装+Docker，见 swebench_full)；smoke: 微任务证明管线通。"""
    det = detect_swebench()
    if mode == "full":
        try:
            if _has_docker():
                from . import swebench_full          # 有 Docker → 官方 harness（官方口径）
                return swebench_full.run(adapter, limit=limit)
            from . import swebench_local             # 没 Docker → 本机 venv 模式（非官方口径）
            return swebench_local.run(adapter, limit=limit)
        except Exception as e:  # noqa: BLE001
            return {"kind": "official", "score_pct": None, "skip": True,
                    "detail": f"SWE-bench 装载失败：{type(e).__name__}:{e}"}
    # smoke 模式
    from . import smoke
    r = smoke.swebench_smoke(adapter)
    if not det["installed"]:
        r["detail"] += f"（真集未安装：{det['hint']}）"
    return r


def run_terminal_bench(adapter, mode: str = "smoke", limit: int = 3) -> dict:
    det = detect_terminal_bench()
    if mode == "full":
        try:
            if _has_docker():
                from . import terminal_full          # 有 Docker → 官方 harness
                return terminal_full.run(adapter, limit=limit)
            from . import terminal_local             # 没 Docker → 本机模式（官方任务+官方 pytest 判分）
            return terminal_local.run(adapter, limit=limit)
        except Exception as e:  # noqa: BLE001
            return {"kind": "official", "skip": True, "score_pct": None,
                    "detail": f"Terminal-bench 装载失败：{type(e).__name__}:{e}"}
    from . import smoke
    r = smoke.terminal_smoke(adapter)
    if not det["installed"]:
        r["detail"] += f"（真集未安装：{det['hint']}）"
    return r
