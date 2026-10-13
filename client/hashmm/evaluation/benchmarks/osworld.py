"""OSWorld —— 真实桌面 computer-use 基准（V314 接入位）。

2026 生产决策六大基准之一（GAIA / SWE-bench Verified / OSWorld / τ² / WebArena /
METR HCAST）。369 个真实 Ubuntu 桌面任务，官方判分需要一个【可被程序操控的桌面
环境】（官方提供 VMware/VirtualBox 镜像与 Docker provider）。

与 WebArena 同一逻辑：**评测程序可以跑在本机**，桌面环境托管在任一台能起
VM/Docker 的机器上；AutoDL 容器实例给不了嵌套虚拟化，所以这里是"接入位 + 能力
预检 + 确切托管指引"——绝不硬凑分数。本机的 computer-use 工具面（cu_action/
computer_use，desktop 端 cu-driver/grounding/actions）已就位，桌面环境 URL 配好
即可接真跑（下一版接 VNC/pyautogui provider）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ._paths import bench_home


def data_dir() -> Path:
    return bench_home() / "osworld"


def desktop_provider() -> str:
    """已配置的桌面环境地址（VNC/官方 provider），如 HASHMM_OSWORLD_VM=host:5900。"""
    return (os.environ.get("HASHMM_OSWORLD_VM") or "").strip()


def detect() -> dict:
    have_data = data_dir().is_dir() and any(data_dir().rglob("*.json"))
    vm = desktop_provider()
    ok = have_data and bool(vm)
    return {"installed": ok, "has_data": have_data, "vm": vm,
            "n_tasks": len(list(data_dir().rglob("*.json"))) if have_data else 0,
            "hint": "" if ok else "OSWorld 前置未满足（任务集/桌面环境），见运行时指引"}


SETUP_GUIDE = """OSWorld 需要一个【可被程序操控的 Ubuntu 桌面】——托管它的机器才需要 VM/Docker，
本机只跑评测程序（你的 computer-use 工具面已就位）。接入三步：

  1) 任一台支持虚拟化的机器（本地电脑/云主机）按官方 README 起环境：
       git clone https://github.com/xlang-ai/OSWorld && 按 provider=vmware|docker 启动
  2) 拿到桌面控制入口后在本机： export HASHMM_OSWORLD_VM=<host:port>
  3) 任务集： benchmarks/install.sh osworld（clone 官方仓库取 369 个任务 json）

⚠️ AutoDL 容器实例不支持嵌套虚拟化——桌面环境放别处，这不是缺陷是物理限制。
参照：人类 72.4% / 2025 顶尖 agent ~45% / 早期多模态 12.2%。"""


def run(adapter, *, limit: int = 5) -> dict:
    det = detect()
    if not det["installed"]:
        why = []
        if not det["has_data"]:
            why.append("任务集未装（benchmarks/install.sh osworld）")
        if not det["vm"]:
            why.append("桌面环境未配（export HASHMM_OSWORLD_VM=<host:port>）")
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "OSWorld 接入位已就绪，前置未满足：" + "；".join(why) + f"。\n\n{SETUP_GUIDE}"}
    # 前置齐备但 provider 驱动尚未接线（下一版）：如实说明，绝不硬跑假分。
    return {"kind": "official", "skip": True, "score_pct": None,
            "detail": (f"OSWorld 前置已满足（任务 {det['n_tasks']} 个，桌面 {det['vm']}），"
                       "provider 驱动将在下一版接线（VNC/pyautogui 通道）——当前不产生分数，避免非官方口径假分。")}
