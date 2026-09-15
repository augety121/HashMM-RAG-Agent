"""基准目录的统一解析 —— V306。

★ 修路径 bug：此前默认用 `~/hashmm-benchmarks`（=`/root/hashmm-benchmarks`），
但 AutoDL 容器里 **只有 `/root/autodl-tmp` 是持久化数据盘**，`/root` 下的东西**重启就丢**、
而且会把几个 G 的基准数据塞进系统盘。现在的默认按这个优先级选：

  1) 环境变量 HASHMM_BENCH_HOME（用户显式指定，最高优先）
  2) /root/autodl-tmp/hashmm-benchmarks   ← AutoDL 持久盘（存在 /root/autodl-tmp 就用它）
  3) <代码仓库同级>/hashmm-benchmarks      ← 退一步：跟着代码走
  4) ~/hashmm-benchmarks                    ← 最后兜底

这样在 AutoDL 上**默认就落在 /root/autodl-tmp 里**，不再污染 /root、重启也不丢。
"""
from __future__ import annotations

import os
from pathlib import Path


def bench_home() -> Path:
    # 1) 显式环境变量优先
    env = os.environ.get("HASHMM_BENCH_HOME")
    if env:
        return Path(env)
    # 2) AutoDL 持久盘
    autodl = Path("/root/autodl-tmp")
    if autodl.is_dir():
        return autodl / "hashmm-benchmarks"
    # 3) 跟随代码仓库（本文件在 hashmm/evaluation/benchmarks/ 下，向上 4 级到仓库根的同级）
    try:
        repo_root = Path(__file__).resolve().parents[3]   # .../<repo>/hashmm/evaluation/benchmarks/_paths.py
        if repo_root.is_dir():
            return repo_root.parent / "hashmm-benchmarks"
    except Exception:  # noqa: BLE001
        pass
    # 4) 兜底
    return Path.home() / "hashmm-benchmarks"
