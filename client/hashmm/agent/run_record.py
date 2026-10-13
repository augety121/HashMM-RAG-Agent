"""hashmm/agent/run_record.py — V56 Agent 运行遥测（默认关，HASHMM_AGENT_TRACE=1 开启）。

大厂 harness 的标配：每次 run 落一条结构化 JSONL（工具序列/状态/耗时/用量），
用于事后回放与故障定位（"昨晚那次为什么超时"不再靠猜）。
默认关闭、永不抛错、单条事件 ≤200 行防爆（铁律 3）。

查看：tail -1 logs/agent_runs/$(date +%Y%m%d).jsonl | python -m json.tool
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _enabled() -> bool:
    # 默认开启运行遥测 —— 让「运行轨迹」面板开箱即有数据（甲方反馈是空 demo）。
    # 显式设 HASHMM_AGENT_TRACE=0 可关闭。每次运行落一条 JSONL（工具序列/停止理由/耗时/用量）。
    return os.environ.get("HASHMM_AGENT_TRACE", "1") != "0"


def _trace_dir() -> Path:
    return Path(os.environ.get("HASHMM_TRACE_DIR", "logs/agent_runs"))


class RunRecord:
    def __init__(self, conv_id: str, query: str):
        self.t0 = time.time()
        self.conv_id = conv_id or ""
        self.query = str(query or "")[:120]
        self.rows: list[dict] = []

    def add(self, kind: str, **kw) -> None:
        if _enabled() and len(self.rows) < 200:
            try:
                self.rows.append({"t": round(time.time() - self.t0, 2), "k": kind, **kw})
            except Exception:
                pass

    def flush(self, status: str = "done", **extra):
        """落盘一条 JSONL；返回路径（未开启/失败返回 None，永不抛错）。"""
        if not _enabled():
            return None
        try:
            d = _trace_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / (time.strftime("%Y%m%d") + ".jsonl")
            rec = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "conv_id": self.conv_id,
                "query": self.query,
                "status": status,
                "elapsed_s": round(time.time() - self.t0, 2),
                "events": self.rows,
                **extra,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return path
        except Exception:
            return None
