"""hashmm/agent/context_offload.py — 符号化短期记忆（V294，移植自 TencentDB Agent Memory）。

移植理念（README 原文可查）：长任务里最烧 token 的是啰嗦的中间日志（搜索结果、代码、
报错栈）。TencentDB 把「上下文卸载 + 符号化记忆」结合：
  ① 把完整工具日志卸载到外部文件（refs/*.md）；
  ② 上下文里只留一张轻量 Mermaid 任务图（带 node_id）；
  ③ Agent 在符号图上推理，要核实细节时 grep node_id 秒取原文——token 省、可追溯不丢。
实测 WideSearch token 降 61.38%、成功率相对 +51.52%。

适配到 HashMM（Python，零新依赖，磁盘落 data/context_offload/<session>/）：
  · offload(session, tool_call, output) → 写 refs/<node_id>.md（原文），
    追加一行到 offload.jsonl（{node_id, tool_call, gist, timestamp}），返回 node_id。
  · build_mermaid(session) → 由 offload.jsonl 生成 Mermaid graph（节点=一次工具调用，
    label=gist，节点 id=node_id）；这就是注入上下文的"符号地图"。
  · drilldown(session, node_id) → grep 回原文（全量），供核实。
  · node_id 形如 NNN-N\\d+（三位会话前缀 - N 递增），与 TencentDB 的 MMD_NODE_ID_RE 一致。

铁律：默认关（HASHMM_CONTEXT_OFFLOAD=1）；永不抛错；单条 gist ≤120 字、单 ref ≤20KB、
      每会话节点 ≤200（超出滚动淘汰最旧）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.context_offload")

MAX_NODES = 200
MAX_GIST = 120
MAX_REF_BYTES = 20 * 1024
NODE_ID_RE = re.compile(r"\b(\d{3}-N\d+)\b")


def enabled() -> bool:
    return os.environ.get("HASHMM_CONTEXT_OFFLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}


def _root(session: str) -> Path:
    base = Path(os.environ.get("HASHMM_CONTEXT_OFFLOAD_DIR", "data/context_offload"))
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "default"))[:64] or "default"
    d = base / safe
    (d / "refs").mkdir(parents=True, exist_ok=True)
    return d


def _prefix(session: str) -> str:
    """三位会话前缀（稳定散列），拼进 node_id 让不同会话的节点互不撞号。"""
    h = 0
    for ch in str(session or "default"):
        h = (h * 131 + ord(ch)) & 0xFFFFFF
    return f"{h % 1000:03d}"


def _offload_path(session: str) -> Path:
    return _root(session) / "offload.jsonl"


def _read_entries(session: str) -> list[dict]:
    out: list[dict] = []
    try:
        p = _offload_path(session)
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and d.get("node_id"):
                    out.append(d)
            except Exception:
                continue
    except Exception as e:
        log_suppressed(logger, e)
    return out


def _next_node_id(session: str, entries: list[dict]) -> str:
    pref = _prefix(session)
    n = 0
    for e in entries:
        m = re.match(rf"^{pref}-N(\d+)$", str(e.get("node_id") or ""))
        if m:
            n = max(n, int(m.group(1)))
    return f"{pref}-N{n + 1}"


def _gist_of(tool_call: str, output: str) -> str:
    """从工具调用与输出里抽一句人话摘要（规则版，不调 LLM）。"""
    out = str(output or "").strip().replace("\n", " ")
    if not out:
        return f"{tool_call}（无输出）"[:MAX_GIST]
    # 报错优先入摘要（最需要被看见）
    m = re.search(r"(Error|Exception|Traceback|失败|错误)[:：]?\s*(.{0,80})", out)
    if m:
        return f"{tool_call} → 错误：{m.group(2).strip()}"[:MAX_GIST]
    return f"{tool_call} → {out[:80]}"[:MAX_GIST]


def offload(session: str, tool_call: str, output: str, gist: str = "") -> str:
    """把一次工具调用的完整输出卸载到 refs/<node_id>.md，上下文里只留 gist。返回 node_id。
    功能关闭时返回空串（调用方据此决定是否仍把原文塞进上下文）。"""
    if not enabled():
        return ""
    try:
        entries = _read_entries(session)
        node_id = _next_node_id(session, entries)
        raw = str(output or "")
        if len(raw.encode("utf-8", "ignore")) > MAX_REF_BYTES:
            raw = raw.encode("utf-8", "ignore")[:MAX_REF_BYTES].decode("utf-8", "ignore") + "\n…（已截断）"
        ref = _root(session) / "refs" / f"{node_id}.md"
        ref.write_text(f"# {node_id}\n\n工具调用：`{tool_call}`\n\n----\n\n{raw}\n", encoding="utf-8")
        entry = {"node_id": node_id, "tool_call": str(tool_call)[:120],
                 "gist": (gist or _gist_of(tool_call, output))[:MAX_GIST],
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        # 滚动淘汰：超容量删最旧 ref + 截断 jsonl
        entries.append(entry)
        if len(entries) > MAX_NODES:
            drop = entries[:len(entries) - MAX_NODES]
            for d in drop:
                try:
                    (_root(session) / "refs" / f"{d['node_id']}.md").unlink()
                except Exception:
                    pass
            entries = entries[-MAX_NODES:]
        tmp = _offload_path(session).with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
        os.replace(tmp, _offload_path(session))
        return node_id
    except Exception as e:
        log_suppressed(logger, e)
        return ""


def build_mermaid(session: str, title: str = "任务符号地图") -> str:
    """由 offload.jsonl 生成 Mermaid graph（注入上下文的符号地图）。空则返回空串。"""
    if not enabled():
        return ""
    try:
        entries = _read_entries(session)
        if not entries:
            return ""
        lines = ["graph TD"]
        prev = None
        for e in entries[-60:]:   # 只画近端，防图过大
            nid = str(e.get("node_id"))
            label = str(e.get("gist") or e.get("tool_call") or nid)
            label = label.replace('"', "'").replace("[", "(").replace("]", ")")[:70]
            safe_id = nid.replace("-", "_")
            lines.append(f'    {safe_id}["{nid}: {label}"]')
            if prev:
                lines.append(f"    {prev} --> {safe_id}")
            prev = safe_id
        return "\n".join(lines)
    except Exception as e:
        log_suppressed(logger, e)
        return ""


def drilldown(session: str, node_id: str) -> str:
    """按 node_id grep 回完整原文（核实细节用）。找不到返回空串。"""
    try:
        nid = str(node_id or "").strip()
        if not NODE_ID_RE.fullmatch(nid):
            m = NODE_ID_RE.search(nid)
            if not m:
                return ""
            nid = m.group(1)
        ref = _root(session) / "refs" / f"{nid}.md"
        return ref.read_text(encoding="utf-8", errors="ignore") if ref.exists() else ""
    except Exception as e:
        log_suppressed(logger, e)
        return ""


def context_view(session: str, query: str = "") -> dict:
    """给上下文/前端的一站式视图：符号地图 + 节点清单 + token 估算（相对省了多少）。"""
    entries = _read_entries(session)
    mermaid = build_mermaid(session)
    # 粗略 token 估算：卸载省下的字符 ≈ 各 ref 原文长度 - gist 长度
    saved_chars = 0
    for e in entries:
        try:
            ref = _root(session) / "refs" / f"{e['node_id']}.md"
            if ref.exists():
                saved_chars += max(0, ref.stat().st_size - len(str(e.get("gist") or "")))
        except Exception:
            continue
    return {
        "enabled": enabled(),
        "nodes": len(entries),
        "mermaid": mermaid,
        "entries": [{"node_id": e.get("node_id"), "gist": e.get("gist"),
                     "tool_call": e.get("tool_call"), "timestamp": e.get("timestamp")}
                    for e in entries[-40:]],
        "approx_tokens_saved": saved_chars // 4,   # ~4 字符/token
    }


def clear(session: str) -> None:
    """清空某会话的卸载数据（会话结束/重置时）。"""
    try:
        import shutil
        d = _root(session)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception as e:
        log_suppressed(logger, e)
