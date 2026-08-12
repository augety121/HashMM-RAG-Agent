"""AgentBench —— 操作系统(OS)子集，V306。

数据：THUDM/AgentBench 官方 `data/os_interaction`（GitHub 直连，install.sh agentbench 拉取）。
任务形式：给 agent 一个 Linux 环境和一句话任务（"找出那个后台程序写日志的间隔是几秒"），
agent 用 shell 命令探索并作答；官方用 `match`（答案字符串匹配）或 `check` 脚本判分。

**关于安全（重要，我不会拿你的服务器冒险）**
官方用 Docker 隔离，因为任务的 init/start 脚本会在系统里创建文件、起后台进程。你没有 Docker，
所以本模块：
  1) **默认关闭**。必须显式设 `HASHMM_AGENTBENCH_ALLOW_LOCAL=1` 才会真跑；
  2) 只跑 **match 型**任务（答案是字符串，不需要 checking 脚本）；
  3) 每题在**独立临时目录**里跑，命令通过 agent 自己的沙箱 run_shell 执行（已 cwd 到会话工作区）；
  4) **静态安全过滤**：init/start 脚本里出现 `rm -rf /`、`apt`、`useradd`、`systemctl`、`mkfs`、
     `shutdown`、`>/dev/sd` 等危险模式的任务**直接跳过**，绝不执行。
分数标为 **"官方任务·本机执行(非官方Docker隔离)"** —— 环境不是官方镜像，仅作参考。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

# 危险模式：命中即跳过该任务（保护你的服务器）
_DANGER = re.compile(
    r"rm\s+-rf\s+/(?!tmp|root/\w)|:\(\)\{|mkfs|shutdown|reboot|halt\b|"
    r"\bapt(-get)?\s+(install|remove|purge)|\byum\s+install|\bdnf\s+install|"
    r"\buseradd|\buserdel|\bgroupadd|\bpasswd\b|\bchown\s+-R\s+/|\bchmod\s+-R\s+777\s+/|"
    r"systemctl|service\s+\w+\s+(start|stop)|>\s*/dev/sd|dd\s+if=.*of=/dev/|"
    r"\bcrontab\b|\biptables\b|\bmount\b|\bumount\b|/etc/(passwd|shadow|sudoers)",
    re.IGNORECASE,
)


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def enabled() -> bool:
    return os.environ.get("HASHMM_AGENTBENCH_ALLOW_LOCAL") == "1"


def _data_files() -> list[Path]:
    root = bench_home() / "AgentBench"
    if not root.is_dir():
        return []
    return sorted(root.rglob("os_interaction/data/**/*.json"))


def detect() -> dict:
    files = _data_files()
    if not files:
        return {"installed": False, "enabled": enabled(),
                "hint": "未装 AgentBench 数据：运行 benchmarks/install.sh agentbench"}
    if not enabled():
        return {"installed": True, "enabled": False,
                "hint": "AgentBench-OS 会在**你的服务器上真实执行 shell**（官方是用 Docker 隔离的）。"
                        "确认可接受后，给后端进程设 HASHMM_AGENTBENCH_ALLOW_LOCAL=1 再重启即可开启。"
                        "已内置危险命令过滤（rm -rf / / apt / useradd / systemctl 等一律跳过）。"}
    return {"installed": True, "enabled": True, "hint": ""}


def is_safe(task: dict) -> bool:
    """静态安全过滤：init/start/create 里有危险模式就不跑。"""
    blob = json.dumps(task, ensure_ascii=False)
    return not _DANGER.search(blob)


def load_tasks(limit: int = 15) -> list[dict]:
    """只取 match 型（答案字符串匹配）且通过安全过滤的任务。"""
    out: list[dict] = []
    for f in _data_files():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for t in (d if isinstance(d, list) else [d]):
            if not isinstance(t, dict):
                continue
            ev = t.get("evaluation") or {}
            if not isinstance(ev, dict) or "match" not in ev:
                continue          # 只跑 match 型（不需要 checking 脚本）
            if not t.get("description"):
                continue
            if not is_safe(t):
                continue          # 危险任务直接跳过
            out.append(t)
            if limit and len(out) >= limit:
                return out
    return out


def _run_setup(task: dict, workdir: Path) -> bool:
    """在独立临时目录里执行任务的 start 脚本（已通过安全过滤）。"""
    start = task.get("start")
    if not start:
        return True
    try:
        subprocess.Popen(["bash", "-c", str(start)], cwd=str(workdir),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


def score(pred: str, task: dict) -> bool:
    gold = str((task.get("evaluation") or {}).get("match", "")).strip()
    p = str(pred or "").strip()
    if not p or not gold:
        return False
    # 官方 match：答案里含目标串即可（agent 常带解释）
    return gold.lower() in p.lower() or p.lower() == gold.lower()


def run(adapter, *, limit: int = 10) -> dict:
    det = detect()
    if not det["installed"] or not det["enabled"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    limit = int(os.environ.get("HASHMM_AGENTBENCH_LIMIT", limit))
    tasks = load_tasks(limit)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "没有可安全执行的 match 型任务（危险任务已全部过滤）"}

    from hashmm.tools import agent_bench as AB
    passed = 0
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentbench_") as td:
        for i, t in enumerate(tasks):
            wd = Path(td) / f"t{i}"
            wd.mkdir(parents=True, exist_ok=True)
            _run_setup(t, wd)
            q = (f"你在一台 Linux 机器上。用 run_shell 执行命令来完成下面的任务，"
                 f"探索完成后**只回答最终答案**（一个数字或一个短词）。\n\n任务：{t['description']}")
            try:
                task = AB.Task(id=f"ab_{i}", category="agentbench", turns=[q], requires=set(),
                               scorers=[AB.answer_nonempty(min_len=1)], max_seconds=240)
                r = AB.run_task(task, adapter.llm_fn)
                ans = str(r.get("answer") or "")
            except Exception:  # noqa: BLE001
                ans = ""
            if score(ans, t):
                passed += 1
            elif len(fails) < 5:
                gold = (t.get("evaluation") or {}).get("match", "")
                fails.append(f"答「{ans[:20]}」应为「{gold}」")

    total = len(tasks)
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "detail": f"官方数据集（AgentBench OS，match 型 {total} 题，**本机执行·非官方Docker隔离**）",
        "breakdown": {"任务类型": "OS/shell 探索", "安全过滤": "危险命令任务已跳过",
                      "判分口径": "官方 match（答案字符串匹配）"},
        "fails": fails[:5],
    }
