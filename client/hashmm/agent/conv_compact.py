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

import os
import re
from dataclasses import dataclass, field

from hashmm.agent.long_horizon import render_handoff

SUMMARY_MARK = "[早前对话摘要]"
PERSISTENT_SUMMARY_MARK = "[会话压缩检查点 / durable context checkpoint]"

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

# ══════════ V273 Pi 规范件（资料 13.3.6）：阈值触发 · token 口径 · LLM 交接单 ══════════
# 既有确定性压缩保持零变化；以下为**可选叠加**：
#  should_compact(): "当前上下文 token > 窗口 − 预留(默认 16384)" 才压——预留是给
#    压缩本身的请求与摘要输出留的（Pi 原文取舍）。token 优先信真实 usage，退化 chars/4。
#  compact_history_llm(): 保留最近 keep_recent_tokens(默认 20000, 从新往旧累加)原文，
#    更早部分交给 LLM 生成"交接班检查点"——切点只认 user/assistant 边界、绝不把
#    tool 结果当切点（不切断工具调用对）；摘要提示词 = 结构化交接单模板，Do NOT 续写，
#    路径/函数名/报错必须原样保留。任何失败回落确定性 compact_history。
RESERVE_TOKENS = 16384
KEEP_RECENT_TOKENS = 20000

_HANDOFF_PROMPT = (
    "你是上下文摘要助手。读完这段对话后，只按下面模板输出结构化摘要，"
    "不要继续对话、不要回答对话里的任何问题（Do NOT answer, Do NOT continue）。\n"
    "模板：\n【目标】用户最初和当前想要什么\n【进度】已完成/已产出（文件名逐一列出）\n"
    "【关键决策】做过的选择与理由\n【未决与下一步】\n【必须原样保留】精确的文件路径、"
    "函数名、报错信息，逐条抄录不许改写\n\n对话如下：\n"
)


def estimate_tokens(history: list | None) -> int:
    """chars/4 估算（Pi 的退化口径）。有真实 usage 时请优先传 usage 给 should_compact。"""
    try:
        return sum(len(str(h.get("content") or "")) for h in (history or []) if isinstance(h, dict)) // 4
    except Exception:  # noqa: BLE001
        return 0


def should_compact(context_tokens: int, model_window: int = 128000,
                   reserve_tokens: int = RESERVE_TOKENS) -> bool:
    try:
        return int(context_tokens) > int(model_window) - int(reserve_tokens)
    except Exception:  # noqa: BLE001
        return False


def _split_by_recent_tokens(hist: list[dict], keep_recent_tokens: int) -> int:
    """从最新往回累加 token，返回切点下标（其之前交给摘要）。切点只落在
    user/assistant 边界，跳过 tool（不切断工具调用对）。"""
    acc = 0
    cut = 0
    for i in range(len(hist) - 1, -1, -1):
        acc += len(str(hist[i].get("content") or "")) // 4
        if acc >= keep_recent_tokens:
            cut = i
            break
    while 0 < cut < len(hist) and str(hist[cut].get("role")) not in ("user", "assistant"):
        cut -= 1   # 回退到合法边界，宁多留原文不切坏语义
    return max(cut, 0)


def compact_history_llm(history: list | None, llm_fn=None,
                        keep_recent_tokens: int = KEEP_RECENT_TOKENS) -> list[dict]:
    """LLM 交接单压缩；无 llm_fn / 早段太短 / 任一失败 → 回落确定性 compact_history。"""
    try:
        hist = [h for h in (history or []) if isinstance(h, dict)]
        if not callable(llm_fn) or len(hist) < 8:
            return compact_history(hist)
        cut = _split_by_recent_tokens(hist, keep_recent_tokens)
        if cut < 2:
            return hist
        early, recent = hist[:cut], hist[cut:]
        convo = "\n".join(_one_line(str(h.get("role")), str(h.get("content") or ""))
                           for h in early)[:24000]
        summ = str(llm_fn(_HANDOFF_PROMPT + convo) or "").strip()
        if not summ or len(summ) < 30:
            return compact_history(hist)
        node = {"role": "system", "content": f"{SUMMARY_MARK}（交接单）\n{summ[:_SUMMARY_CAP]}"}
        return [node] + recent
    except Exception:  # noqa: BLE001
        return compact_history(history)


# ── V358 durable incremental compaction ──────────────────────────────────
# ``compact_history`` above remains the small, pure compatibility primitive.
# This layer persists a cursor + cumulative checkpoint so a 500/5,000 turn
# thread does not silently become "the latest 80 messages" on every request.
_CHECKPOINT_CAP = 7000


@dataclass
class PreparedConversationContext:
    history: list[dict]
    compacted_now: bool = False
    source_messages: int = 0
    working_tokens: int = 0
    compaction_count: int = 0
    through_rowid: int = 0
    degraded: bool = False
    degraded_reason: str = ""
    checkpoint_schema: str = "hashmm.context-checkpoint.v3"
    compaction: dict = field(default_factory=dict)


def _context_tokens(messages: list[dict]) -> int:
    """Conservative mixed Chinese/Latin estimate for admission control.

    It is explicitly an estimate, never billing evidence. Two chars/token is
    deliberately safer than the old chars/4 heuristic for Chinese-heavy chat.
    """
    chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        chars += len(str(message.get("content") or ""))
        # Admission control must account for the structured evidence that will
        # be folded into a checkpoint even when an assistant placeholder has no
        # prose content.
        for key in ("tool_calls", "files", "sources", "groundings", "run_manifest"):
            chars += len(str(message.get(key) or ""))
    return max(0, (chars + 1) // 2 + len(messages) * 4)


def _metadata_present(message: dict) -> bool:
    return any(message.get(key) for key in (
        "tool_calls", "files", "sources", "groundings", "run_manifest",
    ))


def _tool_name(item: dict) -> str:
    function = item.get("function") if isinstance(item.get("function"), dict) else {}
    return str(item.get("tool") or item.get("name") or item.get("node")
               or function.get("name") or "").strip()


def _evidence_lines(messages: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Extract only persisted identifiers/statuses; never infer model claims."""
    tools: list[str] = []
    sources: list[str] = []
    incomplete: list[str] = []
    for message in messages:
        for item in message.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            name = _tool_name(item)
            status = str(item.get("status") or item.get("result_status") or "recorded").strip()
            if name:
                tools.append(f"{name} [{status}]")
            if status.lower() in {"failed", "error", "denied", "cancelled", "interrupted", "pending"}:
                incomplete.append(f"工具 {name or 'unknown'}: {status}")
        for item in message.get("sources") or []:
            if isinstance(item, dict):
                identity = str(item.get("url") or item.get("source") or item.get("title")
                               or item.get("id") or "").strip()
            else:
                identity = str(item or "").strip()
            if identity:
                sources.append(identity[:360])
        manifest = message.get("run_manifest") or {}
        if isinstance(manifest, dict):
            for check in manifest.get("checks") or manifest.get("verification") or []:
                if not isinstance(check, dict):
                    continue
                label = str(check.get("name") or check.get("check") or "verification").strip()
                status = str(check.get("status") or check.get("result") or "recorded").strip()
                tools.append(f"验证 {label} [{status}]")
                if status.lower() not in {"passed", "pass", "ok", "success", "complete", "completed"}:
                    incomplete.append(f"验证 {label}: {status}")
    return (
        list(dict.fromkeys(tools))[-30:],
        list(dict.fromkeys(sources))[-20:],
        list(dict.fromkeys(incomplete))[-20:],
    )


def _working_message(row: dict) -> dict:
    """Return provider-safe role/content while retaining metadata as facts.

    Provider adapters are not given private database keys.  Tool-only turns are
    represented by a compact deterministic note so compaction no longer drops
    their existence, status or artifacts.
    """
    role = str(row.get("role") or "user")
    content = str(row.get("content") or "").strip()
    if not content and _metadata_present(row):
        tools, sources, incomplete = _evidence_lines([row])
        artifacts = _artifact_names([row])
        lines = ["[持久化运行记录；仅包含数据库中已有的标识与状态]"]
        if tools:
            lines.append("工具/验证: " + "; ".join(tools))
        if artifacts:
            lines.append("产物: " + ", ".join(artifacts))
        if sources:
            lines.append("来源: " + "; ".join(sources))
        if incomplete:
            lines.append("未完成/失败: " + "; ".join(incomplete))
        content = "\n".join(lines)
    return {"role": role, "content": content}


def _artifact_names(messages: list[dict]) -> list[str]:
    names: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")
        names.extend(_FILE_RE.findall(content))
        for item in message.get("files") or []:
            if isinstance(item, dict):
                name = str(item.get("filename") or item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                names.append(name)
    return list(dict.fromkeys(names))[:40]


def _bounded_checkpoint(previous: str, messages: list[dict]) -> str:
    """Build a deterministic handoff record without inventing facts."""
    previous = str(previous or "").strip()
    users = [" ".join(str(m.get("content") or "").split())
             for m in messages if m.get("role") == "user" and str(m.get("content") or "").strip()]
    assistants = [" ".join(str(m.get("content") or "").split())
                  for m in messages if m.get("role") == "assistant" and str(m.get("content") or "").strip()]
    constraint_words = ("必须", "不能", "不要", "需要", "要求", "保持", "使用", "对标", "只允许")
    progress_words = ("完成", "修复", "创建", "生成", "通过", "失败", "决定", "采用", "验证")
    constraints = [text for text in users if any(word in text for word in constraint_words)][-18:]
    progress = [text for text in assistants if any(word in text for word in progress_words)][-18:]
    artifacts = _artifact_names(messages)
    tools, sources, incomplete = _evidence_lines(messages)
    evidence: list[dict] = []
    for item in tools:
        match = re.match(r"^(.*?)\s+\[([^\]]+)\]$", item)
        evidence.append({
            "label": (match.group(1) if match else item),
            "status": (match.group(2) if match else "recorded"),
            "source": "persisted_tool_or_verification",
        })
    evidence.extend({
        "label": source,
        "status": "recorded",
        "source": "persisted_source_identifier",
    } for source in sources)
    reported = [
        {
            "summary": "已报告进展（待运行证据核验）: " + text[:_LINE_CAP * 3],
            "source": "assistant_prose_unverified",
        }
        for text in progress
    ]
    handoff = {
        "prior_checkpoint": previous,
        "goal": users[0][:_LINE_CAP * 3] if users else "",
        "current_intent": users[-1][:_LINE_CAP * 3] if users else "",
        "constraints": [text[:_LINE_CAP * 3] for text in constraints],
        "decisions": reported,
        "evidence": evidence,
        "artifacts": [{"name": name, "status": "mentioned"} for name in artifacts],
        "blockers": incomplete,
        "next_actions": (
            ["继续处理最近要求并逐项验证未完成状态"] if users or incomplete else []
        ),
    }
    return render_handoff(handoff, limit=_CHECKPOINT_CAP)


def prepare_persistent_history(db, conv_id: str, *, token_budget: int | None = None,
                               keep_recent_tokens: int | None = None,
                               force: bool = False,
                               trigger: str = "auto",
                               _retry_on_conflict: bool = True) -> PreparedConversationContext:
    """Return a bounded, recoverable working history for one conversation.

    Full messages remain in SQLite/Supabase. Only older turns folded into the
    model input are represented by the durable checkpoint. Any failure falls
    back to the existing recent-history path and never blocks Chat.
    """
    try:
        budget = max(4000, int(token_budget or os.environ.get(
            "HASHMM_CONTEXT_WORKING_TOKENS", "24000")))
        keep_tokens = max(2000, min(budget - 1000, int(keep_recent_tokens or os.environ.get(
            "HASHMM_CONTEXT_KEEP_RECENT_TOKENS", "12000"))))
        state = db.get_context_compaction(conv_id) or {}
        through = max(0, int(state.get("through_rowid") or 0))
        previous = str(state.get("summary") or "")
        rows = db.get_context_messages_after(conv_id, through)

        # A checkpoint cursor deleted by rewind is invalid. Rebuild from full
        # history rather than trusting a summary of turns that no longer exist.
        if through:
            with db._conn() as conn:
                anchor = conn.execute("SELECT 1 FROM messages WHERE rowid=? AND conv_id=?",
                                      (through, conv_id)).fetchone()
            if anchor is None:
                db.invalidate_context_compaction(conv_id)
                state, through, previous = {}, 0, ""
                rows = db.get_context_messages_after(conv_id, 0)

        # The assistant placeholder is created before streaming starts. It is
        # not conversation evidence until content is persisted.
        rows = [row for row in rows if str(row.get("content") or "").strip()
                or _metadata_present(row)]

        prefix = ([{"role": "system", "content": previous}] if previous else [])
        working = prefix + [_working_message(row) for row in rows]
        tokens = _context_tokens(working)
        base_telemetry = {
            "schema": "hashmm.context-compaction.v1",
            "trigger": "manual" if str(trigger).lower() == "manual" else "automatic",
            "reason": (
                "manual_request" if force
                else "model_window_pressure"
            ),
            "strategy": "durable_incremental_handoff",
            "status": "not_needed",
            "tokens_before": tokens,
            "tokens_after": tokens,
            "working_budget": budget,
            "pressure": round(tokens / max(1, budget), 4),
            "source_messages": 0,
            "full_history_deleted": False,
        }
        if tokens <= budget and not force:
            return PreparedConversationContext(
                working, False, 0, tokens, int(state.get("compaction_count") or 0),
                through, compaction=base_telemetry,
            )

        # Keep a verbatim recent tail. Start it at a user boundary when possible
        # so a user/assistant turn is not split across summary and raw history.
        if force:
            # Manual compaction is useful before the automatic threshold. Keep
            # the newest third verbatim (at least two, at most 24 messages) and
            # fold the rest. Fewer than four new messages is a deterministic
            # no-op, preventing repeated clicks from manufacturing checkpoints.
            if len(rows) < 4:
                return PreparedConversationContext(
                    working, False, 0, tokens, int(state.get("compaction_count") or 0),
                    through, compaction={
                        **base_telemetry,
                        "reason": "manual_no_new_boundary",
                    },
                )
            keep_count = max(2, min(24, len(rows) // 3))
            cut = len(rows) - keep_count
        else:
            acc, cut = 0, len(rows)
            for index in range(len(rows) - 1, -1, -1):
                acc += _context_tokens([rows[index]])
                if acc >= keep_tokens:
                    cut = index
                    break
        while 0 < cut < len(rows) and rows[cut].get("role") != "user":
            cut -= 1
        if cut <= 0:
            # One enormous recent turn cannot be safely summarized as "old".
            return PreparedConversationContext(
                working, False, 0, tokens, int(state.get("compaction_count") or 0),
                through, compaction={
                    **base_telemetry,
                    "reason": "recent_turn_exceeds_safe_boundary",
                },
            )

        older, recent = rows[:cut], rows[cut:]
        checkpoint = _bounded_checkpoint(previous, older)
        new_through = int(older[-1].get("_rowid") or through)
        save_args = (
            conv_id, checkpoint, new_through, len(older), _context_tokens([
                {"role": "system", "content": checkpoint}
            ] + [_working_message(row) for row in recent]),
        )
        save_kwargs = {
            "trigger": ("manual" if str(trigger).lower() == "manual" else "auto"),
            "expected_through_rowid": through,
        }
        try:
            saved = db.save_context_compaction(*save_args, **save_kwargs)
        except TypeError:
            # Compatibility for external database adapters that have not yet
            # adopted the optional CAS argument. The bundled SQLite adapter is
            # authoritative and always uses CAS.
            save_kwargs.pop("expected_through_rowid", None)
            saved = db.save_context_compaction(*save_args, **save_kwargs)
        if saved.get("conflict"):
            if _retry_on_conflict:
                return prepare_persistent_history(
                    db, conv_id, token_budget=budget, keep_recent_tokens=keep_tokens,
                    force=force, trigger=trigger, _retry_on_conflict=False,
                )
            fallback = db.get_recent_messages(conv_id, n=80)
            bounded = compact_history(fallback, keep_recent=8, char_budget=16000)
            return PreparedConversationContext(
                bounded, False, 0, _context_tokens(bounded),
                int(saved.get("compaction_count") or 0),
                int(saved.get("through_rowid") or 0), True,
                "concurrent_compaction_conflict",
                compaction={
                    **base_telemetry,
                    "status": "degraded",
                    "reason": "concurrent_compaction_conflict",
                    "tokens_after": _context_tokens(bounded),
                },
            )
        out = [{"role": "system", "content": checkpoint}] + [
            _working_message(row) for row in recent
        ]
        return PreparedConversationContext(
            out, True, len(older), _context_tokens(out),
            int(saved.get("compaction_count") or 1), new_through,
            compaction={
                **base_telemetry,
                "status": "compacted",
                "tokens_after": _context_tokens(out),
                "source_messages": len(older),
                "through_rowid": new_through,
            },
        )
    except Exception as exc:
        fallback = db.get_recent_messages(conv_id, n=80)
        bounded = compact_history(fallback, keep_recent=8, char_budget=16000)
        return PreparedConversationContext(
            bounded, False, 0, _context_tokens(bounded), 0, 0, True,
            f"{type(exc).__name__}: {str(exc)[:180]}",
            compaction={
                "schema": "hashmm.context-compaction.v1",
                "trigger": "manual" if str(trigger).lower() == "manual" else "automatic",
                "reason": "compaction_error",
                "strategy": "bounded_recent_fallback",
                "status": "degraded",
                "tokens_before": 0,
                "tokens_after": _context_tokens(bounded),
                "working_budget": int(token_budget or 0),
                "pressure": 0,
                "source_messages": 0,
                "full_history_deleted": False,
            },
        )
