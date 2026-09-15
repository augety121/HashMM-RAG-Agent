"""hashmm/agent/agent_usage.py — 外部 coding agent token 用量监控（V59）。

适配自 fanbox（com.huashu.fanbox）的 server.js：把本机 Claude Code / Codex 的会话
日志解析成 token 用量仪表盘数据。HashMM 的桌面端内嵌终端正是用来跑这些 agent 的，
这个模块让后端能回答"我今天用 Claude Code 烧了多少 token"。

数据源（与 fanbox 一致）：
- Claude Code: ~/.claude/projects/**/*.jsonl —— 每条 assistant 消息带 usage 明细
- Codex:       ~/.codex/sessions/**/rollout-*.jsonl —— token_count 事件

工程要点（照搬 fanbox 的硬核细节，纯 Python 重写、零新依赖）：
- 增量解析：记 offset，只推进到最后一个完整换行（写到一半的行留给下一轮）；
- 文件被截断重写（size < offset）→ 缓存重置重来；
- 同一条消息分多行落盘、usage 重复 → 按 message.id 去重只记第一次；
- <synthetic> 模型跳过；单文件解析失败不挡整体；
- 默认关：未装/没用过对应 agent → 返回 None（不报错）。

只读、永不写、永不抛错——监控不能影响主系统。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
CLAUDE_PROJ = HOME / ".claude" / "projects"
CODEX_SESS = HOME / ".codex" / "sessions"

# file -> {"offset": int, "last_msg_id": str, "events": [ {t,in,out,cc,cr} ]}
_claude_cache: dict[str, dict] = {}


def _empty_bucket() -> dict:
    return {"total": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_create": 0, "msgs": 0}


def parse_claude_file(path: Path, size: int, mtime_ms: float) -> list[dict]:
    """增量解析单个 Claude Code 会话文件，返回累计 events（带缓存）。"""
    key = str(path)
    c = _claude_cache.get(key)
    if c is None:
        c = {"offset": 0, "last_msg_id": "", "events": []}
        _claude_cache[key] = c
    if size < c["offset"]:                       # 文件被截断重写：重来
        c["offset"], c["last_msg_id"], c["events"] = 0, "", []
    if size == c["offset"]:
        return c["events"]
    try:
        with open(path, "rb") as f:
            f.seek(c["offset"])
            chunk = f.read(size - c["offset"]).decode("utf-8", errors="replace")
    except Exception:
        return c["events"]
    last_nl = chunk.rfind("\n")
    if last_nl == -1:
        return c["events"]                       # 还没有完整行
    c["offset"] += len(chunk[:last_nl + 1].encode("utf-8"))
    for line in chunk[:last_nl].split("\n"):
        if '"usage"' not in line or '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        m = d.get("message") or {}
        u = m.get("usage")
        if not u or m.get("model") == "<synthetic>":
            continue
        mid = m.get("id")
        if mid and mid == c["last_msg_id"]:      # 同消息多行落盘，usage 重复
            continue
        if mid:
            c["last_msg_id"] = mid
        t = _parse_ts(d.get("timestamp")) or mtime_ms
        c["events"].append({
            "t": t,
            "in": u.get("input_tokens", 0) or 0,
            "out": u.get("output_tokens", 0) or 0,
            "cc": u.get("cache_creation_input_tokens", 0) or 0,
            "cr": u.get("cache_read_input_tokens", 0) or 0,
        })
    return c["events"]


def _parse_ts(s) -> float:
    if not s:
        return 0.0
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return 0.0


def claude_usage(now_ms: float | None = None) -> dict | None:
    """聚合 Claude Code 近 8 天用量为 last5h / today / week 三个窗口。未用过返回 None。"""
    if not CLAUDE_PROJ.exists():
        return None
    now = now_ms if now_ms is not None else time.time() * 1000
    cutoff = now - 8 * 86400000
    files = []
    try:
        for d in CLAUDE_PROJ.iterdir():
            if not d.is_dir():
                continue
            for n in d.glob("*.jsonl"):
                try:
                    st = n.stat()
                    if st.st_mtime * 1000 >= cutoff:
                        files.append((n, st.st_size, st.st_mtime * 1000))
                except Exception:
                    pass
    except Exception:
        return None
    live = {str(n) for n, _, _ in files}
    for k in list(_claude_cache.keys()):
        if k not in live:
            _claude_cache.pop(k, None)           # 过期文件出缓存
    all_events = []
    for n, size, mtime in files:
        try:
            all_events.extend(parse_claude_file(n, size, mtime))
        except Exception:
            pass                                 # 单文件坏不挡整体
    day_start = _start_of_day_ms(now)
    last5h, today, week = _empty_bucket(), _empty_bucket(), _empty_bucket()
    for e in all_events:
        tot = e["in"] + e["out"] + e["cc"] + e["cr"]
        for b, frm in ((last5h, now - 5 * 3600000),
                       (today, day_start),
                       (week, now - 7 * 86400000)):
            if e["t"] >= frm:
                b["total"] += tot
                b["input"] += e["in"]
                b["output"] += e["out"]
                b["cache_read"] += e["cr"]
                b["cache_create"] += e["cc"]
                b["msgs"] += 1
    return {"last5h": last5h, "today": today, "week": week,
            "files": len(files)}


def _start_of_day_ms(now_ms: float) -> float:
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp(now_ms / 1000)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000


def codex_usage() -> dict | None:
    """从最近改动的 Codex rollout 文件尾部抓最后一条 token_count 快照。未用过返回 None。"""
    if not CODEX_SESS.exists():
        return None
    files = []
    try:
        for root, _dirs, names in os.walk(CODEX_SESS):
            if root[len(str(CODEX_SESS)):].count(os.sep) > 3:
                continue
            for nm in names:
                if nm.endswith(".jsonl"):
                    fp = Path(root) / nm
                    try:
                        st = fp.stat()
                        files.append((fp, st.st_mtime))
                    except Exception:
                        pass
    except Exception:
        return None
    if not files:
        return None
    files.sort(key=lambda x: x[1], reverse=True)
    for fp, _mt in files[:10]:
        try:
            tail = _read_tail(fp, 65536)
            for line in reversed(tail.split("\n")):
                if '"token_count"' not in line and '"rate_limits"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                snap = _extract_codex_snapshot(d)
                if snap:
                    return snap
        except Exception:
            pass
    return None


def _extract_codex_snapshot(d: dict) -> dict | None:
    """从一条 codex 事件里取 token 计数 + 配额百分比（结构容错）。"""
    payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    tc = info.get("token_count") or info.get("total_token_usage") or {}
    rl = info.get("rate_limits") or {}
    if not tc and not rl:
        return None
    return {
        "tokens": {
            "input": tc.get("input_tokens", 0) or 0,
            "output": tc.get("output_tokens", 0) or 0,
            "total": tc.get("total_tokens", 0)
            or (tc.get("input_tokens", 0) or 0) + (tc.get("output_tokens", 0) or 0),
        },
        "rate_limits": rl,
    }


def _read_tail(path: Path, n: int) -> str:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - n))
        return f.read().decode("utf-8", errors="replace")


def all_agent_usage() -> dict:
    """汇总所有外部 agent 用量（供监控端点）。永不抛错。"""
    out = {}
    try:
        out["claude_code"] = claude_usage()
    except Exception:
        out["claude_code"] = None
    try:
        out["codex"] = codex_usage()
    except Exception:
        out["codex"] = None
    return out
