"""hashmm/agent/conv_compact.py — V51 长对话上下文压缩（对标 Claude 的 compaction）。

问题：此前历史消息被硬切（DB 取 12 条、Agent 再切 [-6:]）——长对话里开场需求、
早期决定、生成过的文件对模型完全不可见，表现为"聊久了就失忆"。

方案：确定性结构化压缩（零 LLM 调用、零额外延迟、零成本）：
- 短对话（条数≤K 或预算内）→ 逐字节原样返回（零变化保证）。
- 长对话 → 一条结构化摘要 + 最近 K 条原文：
    [早前对话摘要]
    用户: 帮我写一个 C++ 的红黑树实现…      ← 首两轮永远锚定（最初要什么最不能忘）
    …（中间 18 轮略）…                        ← 预算不够时折叠中间，保头保尾
    用户: 再加上迭代器支持…
    助手: 已更新 rbtree.h…
    本会话已生成/提到的文件: rbtree.h, main.cpp ← 跨数十轮仍知道有哪些产物
- 防套娃：旧摘要再次进入压缩时只取头部脉络，标记不嵌套。
- 永不抛错：任何异常退回"尾部截取"（与旧行为等价），绝不让压缩本身搞挂请求。

为什么不用 LLM 精炼：标题/摘要类小活在云端推理模型上动辄 10s+（见 V50 title 修复），
而压缩在【每次请求的关键路径】上。确定性提取是大厂在关键路径上的同款取舍；
LLM 精炼可后续作为离线任务叠加（默认关）。
"""
from __future__ import annotations

import re

SUMMARY_MARK = "[早前对话摘要]"

# 摘要正文的字符上限（不含最近 K 条）
_SUMMARY_CAP = 3500
# 单条"最近消息"的长度上限（防一条巨型消息独占预算）
_RECENT_MSG_CAP = 4000
# 每行脉络的截断长度
_LINE_CAP = 90
# 文件名提取（中英文件名 + 常见扩展名）
_FILE_RE = re.compile(
    r"\b[\w\-\u4e00-\u9fff]{1,40}\.(?:py|cpp|cc|cxx|h|hpp|c|js|ts|tsx|jsx|java|go|rs|"
    r"md|txt|json|yaml|yml|toml|csv|xlsx|docx|pptx|pdf|html|css|sql|sh)\b"
)


def _one_line(role: str, content: str) -> str:
    label = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(role, str(role))
    c = " ".join(str(content or "").split())
    if len(c) > _LINE_CAP:
        c = c[:_LINE_CAP] + "…"
    return f"{label}: {c}"


def compact_history(
    history: list | None,
    keep_recent: int = 6,
    char_budget: int = 16000,
) -> list[dict]:
    """压缩对话历史。详见模块 docstring。

    Args:
        history: [{role, content, ...}] 列表（脏数据容忍）。
        keep_recent: 原样保留的最近条数。
        char_budget: 历史总字符预算（超过才触发压缩）。
    """
    try:
        if not history:
            return []
        hist = [h for h in history if isinstance(h, dict)]
        total = sum(len(str(h.get("content") or "")) for h in hist)
        if len(hist) <= keep_recent or total <= char_budget:
            return hist  # 零变化保证

        old, recent = hist[:-keep_recent], hist[-keep_recent:]

        # ── 旧轮 → 逐轮一行脉络 + 文件名收集 ──
        lines: list[str] = []
        files: list[str] = []
        for h in old:
            content = str(h.get("content") or "")
            if SUMMARY_MARK in content:
                # 防套娃：旧摘要只取头部脉络（去掉标记行），不嵌套
                content = content.replace(SUMMARY_MARK, "").strip()[:300]
            files.extend(_FILE_RE.findall(content))
            lines.append(_one_line(h.get("role", "user"), content))

        # ── 预算内取材：首两行永远锚定（开场需求），尾部从新到旧回填，中间折叠 ──
        head = lines[:2]
        used = sum(len(ln) for ln in head)
        tail: list[str] = []
        for ln in reversed(lines[2:]):
            if used + len(ln) > _SUMMARY_CAP:
                break
            tail.append(ln)
            used += len(ln)
        tail.reverse()
        folded = len(lines) - len(head) - len(tail)
        body = head + ([f"…（中间 {folded} 轮略）…"] if folded > 0 else []) + tail

        parts = [SUMMARY_MARK, *body]
        uniq_files = list(dict.fromkeys(f for f in files if f))[:15]
        if uniq_files:
            parts.append("本会话已生成/提到的文件: " + ", ".join(uniq_files))
        summary_msg = {"role": "user", "content": "\n".join(parts)}

        # ── 最近 K 条原样，仅对单条巨型消息截断 ──
        out_recent: list[dict] = []
        for h in recent:
            content = str(h.get("content") or "")
            if len(content) > _RECENT_MSG_CAP:
                h = {**h, "content": content[:_RECENT_MSG_CAP] + "\n…[本条已截断]"}
            out_recent.append(h)

        return [summary_msg] + out_recent
    except Exception:
        # 压缩永不搞挂请求：退回与旧行为等价的尾部截取
        try:
            return [h for h in list(history)[-keep_recent:] if isinstance(h, dict)]
        except Exception:
            return []
