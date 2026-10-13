"""hashmm/api/routes/context_inspect.py — 上下文透视（V279）。

对标面试资料 10.3.5（OpenClaw：`/context list` 可查看每个引导文件的注入状态）与
13.3.3（PI-Agent：Context 组装透明化）：**一眼看清"这轮对话的 system 上下文由哪些块
组成、每块多少字符、内容预览"**——排查"为什么模型知道/不知道某事"、控制上下文预算
（资料要点：引导文件每轮都注入，"内容越多占用越大，都应尽量精简"）全靠它。

GET /api/context/inspect?conv_id=xxx
返回 blocks（每块 {id, name, chars, present, preview, note}）+ 总字符 + 精简建议。
只读观测：直接调各真实注入源（不复制组装逻辑），任何一源失败该块标注错误、不影响其余。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from hashmm.api.auth import require_auth, require_conv_access
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.context_inspect")
router = APIRouter(prefix="/api/context", tags=["context"])

_PREVIEW = 140
# 资料 10.3.3 的量级提示（OpenClaw：单文件 2 万、总 15 万字符会截断）——用作健康线参考
_WARN_BLOCK = 20000
_WARN_TOTAL = 60000   # 本项目更保守：注入总量超 6 万字符提示精简


def _blk(bid: str, name: str, text: str, note: str = "") -> dict:
    t = str(text or "")
    return {"id": bid, "name": name, "present": bool(t.strip()),
            "chars": len(t), "preview": t.strip()[:_PREVIEW], "note": note}


def collect_blocks(uid: str, conv_id: str = "", query: str = "") -> dict:
    """纯组装（无 Request 依赖，供端点与测试中枢共用）。**永不抛错**。"""
    blocks: list[dict] = []

    # ⓪ 基础系统提示（最大注入源）：按每轮 task_type（open/code/doc…）动态选择，
    # 不属于"启动清单"，但必须让人知道它存在且规模最大——否则透视看起来"全空"。
    blocks.append(_blk("base_prompt", "基础系统提示（人格+任务指令，动态按任务类型）", "",
                       note="每轮按 open/code/doc 等任务类型选择模板注入（约 1~3 千字），"
                            "外加诚实拒答护栏；逐轮动态生成，故此处不展示正文"))

    # ① 项目规则（HASHMM.md 多级引导文件——本项目的 Bootstrap Files）
    try:
        from hashmm.project_instructions import load_layers
        layers = load_layers(query or "")
        if layers:
            for L in layers:
                blocks.append(_blk(f"rules:{L.get('layer', '?')}",
                                   f"引导文件 · {L.get('title', L.get('layer', ''))}",
                                   L.get("text", ""),
                                   note=str(L.get("source", ""))))
        else:
            blocks.append(_blk("rules", "引导文件（HASHMM.md 各级）", "",
                               note="未发现任何层——可在项目根/用户目录创建 HASHMM.md"))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        blocks.append(_blk("rules", "引导文件（HASHMM.md 各级）", "", note=f"读取失败：{type(e).__name__}"))

    # ② 长期记忆注入（V281 双源）：主链路每轮真实注入的是 **数据库层** 记忆
    #（streaming: db.get_user_memories(uid, limit=10)）；文件层 user_memory 是反思沉淀层。
    # 此前只看文件层 → 与主链路对不上、永远显示空。现在两层都透视、分别标注。
    try:
        from hashmm.api import database as db
        rows = db.get_user_memories(uid, limit=10) or []
        txt = "\n".join(f"[{r.get('category') or '记忆'}] {r.get('key', '')}: {r.get('value', '')}"
                          for r in rows if isinstance(r, dict))
        blocks.append(_blk("memory_db", "长期记忆注入（主链路 · 数据库层，每轮取 10 条）", txt,
                           note="来源：记忆中心（对话中自动沉淀 + 手动添加）；这是 system 里真正注入的记忆"))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        blocks.append(_blk("memory_db", "长期记忆注入（主链路 · 数据库层）", "",
                           note=f"读取失败：{type(e).__name__}"))
    try:
        from hashmm.agent import user_memory as UM
        blocks.append(_blk("memory_file", "反思沉淀记忆（文件层 · 四类分节）", UM.inject_block(uid),
                           note="来源：记忆自进化（HASHMM_MEMORY_REFLECT 开启后周期沉淀到这里）"))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        blocks.append(_blk("memory_file", "反思沉淀记忆（文件层）", "", note=f"读取失败：{type(e).__name__}"))

    # ③ 用户画像个性化（evolution.user_model）
    try:
        from hashmm.evolution.user_model import get_user_model
        blocks.append(_blk("profile", "用户画像个性化", get_user_model().get_personalization_prompt(uid),
                           note="来源：进化引擎画像"))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        blocks.append(_blk("profile", "用户画像个性化", "", note=f"读取失败：{type(e).__name__}"))

    # ④ 会话运行时补丁（persona / temperature / system_append 等，随会话）
    if conv_id:
        try:
            from hashmm.api import session_runtime as srt
            ov = srt.get_overrides(conv_id) or {}
            txt = ""
            if ov:
                txt = "；".join(f"{k}={str(v)[:60]}" for k, v in ov.items())
            blocks.append(_blk("runtime", "会话运行时补丁（/runtime）", txt,
                               note="persona 专家人格 / 临时温度 / system 追加——仅本会话"))
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            blocks.append(_blk("runtime", "会话运行时补丁", "", note=f"读取失败：{type(e).__name__}"))
    else:
        blocks.append(_blk("runtime", "会话运行时补丁（/runtime）", "",
                           note="未传 conv_id——传入后显示该会话的 persona/温度补丁"))

    # ⑤ 持久长对话检查点。只展示派生摘要及统计，完整消息仍在 messages/Supabase；
    # 该块使用户能确认“是否真的压缩过”，而不是只能相信功能开关。
    if conv_id:
        try:
            from hashmm.api import database as db
            state = db.get_context_compaction(conv_id) or {}
            summary = str(state.get("summary") or "")
            if summary:
                trigger = "手动" if str(state.get("last_trigger") or "") == "manual" else "自动"
                note = (
                    f"已压缩 {int(state.get('source_messages') or 0)} 条早期消息 · "
                    f"检查点 {int(state.get('compaction_count') or 0)} 次 · "
                    f"最近由{trigger}触发 · 更新 {int(state.get('updated_at') or 0)}；完整原文未删除"
                )
            else:
                note = "尚未达到自动压缩阈值；达到预算后会形成持久检查点，完整原文仍保留"
            blocks.append(_blk("conversation_compaction", "长对话压缩检查点", summary, note=note))
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            blocks.append(_blk("conversation_compaction", "长对话压缩检查点", "",
                               note=f"读取失败：{type(e).__name__}"))
    else:
        blocks.append(_blk("conversation_compaction", "长对话压缩检查点", "",
                           note="选择一条 Chat 后显示该会话的压缩状态"))

    # ⑥ 动态块说明（逐轮生成，不在此静态清单里，但要让人知道它们存在）
    blocks.append(_blk("dynamic", "检索注入 / 工具结果（动态）", "",
                       note="逐轮按问题实时生成（RAG 命中、工具输出、诚实拒答护栏），不在启动清单中"))

    total = sum(b["chars"] for b in blocks)
    tips: list[str] = []
    for b in blocks:
        if b["chars"] > _WARN_BLOCK:
            tips.append(f"「{b['name']}」已 {b['chars']} 字符，超过 {_WARN_BLOCK} 建议线——每轮都注入，建议精简（资料 10.3.3）")
    if total > _WARN_TOTAL:
        tips.append(f"注入总量 {total} 字符偏大——考虑收敛引导文件与记忆条目")
    if not tips:
        tips.append("注入量健康：引导文件保持精简（它们每轮都占上下文）")

    return {"ok": True, "conv_id": conv_id, "blocks": blocks,
            "total_chars": total, "tips": tips}


@router.get("/inspect")
async def context_inspect(request: Request, conv_id: str = "", query: str = ""):
    user = require_auth(request)
    # V281：uid 键名对齐主链路（conversations 等均用 user["uid"]；此前误用 username 导致
    # 记忆/画像按错误主体查询、永远为空——透视"5 块全未注入"的根因之一）
    uid = str(user.get("uid") or user.get("username") or user.get("id") or "")

    # Context inspection is conversation data, not a global demo page. Resolve an
    # omitted id to the user's latest conversation and owner-check an explicit id
    # before reading runtime overrides. This also prevents probing another user's
    # conversation id through the inspector.
    from hashmm.api import database as db
    conv = None
    if conv_id:
        conv = require_conv_access(request, conv_id)
    else:
        latest = db.list_conversations(uid, limit=1) or []
        if latest:
            conv = latest[0]
            conv_id = str(conv.get("id") or "")

    # When the App does not provide a query, use the actual latest user turn. The
    # project-rule loader can therefore show the same layered rules that this Chat
    # would select instead of a context-free placeholder.
    if conv_id and not query:
        try:
            messages = db.get_latest_messages(conv_id, limit=200) or []
            for message in reversed(messages):
                if message.get("role") == "user" and str(message.get("content") or "").strip():
                    query = str(message.get("content") or "").strip()
                    break
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)

    out = collect_blocks(uid, conv_id=conv_id, query=query)
    out["conv_title"] = str((conv or {}).get("title") or "")
    out["query"] = query[:500]
    out["resolved_latest"] = bool(conv_id and not request.query_params.get("conv_id"))
    return out
